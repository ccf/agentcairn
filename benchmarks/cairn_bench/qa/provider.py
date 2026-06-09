# SPDX-License-Identifier: Apache-2.0
"""Thin LLM provider seam. FakeProvider for tests; AnthropicProvider for real runs.

AnthropicProvider imports `anthropic` lazily in __init__ so that importing this module
with only the base install (no bench dependency group) works fine — FakeProvider needs
no extra dependencies.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Protocol


class Provider(Protocol):
    def complete(self, prompt: str, *, max_tokens: int = 256, temperature: float = 0.0) -> str: ...


class FakeProvider:
    def __init__(self, reply: str = "yes") -> None:
        self.reply = reply
        self.on_prompt: Callable[[str], None] | None = None

    def complete(self, prompt: str, *, max_tokens: int = 256, temperature: float = 0.0) -> str:
        if self.on_prompt:
            self.on_prompt(prompt)
        return self.reply


class AnthropicProvider:
    """Real Anthropic provider. Imports `anthropic` lazily so the base install is unaffected.

    NOTE: The judge uses Anthropic (not GPT-4o) — QA numbers are NOT comparable to
    published LongMemEval/LoCoMo leaderboards. Use for relative ablation signal only.
    """

    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        from anthropic import Anthropic  # lazy — bench dep group only

        self.model = model
        self._client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def complete(self, prompt: str, *, max_tokens: int = 256, temperature: float = 0.0) -> str:
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
