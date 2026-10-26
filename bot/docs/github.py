import logging
from dataclasses import dataclass
from urllib.parse import quote

import httpx

log = logging.getLogger("bot.docs")

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"

DOC_EXTENSIONS = (".md", ".mdx", ".markdown", ".rst", ".txt")
# .txt files that are build or dependency lists, not documentation.
SKIP_NAMES = ("cmakelists.txt", "requirements.txt", "requirements-dev.txt", "license.txt")
MAX_FILE_BYTES = 200_000


@dataclass(frozen=True)
class RepoSpec:
    """One entry of DOCS_REPOS, written as owner/repo[@ref][:path/prefix]."""

    owner: str
    repo: str
    ref: str | None = None
    path_prefix: str = ""

    @classmethod
    def parse(cls, text: str) -> "RepoSpec":
        text = text.strip()
        path_prefix = ""
        if ":" in text:
            text, path_prefix = text.split(":", 1)
        ref = None
        if "@" in text:
            text, ref = text.split("@", 1)
        owner, sep, repo = text.partition("/")
        if not sep or not owner or not repo or "/" in repo:
            raise ValueError(f"Bad DOCS_REPOS entry {text!r}, expected owner/repo[@ref][:path]")
        return cls(owner, repo, ref or None, path_prefix.strip("/"))

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass(frozen=True)
class Document:
    repo: str  # owner/repo
    path: str
    url: str  # link a user can open in a browser
    text: str


def parse_repo_list(value: str) -> list[RepoSpec]:
    return [RepoSpec.parse(part) for part in value.split(",") if part.strip()]


class GitHubDocsFetcher:
    """Downloads documentation files from public (or token-accessible) repos."""

    def __init__(
        self,
        token: str = "",
        max_files_per_repo: int = 300,
        client: httpx.AsyncClient | None = None,
    ):
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            # Optional, but raises the API limit from 60 to 5000 requests an hour
            # and allows private repos.
            headers["Authorization"] = f"Bearer {token}"
        self._headers = headers
        self._has_token = bool(token)
        self._max_files = max_files_per_repo
        self._client = client or httpx.AsyncClient(timeout=30, follow_redirects=True)

    async def fetch(self, spec: RepoSpec) -> list[Document]:
        ref = spec.ref or await self._default_branch(spec)

        tree = await self._get_json(
            f"{API}/repos/{spec.full_name}/git/trees/{quote(ref, safe='')}?recursive=1"
        )
        if tree.get("truncated"):
            log.warning("%s tree is truncated; some docs may be missing", spec.full_name)

        paths = [
            item["path"]
            for item in tree.get("tree", [])
            if item.get("type") == "blob"
            and item["path"].lower().endswith(DOC_EXTENSIONS)
            and item["path"].rsplit("/", 1)[-1].lower() not in SKIP_NAMES
            and item.get("size", 0) <= MAX_FILE_BYTES
            and _under(item["path"], spec.path_prefix)
        ][: self._max_files]

        documents = []
        for path in paths:
            text = await self._download(spec, ref, path)
            if text is None:
                continue
            documents.append(
                Document(
                    repo=spec.full_name,
                    path=path,
                    url=f"https://github.com/{spec.full_name}/blob/{quote(ref)}/{quote(path)}",
                    text=text,
                )
            )
        return documents

    async def _download(self, spec: RepoSpec, ref: str, path: str) -> str | None:
        # The raw host serves public files without using the API rate limit, so it
        # is tried first, with no token (it rejects some tokens with a 404).
        response = await self._client.get(f"{RAW}/{spec.full_name}/{quote(ref)}/{quote(path)}")
        if response.status_code == 404 and self._has_token:
            # Private repo: the contents API accepts the token.
            response = await self._client.get(
                f"{API}/repos/{spec.full_name}/contents/{quote(path)}",
                params={"ref": ref},
                headers={**self._headers, "Accept": "application/vnd.github.raw"},
            )
        if response.status_code != 200:
            log.warning("Skipping %s/%s (HTTP %s)", spec.full_name, path, response.status_code)
            return None
        return response.text

    async def _default_branch(self, spec: RepoSpec) -> str:
        info = await self._get_json(f"{API}/repos/{spec.full_name}")
        return info["default_branch"]

    async def _get_json(self, url: str) -> dict:
        response = await self._client.get(url, headers=self._headers)
        response.raise_for_status()
        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()


def _under(path: str, prefix: str) -> bool:
    return not prefix or path == prefix or path.startswith(prefix + "/")
