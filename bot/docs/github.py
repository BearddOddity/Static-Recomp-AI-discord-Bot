import asyncio
import base64
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

log = logging.getLogger("bot.docs")

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"

DOC_EXTENSIONS = (".md", ".mdx", ".markdown", ".rst", ".txt")
# .txt files that are build or dependency lists, not documentation.
SKIP_NAMES = ("cmakelists.txt", "requirements.txt", "requirements-dev.txt", "license.txt")
# Folders of bundled libraries, licenses and test data rather than project docs.
SKIP_DIRS = frozenset(
    {
        "third_party", "thirdparty", "vendor", "external", "node_modules",
        "licenses", "fixtures", "scratch", "tests", "test",
    }
)
MAX_FILE_BYTES = 200_000


WIKI_SUFFIX = ".wiki"
WIKI_CLONE_TIMEOUT = 120


@dataclass(frozen=True)
class RepoSpec:
    """One entry of DOCS_REPOS, written as owner/repo[@ref][:path/prefix].

    owner/repo.wiki means the repo's GitHub wiki instead of its files.
    """

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

    @property
    def is_wiki(self) -> bool:
        return self.repo.lower().endswith(WIKI_SUFFIX)

    @property
    def base_name(self) -> str:
        """owner/repo without .wiki, so a wiki's pages count as part of its repo."""
        if self.is_wiki:
            return f"{self.owner}/{self.repo[: -len(WIKI_SUFFIX)]}"
        return self.full_name


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
        self._token = token
        self._has_token = bool(token)
        self._max_files = max_files_per_repo
        self._client = client or httpx.AsyncClient(timeout=30, follow_redirects=True)

    async def fetch(self, spec: RepoSpec) -> list[Document]:
        if spec.is_wiki:
            return await self.fetch_wiki(spec)
        try:
            return await self._fetch_with_api(spec)
        except httpx.HTTPStatusError as error:
            # Usually GitHub's 60-requests-an-hour limit for bots without a token, which
            # a shared server IP can use up. A public repo can still be cloned with git.
            if error.response.status_code not in (403, 429) or self._has_token:
                raise
            log.warning("GitHub API refused %s (HTTP %s); cloning it with git instead",
                        spec.full_name, error.response.status_code)
            return await self.fetch_clone(spec)

    async def _fetch_with_api(self, spec: RepoSpec) -> list[Document]:
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
            and is_doc_path(item["path"], item.get("size", 0), spec.path_prefix)
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

    async def fetch_clone(self, spec: RepoSpec) -> list[Document]:
        """Read a public repo's docs from a shallow git clone, without the API."""
        url = f"https://github.com/{spec.full_name}.git"
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        ref = spec.ref or "HEAD"
        with tempfile.TemporaryDirectory() as workdir:
            await self.clone(url, workdir, env, branch=spec.ref)
            folder = Path(workdir)
            documents = []
            for file in sorted(folder.rglob("*")):
                relative = file.relative_to(folder).as_posix()
                if (
                    not file.is_file()
                    or relative.startswith(".git/")
                    or not is_doc_path(relative, file.stat().st_size, spec.path_prefix)
                ):
                    continue
                documents.append(
                    Document(
                        repo=spec.full_name,
                        path=relative,
                        url=f"https://github.com/{spec.full_name}/blob/{quote(ref)}/{quote(relative)}",
                        text=file.read_text(encoding="utf-8", errors="replace"),
                    )
                )
                if len(documents) == self._max_files:
                    break
            return documents

    async def fetch_wiki(self, spec: RepoSpec) -> list[Document]:
        """GitHub has no API that lists wiki pages, but every wiki is a small git
        repo, so it is shallow-cloned into a temporary folder and read from there."""
        url = f"https://github.com/{spec.full_name}.git"
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

        with tempfile.TemporaryDirectory() as workdir:
            # Public wikis are cloned without the token, like raw files above.
            try:
                await self.clone(url, workdir, env)
            except RuntimeError:
                if not self._token:
                    raise
                # Private wiki. The token goes through the environment so it never
                # shows in process args.
                basic = base64.b64encode(f"x-access-token:{self._token}".encode()).decode()
                token_env = {
                    **env,
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "http.extraHeader",
                    "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
                }
                workdir = tempfile.mkdtemp(dir=workdir)
                await self.clone(url, workdir, token_env)
            return wiki_documents(spec, Path(workdir))

    @staticmethod
    async def clone(url: str, dest: str, env: dict[str, str], branch: str | None = None) -> None:
        process = await asyncio.create_subprocess_exec(
            "git", "clone", "--quiet", "--depth", "1", *(["--branch", branch] if branch else []), url, dest,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=WIKI_CLONE_TIMEOUT)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError(f"git clone of {url} timed out") from None
        if process.returncode != 0:
            raise RuntimeError(f"git clone of {url} failed: {stderr.decode(errors='replace').strip()[:300]}")

    async def _default_branch(self, spec: RepoSpec) -> str:
        info = await self._get_json(f"{API}/repos/{spec.full_name}")
        return info["default_branch"]

    async def _get_json(self, url: str) -> dict:
        response = await self._client.get(url, headers=self._headers)
        response.raise_for_status()
        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()


def wiki_documents(spec: RepoSpec, folder: Path) -> list[Document]:
    documents = []
    for file in sorted(folder.rglob("*")):
        relative = file.relative_to(folder).as_posix()
        # _Sidebar and _Footer are navigation, not content.
        if (
            not file.is_file()
            or relative.startswith(".git/")
            or file.name.startswith("_")
            or not file.name.lower().endswith(DOC_EXTENSIONS)
            or file.stat().st_size > MAX_FILE_BYTES
            or not _under(relative, spec.path_prefix)
        ):
            continue
        page = file.name.rsplit(".", 1)[0]
        documents.append(
            Document(
                repo=spec.base_name,
                path=f"wiki/{relative}",
                url=f"https://github.com/{spec.base_name}/wiki/{quote(page)}",
                text=file.read_text(encoding="utf-8", errors="replace"),
            )
        )
    return documents


def is_doc_path(path: str, size: int, prefix: str = "") -> bool:
    """A documentation file worth indexing: the right extension, not a build or license
    file, not in a vendored or test folder, not too big, and under the chosen folder."""
    return (
        path.lower().endswith(DOC_EXTENSIONS)
        and path.rsplit("/", 1)[-1].lower() not in SKIP_NAMES
        and not SKIP_DIRS.intersection(path.lower().split("/")[:-1])
        and size <= MAX_FILE_BYTES
        and _under(path, prefix)
    )


def _under(path: str, prefix: str) -> bool:
    return not prefix or path == prefix or path.startswith(prefix + "/")
