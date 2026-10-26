import asyncio
import shlex

from .base import AIProvider, Message


class CLIProvider(AIProvider):
    """Runs a command-line AI tool (for example a local Kilo or OpenCode install).

    The command comes from AI_CLI_COMMAND and is run without a shell. The
    conversation is written to the command's stdin as plain text, and the reply
    is read from stdout.
    """

    name = "cli"

    def __init__(self, command: str, timeout: float = 120):
        self._argv = shlex.split(command)
        if not self._argv:
            raise ValueError("AI_CLI_COMMAND is empty")
        self._timeout = timeout

    async def complete(self, messages: list[Message]) -> str:
        prompt = "\n\n".join(f"[{m.role}]\n{m.content}" for m in messages)
        process = await asyncio.create_subprocess_exec(
            *self._argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(prompt.encode()), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError(f"AI CLI timed out after {self._timeout}s") from None

        if process.returncode != 0:
            detail = stderr.decode(errors="replace").strip()[:500]
            raise RuntimeError(f"AI CLI exited with {process.returncode}: {detail}")
        return stdout.decode(errors="replace").strip()
