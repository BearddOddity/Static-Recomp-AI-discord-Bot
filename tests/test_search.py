import asyncio

from bot.assist import DISCORD_MESSAGE_LIMIT, MAX_REPLY_PARTS, ConversationMemory, Turn, find_excerpts, split_reply
from bot.docs import DocsIndex, DocsRouter, Document
from bot.docs.index import CHUNK_CHARS, _close, _stem, split_document


def doc(path: str, text: str, repo: str = "org/repo") -> Document:
    return Document(repo, path, f"https://github.com/{repo}/blob/main/{path}", text)


GUIDE = doc(
    "docs/guide.md",
    "# Installing\nGet the SDK first.\n## Windows\nRun the installer as admin.\n## Linux\n"
    "Use the AppImage.\n# Building\nThe recompiler needs CMake 3.25.\n# Troubleshooting\n"
    "## Crashes on startup\nIf the game segfaults, delete the shader cache.\n"
    "## Save files\nSave data lives in the saves folder.\n",
)


def test_headings_carry_their_parents():
    headings = [c.heading for c in split_document(GUIDE)]
    assert "Installing > Windows" in headings and "Troubleshooting > Crashes on startup" in headings
    assert "Building" in headings


def test_word_forms_match():
    assert _stem("configured") == _stem("configuration") == _stem("configure")
    assert _stem("recompiler") == _stem("recompiling")
    assert _stem("x86_64") == "x86_64" and _stem("class") == "class" and _stem("users") == "user"
    index = DocsIndex([GUIDE])
    assert index.search("recompiling requirements")[0].heading == "Building"


def test_synonyms_and_typos_find_the_right_section():
    index = DocsIndex([GUIDE])
    # "crash" is in the heading; "freezes" is a synonym of crash.
    assert index.search("my game freezes")[0].heading == "Troubleshooting > Crashes on startup"
    assert index.search("instal on linux")[0].heading == "Installing > Linux"  # typo
    assert index.search("setup windows")[0].heading == "Installing > Windows"  # setup ~ install
    assert _close("recompilr", "recompiler") and _close("shdaer", "shader") and not _close("mods", "mode")


def test_words_side_by_side_rank_higher():
    index = DocsIndex([
        doc("a.md", "# Notes\nThe save menu and the files list are separate."),
        doc("b.md", "# Notes\nYour save files are stored next to the game."),
    ])
    assert index.search("save files")[0].document.path == "b.md"


def test_compound_names_are_split():
    index = DocsIndex([GUIDE, doc("x.md", "# Executables\nThe loader reads default.xex at boot.")])
    assert index.search("xex loader")[0].document.path == "x.md"
    assert index.search("default.xex")[0].document.path == "x.md"


def test_long_sections_split_at_paragraphs_and_keep_code_whole():
    code = "```cpp\n" + "\n".join(f"int v{i} = {i};" for i in range(40)) + "\n\nint after_blank = 1;\n```"
    text = "# Big\n" + "\n\n".join(["Paragraph " + "word " * 60] * 6) + "\n\n" + code
    chunks = split_document(doc("big.md", text))
    assert len(chunks) > 1 and all(len(c.text) <= CHUNK_CHARS for c in chunks)
    assert all(c.text.count("```") % 2 == 0 for c in chunks)  # code block never cut
    assert all(c.heading == "Big" for c in chunks)
    # A "#" comment inside code is not a heading.
    assert [c.heading for c in split_document(doc("s.md", "# Real\n```sh\n# not a heading\nls\n```"))] == ["Real"]


def test_one_file_cannot_take_every_slot():
    long_doc = doc("long.md", "\n".join(f"# Part {i}\nshader cache tips {i}" for i in range(6)))
    other = doc("other.md", "# Shaders\nshader cache location")
    paths = [c.document.path for c in DocsIndex([long_doc, other]).search("shader cache", limit=4)]
    assert paths.count("long.md") == 2 and "other.md" in paths


def test_coverage_reports_poor_matches():
    index = DocsIndex([GUIDE])
    assert index.search_scored("delete shader cache").coverage == 1.0
    assert index.search_scored("vulkan validation layers").coverage == 0.0


class FakeAI:
    supports_images = False

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    async def complete(self, messages):
        self.calls.append(messages)
        return self.reply


def run(coro):
    return asyncio.run(coro)


def test_ai_rephrases_only_when_the_search_matches_poorly():
    index, router = DocsIndex([GUIDE]), DocsRouter.parse("")
    ai = FakeAI("segfault shader cache startup crash")
    chunks, _ = run(find_excerpts(index, router, "the game just dies when I open it", limit=2, ai=ai))
    assert len(ai.calls) == 1 and chunks[0].heading == "Troubleshooting > Crashes on startup"

    good = FakeAI("unused")
    run(find_excerpts(index, router, "delete the shader cache", limit=2, ai=good))
    assert good.calls == []  # matched well, no extra AI call
    run(find_excerpts(index, router, "delete the shader cache", limit=2, ai=good, ai_query="always"))
    assert len(good.calls) == 1
    off = FakeAI("unused")
    run(find_excerpts(index, router, "the game just dies", limit=2, ai=off, ai_query="off"))
    assert off.calls == []


def test_follow_ups_search_with_the_earlier_question_and_route():
    linux = doc("README.md", "# Linux\nUse the AppImage.", repo="org/switch-tools")
    other = doc("README.md", "# Linux\nBuild from source.", repo="org/ps3-tools")
    index, router = DocsIndex([linux, other]), DocsRouter.parse("switch=org/switch-tools")
    history = [Turn("How do I install the Switch tools?", "Run the installer.")]
    chunks, repos = run(find_excerpts(index, router, "and on linux?", limit=3, ai_query="off", history=history))
    assert repos == ["org/switch-tools"] and [c.document.repo for c in chunks] == ["org/switch-tools"]


def test_memory_keeps_recent_turns_and_forgets_old_answers():
    memory = ConversationMemory(max_answers=2, max_turns=2)
    memory.remember(1, [], Turn("q1", "a1"))
    memory.remember(2, memory.get(1), Turn("q2", "a2"))
    memory.remember(3, memory.get(2), Turn("q3", "a3"))
    assert [t.question for t in memory.get(3)] == ["q2", "q3"]
    assert memory.get(1) == []  # evicted


def test_long_answers_are_split_not_cut():
    chunk = DocsIndex([GUIDE]).search("shader cache")[0]
    answer = "\n\n".join(f"Step {i}: " + "do the thing " * 20 for i in range(20))
    parts = split_reply(answer, [chunk])
    assert 1 < len(parts) <= MAX_REPLY_PARTS and all(len(p) <= DISCORD_MESSAGE_LIMIT for p in parts)
    assert parts[-1].endswith("<https://github.com/org/repo/blob/main/docs/guide.md>")
    assert parts[0].startswith("Step 0") and "Step 5" in "".join(parts)
    assert split_reply("hi", []) == ["hi"]


def test_split_keeps_code_blocks_valid():
    answer = "Here:\n```python\n" + "\n".join(f"print({i})" for i in range(400)) + "\n```\nDone."
    parts = split_reply(answer, [])
    assert len(parts) > 1
    assert all(p.count("```") % 2 == 0 for p in parts)
    assert parts[1].startswith("```python")


def test_huge_answers_stop_after_the_last_part():
    parts = split_reply("word " * 5000, [])
    assert len(parts) == MAX_REPLY_PARTS and parts[-1].endswith("…")


def test_who_is_asking_the_bot():
    from bot.assist import conversation_for, strip_mention

    memory = ConversationMemory()
    memory.remember(50, [], Turn("q1", "a1"))
    assert conversation_for("and on linux?", 50, 7, memory) == [Turn("q1", "a1")]  # reply to an answer
    assert conversation_for("<@7> how do I build?", None, 7, memory) == []           # mention
    assert conversation_for("nice report", 99, 7, memory) is None                    # reply to a case report
    assert conversation_for("hello all", None, 7, memory) is None
    assert strip_mention("<@7> how do I build?", 7) == "how do I build?"


def test_small_talk_gets_no_doc_excerpts():
    index, router = DocsIndex([GUIDE]), DocsRouter.parse("")
    # Without the AI: weak matches are left out instead of padding the prompt.
    chunks, _ = run(find_excerpts(index, router, "what's a good pizza topping?", limit=3, ai_query="off"))
    assert chunks == []
    # With the AI: it says the message has nothing to do with the docs.
    ai = FakeAI("NONE")
    chunks, _ = run(find_excerpts(index, router, "tell me a joke about robots", limit=3, ai=ai))
    assert chunks == [] and len(ai.calls) == 1


def test_assistant_prompt_has_persona_context_and_images():
    from datetime import datetime, timezone

    from bot.ai import Image
    from bot.assist import PERSONA, AskContext, build_messages, persona_prompt

    context = AskContext(bot_name="Helper", server="Recomp", channel="help", asker="Dana",
                         now=datetime(2026, 10, 26, 21, 0, tzinfo=timezone.utc), recent=("Sam: hi all",))
    prompt = persona_prompt("", context, "retro games")
    assert prompt.startswith("You are Helper, the AI assistant of the Recomp Discord server, a community about retro games.")
    assert "anything members ask" in PERSONA
    assert persona_prompt("Custom {name} for {server}", context, "x") == "Custom Helper for Recomp"

    image = Image(b"png", "image/png")
    messages = build_messages(prompt, "what's this error?", [], context=context, images=[image])
    system = "\n".join(m.content for m in messages if m.role == "system")
    assert "talking with Dana in #help" in system and "Monday 26 October 2026" in system
    assert "Sam: hi all" in system and "anti-piracy" in system
    assert messages[-1].images == (image,)
