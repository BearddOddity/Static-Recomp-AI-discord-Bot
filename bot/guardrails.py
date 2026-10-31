"""Guardrails: rules the server's owners give the AI, on top of its personality.

They live in a plain text file (GUARDRAILS_FILE, see guardrails.example.txt) that is
re-read whenever it changes, so no restart is needed:

  [instructions]     extra instructions the assistant always follows
  [moderation]       extra guidance for the moderation AI
  [redirect]         keywords = a fixed reply sent instead of asking the AI
  [topic]            keywords = an extra instruction for questions about that topic
  [block questions]  keywords: questions that are refused without asking the AI
  [block answers]    keywords: answers that are held back

Keywords are comma separated and match whole words in any case; /slashes/ make a
regular expression. Some protections are built in and always on: jailbreak attempts
are refused, and answers that leak the bot's instructions or a key are held back.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("bot.guardrails")

SECTIONS = ("instructions", "moderation", "redirect", "topic", "block questions", "block answers")
MAX_FILE_CHARS = 20000

JAILBREAK_REPLY = (
    "I'll have to decline that one. I keep to this server's rules however the question is put. "
    "Ask me something else and I'm happy to help."
)
BLOCKED_QUESTION_REPLY = "That's not something I can help with here. A moderator can tell you more."
HELD_BACK_REPLY = "I started to answer with something I'm not allowed to share here, so I've held it back. Try asking another way."

# Built in, always on: attempts to talk the assistant out of its instructions.
JAILBREAK = re.compile(
    r"\b(ignore|disregard|forget|override|bypass)\b.{0,25}\b(all|your|previous|prior|earlier|above|system)\b"
    r".{0,15}\b(instructions?|rules|prompts?|guidelines|guardrails|restrictions)\b"
    r"|\b(developer|dev|god|jailbreak|unrestricted|unfiltered)\s+mode\b"
    r"|(?-i:\bDAN\b)|\bdo anything now\b"
    r"|\b(pretend|act as if|imagine)\b.{0,30}\b(no|without)\b.{0,10}\b(rules|restrictions|filters|guidelines|limits)\b"
    r"|\b(reveal|show|print|repeat|output|tell me|what (is|are))\b.{0,25}\b(your|the)\b.{0,10}"
    r"\b(system prompt|hidden (prompt|instructions)|initial instructions|guardrails)\b",
    re.IGNORECASE | re.DOTALL,
)

# Built in, always on: things that look like keys or tokens never leave the bot.
SECRET = re.compile(
    r"sk-or-v1-[a-f0-9]{20,}|sk-ant-[\w-]{20,}|\bsk-[A-Za-z0-9]{32,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{30,}|github_pat_\w{30,}|\bgsk_\w{30,}|\bAIza[\w-]{30,}"
    r"|\b[MN][\w-]{23,27}\.[\w-]{6}\.[\w-]{27,}"
)
# A sentence of the bot's own instructions this long, repeated word for word, is a leak.
LEAK_MIN_CHARS = 60


def _pattern(spec: str) -> re.Pattern | None:
    """One keyword ("free games"), or a /regex/. None if the regex is broken."""
    spec = spec.strip()
    if len(spec) > 2 and spec.startswith("/") and spec.endswith("/"):
        try:
            return re.compile(spec[1:-1], re.IGNORECASE)
        except re.error as error:
            log.warning("Guardrail regex %s is broken (%s); skipped", spec, error)
            return None
    words = [re.escape(w) for w in spec.split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.IGNORECASE) if words else None


def _patterns(specs: str) -> tuple[re.Pattern, ...]:
    # Commas separate keywords, except inside a /regex/.
    parts = re.findall(r"/(?:\\.|[^/])+/|[^,]+", specs)
    return tuple(p for p in (_pattern(s) for s in parts) if p)


def _matches(patterns: tuple[re.Pattern, ...], text: str) -> str:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return ""


@dataclass(frozen=True)
class Rule:
    patterns: tuple[re.Pattern, ...]
    text: str  # the reply (redirect) or the instruction (topic)
    source: str  # the keywords as written, for /guardrails


@dataclass(frozen=True)
class Decision:
    """What to do with a question before the AI sees it."""

    reply: str | None = None  # send this instead of asking the AI
    instructions: tuple[str, ...] = ()  # extra instructions for this question
    reason: str = ""  # why, for logs and /guardrails test


@dataclass(frozen=True)
class Guardrails:
    instructions: tuple[str, ...] = ()
    moderation: str = ""
    redirects: tuple[Rule, ...] = ()
    topics: tuple[Rule, ...] = ()
    block_questions: tuple[Rule, ...] = ()
    block_answers: tuple[Rule, ...] = ()
    problems: tuple[str, ...] = field(default=(), compare=False)  # lines that couldn't be read

    @classmethod
    def parse(cls, text: str, extra_instructions: str = "") -> "Guardrails":
        sections: dict[str, list[str]] = {name: [] for name in SECTIONS}
        problems = []
        current = None
        for number, raw in enumerate(text[:MAX_FILE_CHARS].splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            header = re.fullmatch(r"\[\s*([a-z ]+?)\s*\]", line, re.IGNORECASE)
            if header:
                current = " ".join(header.group(1).lower().split())
                if current not in sections:
                    problems.append(f"line {number}: unknown section [{header.group(1)}]")
                    current = None
                continue
            if current is None:
                problems.append(f"line {number}: outside a section")
                continue
            sections[current].append(line)

        def rules(name: str, needs_text: bool) -> tuple[Rule, ...]:
            found = []
            for line in sections[name]:
                keywords, sep, value = line.partition("=") if needs_text else (line, "=", "")
                if not sep or (needs_text and not value.strip()):
                    problems.append(f"[{name}] needs 'keywords = text': {line[:60]}")
                    continue
                patterns = _patterns(keywords)
                if patterns:
                    found.append(Rule(patterns, value.strip(), keywords.strip()))
            return tuple(found)

        instructions = sections["instructions"] + [line.strip() for line in extra_instructions.splitlines() if line.strip()]
        return cls(
            instructions=tuple(instructions),
            moderation="\n".join(sections["moderation"]),
            redirects=rules("redirect", True),
            topics=rules("topic", True),
            block_questions=rules("block questions", False),
            block_answers=rules("block answers", False),
            problems=tuple(problems),
        )

    def check_question(self, question: str) -> Decision:
        if JAILBREAK.search(question):
            return Decision(JAILBREAK_REPLY, reason="built-in: tries to get around the AI's instructions")
        for rule in self.block_questions:
            hit = _matches(rule.patterns, question)
            if hit:
                return Decision(BLOCKED_QUESTION_REPLY, reason=f"[block questions] matched “{hit}”")
        for rule in self.redirects:
            hit = _matches(rule.patterns, question)
            if hit:
                return Decision(rule.text, reason=f"[redirect] matched “{hit}”")
        topics = [(rule, _matches(rule.patterns, question)) for rule in self.topics]
        hits = [(rule, hit) for rule, hit in topics if hit]
        return Decision(
            instructions=tuple(rule.text for rule, _ in hits),
            reason="; ".join(f"[topic] matched “{hit}”" for _, hit in hits),
        )

    def check_answer(self, answer: str, instructions: str = "", secrets: tuple[str, ...] = ()) -> str:
        """Why an answer must be held back, or "" if it can be sent. instructions is
        the text the AI was given (so it can't be repeated back), secrets are the bot's
        own keys."""
        if SECRET.search(answer) or any(s and len(s) >= 8 and s in answer for s in secrets):
            return "built-in: looks like a key or token"
        flat = " ".join(answer.split())
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", instructions):
            sentence = " ".join(sentence.split())
            if len(sentence) >= LEAK_MIN_CHARS and sentence in flat:
                return "built-in: repeats the bot's own instructions"
        for rule in self.block_answers:
            hit = _matches(rule.patterns, answer)
            if hit:
                return f"[block answers] matched “{hit}”"
        return ""

    def summary(self) -> str:
        counts = [
            f"{len(self.instructions)} instructions",
            f"{len(self.redirects)} redirects",
            f"{len(self.topics)} topics",
            f"{len(self.block_questions)} blocked question rules",
            f"{len(self.block_answers)} blocked answer rules",
            "moderation guidance" if self.moderation else "no moderation guidance",
        ]
        return ", ".join(counts)


class GuardrailsFile:
    """The guardrails file, re-read when it changes. A missing file means only the
    built-in protections apply."""

    def __init__(self, path: str, extra_instructions: str = ""):
        self.path = Path(path) if path else None
        self.extra = extra_instructions
        self._stamp: tuple[float, int] | None = None
        self._rails = Guardrails.parse("", extra_instructions)

    def current(self) -> Guardrails:
        stamp = None
        if self.path is not None:
            try:
                info = os.stat(self.path)
                stamp = (info.st_mtime, info.st_size)
            except OSError:
                stamp = None
        if stamp != self._stamp:
            self._stamp = stamp
            text = ""
            if stamp is not None:
                try:
                    text = self.path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    log.warning("Couldn't read the guardrails file %s", self.path)
            self._rails = Guardrails.parse(text, self.extra)
            if stamp is not None:
                log.info("Guardrails loaded from %s: %s", self.path, self._rails.summary())
            for problem in self._rails.problems:
                log.warning("Guardrails file %s, %s", self.path, problem)
        return self._rails
