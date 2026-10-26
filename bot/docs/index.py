import math
import re
from collections import Counter
from dataclasses import dataclass

from .github import Document

CHUNK_CHARS = 1500
HEADING_WEIGHT = 3
# At most this many excerpts from one file, so one long page can't crowd out the rest.
MAX_PER_DOCUMENT = 2
# Score multipliers for looser matches: a synonym, or a near-miss spelling.
SYNONYM_WEIGHT = 0.6
FUZZY_WEIGHT = 0.7
# Bonus for query words that appear side by side in a section ("save file").
PHRASE_BONUS = 1.5

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
_FENCE = re.compile(r"^(```|~~~)", re.MULTILINE)
_WORD = re.compile(r"[a-z0-9][a-z0-9_.+-]*")
_PARTS = re.compile(r"[._+-]+")

# Common words that would otherwise match nearly every chunk.
_STOPWORDS = frozenset(
    "a an and are as at be but by can do does for from how i if in is it its me my of on or "
    "so that the this to was what when where which who why will with you your".split()
)

# Words people ask with versus words docs are written with. Each group is equivalent.
# Keep this generic: it ships with the template, not with one community's profile.
_SYNONYM_GROUPS = (
    "install setup",
    "build compile",
    "controller gamepad joypad joystick input",
    "crash segfault freeze hang",
    "error fail failure problem issue",
    "config configuration setting option",
    "mod patch",
    "fps framerate performance lag stutter",
    "graphics render renderer shader gpu",
    "audio sound",
    "save savegame",
    "download get",
    "linux ubuntu",
    "windows win32 win64",
    "mac macos osx",
    "requirement prerequisite dependency",
    "run launch start",
    "dump rip extract",
    "doc docs documentation guide wiki",
)

_SUFFIXES = (
    ("ations", ""), ("ation", ""), ("ings", ""), ("ing", ""), ("ies", "y"), ("ied", "y"),
    ("ers", ""), ("er", ""), ("ed", ""), ("es", ""), ("ly", ""), ("s", ""), ("e", ""),
)


@dataclass(frozen=True)
class Chunk:
    document: Document
    heading: str  # "Install > Windows": the section and the headings above it
    text: str


def _stem(word: str) -> str:
    """Crude suffix stripping, so "configured", "configuration" and "configure" all match,
    and "recompiler" matches "recompiling". Identifiers such as x86_64 are left alone."""
    if len(word) <= 3 or not word.isalpha():
        return word
    for suffix, replacement in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            if suffix == "s" and word.endswith("ss"):
                continue
            return word[: -len(suffix)] + replacement
    return word


def _words(text: str) -> list[str]:
    """Lowercase words, with compound names also split: "default.xex" adds "default"
    and "xex", and "rexglue-sdk" adds "rexglue" and "sdk"."""
    words = []
    for word in _WORD.findall(text.lower()):
        word = word.strip(".+-")
        words.append(word)
        parts = [p for p in _PARTS.split(word) if p]
        if len(parts) > 1:
            words.extend(parts)
    return words


def _tokens(text: str) -> list[str]:
    return [_stem(w) for w in _words(text) if w not in _STOPWORDS]


_SYNONYMS: dict[str, frozenset[str]] = {}
for _group in _SYNONYM_GROUPS:
    _stems = frozenset(_stem(w) for w in _group.split())
    for _s in _stems:
        _SYNONYMS[_s] = _SYNONYMS.get(_s, frozenset()) | (_stems - {_s})


def _blocks(section: str) -> list[str]:
    """Paragraphs, keeping each fenced code block whole so code is never cut in half."""
    blocks, current, in_fence = [], [], False
    for line in section.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
        current.append(line)
        if not in_fence and not line.strip():
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    return [b for b in blocks if b.strip()]


def _pack(section: str) -> list[str]:
    """Group paragraphs into pieces of up to CHUNK_CHARS. A single paragraph that's
    still too long is cut at line breaks, then hard-cut as a last resort."""
    pieces, current = [], ""
    for block in _blocks(section):
        while len(block) > CHUNK_CHARS:
            cut = block.rfind("\n", 0, CHUNK_CHARS)
            cut = cut if cut > CHUNK_CHARS // 2 else CHUNK_CHARS
            if current:
                pieces.append(current)
                current = ""
            pieces.append(block[:cut])
            block = block[cut:].lstrip("\n")
        if current and len(current) + len(block) + 1 > CHUNK_CHARS:
            pieces.append(current)
            current = ""
        current = f"{current}\n{block}" if current else block
    if current.strip():
        pieces.append(current)
    return [p.strip() for p in pieces if p.strip()]


def split_document(document: Document) -> list[Chunk]:
    """Split on Markdown headings (outside code blocks), then into pieces of up to
    CHUNK_CHARS at paragraph breaks. Each chunk's heading includes the headings above it."""
    text = document.text
    fences = [m.start() for m in _FENCE.finditer(text)]

    def in_code(position: int) -> bool:
        return sum(1 for f in fences if f < position) % 2 == 1

    headings = [m for m in _HEADING.finditer(text) if not in_code(m.start())]
    starts = [(m.start(), len(m.group(1)), m.group(2).strip().strip("#").strip()) for m in headings]
    if not starts or starts[0][0] != 0:
        starts.insert(0, (0, 0, ""))
    ends = [s[0] for s in starts[1:]] + [len(text)]

    chunks, trail = [], []  # trail: (level, title) of the headings above this point
    for (begin, level, title), end in zip(starts, ends):
        if level:
            trail = [t for t in trail if t[0] < level] + [(level, title)]
        section = text[begin:end].strip()
        if not section:
            continue
        heading = " > ".join(t for _, t in trail) or document.path
        for piece in _pack(section):
            chunks.append(Chunk(document, heading, piece))
    return chunks


def _close(a: str, b: str) -> bool:
    """True if two words differ by at most one edit (a typo), for words of 5+ letters."""
    if a == b or abs(len(a) - len(b)) > 1 or min(len(a), len(b)) < 5:
        return False
    if len(a) == len(b):
        diffs = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diffs) == 1 or (len(diffs) == 2 and diffs[1] == diffs[0] + 1
                                   and a[diffs[0]] == b[diffs[1]] and a[diffs[1]] == b[diffs[0]])
    short, long = (a, b) if len(a) < len(b) else (b, a)
    i = next((i for i in range(len(short)) if short[i] != long[i]), len(short))
    return short[i:] == long[i + 1:]


@dataclass(frozen=True)
class SearchResult:
    chunks: list[Chunk]
    # Share of the question's words found in the best section (0-1). Low means the
    # docs probably use different words, so the question may be worth rephrasing.
    coverage: float


class DocsIndex:
    """A small in-memory BM25 search over documentation chunks, with stemming,
    synonyms, typo tolerance and a bonus for words that appear side by side."""

    K1 = 1.5
    B = 0.75

    def __init__(self, documents: list[Document] | None = None):
        self._chunks: list[Chunk] = []
        self._term_counts: list[Counter] = []
        self._pairs: list[frozenset[tuple[str, str]]] = []
        self._doc_freq: Counter = Counter()
        self._avg_len = 0.0
        if documents:
            self.replace(documents)

    def __len__(self) -> int:
        return len(self._chunks)

    def replace(self, documents: list[Document]) -> None:
        chunks = [chunk for document in documents for chunk in split_document(document)]
        # A word in the file path or heading says more about a section than one in its body.
        counts, pairs = [], []
        for c in chunks:
            body = _tokens(c.text)
            counter = Counter(body)
            for token in _tokens(f"{c.document.path.replace('_', ' ')} {c.heading}"):
                counter[token] += HEADING_WEIGHT
            counts.append(counter)
            pairs.append(frozenset(zip(body, body[1:])))
        doc_freq: Counter = Counter()
        for counter in counts:
            doc_freq.update(counter.keys())

        self._chunks = chunks
        self._term_counts = counts
        self._pairs = pairs
        self._doc_freq = doc_freq
        self._avg_len = sum(sum(c.values()) for c in counts) / len(counts) if counts else 0.0

    def _expand(self, terms: list[str]) -> dict[str, list[tuple[str, float]]]:
        """For each query word: the indexed words that count as a match, with weights."""
        expanded = {}
        for term in dict.fromkeys(terms):
            matches = [(term, 1.0)] if term in self._doc_freq else []
            matches += [(s, SYNONYM_WEIGHT) for s in _SYNONYMS.get(term, ()) if s in self._doc_freq]
            if not matches and len(term) >= 5:
                # Unknown word: likely a typo or a different form ("recompilr").
                matches = [(w, FUZZY_WEIGHT) for w in self._doc_freq if _close(term, w)][:3]
            expanded[term] = matches
        return expanded

    def search(self, query: str, limit: int = 4, repos: list[str] | None = None) -> list[Chunk]:
        """Best-matching chunks. With repos, only chunks from those owner/repo names."""
        return self.search_scored(query, limit, repos).chunks

    def search_scored(self, query: str, limit: int = 4, repos: list[str] | None = None) -> SearchResult:
        terms = _tokens(query)
        if not terms or not self._chunks:
            return SearchResult([], 0.0)
        allowed = {r.lower() for r in repos} if repos else None
        expanded = self._expand(terms)
        query_pairs = set(zip(terms, terms[1:]))

        total = len(self._chunks)
        scored = []
        for chunk, counts, pairs in zip(self._chunks, self._term_counts, self._pairs):
            if allowed is not None and chunk.document.repo.lower() not in allowed:
                continue
            length = sum(counts.values()) or 1
            score, found = 0.0, 0
            for matches in expanded.values():
                best = 0.0
                for word, weight in matches:
                    tf = counts.get(word, 0)
                    if not tf:
                        continue
                    df = self._doc_freq[word]
                    idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
                    norm = tf + self.K1 * (1 - self.B + self.B * length / self._avg_len)
                    best = max(best, weight * idf * tf * (self.K1 + 1) / norm)
                if best:
                    score += best
                    found += 1
            if score > 0:
                score += PHRASE_BONUS * len(query_pairs & pairs)
                scored.append((score, found, chunk))

        scored.sort(key=lambda item: item[0], reverse=True)
        results, per_document = [], Counter()
        for _, _, chunk in scored:
            key = (chunk.document.repo, chunk.document.path)
            if per_document[key] >= MAX_PER_DOCUMENT:
                continue
            per_document[key] += 1
            results.append(chunk)
            if len(results) == limit:
                break
        coverage = scored[0][1] / len(expanded) if scored else 0.0
        return SearchResult(results, coverage)
