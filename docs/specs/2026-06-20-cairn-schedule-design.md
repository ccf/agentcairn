# `cairn schedule` — Managed Capture Scheduler — Design

**Status:** approved 2026-06-20
**Goal:** Let `cairn` manage a per-OS scheduled job that periodically runs `cairn sweep`, so users get a host-agnostic capture backstop without hand-editing crontab. This closes the "long/resumed session not captured" gap for every host — including the ones that can't get a Claude-Code `PreCompact` hook (Codex, Antigravity, and the MCP-only hosts Cursor/VS Code/Claude Desktop/Gemini), since `sweep` ingests transcripts from all detected harnesses.

## Background

Capture is otherwise hook-driven (SessionStart/SessionEnd + the new PreCompact on Claude Code). Long-lived/resumed sessions and non-Claude-Code hosts can accumulate uncaptured work between session-end events. The documented mitigation is "set up a cron to run `cairn sweep`," but `cron` is deprecated/awkward on macOS (needs Full Disk Access). This feature makes the scheduler a first-class, opt-in `cairn` command.

## Architecture

A small scheduler-abstraction module `src/cairn/schedule.py` with a per-OS backend, exposed as a `cairn schedule` Typer sub-app (`install` / `uninstall` / `status`). Each backend is split into a **pure render function** (produces the file content / cron line — deterministic, unit-testable on any OS) and a **thin runner** (the `launchctl` / `crontab` side effects — monkeypatched in tests).

The scheduled job is always: `<abs cairn> sweep --vault <abs vault> >> <log> 2>&1` at the chosen interval.

## Components

`src/cairn/schedule.py`:
- `Backend` selection on `sys.platform`: `darwin` → launchd; `linux` → crontab; else → unsupported.
- **macOS (launchd):**
  - `render_plist(cairn, vault, interval_s, log) -> str` — a `.plist` with `Label=dev.agentcairn.sweep`, `ProgramArguments=[cairn, "sweep", "--vault", vault]`, `StartInterval=interval_s`, `RunAtLoad=false`, `StandardOutPath`/`StandardErrorPath=log`.
  - install: write `~/Library/LaunchAgents/dev.agentcairn.sweep.plist`, then `launchctl unload <plist>` (ignore error) + `launchctl load <plist>` (idempotent re-write).
  - uninstall: `launchctl unload <plist>` (ignore error) + remove the plist.
  - status: plist exists? + parse its `StartInterval`; report `launchctl list dev.agentcairn.sweep` presence.
- **Linux (crontab):**
  - `render_cron_line(cairn, vault, interval_min, log) -> str` — cron's minute field can't express `*/N` for `N >= 60`, so: **< 60 min** → `*/<min> * * * * …`; **whole-hour multiples** (60, 120, …) → `0 */<hours> * * * …`; any other `>= 60` value (e.g. 90) is rejected at parse time with a clear message (round to 60 or use a sub-hourly value). Every line ends with the `# agentcairn-sweep` marker and `<cairn> sweep --vault <vault> >> <log> 2>&1`.
  - install: read `crontab -l` (empty if none), drop any existing `# agentcairn-sweep` line, append the new one, write back via `crontab -`.
  - uninstall: read, drop the marked line, write back.
  - status: is a `# agentcairn-sweep` line present? + its interval.
- **Unsupported platform:** `install`/`uninstall` raise a clear "scheduling isn't supported on `<platform>` yet — run `cairn schedule install --print` and add it to your scheduler manually" (Windows/systemd-timer are follow-ups).
- **Helpers:** resolve the absolute `cairn` path (`shutil.which("cairn")` → fallback `sys.argv[0]`/`sys.executable`-based) because launchd/cron run with a minimal PATH; resolve the vault via the existing `cairn.paths` resolver; default log `~/.cache/agentcairn/sweep.log` (parent created on install).
- **Interval parsing:** `parse_interval("30m"|"1h"|"45") -> minutes` (bare number = minutes; `m`/`h` suffix). Reject < 5 minutes (avoid hammering). On Linux only, also reject `>= 60` values that aren't whole-hour multiples (cron can't express them cleanly — see `render_cron_line`); macOS (launchd `StartInterval`, arbitrary seconds = `minutes*60`) has no such restriction.

CLI (`src/cairn/cli.py`): a `schedule` sub-app via `app.add_typer(schedule_app, name="schedule")`:
- `cairn schedule install [--interval 30m] [--vault PATH] [--print]` — `--print` renders the plist/cron line and writes nothing.
- `cairn schedule uninstall` — removes the entry (no-op + friendly message if absent).
- `cairn schedule status` — installed? interval, the scheduled command, and the log path.

## `cairn install` integration

Not an interactive prompt (keeps `install` non-interactive and `--all`-safe). After a successful install, print one hint line:

> Tip: run `cairn schedule install` to capture sessions periodically in the background. Useful for more timely ingestion of memories from long-running sessions.

## Error handling

- Missing `launchctl`/`crontab` binary → clear error naming the missing tool, plus the `--print` suggestion.
- `install` is idempotent: re-running replaces the existing entry, never duplicates.
- `uninstall` when nothing is installed → no error, prints "no agentcairn schedule found."
- `--print` never touches the system.
- Interval below the floor (5m) → rejected with a clear message.

## Testing

- **Pure render functions** (`render_plist`, `render_cron_line`, `parse_interval`) — deterministic unit tests on any OS: assert the plist contains the label/interval/program-args and the cron line has the marker + `*/N` + the resolved command. Test `parse_interval` units + the <5m rejection.
- **Backend install/uninstall/status** — monkeypatch the thin runner (the `launchctl`/`crontab` invocations and file writes redirected under `tmp_path`/a fake HOME); assert the right side effects and idempotency (install twice → one entry) and uninstall-when-absent no-op.
- **CLI** — `CliRunner` over `cairn schedule install --print` asserts rendered output and that nothing was written; `status` on a clean HOME reports "not installed."
- Cross-platform: render tests cover both backends regardless of host OS; the side-effecting tests run the host-OS backend (and the other backend's runner is exercised via monkeypatch).

## Out of scope (follow-ups)

systemd user timer (Linux alternative), Windows Task Scheduler, a config knob for the interval (flag-only for now), and auto-install during `cairn install` (hint only).

## Definition of done

- `cairn schedule install/uninstall/status` work on macOS (launchd) and Linux (crontab), idempotent, opt-in, with `--print`.
- The scheduled job runs `cairn sweep --vault <abs>` at the chosen interval, logging to `~/.cache/agentcairn/sweep.log`.
- `cairn install` prints the hint.
- Unsupported platforms get a clear message + `--print` fallback.
- Render/parse logic is unit-tested on any OS; side effects are tested via a monkeypatched runner.
