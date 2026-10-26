import asyncio

import httpx
import pytest

from bot.docs import DocsIndex, Document, GitHubDocsFetcher, RepoSpec, parse_repo_list
from bot.main import build_messages, format_reply


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


def test_reply_includes_sources_and_fits_discord_limit():
    chunk = DocsIndex([doc("README.md", "# Setup\nInstall it.")]).search("setup install")[0]
    reply = format_reply("x" * 5000, [chunk])
    assert len(reply) <= 2000
    assert reply.endswith("<https://github.com/org/repo/blob/main/README.md>")

    messages = build_messages("Be helpful.", "setup?", [chunk])
    assert [m.role for m in messages] == ["system", "system", "user"]
    assert "Install it." in messages[1].content
    assert format_reply("hi", []) == "hi"


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
    spec = RepoSpec.parse("rexglue/rexglue-sdk.wiki")
    assert spec.is_wiki
    assert spec.base_name == "rexglue/rexglue-sdk"
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
