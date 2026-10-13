"""LLM access. One narrow interface (`complete`) so the graph pipeline does not care about the provider.

Claude (Anthropic SDK) is the default; Groq is supported through its own SDK so the POC can run
on an existing Groq key. Usage is accumulated so every run can report its real token spend.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    def __str__(self) -> str:
        return f"{self.calls} calls, {self.input_tokens:,} in / {self.output_tokens:,} out tokens" + (f" ({self.cache_read_tokens:,} cache-read)" if self.cache_read_tokens else "")


MAX_OUTPUT_TOKENS = int(os.environ.get("ORGKG_MAX_OUTPUT_TOKENS", 16000))


class LLMRefusal(RuntimeError):
    pass


@dataclass
class LLM:
    provider: str
    model: str
    usage: Usage = field(default_factory=Usage)
    truncated: bool = False  # last reply hit max_tokens

    def complete(self, system: str, user: str, max_tokens: int = MAX_OUTPUT_TOKENS) -> str:
        raise NotImplementedError

    def complete_json(self, system: str, user: str, max_tokens: int = MAX_OUTPUT_TOKENS) -> dict:
        text = self.complete(system, user + "\n\nReturn only one JSON object, no prose and no code fences.", max_tokens)
        try:
            return parse_json(text)
        except ValueError:
            if self.truncated:  # retrying with the same ceiling would only spend the same tokens again
                raise ValueError(f"reply cut off at {max_tokens} output tokens; raise ORGKG_MAX_OUTPUT_TOKENS") from None
            retry = self.complete(system, user + "\n\nYour previous reply was not valid JSON. Return only one valid JSON object.", max_tokens)
            return parse_json(retry)


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start : end + 1], strict=False)


class AnthropicLLM(LLM):
    def __init__(self, model: str, effort: str = "medium"):
        import anthropic

        super().__init__("anthropic", model)
        self._anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.effort = effort

    def complete(self, system: str, user: str, max_tokens: int = MAX_OUTPUT_TOKENS) -> str:
        # The system prompt is identical across a whole enrichment run, so it is the cache breakpoint.
        # Server-side fallback: if a safety classifier declines (e.g. on security-themed repos),
        # the API re-runs the request on a fallback model instead of returning nothing.
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": self.effort},
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        self.usage.calls += 1
        self.usage.input_tokens += response.usage.input_tokens + (response.usage.cache_creation_input_tokens or 0)
        self.usage.cache_read_tokens += response.usage.cache_read_input_tokens or 0
        self.usage.output_tokens += response.usage.output_tokens
        self.truncated = response.stop_reason == "max_tokens"
        if response.stop_reason == "refusal":
            raise LLMRefusal(f"{self.model} declined this request")
        return "".join(block.text for block in response.content if block.type == "text")


class GroqLLM(LLM):
    def __init__(self, model: str):
        import groq

        super().__init__("groq", model)
        self._groq = groq
        self.client = groq.Groq(max_retries=1)

    def complete(self, system: str, user: str, max_tokens: int = MAX_OUTPUT_TOKENS) -> str:
        for attempt in range(6):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    max_completion_tokens=max_tokens,
                    temperature=0.2,
                    **({"reasoning_effort": "low"} if "gpt-oss" in self.model else {}),
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                )
                break
            except self._groq.RateLimitError as exc:  # free-tier TPM limits: wait out the window
                wait = float(exc.response.headers.get("retry-after", 20)) + 1
                if attempt == 5 or wait > 120:
                    raise
                time.sleep(wait)
        self.usage.calls += 1
        self.usage.input_tokens += response.usage.prompt_tokens
        self.usage.output_tokens += response.usage.completion_tokens
        self.truncated = response.choices[0].finish_reason == "length"
        return response.choices[0].message.content or ""


def get_llm(cfg_llm: dict) -> LLM:
    provider = os.environ.get("ORGKG_LLM_PROVIDER", cfg_llm.get("provider", "auto"))
    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    if provider == "auto":
        provider = "anthropic" if has_anthropic or not os.environ.get("GROQ_API_KEY") else "groq"
    if provider == "groq":
        return GroqLLM(os.environ.get("ORGKG_GROQ_MODEL", cfg_llm.get("groq_model", "openai/gpt-oss-120b")))
    return AnthropicLLM(os.environ.get("ORGKG_ANTHROPIC_MODEL", cfg_llm.get("anthropic_model", "claude-opus-5-5")))
