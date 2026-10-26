import logging

from .github import Document, GitHubDocsFetcher, RepoSpec, parse_repo_list
from .index import Chunk, DocsIndex
from .routing import DocsRouter

__all__ = [
    "Chunk",
    "DocsIndex",
    "DocsRouter",
    "Document",
    "GitHubDocsFetcher",
    "RepoSpec",
    "load_docs",
    "parse_repo_list",
]

log = logging.getLogger("bot.docs")


async def load_docs(fetcher: GitHubDocsFetcher, specs: list[RepoSpec]) -> list[Document]:
    """Fetch every configured repo. A failing repo is logged and skipped."""
    documents: list[Document] = []
    for spec in specs:
        try:
            fetched = await fetcher.fetch(spec)
        except Exception:
            log.exception("Could not load docs from %s", spec.full_name)
            continue
        log.info("Loaded %d doc files from %s", len(fetched), spec.full_name)
        documents.extend(fetched)
    return documents
