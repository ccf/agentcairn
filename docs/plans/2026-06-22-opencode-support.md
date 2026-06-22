# First-Class OpenCode Support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make OpenCode (`sst/opencode`) a first-class agentcairn host: a `HarnessAdapter` for `cairn sweep` ingest, `cairn install opencode` (MCP `mcp`-block writer + slash commands + ambient plugin), and a lean TS plugin (recall-at-start + capture-at-end) that shells the `cairn` CLI.

**Architecture:** A (Python) = `OpenCodeAdapter` + `cairn recall --json` + `cairn install opencode`. B (TS) = `@opencode-ai/plugin` thin shell over the CLI. All memory logic stays Python; the plugin orchestrates.

**Tech Stack:** Python ≥3.12 (adapter/install/CLI), TypeScript (`@opencode-ai/plugin`), pytest.

## Global Constraints

- **Reuse, don't reimplement:** the adapter mirrors `src/cairn/ingest/harness/cursor.py`; ingest flows through the existing pipeline; the TS plugin only shells `cairn recall`/`cairn sweep`.
- **Positive-ID + fail-closed** classification (a row not affirmatively authored-user prose is never a candidate); malformed JSON is skipped, never aborts the sweep.
- **Plugin is fail-safe:** every CLI shell-out is wrapped; a `cairn` failure logs + is swallowed, never breaks the OpenCode session.
- **Install non-destructive, idempotent, backup-first** (match `hosts/writers.py`).
- OpenCode paths: config `~/.config/opencode/opencode.json` (`mcp` block), commands `~/.config/opencode/commands/`, plugins `~/.config/opencode/plugin/`; storage `$OPENCODE_DATA_DIR or ~/.local/share/opencode` + `/storage`.
- Every commit: `uv run pytest -q` green + ruff clean.

---

## Task 1: `OpenCodeAdapter` (ingest)

**Files:** Create `src/cairn/ingest/harness/opencode.py`; Modify `src/cairn/ingest/harness/__init__.py` (import the module so `_register` runs — check how cursor/claude_code are imported there); Test: `tests/ingest/harness/test_opencode.py` (match the existing cursor test's location/style).

**Interfaces:** Produces an `OpenCodeAdapter` (name `"opencode"`) implementing the `HarnessAdapter` protocol (`default_root`/`is_present`/`find`/`iter_raw`/`classify`/`to_event`), registered in `REGISTRY`.

**VERIFY FIRST:** capture one real OpenCode `message/<sid>/<mid>.json` + `part/<mid>/<pid>.json` sample (or the documented schema) to confirm: the message **role field** (assume `role` ∈ {`user`,`assistant`}), and that a **text part** is `{"type":"text","text":"…"}`. Adjust the field names below to the real schema.

- [ ] **Step 1: Write failing tests** — build a fixture storage tree under `tmp_path`: `storage/message/sess1/msg1.json` (role user) + `storage/part/msg1/p1.json` (type text), an assistant message, and a tool/non-text part. Assert:
```python
# tests/ingest/harness/test_opencode.py
from cairn.ingest.events import EventKind
from cairn.ingest.harness.opencode import OpenCodeAdapter

def _write(tmp_path): ...  # build storage/message + storage/part fixtures (user "deploy with make ship", assistant, a tool part)

def test_present_and_find(tmp_path, monkeypatch):
    root = _write(tmp_path)
    monkeypatch.setenv("OPENCODE_DATA_DIR", str(tmp_path))
    a = OpenCodeAdapter()
    assert a.is_present()
    assert any("sess1" in str(p) for p in a.find(root=None, project=None))

def test_user_text_is_candidate_assistant_is_context(tmp_path):
    a = OpenCodeAdapter()
    sess = tmp_path / "storage" / "message" / "sess1"  # built by _write
    rows = list(a.iter_raw(sess))
    kinds = {a.classify(r) for r in rows}
    assert EventKind.AUTHORED_USER in kinds
    # a user row → to_event yields authored text; tool/non-text → None or non-candidate
    user = next(r for r in rows if a.classify(r) == EventKind.AUTHORED_USER)
    ev = a.to_event(user, EventKind.AUTHORED_USER, _ctx(sess))
    assert ev and "make ship" in ev.text and ev.harness == "opencode"

def test_malformed_message_skipped(tmp_path):
    # a junk .json file in the session dir must be skipped, not raise
    ...
```
Run → FAIL.

- [ ] **Step 2: Implement** `src/cairn/ingest/harness/opencode.py` (mirror `cursor.py`'s structure + robustness):
```python
# SPDX-License-Identifier: Apache-2.0
"""OpenCode adapter: $OPENCODE_DATA_DIR (or ~/.local/share/opencode)/storage.
A session = message/<sessionID>/; each message/<mid>.json joins its text parts
from part/<mid>/*.json. Positive-ID, fail-closed: only a user message's text
parts are authored prose."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

from cairn.ingest.events import EventKind, NormalizedEvent, project_from_cwd
from cairn.ingest.harness import ParseCtx
from cairn.ingest.sanitize import sanitize_text


def _roots() -> list[Path]:
    raw = os.environ.get("OPENCODE_DATA_DIR")
    bases = [Path(p) for p in raw.split(",")] if raw else [Path.home() / ".local" / "share" / "opencode"]
    return [b / "storage" for b in bases]


def _load(p: Path) -> dict | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _message_text(storage: Path, mid: str) -> str:
    """Join the text parts of a message (part/<mid>/*.json, type == 'text')."""
    pdir = storage / "part" / mid
    if not pdir.is_dir():
        return ""
    chunks: list[str] = []
    for pf in sorted(pdir.glob("*.json")):
        part = _load(pf)
        if part and part.get("type") == "text" and isinstance(part.get("text"), str):
            chunks.append(part["text"])
    return sanitize_text("".join(chunks)).strip()


class OpenCodeAdapter:
    name = "opencode"

    def default_root(self) -> Path:
        return _roots()[0]

    def is_present(self) -> bool:
        return any((r / "message").is_dir() for r in _roots())

    def find(self, *, root: Path | None, project: str | None) -> list[Path]:
        roots = [Path(root)] if root is not None else _roots()
        out: list[Path] = []
        for storage in roots:
            mdir = storage / "message"
            if mdir.is_dir():
                out.extend(d for d in sorted(mdir.iterdir()) if d.is_dir())
        return out  # one dir per session

    def iter_raw(self, path: Path) -> Iterator[dict]:
        # path = storage/message/<sessionID>/ ; storage is its grandparent.
        storage = path.parent.parent
        for mf in sorted(path.glob("*.json")):
            msg = _load(mf)
            if msg is None:
                continue  # malformed → skip
            msg["_text"] = _message_text(storage, mf.stem)
            msg["_session_id"] = path.name
            yield msg

    def classify(self, raw: dict) -> EventKind:
        role = raw.get("role")
        if role == "user" and raw.get("_text"):
            return EventKind.AUTHORED_USER
        if role == "assistant":
            return EventKind.AUTHORED_ASSISTANT
        return EventKind.UNKNOWN

    def to_event(self, raw: dict, kind: EventKind, ctx: ParseCtx) -> NormalizedEvent | None:
        text = raw.get("_text") or ""
        if kind == EventKind.AUTHORED_USER and not text:
            return None
        ts = raw.get("time", {}).get("created") if isinstance(raw.get("time"), dict) else None
        return NormalizedEvent(
            kind=kind,
            role=raw.get("role") or "user",
            text=text,
            timestamp=str(ts) if ts is not None else None,
            session_id=raw.get("_session_id") or ctx.path.name,
            project=project_from_cwd(None),  # OpenCode cwd, if available, can be wired from session.json later
            git_branch=None,
            source_path=ctx.path,
            harness=self.name,
        )
```
Register it: in `src/cairn/ingest/harness/__init__.py`, import the module + `_register(OpenCodeAdapter())` exactly the way the other adapters are registered (read the bottom of that file). **Adjust `role`/`time`/text-part field names to the verified real schema.**

- [ ] **Step 3:** `uv run pytest tests/ingest/harness/test_opencode.py -q` → pass; `uv run pytest -q` green. Commit `feat(ingest): OpenCodeAdapter — sweep-ingest OpenCode sessions`.

---

## Task 2: `cairn recall --json`

**Files:** Modify `src/cairn/cli.py` (the `recall` command at ~line 281); Test: `tests/test_cli.py` (or where recall is tested).

**Why:** the TS plugin (Task 4) needs machine-readable recall output to inject. Add a `--json` flag that emits the recall results (the same notes the human format shows) as a JSON array to stdout.

- [ ] **Step 1: Failing test** — `CliRunner` invoking `recall <q> --json` against a tiny temp vault+index (reuse an existing recall test fixture) asserts stdout parses as JSON and contains the expected note text/permalink fields.
- [ ] **Step 2: Implement** — add `json_out: bool = typer.Option(False, "--json", help="Emit results as JSON (for tooling/plugins).")` to `recall`. After computing results, if `json_out`: `typer.echo(json.dumps([...], ensure_ascii=False))` with each result as `{"permalink":…, "title":…, "text":…, "score":…}` (match the fields recall already surfaces — read the existing recall body to reuse the same result objects) and `return` before the human-format echo. Keep the human path unchanged.
- [ ] **Step 3:** test passes; `uv run pytest -q` green. Commit `feat(cli): cairn recall --json for tooling/plugins`.

---

## Task 3: `cairn install opencode` — MCP `mcp`-block + host

**Files:** Modify `src/cairn/hosts/__init__.py` (add the Host), `src/cairn/hosts/entry.py` (OpenCode entry shape), and the `install` dispatch in `src/cairn/cli.py` (~line 489) if it needs per-host entry selection; Test: `tests/hosts/test_opencode_install.py` (match existing host-writer tests).

**VERIFY FIRST:** read `src/cairn/hosts/entry.py` `mcp_entry()` to see how the standard `{command,args}` entry is built (the `uvx agentcairn`/`cairn serve` invocation), then mirror it for OpenCode's shape.

- [ ] **Step 1: Failing tests** — write into a temp `opencode.json`:
```python
def test_opencode_writer_uses_mcp_block_local_shape(tmp_path):
    # write_host for the opencode Host puts agentcairn under data["mcp"] as
    # {"type":"local","command":[...],"enabled":true}, NOT under "mcpServers"
    ...
def test_opencode_install_idempotent_and_preserves_others(tmp_path):
    # pre-existing mcp.other server survives; re-run → single agentcairn entry; backup made
    ...
```
- [ ] **Step 2: Implement**
  - In `hosts/entry.py` add `opencode_mcp_entry(vault, index) -> dict` returning `{"type": "local", "command": [<same argv mcp_entry uses, e.g. "uvx","agentcairn" or "cairn","serve" with --vault/env>], "enabled": True}`. Reuse `mcp_entry`'s argv/env construction — only the wrapper shape differs.
  - In `hosts/__init__.py` add to `HOSTS`: `Host("opencode", "OpenCode", "json", "~/.config/opencode/opencode.json", root_key="mcp")`. (`write_json_mcp` already honors `root_key`, so it writes under `data["mcp"]`.)
  - In the `install` command (`cli.py`), select the entry per host: build `entry = opencode_mcp_entry(...) if h.id == "opencode" else mcp_entry(...)` (or add a small `Host` field/lookup if cleaner). Update the `install` help string to add `opencode`.
- [ ] **Step 3:** tests pass; `uv run pytest -q` green; manual sanity `cairn install opencode --print` shows the `mcp` block. Commit `feat(install): cairn install opencode — MCP mcp-block writer + host`.

---

## Task 4: lean TS ambient plugin + slash commands (artifacts)

**Files:** Create `integrations/opencode/agentcairn.ts` (the plugin), `integrations/opencode/commands/recall.md`, `integrations/opencode/commands/remember.md`, `integrations/opencode/README.md`; Test: `integrations/opencode/agentcairn.test.ts` (pure-logic only).

**VERIFY FIRST:** the `@opencode-ai/plugin` hook signatures + the `system.transform` / `session.idle` / `session.compacted` event payloads (from OpenCode's plugin docs/types). Adjust hook names/shapes to the real API.

- [ ] **Step 1:** Write the plugin as a thin shell over the `cairn` CLI. Structure:
```ts
import type { Plugin } from "@opencode-ai/plugin";
import { execFile } from "node:child_process";

function sh(cmd: string, args: string[]): Promise<string> {
  return new Promise((resolve) => {
    execFile(cmd, args, { timeout: 30_000 }, (err, stdout) => {
      if (err) { console.error("[agentcairn]", err.message); resolve(""); }  // fail-safe: never throw into OpenCode
      else resolve(stdout);
    });
  });
}

export const agentcairn: Plugin = async ({ /* client, $, etc. per real API */ }) => ({
  // recall-at-start: inject relevant memories into the model context
  "system.transform": async (input, output) => {
    const q = /* latest user prompt from input */;
    const raw = await sh("cairn", ["recall", q, "--json", "--k", "5"]);
    const notes = raw ? JSON.parse(raw) : [];
    if (notes.length) { /* append a "## Relevant memories (agentcairn)" block to output.system/parts */ }
  },
  // capture-at-end: ingest the just-ended session (reuses the OpenCodeAdapter via sweep)
  "session.idle": async () => { void sh("cairn", ["sweep"]); },        // non-blocking
  "session.compacted": async () => { void sh("cairn", ["sweep"]); },
});
```
Keep the **command-construction + recall-JSON-parsing in a pure exported function** (e.g. `buildRecallArgs(q)`, `formatMemoryBlock(notes)`) so it's unit-testable without OpenCode.
- [ ] **Step 2:** `commands/recall.md` + `remember.md` — OpenCode slash-command markdown that runs the recall/remember flow (invoke the MCP `recall`/`remember` tool, or `!cairn recall …`). Mirror an existing OpenCode command file format; keep minimal.
- [ ] **Step 3:** `integrations/opencode/agentcairn.test.ts` — unit-test `buildRecallArgs`/`formatMemoryBlock` (pure). Note in the README that hook wiring is manual-QA in a real OpenCode session. Commit `feat(opencode): lean ambient TS plugin (recall via system.transform, capture via session.idle) + slash commands`.

---

## Task 5: wire plugin+commands into `cairn install opencode` + docs

**Files:** Modify the `install` flow (`cli.py` / `src/cairn/hosts/`) to also copy the Task-4 artifacts; Modify `README.md` + `website/src/lib/content.ts` (host table); Test: extend `tests/hosts/test_opencode_install.py`.

- [ ] **Step 1:** On `cairn install opencode`, in addition to the MCP write: copy `integrations/opencode/agentcairn.ts` → `~/.config/opencode/plugin/agentcairn.ts` and the two `commands/*.md` → `~/.config/opencode/commands/` (create dirs, idempotent, don't clobber unrelated files). Add a small helper (e.g. in `hosts/`) + a `Host` flag or an `h.id == "opencode"` branch. Test: install into a temp HOME → plugin + command files present; re-run idempotent.
- [ ] **Step 2:** README + website host table — add the **OpenCode** row (first-class: plugin + MCP + ingest; ambient ✅ recall-every-turn + capture). Place it per the existing ordering (e.g. after Cursor). `cd website && npm run build` passes if touched.
- [ ] **Step 3:** `uv run pytest -q` green. Commit `feat(install): cairn install opencode installs the plugin + commands; docs`.

---

## Self-Review

**Spec coverage:** OpenCodeAdapter ingest (T1); `recall --json` for the plugin (T2); `cairn install opencode` MCP `mcp`-block writer + host (T3); lean TS plugin (system.transform recall + session.idle capture) + slash commands (T4); install wiring of plugin/commands + docs (T5). Fail-closed adapter, fail-safe plugin, non-destructive install — all enforced. Out-of-scope (22-hook maximalism, npm distribution, remote MCP, project-scoped config) correctly excluded.

**Placeholder scan:** code is concrete; the three "VERIFY FIRST" notes (OpenCode message/part schema, `mcp_entry` argv, `@opencode-ai/plugin` hook API) are real confirm-against-source steps (we have no live OpenCode sample locally), each with a sensible default + instruction to adjust — not vague placeholders.

**Type consistency:** `OpenCodeAdapter` implements the real `HarnessAdapter` protocol (matches `cursor.py`); `recall --json` output shape (T2) is what the TS plugin parses (T4); `opencode_mcp_entry` + `Host(root_key="mcp")` feed the existing `write_json_mcp` (T3); the install artifact copy (T5) targets the files created in T4.
