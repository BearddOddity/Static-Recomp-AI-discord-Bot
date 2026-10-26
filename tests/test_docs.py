import asyncio
from pathlib import Path

import httpx
import pytest

from bot.assist import Turn
from bot.docs import DocsIndex, Document, GitHubDocsFetcher, RepoSpec, parse_repo_list
from bot.main import build_messages


def doc(path: str, text: str) -> Document:
    return Document("org/repo", path, f"https://github.com/org/repo/blob/main/{path}", text)


def test_repo_spec_parsing():
    assert RepoSpec.parse("org/repo") == RepoSpec("org", "repo")
    assert RepoSpec.parse(" org/repo@dev:docs/ ") == RepoSpec("org", "repo", "dev", "docs")
    assert [s.full_name for s in parse_repo_list("a/b, c/d,")] == ["a/b", "c/d"]
    with pytest.raises(ValueError):
        RepoSpec.parse("not-a-repo")


def test_search_prefers_the_matching_section():
    index = DocsIndex(
        [
            doc("README.md", "# Building\nRun cmake to build the recompiler.\n# Mods\nMods load from the mods folder."),
            doc("docs/faq.md", "# Controllers\nGamepads are configured in the settings menu."),
        ]
    )
    results = index.search("how do I configure my gamepad?")
    assert results[0].heading == "Controllers"
    assert index.search("mods folder")[0].heading == "Mods"
    assert index.search("the and of") == []


def test_messages_include_excerpts_and_history():
    chunk = DocsIndex([doc("README.md", "# Setup\nInstall it.")]).search("setup install")[0]
    messages = build_messages("Be helpful.", "setup?", [chunk], history=[Turn("q1", "a1")])
    assert [m.role for m in messages][-3:] == ["user", "assistant", "user"]
    assert any("Install it." in m.content for m in messages if m.role == "system")
    assert "(Setup)" in next(m.content for m in messages if "Install it." in m.content)


def test_fetcher_lists_tree_and_downloads_doc_files_under_prefix():
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/repos/org/repo":
            return httpx.Response(200, json={"default_branch": "main"})
        if request.url.path == "/repos/org/repo/git/trees/main":
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {"path": "docs/guide.md", "type": "blob", "size": 10},
                        {"path": "docs/logo.png", "type": "blob", "size": 10},
                        {"path": "docs/CMakeLists.txt", "type": "blob", "size": 10},
                        {"path": "src/notes.md", "type": "blob", "size": 10},
                        {"path": "docs", "type": "tree"},
                    ]
                },
            )
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(200, text="# Guide\nHello")
        return httpx.Response(404)

    fetcher = GitHubDocsFetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    documents = asyncio.run(fetcher.fetch(RepoSpec("org", "repo", path_prefix="docs")))

    assert [d.path for d in documents] == ["docs/guide.md"]
    assert documents[0].url == "https://github.com/org/repo/blob/main/docs/guide.md"
    assert "https://raw.githubusercontent.com/org/repo/main/docs/guide.md" in requested


def test_private_repo_falls_back_to_contents_api_with_token():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "raw.githubusercontent.com":
            assert "authorization" not in request.headers
            return httpx.Response(404)
        if request.url.path == "/repos/org/private/git/trees/main":
            return httpx.Response(200, json={"tree": [{"path": "README.md", "type": "blob", "size": 5}]})
        if request.url.path == "/repos/org/private/contents/README.md":
            assert request.headers["authorization"] == "Bearer secret"
            assert request.url.params["ref"] == "main"
            return httpx.Response(200, text="# Private\nDocs")
        return httpx.Response(404)

    fetcher = GitHubDocsFetcher(
        token="secret", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    documents = asyncio.run(fetcher.fetch(RepoSpec("org", "private", ref="main")))
    assert [d.text for d in documents] == ["# Private\nDocs"]


def test_wiki_spec_counts_as_its_base_repo():
    spec = RepoSpec.parse("example/game-tools.wiki")
    assert spec.is_wiki
    assert spec.base_name == "example/game-tools"
    assert not RepoSpec.parse("org/repo").is_wiki


def test_wiki_pages_become_documents(tmp_path):
    from bot.docs.github import wiki_documents

    (tmp_path / "Home.md").write_text("# Welcome\nStart here.")
    (tmp_path / "Kernel-State-&-Objects.md").write_text("# Kernel\nObjects.")
    (tmp_path / "_Sidebar.md").write_text("nav")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD.md").write_text("not a page")

    documents = wiki_documents(RepoSpec.parse("org/repo.wiki"), tmp_path)
    assert [d.path for d in documents] == ["wiki/Home.md", "wiki/Kernel-State-&-Objects.md"]
    assert {d.repo for d in documents} == {"org/repo"}
    assert documents[1].url == "https://github.com/org/repo/wiki/Kernel-State-%26-Objects"


def test_rate_limited_api_falls_back_to_git_clone(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "API rate limit exceeded"})

    async def fake_clone(url, dest, env, branch=None):
        root = Path(dest)
        (root / "docs").mkdir()
        (root / "docs" / "guide.md").write_text("# Guide\nHello")
        (root / "src.cpp").write_text("int main() {}")
        (root / "tests").mkdir()
        (root / "tests" / "notes.md").write_text("# skip me")

    fetcher = GitHubDocsFetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(fetcher, "clone", fake_clone)
    documents = asyncio.run(fetcher.fetch(RepoSpec("org", "repo")))
    assert [d.path for d in documents] == ["docs/guide.md"]
    assert documents[0].url == "https://github.com/org/repo/blob/HEAD/docs/guide.md"
