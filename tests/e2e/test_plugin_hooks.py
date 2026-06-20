# SPDX-License-Identifier: Apache-2.0
import json
import os
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
START = REPO / "plugin" / "scripts" / "session-start.sh"
END = REPO / "plugin" / "scripts" / "session-end.sh"


def _shim_dir(tmp_path: Path, body: str) -> Path:
    """A bin dir whose fake `uvx` runs `body` with the post-`uvx` argv. Real
    `uvx --from agentcairn>=0.2 cairn X` -> body sees `--from agentcairn>=0.2 cairn X`.

    The correct shim uses `shift 2; exec "$@"` so that after dropping
    `--from agentcairn>=0.2` the remaining argv (`cairn <subcmd> …`) is exec'd
    directly — `exec cairn "$@"` would insert a second `cairn` token and fail.
    Callers that need a custom body (e.g. to record argv) follow the same
    convention: shift 2 drops the uvx flags, then $@ starts with `cairn`."""
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    uvx = d / "uvx"
    uvx.write_text("#!/bin/sh\n" + body + "\n")
    uvx.chmod(0o755)
    return d


def test_session_start_emits_digest(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text(
        "---\ntitle: Pin DuckDB to 1.1\npermalink: pin-duckdb\n---\nWe pinned DuckDB.\n"
    )
    (vault / "b.md").write_text(
        "---\ntitle: Vault is the source of truth\npermalink: vault-truth\n---\nFiles are truth.\n"
    )
    # `shift 2` drops `--from agentcairn>=0.2`; remaining $@ is `cairn <subcmd> …`
    # so `exec "$@"` correctly invokes the local cairn binary.
    shim = _shim_dir(tmp_path, 'shift 2\nexec "$@"')
    env = dict(
        os.environ,
        HOME=str(tmp_path),
        PATH=f"{shim}:{os.environ['PATH']}",
        CAIRN_EMBEDDER="fake",
    )

    # Build the vault-scoped index where `cairn recent` reads it.
    # HOME=tmp_path → cache_root() = tmp_path/.cache/agentcairn
    # → index at tmp_path/.cache/agentcairn/indexes/<vault_key>.duckdb
    subprocess.run(
        ["cairn", "reindex", str(vault), "--embedder", "fake"],
        env=env,
        check=True,
    )

    # The session-start fast-path guard checks for the indexes dir (or legacy
    # index.duckdb). Creating it after reindex ensures the dir exists so the
    # script takes the fast digest path instead of the cold-start branch.
    (tmp_path / ".cache" / "agentcairn" / "indexes").mkdir(parents=True, exist_ok=True)

    res = subprocess.run(
        ["sh", str(START), str(vault)],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert res.returncode == 0, res.stderr
    out = res.stdout.strip()
    assert out, f"session-start emitted nothing; stderr={res.stderr}"
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Pin DuckDB" in payload["hookSpecificOutput"]["additionalContext"]


def test_session_end_invokes_sweep(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    rec = tmp_path / "argv.txt"
    # Shim records the subcommand args (after `shift 2`, $@ = `cairn sweep …`)
    # then exits 0 immediately without actually running cairn. Uses "$@" (one
    # arg per line) rather than "$*" so paths containing spaces don't merge.
    shim = _shim_dir(
        tmp_path,
        f'shift 2\nprintf "%s\\n" "$@" >> "{rec}"\nexit 0',
    )
    env = dict(
        os.environ,
        HOME=str(tmp_path),
        PATH=f"{shim}:{os.environ['PATH']}",
    )
    res = subprocess.run(
        ["sh", str(END), str(vault)],
        input='{"cwd": "/Users/x/proj"}',
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert res.returncode == 0, res.stderr

    # The sweep is detached (nohup &); poll for the recorder file.
    deadline = time.time() + 10
    recorded = ""
    while time.time() < deadline:
        if rec.exists():
            recorded = rec.read_text()
            if "sweep" in recorded:
                break
        time.sleep(0.2)

    assert "sweep" in recorded, f"session-end did not invoke sweep; recorded={recorded!r}"
    assert "--vault" in recorded and str(vault) in recorded
    assert "--project" in recorded and "/Users/x/proj" in recorded
