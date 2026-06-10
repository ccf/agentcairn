# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path

from typer.testing import CliRunner

from cairn.cli import app

runner = CliRunner()


def _seed_transcript(projects_root, cwd, session, turns):
    enc = cwd.replace("/", "-")
    d = projects_root / enc
    d.mkdir(parents=True)
    lines = []
    for role, text in turns:
        lines.append(
            json.dumps(
                {
                    "type": role,
                    "sessionId": session,
                    "message": {"role": role, "content": text},
                    "cwd": cwd,
                    "timestamp": "2026-06-08T10:00:00Z",
                    "gitBranch": "main",
                }
            )
        )
    (d / f"{session}.jsonl").write_text("\n".join(lines) + "\n")


def test_ingest_command(tmp_path):
    projects = tmp_path / "projects"
    cwd = "/Users/x/proj"
    _seed_transcript(
        projects,
        cwd,
        "sess-1",
        [
            ("user", "thanks!"),
            ("user", "We decided to always escape the ATTACH path before interpolating it."),
        ],
    )
    vault = tmp_path / "vault"
    vault.mkdir()
    result = runner.invoke(
        app,
        [
            "ingest",
            "--vault",
            str(vault),
            "--transcripts-dir",
            str(projects),
            "--project",
            cwd,
        ],
    )
    assert result.exit_code == 0, result.output
    written = list(vault.rglob("*.md"))
    assert len(written) == 1
    assert "escape the ATTACH path" in written[0].read_text()
    assert "1 written" in result.output or "written: 1" in result.output.lower()


def test_version_flag_prints_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.2.0" in result.stdout


def test_parse_command_outputs_json(tmp_path: Path):
    note_file = tmp_path / "coffee.md"
    note_file.write_text(
        "---\ntitle: Coffee\npermalink: coffee\n---\n\n- [method] pour over #brewing\n"
    )
    result = runner.invoke(app, ["parse", str(note_file)])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["permalink"] == "coffee"
    assert data["observations"][0]["category"] == "method"


def test_reindex_and_status(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    (v / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha [[B]]\n")
    idx = tmp_path / "i.duckdb"
    r = runner.invoke(app, ["reindex", str(v), "--index", str(idx), "--embedder", "fake"])
    assert r.exit_code == 0, r.output
    assert "1 note" in r.output
    s = runner.invoke(app, ["index-status", "--index", str(idx)])
    assert s.exit_code == 0
    assert "notes: 1" in s.output


def test_default_ledger_is_outside_vault(tmp_path, monkeypatch):
    """Default dedup ledger must NOT be placed inside the vault root (I2)."""
    projects = tmp_path / "projects"
    cwd = "/Users/x/proj"
    _seed_transcript(
        projects,
        cwd,
        "sess-ledger",
        [("user", "We decided to always escape the ATTACH path before interpolating it.")],
    )
    vault = tmp_path / "vault"
    vault.mkdir()
    # Redirect ~/.cache so we don't pollute the real home dir
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # Patch Path.home() used inside cli.py at call time
    import cairn.cli as cli_mod

    monkeypatch.setattr(cli_mod.Path, "home", staticmethod(lambda: fake_home))
    result = runner.invoke(
        app,
        [
            "ingest",
            "--vault",
            str(vault),
            "--transcripts-dir",
            str(projects),
            "--project",
            cwd,
        ],
    )
    assert result.exit_code == 0, result.output
    # No .sha256 file and no .cairn/ directory anywhere inside the vault
    sha_files = list(vault.rglob("*.sha256"))
    assert sha_files == [], f".sha256 found inside vault: {sha_files}"
    cairn_dirs = list(vault.rglob(".cairn"))
    assert cairn_dirs == [], f".cairn/ found inside vault: {cairn_dirs}"


def test_recall_command(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    (v / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha apple brewing\n")
    idx = tmp_path / "i.duckdb"
    r = runner.invoke(app, ["reindex", str(v), "--index", str(idx), "--embedder", "fake"])
    assert r.exit_code == 0, r.output
    # --no-rerank keeps this hermetic: the default-on reranker would download the
    # ms-marco cross-encoder from HF (flaky/offline-hostile in CI). The default-on
    # resolution itself is covered by test_recall_rerank_default_on (search() spied).
    s = runner.invoke(
        app, ["recall", "apple brewing", "--index", str(idx), "--embedder", "fake", "--no-rerank"]
    )
    assert s.exit_code == 0, s.output
    assert "a" in s.output  # the permalink shows up in results


# add to tests/test_cli.py  (reuses _seed_transcript from test_ingest_command)
def test_sweep_command(tmp_path):
    projects = tmp_path / "projects"
    cwd = "/Users/x/proj"
    _seed_transcript(
        projects,
        cwd,
        "sess-1",
        [
            ("user", "We decided to always escape the ATTACH path before interpolating it."),
        ],
    )
    vault = tmp_path / "vault"
    vault.mkdir()
    idx = tmp_path / "i.duckdb"
    result = runner.invoke(
        app,
        [
            "sweep",
            "--vault",
            str(vault),
            "--transcripts-dir",
            str(projects),
            "--project",
            cwd,
            "--index",
            str(idx),
            "--embedder",
            "fake",
        ],
    )
    assert result.exit_code == 0, result.output
    # a memory note was written AND the index now contains it
    assert list(vault.rglob("*.md"))
    assert idx.exists()
    import duckdb

    n = duckdb.connect(str(idx)).execute("SELECT count(*) FROM notes").fetchone()[0]
    assert n >= 1
    assert "reindex" in result.output.lower() or "indexed" in result.output.lower()


def test_sweep_closes_index_when_reconcile_fails(tmp_path, monkeypatch):
    # If reconcile raises, the writable index connection must still be closed
    # (try/finally) — not leaked.
    projects = tmp_path / "projects"
    cwd = "/Users/x/proj"
    _seed_transcript(projects, cwd, "s1", [("user", "We decided to always do the thing well.")])
    vault = tmp_path / "vault"
    vault.mkdir()
    idx = tmp_path / "i.duckdb"
    closed = {"v": False}

    class _FakeCon:
        def close(self):
            closed["v"] = True

    monkeypatch.setattr("cairn.cli.open_index", lambda *a, **k: _FakeCon())

    def _boom(*a, **k):
        raise RuntimeError("reconcile blew up")

    monkeypatch.setattr("cairn.cli.reconcile", _boom)
    result = runner.invoke(
        app,
        [
            "sweep",
            "--vault",
            str(vault),
            "--transcripts-dir",
            str(projects),
            "--project",
            cwd,
            "--index",
            str(idx),
            "--embedder",
            "fake",
        ],
    )
    assert result.exit_code != 0
    assert closed["v"] is True, "sweep leaked the index connection on failure"


def test_doctor_command_healthy(tmp_path):
    # build a small index via reindex --embedder fake
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha body\n")
    idx = tmp_path / "i.duckdb"
    assert (
        runner.invoke(
            app, ["reindex", str(vault), "--index", str(idx), "--embedder", "fake"]
        ).exit_code
        == 0
    )
    result = runner.invoke(app, ["doctor", "--index", str(idx)])
    assert result.exit_code == 0, result.output
    assert "notes" in result.output.lower()
    assert "ok" in result.output.lower() or "healthy" in result.output.lower()


def test_doctor_command_missing_index(tmp_path):
    result = runner.invoke(app, ["doctor", "--index", str(tmp_path / "nope.duckdb")])
    assert result.exit_code == 1
    assert "no index" in result.output.lower()


def _spy_recall(tmp_path, monkeypatch, argv_extra, env=None):
    """Run `recall` with search() spied; return the captured rerank kwarg."""
    idx = tmp_path / "i.duckdb"
    idx.write_text("")  # make idx.exists() true so the command proceeds
    captured = {}

    monkeypatch.setattr("cairn.cli.open_search", lambda p: object())

    def _spy(con, query, **kw):
        captured.update(kw)
        return []

    monkeypatch.setattr("cairn.cli.search", _spy)
    monkeypatch.delenv("CAIRN_RERANK", raising=False)
    if env:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
    result = runner.invoke(
        app, ["recall", "q", "--index", str(idx), "--embedder", "fake", *argv_extra]
    )
    assert result.exit_code == 0, result.output
    return captured.get("rerank")


def test_recall_rerank_default_on(tmp_path, monkeypatch):
    assert _spy_recall(tmp_path, monkeypatch, []) is True


def test_recall_no_rerank_flag(tmp_path, monkeypatch):
    assert _spy_recall(tmp_path, monkeypatch, ["--no-rerank"]) is False


def test_recall_env_off(tmp_path, monkeypatch):
    assert _spy_recall(tmp_path, monkeypatch, [], env={"CAIRN_RERANK": "0"}) is False


def test_recall_flag_overrides_env(tmp_path, monkeypatch):
    assert _spy_recall(tmp_path, monkeypatch, ["--rerank"], env={"CAIRN_RERANK": "0"}) is True


def test_recent_returns_recent_notes_json(tmp_path):
    import json

    v = tmp_path / "vault"
    v.mkdir()
    (v / "a.md").write_text("---\ntitle: Alpha\npermalink: a\n---\nalpha body\n")
    (v / "b.md").write_text("---\ntitle: Beta\npermalink: b\n---\nbeta body\n")
    idx = tmp_path / "i.duckdb"
    r = runner.invoke(app, ["reindex", str(v), "--index", str(idx), "--embedder", "fake"])
    assert r.exit_code == 0, r.output
    s = runner.invoke(app, ["recent", "--index", str(idx), "-n", "5", "--json"])
    assert s.exit_code == 0, s.output
    data = json.loads(s.stdout)
    perms = {note["permalink"] for note in data["notes"]}
    assert {"a", "b"} <= perms


def test_recent_missing_index_json_is_empty(tmp_path):
    import json

    s = runner.invoke(app, ["recent", "--index", str(tmp_path / "nope.duckdb"), "--json"])
    assert s.exit_code == 0
    assert json.loads(s.stdout) == {"notes": []}


def test_init_creates_obsidian_ready_vault(tmp_path):
    target = tmp_path / "myvault"
    r = runner.invoke(app, ["init", str(target)])
    assert r.exit_code == 0, r.output
    assert (target / ".obsidian" / "app.json").exists()
    welcome = (target / "welcome.md").read_text()
    assert "permalink: welcome" in welcome
    assert str(target) in r.output


def test_init_idempotent_preserves_edits(tmp_path):
    target = tmp_path / "myvault"
    runner.invoke(app, ["init", str(target)])
    (target / "welcome.md").write_text("---\ntitle: Mine\npermalink: welcome\n---\nedited\n")
    (target / "note.md").write_text("---\ntitle: N\npermalink: n\n---\nkeep me\n")
    r2 = runner.invoke(app, ["init", str(target)])  # second run
    assert r2.exit_code == 0
    assert "edited" in (target / "welcome.md").read_text()  # not clobbered
    assert (target / "note.md").exists()  # existing notes untouched


def test_recent_project_filters_by_path_substring(tmp_path):
    import json

    v = tmp_path / "vault"
    v.mkdir()
    (v / "alpha.md").write_text("---\ntitle: Alpha\npermalink: alpha\n---\nbody\n")
    (v / "beta.md").write_text("---\ntitle: Beta\npermalink: beta\n---\nbody\n")
    idx = tmp_path / "i.duckdb"
    r = runner.invoke(app, ["reindex", str(v), "--index", str(idx), "--embedder", "fake"])
    assert r.exit_code == 0, r.output
    s = runner.invoke(app, ["recent", "--index", str(idx), "--project", "alpha", "--json"])
    assert s.exit_code == 0, s.output
    perms = {n["permalink"] for n in json.loads(s.stdout)["notes"]}
    assert "alpha" in perms and "beta" not in perms


def test_default_index_honors_cairn_index_env(monkeypatch, tmp_path):
    """_default_index() uses CAIRN_INDEX (expanding ~) when set, matching the MCP
    server — so CLI commands, hooks, and MCP all target the same customized index."""
    import cairn.cli as cli_mod

    target = tmp_path / "custom.duckdb"
    monkeypatch.setenv("CAIRN_INDEX", str(target))
    assert cli_mod._default_index() == target

    monkeypatch.setenv("CAIRN_INDEX", "~/some-index.duckdb")
    assert cli_mod._default_index() == Path.home() / "some-index.duckdb"

    monkeypatch.delenv("CAIRN_INDEX", raising=False)
    assert cli_mod._default_index() == Path.home() / ".cache" / "agentcairn" / "index.duckdb"


def test_reindex_caches_haystack_tokens(tmp_path):
    import duckdb

    from cairn.index.schema import get_meta

    v = tmp_path / "vault"
    v.mkdir()
    (v / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha beta gamma delta\n")
    idx = tmp_path / "i.duckdb"
    r = runner.invoke(app, ["reindex", str(v), "--index", str(idx), "--embedder", "fake"])
    assert r.exit_code == 0, r.output
    con = duckdb.connect(str(idx))
    cached = get_meta(con, "haystack_tokens")
    assert cached is not None
    # Equals the sum of per-chunk ceil(len/4) over the chunks table.
    expected = con.execute(
        "SELECT COALESCE(SUM(CAST((LENGTH(text)+3)/4 AS BIGINT)),0) FROM chunks"
    ).fetchone()[0]
    assert int(cached) == int(expected)
    assert int(cached) > 0
