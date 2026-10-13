"""Two ways to have Claude write the Radar's generated texts, behind one small interface.

`lead.py` hands a backend a frozen system prompt, a list of source documents (each with the id the text must cite,
"hib 787/2026", "Drucksache 21/8379") and the task, and gets back plain text in which every sentence ends with its
ids in brackets. What happens to that text (checks, retry, cache) is lead.py's business, so both backends stay thin:

- `ClaudeCLI` runs headless Claude Code (`claude -p --output-format json`, no tools, prompt on stdin). It needs no
  API key but a logged-in Claude subscription, so it is for lab runs, not the nightly job. The model writes the
  bracketed ids itself.
- `AnthropicAPI` calls the Messages API through the `anthropic` SDK (not a dependency yet: imported on first use).
  Each source goes in as its own `document` block with Citations enabled, so the API returns, for each cited
  passage, which document it comes from; this backend turns those into the same bracketed ids, so a cited id can
  never be one the input lacks. Model from RADAR_LLM_MODEL (default `claude-opus-5-5`), key from
  ANTHROPIC_API_KEY. Citations rule out structured output, hence plain text.

Both return `Result`; tests use fakes for the subprocess runner and the SDK client."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_API_MODEL = "claude-opus-5-5"
DEFAULT_CLI_MODEL = "sonnet"


@dataclass(frozen=True)
class Document:
    """One source the model may cite: `id` is what the text writes in brackets, `text` everything it may use."""

    id: str
    text: str


@dataclass
class Result:
    text: str
    model: str
    backend: str
    cost_usd: float | None = None
    usage: dict[str, Any] = field(default_factory=dict)


class Backend(Protocol):
    name: str
    model: str

    def generate(self, system: str, documents: list[Document], task: str) -> Result: ...


def render_documents(documents: list[Document]) -> str:
    """The sources inline, for a backend without document blocks."""
    return "\n\n".join(f'<quelle id="{d.id}">\n{d.text}\n</quelle>' for d in documents)


class ClaudeCLI:
    """Headless Claude Code: `claude -p --output-format json --model <model> --tools "" --system-prompt …`."""

    name = "claude-cli"

    def __init__(self, model: str | None = None, command: str = "claude", timeout: int = 600,
                 runner: Callable[..., subprocess.CompletedProcess] = subprocess.run, cwd: str | None = None) -> None:  # fmt: skip
        self.model = model or os.environ.get("RADAR_CLI_MODEL", DEFAULT_CLI_MODEL)
        self.command, self.timeout, self.runner, self.cwd = command, timeout, runner, cwd

    def args(self, system: str) -> list[str]:
        return [self.command, "-p", "--output-format", "json", "--model", self.model, "--tools", "",
                "--system-prompt", system, "--no-session-persistence"]  # fmt: skip

    def generate(self, system: str, documents: list[Document], task: str) -> Result:
        prompt = f"{render_documents(documents)}\n\n{task}"
        done = self.runner(self.args(system), input=prompt, capture_output=True, text=True, timeout=self.timeout,
                           cwd=self.cwd)  # fmt: skip
        if done.returncode != 0:
            raise RuntimeError(f"claude -p failed ({done.returncode}): {done.stderr.strip()[:500]}")
        out = json.loads(done.stdout)
        if out.get("is_error") or out.get("subtype", "success") != "success":
            raise RuntimeError(f"claude -p error: {str(out.get('result'))[:500]}")
        models = list(out.get("modelUsage") or {})
        return Result(text=out["result"], model=models[0] if models else self.model, backend=self.name,
                      cost_usd=out.get("total_cost_usd"), usage=out.get("usage") or {})  # fmt: skip


class AnthropicAPI:
    """The Messages API with Citations: every source a plain-text `document` block titled with its id."""

    name = "anthropic-api"

    def __init__(self, model: str | None = None, client: Any = None, effort: str = "medium",
                 max_tokens: int = 16000, fallbacks: bool = True) -> None:  # fmt: skip
        self.model = model or os.environ.get("RADAR_LLM_MODEL", DEFAULT_API_MODEL)
        self.effort, self.max_tokens, self.fallbacks = effort, max_tokens, fallbacks
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic  # optional: only the nightly job needs it

            self._client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from the environment
        return self._client

    def request(self, system: str, documents: list[Document], task: str) -> dict:
        """The request body; the system prompt is cached (it is the same for every call of a kind)."""
        blocks: list[dict] = [
            {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": d.text},
             "title": d.id, "citations": {"enabled": True}}  # fmt: skip
            for d in documents
        ]
        blocks.append({"type": "text", "text": task})
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": blocks}],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort},
        }
        if self.fallbacks:  # on a policy decline the API re-runs the request on a fallback model
            body |= {"betas": ["server-side-fallback-2026-07-01"], "extra_body": {"fallbacks": "default"}}
        return body

    def generate(self, system: str, documents: list[Document], task: str) -> Result:
        body = self.request(system, documents, task)
        api = self.client.beta.messages if "betas" in body else self.client.messages
        message = api.create(**body)
        if message.stop_reason == "refusal":
            raise RuntimeError(f"refused: {getattr(message, 'stop_details', None)}")
        usage = message.usage.model_dump() if hasattr(message.usage, "model_dump") else dict(message.usage or {})
        return Result(text=cited_text(message.content, documents), model=message.model, backend=self.name,
                      usage=usage)  # fmt: skip


def cited_text(content: list[Any], documents: list[Document]) -> str:
    """The response's text with each cited passage followed by its documents' ids in brackets, the form the
    CLI backend's model writes itself. A citation names its document by index (and title)."""
    out = []
    for block in content:
        if getattr(block, "type", None) != "text":
            continue
        text = block.text
        ids: list[str] = []
        for c in getattr(block, "citations", None) or []:
            title = getattr(c, "document_title", None) or documents[c.document_index].id
            if title not in ids:
                ids.append(title)
        if ids:
            stripped = text.rstrip()
            text = f"{stripped} [{'; '.join(ids)}]{text[len(stripped):]}"
        out.append(text)
    return "".join(out).strip()


def backend(name: str, **kwargs: Any) -> Backend:
    """"cli" or "api"."""
    return ClaudeCLI(**kwargs) if name == "cli" else AnthropicAPI(**kwargs)
