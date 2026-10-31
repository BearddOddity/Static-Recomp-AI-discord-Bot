"""Model fallback, AI health reports (pull requests and issues) and self-updates."""

import asyncio
import base64
import json
import subprocess

import httpx
import pytest

from bot.ai import AIError, FallbackProvider, Message, OpenAICompatibleProvider, Problem
from bot.ai.base import classify
from bot.ai.fallback import FreeModelFinder, parse_free_models
from bot.health import GitHubReporter, HealthMonitor, set_env_value
from bot.updater import Updater

HI = [Message("user", "hi")]


def run(coro):
    return asyncio.run(coro)


# --- errors ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "status, message, kind",
    [
        (401, "No auth credentials found", "key"),
        (402, "Insufficient credits", "key"),
        (404, "No endpoints found for some/model:free", "model"),
        (400, "some/model is not a valid model ID", "model"),
        (429, "Rate limit exceeded", "rate_limit"),
        (503, "Overloaded", "unavailable"),
        (403, "Input was flagged by moderation", "other"),
    ],
)
def test_errors_say_what_needs_fixing(status, message, kind):
    assert classify(status, message) == kind


def test_openrouter_error_inside_a_200_is_classified():
    def handler(request):
        return httpx.Response(200, json={"error": {"message": "No endpoints found for x:free", "code": 404}})

    provider = OpenAICompatibleProvider("https://or.test/v1", "k", "x:free",
                                        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(AIError) as raised:
        run(provider.complete(HI))
    assert raised.value.kind == "model" and "No endpoints found" in str(raised.value)


# --- fallback ----------------------------------------------------------------------


class Scripted:
    """A model that fails with the given kind, or answers with its name."""

    supports_images = True

    def __init__(self, model, failures):
        self.model, self.failures = model, failures

    async def complete(self, messages):
        if self.model in self.failures:
            raise AIError(self.failures[self.model], f"{self.model} failed")
        return f"answer from {self.model}"

    async def aclose(self):
        pass


class Finder:
    def __init__(self, models):
        self.list, self.calls = models, 0

    async def models(self, needs_images=False):
        self.calls += 1
        return self.list


def fallback(models, failures, finder=None):
    provider = FallbackProvider(lambda m: Scripted(m, failures), models, setting="OPENAI_COMPAT_MODEL",
                                name="openrouter", finder=finder)
    reports = []

    async def on_problem(problem):
        reports.append(problem)

    provider.on_problem = on_problem
    return provider, reports


def test_busy_model_falls_back_to_the_next_without_a_report():
    provider, reports = fallback(["a:free", "b:free"], {"a:free": "rate_limit"})
    assert run(provider.complete(HI)) == "answer from b:free"
    assert reports == [] and "a:free" not in provider.dead


def test_removed_model_is_replaced_by_a_free_one_and_reported():
    finder = Finder(["a:free", "new:free"])
    provider, reports = fallback(["a:free"], {"a:free": "model"}, finder)
    assert run(provider.complete(HI)) == "answer from new:free"
    assert provider.current == "new:free" and provider.dead == {"a:free"}
    assert reports == [Problem("model", "OPENAI_COMPAT_MODEL", "a:free", "a:free is no longer available from openrouter.",
                               replacement="new:free")]
    # Next time it goes straight to the working model.
    assert run(provider.complete(HI)) == "answer from new:free"


def test_bad_key_is_reported_and_not_retried():
    provider, reports = fallback(["a:free", "b:free"], {"a:free": "key"})
    with pytest.raises(AIError):
        run(provider.complete(HI))
    assert [r.kind for r in reports] == ["key"]


def test_no_model_configured_picks_a_free_one():
    finder = Finder(["first:free", "second:free"])
    provider, reports = fallback([], {"first:free": "model"}, finder)
    assert run(provider.complete(HI)) == "answer from second:free"
    assert reports == []  # nothing configured, so nothing to replace


def test_nothing_working_is_reported():
    provider, reports = fallback(["a:free"], {"a:free": "model"}, Finder([]))
    with pytest.raises(AIError):
        run(provider.complete(HI))
    assert [r.kind for r in reports] == ["no_model"]


def test_free_model_list_is_parsed_and_image_models_come_first():
    data = {"data": [
        {"id": "paid/model", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
        {"id": "text/model:free", "pricing": {"prompt": "0", "completion": "0"}, "context_length": 131072,
         "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
        {"id": "vision/model:free", "pricing": {"prompt": "0", "completion": "0"}, "context_length": 32768,
         "architecture": {"modality": "text+image->text"}},
        {"id": "image/gen:free", "pricing": {"prompt": "0", "completion": "0"},
         "architecture": {"input_modalities": ["text"], "output_modalities": ["image"]}},
    ]}
    assert [m.id for m in parse_free_models(data)] == ["text/model:free", "vision/model:free"]

    def handler(request):
        assert request.url.path == "/api/v1/models"
        return httpx.Response(200, json=data)

    finder = FreeModelFinder(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert run(finder.models(needs_images=True)) == ["vision/model:free", "text/model:free"]


# --- health reports -----------------------------------------------------------------


def test_env_value_is_replaced_or_added():
    text = "# models\nOPENAI_COMPAT_MODEL=old:free\nDOCS_REPOS=a/b\n"
    assert set_env_value(text, "OPENAI_COMPAT_MODEL", "new:free") == "# models\nOPENAI_COMPAT_MODEL=new:free\nDOCS_REPOS=a/b\n"
    assert set_env_value("A=1", "MOD_AI_MODEL", "v:free") == "A=1\nMOD_AI_MODEL=v:free\n"


class FakeGitHub:
    def __init__(self, push=True):
        self.push = push
        self.files = {"servers/x/server.env": "COMMUNITY_ABOUT=games\nOPENAI_COMPAT_MODEL=old:free\n"}
        self.calls, self.prs, self.issues = [], [], []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path.removeprefix("/repos/me/bot"), request.method
        self.calls.append((method, path))
        body = json.loads(request.content) if request.content else {}
        if method == "GET" and path == "/pulls":
            return httpx.Response(200, json=[p for p in self.prs if p["head"] == request.url.params["head"].split(":")[1]])
        if method == "GET" and path == "":
            return httpx.Response(200, json={"default_branch": "main", "permissions": {"push": self.push}})
        if method == "GET" and path == "/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "abc"}})
        if method == "POST" and path == "/git/refs":
            return httpx.Response(201, json={})
        if path.startswith("/contents/"):
            name = path.removeprefix("/contents/")
            if method == "GET":
                return httpx.Response(200, json={"sha": "f1", "content": base64.b64encode(self.files[name].encode()).decode()})
            self.files[name] = base64.b64decode(body["content"]).decode()
            return httpx.Response(200, json={})
        if method == "POST" and path == "/pulls":
            self.prs.append({**body, "html_url": "https://github.com/me/bot/pull/7"})
            return httpx.Response(201, json=self.prs[-1])
        if method == "GET" and path == "/issues":
            return httpx.Response(200, json=self.issues)
        if method == "POST" and path == "/issues":
            self.issues.append({**body, "html_url": "https://github.com/me/bot/issues/8"})
            return httpx.Response(201, json=self.issues[-1])
        return httpx.Response(404)


def monitor(github, pr_file="servers/x/server.env"):
    notes = []

    async def notify(text):
        notes.append(text)

    reporter = GitHubReporter("me/bot", "tok", client=httpx.AsyncClient(transport=httpx.MockTransport(github)))
    return HealthMonitor(notify, reporter, pr_file), notes


def test_replaced_model_opens_one_pull_request():
    github = FakeGitHub()
    health, notes = monitor(github)
    problem = Problem("model", "OPENAI_COMPAT_MODEL", "old:free", "gone", replacement="new:free")
    run(health.report(problem))
    run(health.report(problem))  # reported again: no second PR
    assert len(github.prs) == 1
    pr = github.prs[0]
    assert pr["title"] == "Switch OPENAI_COMPAT_MODEL to new:free" and pr["base"] == "main"
    assert "OPENAI_COMPAT_MODEL=new:free" in github.files["servers/x/server.env"]
    assert "COMMUNITY_ABOUT=games" in github.files["servers/x/server.env"]
    assert len(notes) == 1 and "pull/7" in notes[0] and "new:free" in notes[0]


def test_bad_key_opens_an_issue_never_a_pull_request():
    github = FakeGitHub()
    health, notes = monitor(github)
    run(health.report(Problem("key", "OPENAI_COMPAT_MODEL", "a:free", "No auth credentials found")))
    assert github.prs == [] and len(github.issues) == 1
    issue = github.issues[0]
    assert issue["title"] == "The AI key needs replacing" and issue["labels"] == ["ai-health"]
    assert "must never be committed" in issue["body"]
    assert "issues/8" in notes[0]


def test_someone_elses_copy_never_files_on_the_original_repo():
    github = FakeGitHub(push=False)  # a token that can't push: the repo isn't theirs
    health, notes = monitor(github)
    run(health.report(Problem("key", "OPENAI_COMPAT_MODEL", "a:free", "No auth credentials found")))
    run(health.report(Problem("model", "OPENAI_COMPAT_MODEL", "old:free", "gone", replacement="new:free")))
    assert github.prs == [] and github.issues == []
    assert not any(method == "POST" for method, _ in github.calls)
    assert len(notes) == 2 and "The AI key needs replacing" in notes[0]  # the mod log still hears


def test_without_github_it_still_tells_the_moderators():
    notes = []

    async def notify(text):
        notes.append(text)

    health = HealthMonitor(notify)
    run(health.report(Problem("no_model", "MOD_AI_MODEL", "x:free", "gone")))
    assert len(notes) == 1 and "No AI model is working" in notes[0]


# --- self-update ------------------------------------------------------------------


def git(folder, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=folder, check=True,
                   capture_output=True)


@pytest.fixture
def clone(tmp_path):
    origin, work, local = tmp_path / "origin.git", tmp_path / "work", tmp_path / "local"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", str(origin), str(work))
    (work / "bot.txt").write_text("v1\n")
    git(work, "add", ".")
    git(work, "commit", "-m", "first")
    git(work, "push", "origin", "HEAD:main")
    git(tmp_path, "clone", "-b", "main", str(origin), str(local))
    return work, local


def test_updater_pulls_new_commits(clone):
    work, local = clone
    updater = Updater(local)
    assert updater.available and run(updater.check()) is None  # already up to date

    (work / "bot.txt").write_text("v2\n")
    git(work, "commit", "-am", "Better answers")
    git(work, "push", "origin", "HEAD:main")
    update = run(updater.check())
    assert update is not None and update.summary.endswith("Better answers")
    assert (local / "bot.txt").read_text() == "v2\n"


def test_updater_never_overwrites_local_edits(clone):
    work, local = clone
    (work / "bot.txt").write_text("v2\n")
    git(work, "commit", "-am", "upstream")
    git(work, "push", "origin", "HEAD:main")
    (local / "bot.txt").write_text("my edit\n")
    assert run(Updater(local).check()) is None
    assert (local / "bot.txt").read_text() == "my edit\n"


def test_updater_skips_folders_that_arent_clones(tmp_path):
    assert not Updater(tmp_path).available and run(Updater(tmp_path).check()) is None
