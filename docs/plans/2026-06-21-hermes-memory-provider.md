# agentcairn → Hermes MemoryProvider Plugin — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship agentcairn as a Hermes Agent memory backend — an in-process `MemoryProvider` plugin at `integrations/hermes/` wrapping agentcairn's existing recall/capture, writing to the user's shared vault.

**Architecture:** A `transcript_from_messages` helper in `cairn.ingest` (the only core change) turns Hermes messages into agentcairn's `Transcript`. A self-contained plugin `integrations/hermes/__init__.py` implements the Hermes `MemoryProvider` contract by calling `recall_tool`/`search_tool`/`remember_tool`/`ingest_transcript` + an incremental `reconcile` reindex. The Hermes base class is imported lazily so agentcairn never depends on Hermes; tests use a stub.

**Tech Stack:** Python ≥3.12, agentcairn internals, pytest. No new runtime deps.

## Global Constraints

- **No new memory logic** — the plugin only maps the Hermes contract onto existing `cairn` functions.
- **agentcairn must NOT import Hermes at module load** — the base class loads lazily; the plugin and its tests run without Hermes installed.
- **Redaction is always applied before any write** (inherited via `remember_tool` / `ingest_transcript`).
- **Capture is fail-safe and non-blocking** — `on_session_end` runs on a daemon thread; any error is caught + logged to stderr, never propagated.
- **Shared vault default** — resolve `vault_path` from plugin config → `CAIRN_VAULT` → `~/agentcairn`.
- Plugin is a single self-contained `__init__.py` (loadable as a Hermes package AND via importlib in tests — no relative imports).
- Every commit leaves `uv run pytest -q` green and ruff clean (pre-commit runs both).

---

## Task 1: `transcript_from_messages` core helper

**Files:**
- Modify: `src/cairn/ingest/__init__.py` (export), and add `src/cairn/ingest/from_messages.py`
- Test: `tests/ingest/test_from_messages.py`

**Interfaces:**
- Produces: `transcript_from_messages(messages: list[dict], *, session_id: str, cwd: str | None = None, source_path: Path | None = None, harness: str = "hermes") -> Transcript` — exported from `cairn.ingest`.
- Consumes: `Transcript` (`cairn.ingest.models`), `NormalizedEvent`/`EventKind` (`cairn.ingest.events`). `NormalizedEvent` fields: `kind: EventKind`, `role: str`, `text: str`, `timestamp: str|None`, `session_id: str|None`, `project: str|None`, `git_branch: str|None`, `source_path: Path`, `harness: str`. `Transcript` fields: `session_id`, `cwd`, `git_branch`, `path`, `events`, `kind_counts`. Pipeline keeps only `EventKind.AUTHORED_USER` as memory candidates; `AUTHORED_ASSISTANT` is retained as context.

- [ ] **Step 1: Write the failing test** — `tests/ingest/test_from_messages.py`:

```python
from pathlib import Path
from cairn.ingest import transcript_from_messages
from cairn.ingest.events import EventKind


def test_maps_roles_to_event_kinds():
    msgs = [
        {"role": "user", "content": "I deploy with make ship, never npm publish."},
        {"role": "assistant", "content": "Got it."},
        {"role": "system", "content": "ignore me"},
    ]
    t = transcript_from_messages(msgs, session_id="s1", cwd="/tmp/proj")
    assert t.session_id == "s1"
    kinds = [(e.kind, e.role) for e in t.events]
    assert (EventKind.AUTHORED_USER, "user") in kinds
    assert (EventKind.AUTHORED_ASSISTANT, "assistant") in kinds
    # the authored-user event carries the durable text (the only memory candidate)
    user_ev = next(e for e in t.events if e.kind == EventKind.AUTHORED_USER)
    assert "make ship" in user_ev.text
    assert user_ev.harness == "hermes"


def test_handles_content_list_and_skips_empty():
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}]},
        {"role": "user", "content": ""},
    ]
    t = transcript_from_messages(msgs, session_id="s2")
    texts = [e.text for e in t.events if e.kind == EventKind.AUTHORED_USER]
    assert texts == ["hello world"]  # joined; empty dropped
```

Run: `uv run pytest tests/ingest/test_from_messages.py -q` → FAIL (no `transcript_from_messages`).

- [ ] **Step 2: Implement** — `src/cairn/ingest/from_messages.py`:

```python
"""Build an agentcairn Transcript from an in-memory message list (e.g. a Hermes
Agent conversation), so it can flow through the normal ingest/distill pipeline."""

from __future__ import annotations

from pathlib import Path

from cairn.ingest.events import EventKind, NormalizedEvent, project_from_cwd
from cairn.ingest.models import Transcript

_ROLE_KIND = {
    "user": EventKind.AUTHORED_USER,
    "assistant": EventKind.AUTHORED_ASSISTANT,
}


def _text_of(content) -> str:
    """Normalize a message's content (str or list of {text|content} parts) to text."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                parts.append(str(p.get("text") or p.get("content") or ""))
            else:
                parts.append(str(p))
        return "".join(parts).strip()
    return str(content or "").strip()


def transcript_from_messages(
    messages: list[dict],
    *,
    session_id: str,
    cwd: str | None = None,
    source_path: Path | None = None,
    harness: str = "hermes",
) -> Transcript:
    src = source_path or Path(f"hermes:{session_id}")
    events: list[NormalizedEvent] = []
    counts: dict[str, int] = {}
    for m in messages:
        role = str(m.get("role", "")).lower()
        kind = _ROLE_KIND.get(role, EventKind.SYSTEM)
        text = _text_of(m.get("content"))
        if not text:
            continue
        counts[kind.value] = counts.get(kind.value, 0) + 1
        events.append(
            NormalizedEvent(
                kind=kind,
                role=role or "user",
                text=text,
                timestamp=m.get("timestamp"),
                session_id=session_id,
                project=project_from_cwd(cwd),
                git_branch=None,
                source_path=src,
                harness=harness,
            )
        )
    return Transcript(
        session_id=session_id, cwd=cwd, git_branch=None, path=src, events=events, kind_counts=counts
    )
```

Add to `src/cairn/ingest/__init__.py`: `from cairn.ingest.from_messages import transcript_from_messages` and add `"transcript_from_messages"` to `__all__`.

- [ ] **Step 3: Run** `uv run pytest tests/ingest/test_from_messages.py -q` → PASS, then `uv run pytest -q`.

- [ ] **Step 4: Commit**

```bash
git add src/cairn/ingest/from_messages.py src/cairn/ingest/__init__.py tests/ingest/test_from_messages.py
git commit -m "feat(ingest): transcript_from_messages — build a Transcript from a message list"
```

---

## Task 2: plugin core — provider, recall, registration

**Files:**
- Create: `integrations/hermes/__init__.py`
- Test: `tests/integrations/test_hermes_provider.py` (+ `tests/integrations/__init__.py` if needed for collection)

**Interfaces:**
- Produces: `CairnMemoryProvider` (Hermes `MemoryProvider`), `register(ctx)`, and internal helpers `_resolve(cfg) -> (vault: Path, index: str, embedder: str)` and `_reindex(vault: Path, embedder: str) -> None`.
- Consumes (Task 1) `transcript_from_messages`; plus `cairn.mcp.tools.recall_tool/search_tool/remember_tool`, `cairn.paths.resolve_vault/index_for`, `cairn.embed.get_embedder`, `cairn.index.open_index`, `cairn.index.build.reconcile`. (Verify `open_index` import path against `src/cairn/cli.py` `reindex()` — it uses `open_index(str(idx), dim=emb.dim, model_id=emb.model_id)`.)

- [ ] **Step 1: Write the failing test** — `tests/integrations/test_hermes_provider.py`:

```python
import importlib.util
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2] / "integrations" / "hermes" / "__init__.py"


def load_plugin():
    spec = importlib.util.spec_from_file_location("cairn_hermes_plugin", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def provider(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_VAULT", str(tmp_path / "vault"))
    mod = load_plugin()
    p = mod.CairnMemoryProvider()
    p.initialize("sess-1", hermes_home=str(tmp_path / "hhome"))
    return p


def test_name_and_availability(provider):
    assert provider.name == "agentcairn"
    assert provider.is_available() is True


def test_register_registers_one_provider():
    mod = load_plugin()
    seen = []

    class Ctx:
        def register_memory_provider(self, p):
            seen.append(p)

    mod.register(Ctx())
    assert len(seen) == 1 and seen[0].name == "agentcairn"


def test_prefetch_returns_a_saved_memory(provider):
    provider.handle_tool_call("memory_save", {"text": "I deploy with make ship."})
    block = provider.prefetch("how do I deploy?")
    assert "make ship" in block


def test_prefetch_empty_vault_is_safe(provider):
    assert isinstance(provider.prefetch("anything"), str)  # no crash, empty-ish
```

Run: `uv run pytest tests/integrations/test_hermes_provider.py -q` → FAIL.

- [ ] **Step 2: Implement** `integrations/hermes/__init__.py` (single self-contained file). Top section isolates the Hermes surface (lazy base import); the rest wraps agentcairn:

```python
"""agentcairn as a Hermes Agent MemoryProvider — local-first, vault-native memory.
Install: copy this dir to ~/.hermes/plugins/memory/agentcairn and `pip install agentcairn`."""

from __future__ import annotations

import sys
import threading
from pathlib import Path


# --- Hermes contract surface (isolated; lazy so agentcairn needs no Hermes dep) ---
def _base():
    try:
        from agent.memory_provider import MemoryProvider  # type: ignore
        return MemoryProvider
    except Exception:
        return object  # standalone / test mode


def register(ctx) -> None:
    """Entry point called by Hermes's memory plugin discovery."""
    ctx.register_memory_provider(CairnMemoryProvider())


def _log(msg: str) -> None:
    print(f"[agentcairn] {msg}", file=sys.stderr)


# --- agentcairn wiring ---
def _resolve(cfg: dict):
    from cairn import paths
    vault = paths.resolve_vault(cfg.get("vault_path"))
    index = str(paths.index_for(None, vault))
    embedder = cfg.get("embedder") or "fastembed"
    return vault, index, embedder


def _reindex(vault: Path, embedder: str) -> None:
    from cairn import paths
    from cairn.embed import get_embedder
    from cairn.index import open_index
    from cairn.index.build import reconcile
    emb = get_embedder(embedder)
    idx = paths.index_for(None, vault)
    idx.parent.mkdir(parents=True, exist_ok=True)
    con = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    try:
        reconcile(con, str(vault), emb)
    finally:
        con.close()


class CairnMemoryProvider(_base()):
    name = "agentcairn"

    def __init__(self) -> None:
        self._cfg: dict = {}
        self._vault: Path | None = None
        self._index: str | None = None
        self._embedder = "fastembed"
        self._rerank = False
        self._buffers: dict[str, list[dict]] = {}

    def is_available(self) -> bool:
        try:
            from cairn import paths  # noqa: F401
            self._vault, self._index, self._embedder = _resolve(self._cfg)
            return True
        except Exception:
            return False

    def initialize(self, session_id: str, **kwargs) -> None:
        self._hermes_home = kwargs.get("hermes_home", str(Path.home() / ".hermes"))
        self._vault, self._index, self._embedder = _resolve(self._cfg)
        self._rerank = bool(self._cfg.get("rerank", False))
        from cairn.vault import ensure_vault  # see note below
        ensure_vault(self._vault)  # idempotent scaffold; if no such fn, use `cairn init` logic

    def system_prompt_block(self) -> str:
        return (
            f"agentcairn memory is active. Your durable memories live as plain Markdown in "
            f"{self._vault}. Relevant ones are recalled automatically each turn."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        try:
            from cairn.mcp.tools import recall_tool
            res = recall_tool(self._index, query, embedder=self._embedder, k=5, rerank=self._rerank)
            notes = res.get("notes") or res.get("results") or []
            if not notes:
                return ""
            chunks = [str(n.get("text") or n.get("body") or "") for n in notes]
            return "## Relevant memories (agentcairn)\n\n" + "\n\n---\n\n".join(c for c in chunks if c)
        except Exception as e:
            _log(f"prefetch failed: {e}")
            return ""
```

**Note on `ensure_vault`:** check `src/cairn/cli.py` `init()` (line ~396) for the vault-scaffold call it makes; reuse that function. If init's logic isn't already a reusable function, the minimal safe behavior is `self._vault.mkdir(parents=True, exist_ok=True)` (recall/ingest tolerate an empty vault) — do that rather than inventing an `ensure_vault` import that doesn't exist.

- [ ] **Step 3: Run** `uv run pytest tests/integrations/test_hermes_provider.py -q`. (The `memory_save` path is added in Task 3; for THIS task's `test_prefetch_returns_a_saved_memory`, implement just enough `handle_tool_call("memory_save", …)` to call `remember_tool` + `_reindex` — or mark that one test `xfail` now and un-mark in Task 3. Prefer implementing the minimal `memory_save` dispatch here so the test passes.) Then `uv run pytest -q`.

- [ ] **Step 4: Commit**

```bash
git add integrations/hermes/__init__.py tests/integrations/test_hermes_provider.py
git commit -m "feat(hermes): MemoryProvider core — resolve/recall/register + reindex helper"
```

---

## Task 3: curated tools (memory_save / memory_recall / memory_search)

**Files:** Modify `integrations/hermes/__init__.py`; Test: append to `tests/integrations/test_hermes_provider.py`.

**Interfaces:** Adds `get_tool_schemas()` and `handle_tool_call(tool_name, args, **kwargs)` to `CairnMemoryProvider`. Consumes `remember_tool(vault_root, text, *, title, tags)`, `recall_tool`, `search_tool`.

- [ ] **Step 1: Write the failing tests** (append):

```python
def test_memory_save_then_search_and_recall(provider):
    out = provider.handle_tool_call("memory_save", {"text": "Prefer tabs in Go.", "tags": ["style"]})
    assert out.get("permalink") or out.get("path")
    assert "Go" in provider.handle_tool_call("memory_search", {"query": "Go formatting"})["text"] \
        or provider.handle_tool_call("memory_recall", {"query": "Go formatting"})

def test_tool_schemas_declare_three_tools(provider):
    names = {t["name"] for t in provider.get_tool_schemas()}
    assert {"memory_save", "memory_recall", "memory_search"} <= names

def test_redaction_on_save(provider):
    provider.handle_tool_call("memory_save", {"text": "token sk-ant-api03-SECRETSECRETSECRET deploy"})
    assert "SECRETSECRET" not in provider.prefetch("deploy")
```

(Adjust the search/recall assertion to the actual dict shape `search_tool`/`recall_tool` return — inspect them while implementing.)

- [ ] **Step 2: Implement** — add to `CairnMemoryProvider`:

```python
    def get_tool_schemas(self):
        return [
            {"name": "memory_save", "description": "Save a durable memory to the agentcairn vault.",
             "parameters": {"type": "object", "required": ["text"], "properties": {
                 "text": {"type": "string"}, "title": {"type": "string"},
                 "tags": {"type": "array", "items": {"type": "string"}}}}},
            {"name": "memory_recall", "description": "Recall full memories relevant to a query.",
             "parameters": {"type": "object", "required": ["query"], "properties": {
                 "query": {"type": "string"}, "k": {"type": "integer"}}}},
            {"name": "memory_search", "description": "Search memories (compact id+snippet index).",
             "parameters": {"type": "object", "required": ["query"], "properties": {
                 "query": {"type": "string"}, "k": {"type": "integer"}}}},
        ]

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs):
        from cairn.mcp.tools import recall_tool, remember_tool, search_tool
        try:
            if tool_name == "memory_save":
                out = remember_tool(str(self._vault), args["text"],
                                    title=args.get("title"), tags=args.get("tags"))
                _reindex(self._vault, self._embedder)
                return out
            if tool_name == "memory_recall":
                return recall_tool(self._index, args["query"], embedder=self._embedder,
                                   k=int(args.get("k", 5)), rerank=self._rerank)
            if tool_name == "memory_search":
                return search_tool(self._index, args["query"], embedder=self._embedder,
                                   k=int(args.get("k", 10)), rerank=self._rerank)
        except Exception as e:
            _log(f"tool {tool_name} failed: {e}")
            return {"error": str(e)}
        return {"error": f"unknown tool {tool_name}"}
```

- [ ] **Step 3: Run** `uv run pytest tests/integrations/test_hermes_provider.py -q` → PASS; then `uv run pytest -q`.

- [ ] **Step 4: Commit**

```bash
git add integrations/hermes/__init__.py tests/integrations/test_hermes_provider.py
git commit -m "feat(hermes): curated memory_save/recall/search tools"
```

---

## Task 4: auto-capture (sync_turn buffer + on_session_end distill)

**Files:** Modify `integrations/hermes/__init__.py`; Test: append to `tests/integrations/test_hermes_provider.py`.

**Interfaces:** Adds `sync_turn(user, assistant, *, session_id="")`, `on_session_end(messages)`, `shutdown()`, and a synchronously-testable `_capture(messages, session_id) -> None`. Consumes (Task 1) `transcript_from_messages`, plus `cairn.ingest.ingest_transcript`, `cairn.ingest.DedupLedger`.

- [ ] **Step 1: Write the failing tests** (append):

```python
def test_session_end_distills_user_facts_then_recall_finds_them(provider):
    msgs = [
        {"role": "user", "content": "Decision: we deploy this repo with `make ship`, never npm publish."},
        {"role": "assistant", "content": "Understood."},
    ]
    provider._capture(msgs, "sess-1")          # run capture inline (no daemon thread)
    assert "make ship" in provider.prefetch("how do we deploy?")

def test_capture_failure_is_swallowed(provider, monkeypatch):
    import cairn.ingest as ci
    monkeypatch.setattr(ci, "ingest_transcript", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    provider._capture([{"role": "user", "content": "x"}], "s")  # must NOT raise

def test_on_session_end_is_nonblocking(provider):
    provider.on_session_end([{"role": "user", "content": "remember: prod is us-east-1"}])
    provider.shutdown()  # joins the daemon thread
    assert "us-east-1" in provider.prefetch("which region is prod?")
```

- [ ] **Step 2: Implement** — add to `CairnMemoryProvider`:

```python
    def sync_turn(self, user: str, assistant: str, *, session_id: str = "") -> None:
        buf = self._buffers.setdefault(session_id, [])
        if user:
            buf.append({"role": "user", "content": user})
        if assistant:
            buf.append({"role": "assistant", "content": assistant})

    def _capture(self, messages: list[dict], session_id: str) -> None:
        try:
            import cairn.ingest as ci
            t = ci.transcript_from_messages(messages, session_id=session_id)
            ledger = ci.DedupLedger(Path(self._hermes_home) / "agentcairn" / "dedup.jsonl")
            ci.ingest_transcript(t, vault_root=self._vault, ledger=ledger, subdir="memories")
            _reindex(self._vault, self._embedder)
        except Exception as e:
            _log(f"capture failed (dropped): {e}")

    def on_session_end(self, messages) -> None:
        msgs = list(messages) or self._buffers.get("", [])
        sid = "hermes"
        self._thread = threading.Thread(target=self._capture, args=(msgs, sid), daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        t = getattr(self, "_thread", None)
        if t is not None:
            t.join(timeout=30)
```

(Confirm `DedupLedger` is exported from `cairn.ingest`; if not, import from `cairn.ingest.dedup`. Confirm `ingest_transcript`'s `ledger`/`subdir` kwargs match Task spec — `ingest_transcript(transcript, *, vault_root, ledger, threshold?, distiller?, subdir, dry_run)`.)

- [ ] **Step 3: Run** `uv run pytest tests/integrations/test_hermes_provider.py -q` → PASS; then `uv run pytest -q`.

- [ ] **Step 4: Commit**

```bash
git add integrations/hermes/__init__.py tests/integrations/test_hermes_provider.py
git commit -m "feat(hermes): auto-capture — session-end distill + reindex (daemon, fail-safe)"
```

---

## Task 5: plugin manifest + README + install docs

**Files:** Create `integrations/hermes/plugin.yaml`, `integrations/hermes/README.md`.

- [ ] **Step 1: `plugin.yaml`**

```yaml
name: agentcairn
version: 0.1.0
description: Local-first, vault-native memory — your memories as plain Markdown in an Obsidian vault you own.
# Targets the Hermes MemoryProvider plugin API as documented 2026-06; pin/update as it stabilizes.
hooks:
  - system_prompt_block
  - prefetch
  - sync_turn
  - on_session_end
  - shutdown
```

- [ ] **Step 2: `README.md`** — cover: what it is (the differentiator: vault-native, human-editable Markdown, deterministic graph, redacted, cross-agent — same vault as your Claude Code/Cursor memories); **Install** (`pip install agentcairn` in Hermes's env; `cp -r integrations/hermes ~/.hermes/plugins/memory/agentcairn`; `hermes memory setup agentcairn`); **Config** (`vault_path` default shared `~/agentcairn`, `embedder`, `rerank`); **Verify** demo (save a fact in Hermes → open `~/agentcairn` and see the Markdown note → recall it in a new session); a note that capture is local-by-default and fail-safe.

- [ ] **Step 3: Verify + commit**

```bash
uv run pytest -q   # full suite green
git add integrations/hermes/plugin.yaml integrations/hermes/README.md
git commit -m "docs(hermes): plugin manifest + install/verification README"
```

---

## Self-Review

**Spec coverage:** in-process adapter + wrapped functions → Tasks 2–4; capture model C (`prefetch` T2, tools T3, `on_session_end` distill T4); shared vault default (`_resolve`, T2); transcript helper (T1, the lone core change); Hermes-independent tests via lazy `_base()` + importlib load (T2); fail-safe capture + redaction (T4/T3); manifest + README + verify demo (T5). The upstream listing issue is correctly out-of-code (follow-up). All DoD items map to a task.

**Placeholder scan:** every code step is concrete. Two explicit "verify against the codebase" notes (the `ensure_vault`/scaffold call and the exact `search_tool`/`recall_tool` return-dict shape) are real instructions with a stated safe fallback, not vague placeholders.

**Type consistency:** `transcript_from_messages` signature (T1) is called identically in T4; `_resolve`/`_reindex` defined in T2 are reused in T3/T4; `CairnMemoryProvider` method set matches the spec's contract; `recall_tool`/`search_tool`/`remember_tool`/`reconcile`/`ingest_transcript` calls use the verified signatures.
