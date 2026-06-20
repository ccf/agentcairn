# e2e Harness Legs 2–4 — Design

**Status:** approved 2026-06-19
**Issue:** #45 (e2e dogfood the full system per host) — legs **2, 3, 4**. Leg 1 (recall-eval harness) shipped in PR #95.
**Goal:** Close the genuine end-to-end gaps the per-piece unit tests don't cover: the MCP-over-stdio tool contract, the per-host `cairn install` matrix, and the Claude Code plugin hook contract. Each new leg is a module under `tests/e2e/` following leg 1's conventions (tmp_path vaults, `conftest` config isolation, fake embedder, `CAIRN_E2E` gate for heavy parts).

## Coverage gaps (why these legs)

- **MCP:** `tests/mcp/` exercises `build_server()` and tool/config logic **in-process only** — nothing boots the server and drives tools over the wire. Gap = the protocol contract every non-Claude-Code host depends on.
- **Hosts:** `tests/test_hosts.py` tests the registry, `mcp_entry` shape, and writers in isolation — nothing runs `cairn install <host>` and asserts the **written config file** per host. Gap = install→file→shape, per host.
- **Plugin hooks:** `tests/test_hook_scripts.py` only does a **static** check (`--vault` present, `--index` absent). Gap = actually running `session-start.sh` / `session-end.sh` against our local code.

## Leg 2 — MCP-over-stdio contract (gated)

**File:** `tests/e2e/test_mcp_contract.py`. Gated on `CAIRN_E2E` (spawns a process + does the MCP handshake).

Launch the **local** server over stdio — `uv run agentcairn` (the console entrypoint the plugin's `.mcp.json` runs as `uvx agentcairn`; using `uv run` tests our working copy, not PyPI) — with `CAIRN_VAULT` → a temp vault pre-seeded with a known memory note and a built index, and `CAIRN_EMBEDDER=fake` (no model download). Drive it with the `mcp` SDK (already a dependency, `mcp>=1.27.2`) via `stdio_client` + `ClientSession`:

- `initialize` succeeds.
- `list_tools()` returns the same tool set `build_server()` registers (`recall`, `search`, `recent`, `build_context`, `remember`).
- `call_tool("recall", {query: …})` returns the seeded note (real protocol response, not an imported function).
- `call_tool("remember", {…})` writes a note (assert it lands in the vault).
- `call_tool("recent")` / `call_tool("build_context", …)` return without error.

Skip cleanly (never fail) if the server can't launch or `mcp` client import fails.

## Leg 3 — per-host install matrix (offline, always-on)

**File:** `tests/e2e/test_install_matrix.py`. Offline, keyless → default `check` job.

Parametrized over every registry host (`cairn.hosts` — `cursor`, `claude-desktop`, `vscode`, `gemini`, `claude-code`, `codex`, `antigravity`). For each host: run `cairn install <host> --vault <tmp-vault>` via Typer's `CliRunner` into a temp HOME (monkeypatch `HOME` and any host-specific base dirs so the host's config path, e.g. `~/.cursor/mcp.json`, resolves under `tmp_path`). Then assert the **written config**:

- The file exists at the host's declared path.
- It parses (JSON for json-format hosts; the appropriate format otherwise).
- **mcp-format hosts:** contains an `agentcairn` server entry whose command launches cairn and whose env sets `CAIRN_VAULT` and does **not** pin `CAIRN_INDEX` (mirrors `tests/test_hosts.py::test_mcp_entry_shape`, but end-to-end through the CLI).
- **plugin-kind hosts** (`claude-code`, `codex`, `antigravity`): assert the plugin install marker the writer produces (the plugin dir/manifest reference), not an mcp entry.

A matrix (one param per host) so a single host's format drift fails only its row. If a host's real config path can't be safely redirected under `tmp_path` via env, that host is skipped with a reason (documented), not hard-failed.

## Leg 4 — Claude Code plugin hook contract (pragmatic, offline)

**File:** `tests/e2e/test_plugin_hooks.py`. Offline → default `check` job.

The scripts invoke `uvx --from agentcairn>=0.2 cairn …` (PyPI). To test **our** code, put a tiny **PATH shim** first on `PATH`: a `uvx` executable that drops the leading `--from <pkg>` args and execs the local `cairn` (which is on `PATH` under `uv run`). This runs the real `.sh` against the working copy.

- **`session-start.sh`:** seed a temp vault with a couple of memory notes + a built index, and create `$HOME/.cache/agentcairn/indexes` (with `HOME` → tmp) so the script takes the **fast digest path** (not the first-run detached-warm branch). Run it; assert stdout is valid JSON with `hookSpecificOutput.hookEventName == "SessionStart"` and an `additionalContext` digest listing the seeded note titles.
- **`session-end.sh`:** use a shim whose `cairn` **records its argv** to a file (then exits 0). Run `session-end.sh` with a hook-JSON cwd on stdin; assert it invoked `sweep --vault <vault> --project <cwd>` (the detached-sweep contract). The sweep's actual capture behavior is already covered by leg 1's Tier 1 and the existing sweep tests — this leg asserts the **wiring**, which is what the static test couldn't.

Fallback if the shim proves too fiddly across shells: assert the script wiring statically (already done) and drive the `recent`/`savings`/`sweep` subcommands the scripts call directly. The shim approach is preferred because it exercises the real scripts.

## Gating / CI

- Legs 3 & 4 are offline/hermetic → run in the default `check` job (`testpaths` already picks up `tests/e2e/`).
- Leg 2 is gated on `CAIRN_E2E`. Generalize the existing main-only `recall-eval` CI job into an `e2e` job that runs the whole `tests/e2e/` dir with `CAIRN_E2E=1` (so both leg 1's Tier 2 and leg 2 run there). Keep it `main` + `workflow_dispatch`.

## Hermeticity / error handling

- All legs use `tmp_path` vaults/HOME and the autouse `conftest` config isolation — no read of the developer's real vault/config.
- Legs 3 & 4 are fully offline (fake embedder / recorded-argv shim).
- Leg 2 uses `CAIRN_EMBEDDER=fake` (no network); still gated because it spawns a subprocess.
- Any leg skips (never fails) when its prerequisite is unavailable (mcp client import, un-redirectable host path, shell shim unsupported).

## Decomposition

One combined spec (this) + one implementation plan with three task groups (2, 3, 4) + the CI-generalization task. Subagent-driven. The legs are independent modules, so a task group can be split out if it balloons.

## Definition of done

- Leg 2 (gated): boots the local server over stdio and asserts real tool responses for the core tools.
- Leg 3 (default suite): `cairn install <host>` writes a correct, parseable config for every registry host (matrix).
- Leg 4 (default suite): the real `session-start.sh` emits a valid SessionStart digest, and `session-end.sh` invokes the sweep with the right args, both against local code.
- CI: the gated `e2e` job runs `tests/e2e/` with `CAIRN_E2E=1` on main + dispatch; default `check` runs the offline legs.
- #45 can be closed (all four legs landed).
