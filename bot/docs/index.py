import math
import re
from collections import Counter
from dataclasses import dataclass

from .github import Document

CHUNK_CHARS = 1500
HEADING_WEIGHT = 3
_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)
_WORD = re.compile(r"[a-z0-9][a-z0-9_.+-]*")

# Common words that would otherwise match nearly every chunk.
_STOPWORDS = frozenset(
    "a an and are as at be but by can do does for from how i if in is it its me my of on or "
    "so that the this to was what when where which who why will with you your".split()
)


@dataclass(frozen=True)
class Chunk:
    document: Document
    heading: str
    text: str


def _stem(word: str) -> str:
    """Crude suffix stripping so "configured" matches "configure" and "gamepads" "gamepad"."""
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    for suffix in ("ing", "ed", "e"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _tokens(text: str) -> list[str]:
    return [_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]


def split_document(document: Document) -> list[Chunk]:
    """Split on Markdown headings, then cap each section at CHUNK_CHARS."""
    starts = [m.start() for m in _HEADING.finditer(document.text)]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    starts.append(len(document.text))

    chunks = []
    for begin, end in zip(starts, starts[1:]):
        section = document.text[begin:end].strip()
        if not section:
            continue
        first_line = section.splitlines()[0]
        heading = first_line.lstrip("#").strip() if first_line.startswith("#") else document.path
        for offset in range(0, len(section), CHUNK_CHARS):
            chunks.append(Chunk(document, heading, section[offset : offset + CHUNK_CHARS]))
    return chunks


class DocsIndex:
    """A small in-memory BM25 search over documentation chunks."""

    K1 = 1.5
    B = 0.75

    def __init__(self, documents: list[Document] | None = None):
        self._chunks: list[Chunk] = []
        self._term_counts: list[Counter] = []
        self._doc_freq: Counter = Counter()
        self._avg_len = 0.0
        if documents:
            self.replace(documents)

    def __len__(self) -> int:
        return len(self._chunks)

    def replace(self, documents: list[Document]) -> None:
        chunks = [chunk for document in documents for chunk in split_document(document)]
        # A word in the file path or heading says more about a section than one in its body.
        counts = []
        for c in chunks:
            counter = Counter(_tokens(c.text))
            for token in _tokens(f"{c.document.path.replace('_', ' ')} {c.heading}"):
                counter[token] += HEADING_WEIGHT
            counts.append(counter)
        doc_freq: Counter = Counter()
        for counter in counts:
            doc_freq.update(counter.keys())

        self._chunks = chunks
        self._term_counts = counts
        self._doc_freq = doc_freq
        self._avg_len = sum(sum(c.values()) for c in counts) / len(counts) if counts else 0.0

    def search(self, query: str, limit: int = 4, repos: list[str] | None = None) -> list[Chunk]:
        """Best-matching chunks. With repos, only chunks from those owner/repo names."""
        terms = set(_tokens(query))
        if not terms or not self._chunks:
            return []
        allowed = {r.lower() for r in repos} if repos else None

        total = len(self._chunks)
        scored = []
        for chunk, counts in zip(self._chunks, self._term_counts):
            if allowed is not None and chunk.document.repo.lower() not in allowed:
                continue
            length = sum(counts.values()) or 1
            score = 0.0
            for term in terms:
                tf = counts.get(term, 0)
                if not tf:
                    continue
                df = self._doc_freq[term]
                idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
                norm = tf + self.K1 * (1 - self.B + self.B * length / self._avg_len)
                score += idf * tf * (self.K1 + 1) / norm
            if score > 0:
                scored.append((score, chunk))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [chunk for _, chunk in scored[:limit]]
