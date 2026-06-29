# SPDX-License-Identifier: Apache-2.0
"""UserPromptSubmit auto-recall.

Runs a hybrid recall against the user's prompt and emits it as Claude Code
`additionalContext`. All logic lives here as small, testable units; the plugin
ships only a thin shell wrapper that execs the `cairn recall-hook` CLI command,
which delegates to `run()`. Every path is fail-open: `run()` never raises and
returns "" (inject nothing) on any problem."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from cairn import paths
from cairn.config import (
    cairn_env,
    resolve_auto_recall,
    resolve_auto_recall_k,
    resolve_auto_recall_scope,
)
from cairn.embed import get_embedder
from cairn.search import open_search, resolve_current_project, search

_DEFAULT_MIN_CHARS = 12


def _min_chars(env: Mapping[str, str]) -> int:
    try:
        return int(env.get("CAIRN_AUTO_RECALL_MIN_CHARS") or _DEFAULT_MIN_CHARS)
    except ValueError:
        return _DEFAULT_MIN_CHARS


def should_recall(prompt: str, env: Mapping[str, str] | None = None) -> bool:
    """True iff auto-recall is enabled and the prompt is substantive.
    Skips trivially-short prompts ("yes", "go") — continuations where recall
    adds noise, not signal."""
    if env is None:
        env = cairn_env()
    if not resolve_auto_recall(env):
        return False
    return len(prompt.strip()) >= _min_chars(env)


def format_block(notes: list[dict]) -> str:
    """Render recalled notes into the injection markdown. Returns "" when there
    is nothing to inject (empty list / all-blank texts) so callers skip-inject."""
    items: list[str] = []
    for n in notes:
        text = (n.get("text") or "").strip()
        if not text:
            continue
        permalink = n.get("permalink")
        items.append(f"{text}\n— [[{permalink}]]" if permalink else text)
    if not items:
        return ""
    return "## Relevant memories (agentcairn)\n\n" + "\n\n---\n\n".join(items)


def build_hook_output(block: str) -> dict:
    """Wrap an injection block in the Claude Code UserPromptSubmit envelope."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": block,
        }
    }


def _recall(prompt: str, *, vault, index, embedder_name: str, k: int, scope: str) -> list[dict]:
    """Run one hybrid recall; returns note dicts (possibly empty). Falls back to
    BM25-only if the embedder cannot load. Records the savings ledger
    best-effort (this is the observability that proves recall fired)."""
    idx = paths.index_for(index, paths.resolve_vault(vault))
    if not idx.exists():
        return []
    try:
        emb = None if embedder_name == "none" else get_embedder(embedder_name)
    except Exception:
        emb = None  # BM25-only fallback when the embedder can't load
    current = resolve_current_project(None)
    con = open_search(str(idx))
    try:
        hits = search(con, prompt, embedder=emb, k=k, rerank=False, project=current, scope=scope)
        notes = [
            {"permalink": h.permalink, "title": h.heading_path, "text": h.snippet, "score": h.score}
            for h in hits
        ]
        try:
            from cairn import usage
            from cairn.index.schema import cached_haystack_tokens

            full = cached_haystack_tokens(con)
            recalled = sum(usage.estimate_tokens(n["text"]) for n in notes)
            usage.record("recall", full=full, recalled=recalled, k=k)
        except Exception:
            pass
    finally:
        con.close()
    return notes


def run(
    stdin_text: str,
    *,
    vault: Path | str | None = None,
    index: Path | str | None = None,
    embedder_name: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Parse a UserPromptSubmit payload (JSON on stdin) and return the string to
    print: a hook-output JSON envelope, or "" to inject nothing. NEVER raises —
    every failure path returns "" (fail-open)."""
    try:
        if env is None:
            env = cairn_env()
        name = embedder_name or env.get("CAIRN_EMBEDDER") or "fastembed"
        try:
            obj = json.loads(stdin_text)
            prompt = obj.get("prompt") or "" if isinstance(obj, dict) else ""
        except (ValueError, TypeError):
            prompt = ""
        if not should_recall(prompt, env):
            return ""
        notes = _recall(
            prompt,
            vault=vault,
            index=index,
            embedder_name=name,
            k=resolve_auto_recall_k(env),
            scope=resolve_auto_recall_scope(env),
        )
        block = format_block(notes)
        return json.dumps(build_hook_output(block)) if block else ""
    except Exception:
        return ""
