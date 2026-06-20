# SPDX-License-Identifier: Apache-2.0
import json
import re
import time
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
            "--harness",
            "claude-code",
            "--project",
            cwd,
            "--ledger",
            str(tmp_path / "led.sha256"),  # hermetic: don't share the real ~/.cache ledger
        ],
        env={"CAIRN_JUDGE": "none"},  # hermetic: don't load the fastembed judge
    )
    assert result.exit_code == 0, result.output
    written = list(vault.rglob("*.md"))
    assert len(written) == 1
    assert "escape the ATTACH path" in written[0].read_text()
    assert "1 written" in result.output or "written: 1" in result.output.lower()


def test_version_flag_prints_version():
    import cairn

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert cairn.__version__ in result.stdout  # track the package version, not a literal


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
            "--harness",
            "claude-code",
            "--project",
            cwd,
        ],
        env={"CAIRN_JUDGE": "none"},  # hermetic: don't load the fastembed judge
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


def test_cli_recall_marks_cross_project(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    v.mkdir()
    (v / "other.md").write_text(
        "---\ntitle: Other\npermalink: other\nproject: otherrepo\n---\n"
        "this is the query content about widgets\n"
    )
    idx = tmp_path / "i.duckdb"
    r = runner.invoke(app, ["reindex", str(v), "--index", str(idx), "--embedder", "fake"])
    assert r.exit_code == 0, r.output
    monkeypatch.setattr("os.getcwd", lambda: "/Users/x/git/agentcairn")
    r = runner.invoke(
        app,
        [
            "recall",
            "the query",
            "--index",
            str(idx),
            "--embedder",
            "fake",
            "--no-rerank",
            "--project",
            "agentcairn",
        ],
    )
    assert r.exit_code == 0, r.output
    assert "[from: otherrepo]" in r.output


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
            "--harness",
            "claude-code",
            "--project",
            cwd,
            "--index",
            str(idx),
            "--embedder",
            "fake",
            "--ledger",
            str(tmp_path / "led.sha256"),  # hermetic: don't share the real ~/.cache ledger
        ],
        env={"CAIRN_JUDGE": "none"},  # hermetic: don't load the fastembed judge
    )
    assert result.exit_code == 0, result.output
    # a memory note was written AND the index now contains it
    assert list(vault.rglob("*.md"))
    assert idx.exists()
    import duckdb

    n = duckdb.connect(str(idx)).execute("SELECT count(*) FROM notes").fetchone()[0]
    assert n >= 1
    assert "reindex" in result.output.lower() or "indexed" in result.output.lower()


def test_sweep_embedder_from_config_file(tmp_path, monkeypatch):
    """embedder = "fake" in the config file drives sweep with NO --embedder flag
    (the CLI default must honor the file/env layer, not hardcode fastembed)."""
    import cairn.config as cfg

    conf = tmp_path / "config.toml"
    conf.write_text('embedder = "fake"\n')
    monkeypatch.setenv("CAIRN_CONFIG", str(conf))
    monkeypatch.delenv("CAIRN_EMBEDDER", raising=False)
    cfg._reset()
    projects = tmp_path / "projects"
    cwd = "/Users/x/proj"
    _seed_transcript(
        projects,
        cwd,
        "sess-cfg",
        [("user", "We decided to always escape the ATTACH path before interpolating it.")],
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
            "--harness",
            "claude-code",
            "--project",
            cwd,
            "--index",
            str(idx),
            "--ledger",
            str(tmp_path / "led.sha256"),
        ],
        env={"CAIRN_JUDGE": "none"},  # hermetic: don't load the fastembed judge
    )
    cfg._reset()
    assert result.exit_code == 0, result.output
    assert idx.exists()  # index built with the fake embedder (no model download)
    import duckdb

    from cairn.index import get_meta

    con = duckdb.connect(str(idx))
    assert con.execute("SELECT count(*) FROM notes").fetchone()[0] >= 1
    assert get_meta(con, "embedding_model").startswith("fake")  # file layer won, not fastembed
    con.close()


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
            "--harness",
            "claude-code",
            "--project",
            cwd,
            "--index",
            str(idx),
            "--embedder",
            "fake",
        ],
        env={"CAIRN_JUDGE": "none"},  # hermetic: don't load the fastembed judge
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
    result = runner.invoke(app, ["doctor", "--index", str(idx), "--vault", str(vault)])
    assert result.exit_code == 0, result.output
    assert "notes" in result.output.lower()
    assert "ok" in result.output.lower() or "healthy" in result.output.lower()


def test_doctor_command_missing_index(tmp_path):
    result = runner.invoke(app, ["doctor", "--index", str(tmp_path / "nope.duckdb")])
    assert result.exit_code == 1
    assert "no index" in result.output.lower()


def test_doctor_reports_drift_on_dead_path(tmp_path, monkeypatch):
    from cairn import paths

    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha body\n")
    assert runner.invoke(app, ["reindex", str(vault), "--embedder", "fake"]).exit_code == 0
    (vault / "a.md").unlink()  # delete on-disk note → index path now dead
    r = runner.invoke(app, ["doctor", "--vault", str(vault)])
    assert "DRIFT" in r.output
    assert "indexed" in r.output.lower()


def test_doctor_reports_drift_on_unindexed_note(tmp_path, monkeypatch):
    from cairn import paths

    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha body\n")
    assert runner.invoke(app, ["reindex", str(vault), "--embedder", "fake"]).exit_code == 0
    (vault / "b.md").write_text("---\ntitle: B\npermalink: b\n---\nbeta body\n")  # unindexed
    r = runner.invoke(app, ["doctor", "--vault", str(vault)])
    assert "DRIFT" in r.output


def test_doctor_ok_when_in_sync(tmp_path, monkeypatch):
    from cairn import paths

    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha body\n")
    assert runner.invoke(app, ["reindex", str(vault), "--embedder", "fake"]).exit_code == 0
    r = runner.invoke(app, ["doctor", "--vault", str(vault)])
    assert "status: OK" in r.output


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
    # Must equal the shared Python estimator summed per chunk — the cached SQL
    # value and estimate_tokens are claimed to be the identical model, so assert
    # it directly (a rounding SQL would diverge here).
    from cairn.usage import estimate_tokens

    texts = [row[0] for row in con.execute("SELECT text FROM chunks").fetchall()]
    assert int(cached) == sum(estimate_tokens(t) for t in texts)
    assert int(cached) > 0


def test_cli_recall_records_savings(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    v.mkdir()
    (v / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha apple brewing\n")
    idx = tmp_path / "i.duckdb"
    assert (
        runner.invoke(app, ["reindex", str(v), "--index", str(idx), "--embedder", "fake"]).exit_code
        == 0
    )
    led = tmp_path / "usage.jsonl"
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(led))
    monkeypatch.delenv("CAIRN_USAGE", raising=False)
    r = runner.invoke(
        app, ["recall", "apple brewing", "--index", str(idx), "--embedder", "fake", "--no-rerank"]
    )
    assert r.exit_code == 0, r.output
    import json as _j

    rows = [_j.loads(x) for x in led.read_text().splitlines() if x.strip()]
    assert len(rows) == 1
    assert rows[0]["event"] == "recall"
    assert rows[0]["full"] > 0


def test_savings_command_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(tmp_path / "u.jsonl"))
    monkeypatch.delenv("CAIRN_USAGE", raising=False)
    r = runner.invoke(app, ["savings"])
    assert r.exit_code == 0, r.output
    assert "No recalls recorded" in r.output


def test_savings_command_reports(tmp_path, monkeypatch):
    led = tmp_path / "u.jsonl"
    led.write_text(
        '{"v":1,"ts":"2026-06-01T00:00:00+00:00","event":"recall","k":5,"full":10000,"recalled":200}\n'
    )
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(led))
    monkeypatch.delenv("CAIRN_USAGE", raising=False)
    r = runner.invoke(app, ["savings"])
    assert r.exit_code == 0, r.output
    assert "9,800" in r.output  # 10000 - 200 saved, comma-grouped
    assert "1" in r.output  # recalls


def test_savings_json(tmp_path, monkeypatch):
    led = tmp_path / "u.jsonl"
    led.write_text(
        '{"v":1,"ts":"2026-06-01T00:00:00+00:00","event":"recall","k":5,"full":10000,"recalled":200}\n'
    )
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(led))
    r = runner.invoke(app, ["savings", "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert data["recalls"] == 1
    assert data["total_saved"] == 9800


def test_savings_oneline(tmp_path, monkeypatch):
    led = tmp_path / "u.jsonl"
    led.write_text(
        '{"v":1,"ts":"2026-06-01T00:00:00+00:00","event":"recall","k":5,"full":10000,"recalled":200}\n'
    )
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(led))
    r = runner.invoke(app, ["savings", "--oneline"])
    assert r.exit_code == 0, r.output
    assert "saved you" in r.stdout


def test_savings_oneline_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(tmp_path / "u.jsonl"))
    r = runner.invoke(app, ["savings", "--oneline"])
    assert r.exit_code == 0
    assert r.stdout.strip() == ""


def test_warm_fake_embedder_rerank_off_is_noop(monkeypatch):
    monkeypatch.setenv("CAIRN_EMBEDDER", "fake")
    monkeypatch.setenv("CAIRN_RERANK", "0")
    r = runner.invoke(app, ["warm"])
    assert r.exit_code == 0, r.output
    assert "fake" in r.output  # nothing to warm for the fake embedder
    assert "skipped" in r.output.lower()  # reranker skipped


def test_warm_embedder_failure_is_best_effort(monkeypatch):
    monkeypatch.setenv("CAIRN_EMBEDDER", "fastembed")
    monkeypatch.setenv("CAIRN_RERANK", "0")

    def _boom(name):
        raise RuntimeError("download blew up")

    monkeypatch.setattr("cairn.cli.get_embedder", _boom)
    r = runner.invoke(app, ["warm"])
    assert r.exit_code == 0, r.output  # best-effort: never crashes
    assert "fail" in r.output.lower()


def test_warm_warms_reranker_when_enabled(monkeypatch):
    monkeypatch.setenv("CAIRN_EMBEDDER", "fake")  # skip the embedder download
    monkeypatch.delenv("CAIRN_RERANK", raising=False)  # default: rerank ON
    called = {}

    def _spy(query, candidates, **kw):
        called["args"] = (query, candidates)
        return candidates

    monkeypatch.setattr("cairn.search.rerank_candidates", _spy)
    r = runner.invoke(app, ["warm"])
    assert r.exit_code == 0, r.output
    assert called["args"][0] == "warm"
    assert called["args"][1] == [{"text": "hello"}]


def test_warm_skips_reranker_when_disabled(monkeypatch):
    monkeypatch.setenv("CAIRN_EMBEDDER", "fake")
    monkeypatch.setenv("CAIRN_RERANK", "0")
    called = {"n": 0}

    def _spy(query, candidates, **kw):
        called["n"] += 1
        return candidates

    monkeypatch.setattr("cairn.search.rerank_candidates", _spy)
    r = runner.invoke(app, ["warm"])
    assert r.exit_code == 0, r.output
    assert called["n"] == 0  # reranker not warmed when disabled


def test_warm_forces_embedder_probe_via_dim(monkeypatch):
    # ollama probes the server lazily on .dim; warm must touch .dim so it
    # actually loads/validates instead of just constructing the object.
    monkeypatch.setenv("CAIRN_EMBEDDER", "ollama")
    monkeypatch.setenv("CAIRN_RERANK", "0")
    touched = {"dim": False}

    class _Emb:
        @property
        def dim(self):
            touched["dim"] = True
            return 768

    monkeypatch.setattr("cairn.cli.get_embedder", lambda name: _Emb())
    r = runner.invoke(app, ["warm"])
    assert r.exit_code == 0, r.output
    assert touched["dim"] is True


def test_install_cursor_writes_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".cursor").mkdir()
    r = runner.invoke(app, ["install", "cursor", "--vault", str(tmp_path / "v")])
    assert r.exit_code == 0, r.output
    import json as _j

    data = _j.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    ac = data["mcpServers"]["agentcairn"]
    assert ac["command"] == "uvx" and ac["args"] == ["agentcairn"]
    assert ac["env"]["CAIRN_VAULT"] == str((tmp_path / "v").resolve())  # absolute


def test_install_print_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".cursor").mkdir()
    r = runner.invoke(app, ["install", "cursor", "--print"])
    assert r.exit_code == 0, r.output
    assert "agentcairn" in r.output
    assert not (tmp_path / ".cursor" / "mcp.json").exists()


def test_install_cursor_writes_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".cursor").mkdir()
    r = runner.invoke(app, ["install", "cursor", "--vault", str(tmp_path / "v")])
    assert r.exit_code == 0, r.output
    skill = tmp_path / ".cursor" / "skills" / "using-agentcairn-memory" / "SKILL.md"
    assert skill.is_file()
    assert "name: using-agentcairn-memory" in skill.read_text(encoding="utf-8")


def test_install_cursor_print_notes_skill_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".cursor").mkdir()
    r = runner.invoke(app, ["install", "cursor", "--print"])
    assert r.exit_code == 0, r.output
    assert "would install skill" in r.output
    assert not (tmp_path / ".cursor" / "skills").exists()


def test_install_non_skill_host_writes_no_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # vscode is an mcp host with skill_dir=None; installing it must not create a skill.
    r = runner.invoke(app, ["install", "vscode", "--print"])
    assert r.exit_code == 0, r.output
    assert "would install skill" not in r.output


def test_install_no_arg_previews(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".cursor").mkdir()
    r = runner.invoke(app, ["install"])
    assert r.exit_code == 0, r.output
    assert "cursor" in r.output.lower()
    assert not (tmp_path / ".cursor" / "mcp.json").exists()  # preview only


def test_install_unknown_host_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = runner.invoke(app, ["install", "nope"])
    assert r.exit_code == 1
    assert "unknown host" in r.output.lower()


def test_install_defaults_honor_config_file(tmp_path, monkeypatch):
    """Without --vault, install resolves CAIRN_VAULT from the env/file layer instead
    of hardcoding ~/agentcairn. CAIRN_INDEX is no longer pinned in the MCP entry."""
    import json as _j

    import cairn.config as cfg

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".cursor").mkdir()
    vault_dir = tmp_path / "myvault"
    idx = tmp_path / "ix" / "index.duckdb"
    conf = tmp_path / "config.toml"
    conf.write_text(f'vault = "{vault_dir}"\nindex = "{idx}"\n')
    monkeypatch.setenv("CAIRN_CONFIG", str(conf))
    monkeypatch.delenv("CAIRN_VAULT", raising=False)
    monkeypatch.delenv("CAIRN_INDEX", raising=False)
    cfg._reset()
    r = runner.invoke(app, ["install", "cursor"])
    cfg._reset()
    assert r.exit_code == 0, r.output
    data = _j.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    env = data["mcpServers"]["agentcairn"]["env"]
    assert env["CAIRN_VAULT"] == str(vault_dir.resolve())
    assert "CAIRN_INDEX" not in env  # index is now derived from the vault


def test_install_all_with_none_detected_reports_and_exits_0(tmp_path, monkeypatch):
    import cairn.hosts as _hosts

    monkeypatch.setenv("HOME", str(tmp_path))  # empty HOME → no host dirs present
    monkeypatch.setattr(_hosts.shutil, "which", lambda c: None)  # no plugin CLIs on PATH
    r = runner.invoke(app, ["install", "--all"])
    assert r.exit_code == 0, r.output
    assert "no supported agents detected" in r.output.lower()


def test_install_all_print_labels_each_host(tmp_path, monkeypatch):
    import cairn.hosts as _hosts

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(_hosts.shutil, "which", lambda c: None)  # no plugin CLIs (MCP-only test)
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".gemini").mkdir()
    (tmp_path / ".gemini" / "settings.json").write_text("{}")
    r = runner.invoke(app, ["install", "--all", "--print"])
    assert r.exit_code == 0, r.output
    assert "# Cursor" in r.output
    assert "# Gemini CLI" in r.output
    assert not (tmp_path / ".cursor" / "mcp.json").exists()


def test_install_codex_print_shows_plugin_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = runner.invoke(app, ["install", "codex", "--print"])
    assert r.exit_code == 0, r.output
    assert "codex plugin marketplace add ccf/agentcairn" in r.output
    assert "codex plugin add agentcairn@agentcairn" in r.output


def test_install_claude_code_print_and_source_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = runner.invoke(app, ["install", "claude-code", "--print", "--source", "/local/checkout"])
    assert r.exit_code == 0, r.output
    assert "claude plugin marketplace add /local/checkout" in r.output
    assert "claude plugin install agentcairn@agentcairn" in r.output


def test_install_codex_failed_install_preserves_stale_mcp_block(tmp_path, monkeypatch):
    # If the plugin install fails (codex CLI absent), the stale MCP block must NOT
    # be removed — migration runs only after a successful install.
    import cairn.hosts.plugins as _pl

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(_pl.shutil, "which", lambda c: None)  # codex CLI absent
    cfg = tmp_path / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[mcp_servers.agentcairn]\ncommand = "uvx"\nargs = ["agentcairn"]\n')
    r = runner.invoke(app, ["install", "codex"])  # real run (not --print)
    assert r.exit_code != 0  # install failed
    assert "[mcp_servers.agentcairn]" in cfg.read_text()  # stale block preserved
    assert not cfg.with_name("config.toml.bak").exists()  # migration never ran


def test_install_plugin_vault_notice_shows_under_print(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = runner.invoke(app, ["install", "codex", "--print", "--vault", str(tmp_path / "v")])
    assert r.exit_code == 0, r.output
    assert "doesn't apply" in r.output  # notice shown even under --print


def test_install_codex_print_reports_stale_mcp_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[mcp_servers.agentcairn]\ncommand = "uvx"\nargs = ["agentcairn"]\n')
    r = runner.invoke(app, ["install", "codex", "--print"])
    assert r.exit_code == 0, r.output
    assert "mcp_servers.agentcairn" in r.output
    assert cfg.read_text().startswith("[mcp_servers.agentcairn]")


def test_install_antigravity_print_shows_agy_command(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = runner.invoke(app, ["install", "antigravity", "--print", "--source", "/x/plugin"])
    assert r.exit_code == 0, r.output
    assert "agy plugin install /x/plugin" in r.output


def test_install_antigravity_print_reports_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / ".gemini" / "config" / "mcp_config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('{"mcpServers": {"agentcairn": {"command": "uvx"}}}')
    r = runner.invoke(app, ["install", "antigravity", "--print", "--source", "/x/plugin"])
    assert r.exit_code == 0, r.output
    assert "mcpServers.agentcairn" in r.output
    assert "agentcairn" in cfg.read_text()  # --print writes nothing


def test_install_antigravity_requires_source(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = runner.invoke(app, ["install", "antigravity", "--print"])
    assert r.exit_code != 0
    assert "needs --source" in r.output


def test_install_codex_still_defaults_source(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = runner.invoke(app, ["install", "codex", "--print"])
    assert r.exit_code == 0, r.output
    assert "codex plugin add agentcairn@agentcairn" in r.output  # default source still works


def test_ingest_reports_per_kind_skips(tmp_path, monkeypatch):
    import json as _j

    # a transcript with one authored user turn + one tool-result + one task-notification
    proj = tmp_path / "projects" / "-Users-x-proj"
    proj.mkdir(parents=True)
    lines = [
        _j.dumps(
            {
                "type": "user",
                "sessionId": "s",
                "cwd": "/Users/x/proj",
                "message": {
                    "role": "user",
                    "content": "we decided to always rebase-merge the branch",
                },
            }
        ),
        _j.dumps(
            {
                "type": "user",
                "sessionId": "s",
                "toolUseResult": {},
                "message": {"role": "user", "content": "tool output blah blah blah blah blah"},
            }
        ),
        _j.dumps(
            {
                "type": "user",
                "sessionId": "s",
                "origin": {"kind": "task-notification"},
                "message": {"role": "user", "content": "<task-notification> done done done done"},
            }
        ),
    ]
    (proj / "t.jsonl").write_text("\n".join(lines) + "\n")
    vault = tmp_path / "vault"
    r = runner.invoke(
        app,
        [
            "ingest",
            "--vault",
            str(vault),
            "--transcripts-dir",
            str(tmp_path / "projects"),
            "--harness",
            "claude-code",
            "--ledger",
            str(tmp_path / "led.sha256"),
        ],
        env={"CAIRN_JUDGE": "none"},  # hermetic: don't load the fastembed judge
    )
    assert r.exit_code == 0, r.output
    assert "1 authored" in r.output
    assert "tool_result" in r.output and "meta_injection" in r.output


def test_ingest_counts_nontext_tool_results(tmp_path):
    import json as _j

    proj = tmp_path / "projects" / "-Users-x-proj"
    proj.mkdir(parents=True)
    lines = [
        _j.dumps(
            {
                "type": "user",
                "sessionId": "s",
                "cwd": "/Users/x/proj",
                "message": {
                    "role": "user",
                    "content": "we decided to always rebase-merge the branch",
                },
            }
        ),
        # tool result with NON-text content -> dropped from events, but must still be counted
        _j.dumps(
            {
                "type": "user",
                "sessionId": "s",
                "toolUseResult": {},
                "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]},
            }
        ),
    ]
    (proj / "t.jsonl").write_text("\n".join(lines) + "\n")
    vault = tmp_path / "vault"
    r = runner.invoke(
        app,
        [
            "ingest",
            "--vault",
            str(vault),
            "--transcripts-dir",
            str(tmp_path / "projects"),
            "--harness",
            "claude-code",
            "--ledger",
            str(tmp_path / "led.sha256"),
        ],
        env={"CAIRN_JUDGE": "none"},  # hermetic: don't load the fastembed judge
    )
    assert r.exit_code == 0, r.output
    assert "1 tool_result" in r.output  # counted despite non-text content being dropped


def test_ingest_dry_run_skips_llm_judge(tmp_path, monkeypatch):
    """--dry-run must never hit the live LLM: the judge is resolved with
    CAIRN_JUDGE forced down to 'embedding' (or kept at 'none')."""
    seen: dict = {}

    def spy(**kw):
        seen.update(kw)
        return None

    monkeypatch.setattr("cairn.cli.resolve_judge", spy)
    projects = tmp_path / "projects"
    cwd = "/Users/x/proj"
    _seed_transcript(projects, cwd, "s-dry", [("user", "We decided to always do the thing.")])
    vault = tmp_path / "vault"
    vault.mkdir()
    r = runner.invoke(
        app,
        [
            "ingest",
            "--vault",
            str(vault),
            "--transcripts-dir",
            str(projects),
            "--harness",
            "claude-code",
            "--ledger",
            str(tmp_path / "led.sha256"),
            "--dry-run",
        ],
        env={"CAIRN_JUDGE": "anthropic", "ANTHROPIC_API_KEY": "k"},
    )
    assert r.exit_code == 0, r.output
    assert seen["env"]["CAIRN_JUDGE"] == "embedding"  # anthropic forced away on dry runs


def test_ingest_notes_when_anthropic_tier_unavailable(tmp_path, monkeypatch):
    """CAIRN_JUDGE=anthropic but the run used a lower tier -> one explanatory line."""
    monkeypatch.setattr("cairn.cli.resolve_judge", lambda **kw: None)
    projects = tmp_path / "projects"
    cwd = "/Users/x/proj"
    _seed_transcript(projects, cwd, "s-note", [("user", "We decided to always do the thing.")])
    vault = tmp_path / "vault"
    vault.mkdir()
    r = runner.invoke(
        app,
        [
            "ingest",
            "--vault",
            str(vault),
            "--transcripts-dir",
            str(projects),
            "--harness",
            "claude-code",
            "--ledger",
            str(tmp_path / "led.sha256"),
        ],
        env={"CAIRN_JUDGE": "anthropic"},  # no key -> tier degrades
    )
    assert r.exit_code == 0, r.output
    assert "judge=anthropic configured but LLM tier unavailable" in r.output


def test_ingest_warns_loudly_when_llm_tier_degraded(tmp_path, monkeypatch):
    """The LLM tier was RESOLVED (judge_tier == 'llm') but every batch failed and
    degraded to a fallback — the old warning (which only checked tier != 'llm')
    stayed silent. A degraded run must say so, with a count and a remedy."""
    import cairn.ingest.judge as jmod

    def boom(payload, api_key, timeout):
        raise TimeoutError("batch too slow for the timeout")

    monkeypatch.setattr(jmod, "_anthropic_request", boom)
    monkeypatch.setattr(
        "cairn.cli.resolve_judge",
        lambda **kw: jmod.LLMJudge(api_key="k", model="m", timeout=1.0),
    )
    projects = tmp_path / "projects"
    cwd = "/Users/x/proj"
    _seed_transcript(projects, cwd, "s-deg", [("user", "We decided to always do the thing.")])
    vault = tmp_path / "vault"
    vault.mkdir()
    r = runner.invoke(
        app,
        [
            "ingest",
            "--vault",
            str(vault),
            "--transcripts-dir",
            str(projects),
            "--harness",
            "claude-code",
            "--ledger",
            str(tmp_path / "led.sha256"),
        ],
        env={"CAIRN_JUDGE": "anthropic"},
    )
    assert r.exit_code == 0, r.output
    assert "degraded" in r.output.lower()
    assert "judge_timeout" in r.output  # points at the actual remedy


def test_ingest_reports_judge_tier(tmp_path):
    import json as _j

    proj = tmp_path / "projects" / "-Users-x-proj"
    proj.mkdir(parents=True)
    (proj / "t.jsonl").write_text(
        _j.dumps(
            {
                "type": "user",
                "sessionId": "s",
                "cwd": "/Users/x/proj",
                "message": {
                    "role": "user",
                    "content": "we decided to always rebase-merge the branch",
                },
            }
        )
        + "\n"
    )
    vault = tmp_path / "vault"
    r = runner.invoke(
        app,
        [
            "ingest",
            "--vault",
            str(vault),
            "--transcripts-dir",
            str(tmp_path / "projects"),
            "--harness",
            "claude-code",
            "--ledger",
            str(tmp_path / "led.sha256"),
        ],
        env={"CAIRN_JUDGE": "none"},
    )
    assert r.exit_code == 0, r.output
    assert "judge: none" in r.output


def test_ingest_embedder_flag_drives_judge(tmp_path):
    """Bugbot (PR #57): ingest must honor --embedder like sweep does, so the
    judge scores in the same embedding space regardless of entry command."""
    import json as _j

    proj = tmp_path / "projects" / "-Users-x-proj"
    proj.mkdir(parents=True)
    (proj / "t.jsonl").write_text(
        _j.dumps(
            {
                "type": "user",
                "sessionId": "s",
                "cwd": "/Users/x/proj",
                "message": {
                    "role": "user",
                    "content": "we decided to always rebase-merge the branch",
                },
            }
        )
        + "\n"
    )
    r = runner.invoke(
        app,
        [
            "ingest",
            "--vault",
            str(tmp_path / "vault"),
            "--transcripts-dir",
            str(tmp_path / "projects"),
            "--harness",
            "claude-code",
            "--ledger",
            str(tmp_path / "led.sha256"),
            "--embedder",
            "fake",
        ],
        env={"CAIRN_JUDGE": "embedding"},
    )
    assert r.exit_code == 0, r.output
    assert "judge: embedding" in r.output  # judge ran on the fake embedder (no model download)


def test_config_file_drives_judge_tier(tmp_path, monkeypatch):
    """End-to-end: judge = "none" in the config file changes the ingest tier
    with NO env var set (the whole point of the file)."""
    import json as _j

    import cairn.config as cfg

    conf = tmp_path / "config.toml"
    conf.write_text('judge = "none"\n')
    monkeypatch.setenv("CAIRN_CONFIG", str(conf))
    monkeypatch.delenv("CAIRN_JUDGE", raising=False)
    cfg._reset()
    proj = tmp_path / "projects" / "-Users-x-proj"
    proj.mkdir(parents=True)
    (proj / "t.jsonl").write_text(
        _j.dumps(
            {
                "type": "user",
                "sessionId": "s",
                "cwd": "/Users/x/proj",
                "message": {"role": "user", "content": "we decided to always rebase-merge"},
            }
        )
        + "\n"
    )
    r = runner.invoke(
        app,
        [
            "ingest",
            "--vault",
            str(tmp_path / "vault"),
            "--transcripts-dir",
            str(tmp_path / "projects"),
            "--harness",
            "claude-code",
            "--ledger",
            str(tmp_path / "led.sha256"),
        ],
    )
    cfg._reset()
    assert r.exit_code == 0, r.output
    assert "judge: none" in r.output


def test_config_inspect_shows_sources(tmp_path, monkeypatch):
    import cairn.config as cfg

    conf = tmp_path / "config.toml"
    conf.write_text('judge = "anthropic"\nanthropic_api_key = "sk-ant-test-abcdef12345678"\n')
    monkeypatch.setenv("CAIRN_CONFIG", str(conf))
    monkeypatch.setenv("CAIRN_EMBEDDER", "fake")
    monkeypatch.delenv("CAIRN_JUDGE", raising=False)
    cfg._reset()
    r = runner.invoke(app, ["config"])
    cfg._reset()
    assert r.exit_code == 0, r.output
    out = r.output
    lines = out.splitlines()
    judge_line = next(ln for ln in lines if ln.strip().startswith("judge "))
    assert "anthropic" in judge_line and "[file]" in judge_line  # file-sourced
    emb_line = next(ln for ln in lines if ln.strip().startswith("embedder "))
    assert "fake" in emb_line and "[env]" in emb_line  # env-sourced
    assert "default" in out  # untouched knobs
    assert "sk-ant-test-abcdef12345678" not in out  # secret masked
    assert "5678" in out  # long secret (26 chars > 20): last4 shown


def test_config_inspect_short_secret_fully_masked(tmp_path, monkeypatch):
    """Secrets of <= 20 chars show '…set…' — prefix+last4 would leave too little."""
    import cairn.config as cfg

    conf = tmp_path / "config.toml"
    conf.write_text('anthropic_api_key = "sk-ant-12345"\n')  # 12 chars
    monkeypatch.setenv("CAIRN_CONFIG", str(conf))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg._reset()
    r = runner.invoke(app, ["config"])
    cfg._reset()
    assert r.exit_code == 0, r.output
    assert "sk-ant-12345" not in r.output
    assert "…set…" in r.output
    assert "2345" not in r.output  # no last4 leak for short secrets


def test_config_init_scaffolds_template(tmp_path, monkeypatch):
    import cairn.config as cfg

    conf = tmp_path / "sub" / "config.toml"  # parent must be created
    monkeypatch.setenv("CAIRN_CONFIG", str(conf))
    cfg._reset()
    r = runner.invoke(app, ["config", "--init"])
    cfg._reset()
    assert r.exit_code == 0, r.output
    assert conf.exists()
    assert (conf.stat().st_mode & 0o777) == 0o600  # key may live here
    body = conf.read_text()
    assert '# judge = "embedding"' in body  # every knob present, commented out
    assert "# anthropic_api_key" in body
    # non-string knobs emit valid (unquoted) TOML so uncommenting just works
    assert "# rerank = true" in body and '# rerank = "true"' not in body
    assert "# usage = 1" in body and '# usage = "1"' not in body
    assert "# judge_timeout = 90" in body and '# judge_timeout = "90"' not in body
    # refuses overwrite
    r2 = runner.invoke(app, ["config", "--init"])
    assert r2.exit_code == 0
    assert "exists" in r2.output.lower()


def test_distilled_neighbor_index_loads_live_and_excludes_superseded(tmp_path):
    from cairn.cli import _DistilledNeighborIndex

    class FakeEmbedder:
        dim = 3

        def embed(self, texts):
            out = []
            for t in texts:
                tl = t.lower()
                if "ram" in tl:
                    out.append([1.0, 0.0, 0.0])
                elif "signoz" in tl:
                    out.append([0.0, 1.0, 0.0])
                else:
                    out.append([0.0, 0.0, 1.0])
            return out

    mem = tmp_path / "memories"
    mem.mkdir()
    (mem / "ram-live.md").write_text(
        "---\ntitle: RAM\ntype: memory\npermalink: ram-live\n"
        "created: '2026-06-01T00:00:00'\n---\n\n- [context] scale RAM to 2GB #ingested\n",
        encoding="utf-8",
    )
    (mem / "ram-old.md").write_text(
        "---\ntitle: RAM old\ntype: memory\npermalink: ram-old\n"
        "superseded_by: ram-live\n---\n\n- [context] scale RAM to 1GB #ingested\n",
        encoding="utf-8",
    )
    (mem / "no-context.md").write_text(
        "---\ntitle: hand\ntype: memory\npermalink: hand\n---\n\nhand-authored body\n",
        encoding="utf-8",
    )
    nidx = _DistilledNeighborIndex(vault_root=tmp_path, subdir="memories", embedder=FakeEmbedder())
    hit = nidx.nearest("scale RAM to 4GB")
    assert hit is not None
    neighbor, cos = hit
    assert neighbor.permalink == "ram-live"  # live note, not the superseded one
    assert neighbor.timestamp == "2026-06-01T00:00:00"  # created frontmatter
    assert neighbor.path and neighbor.path.endswith("ram-live.md")
    assert nidx.nearest("totally unrelated topic xyz") is None  # orthogonal -> below gate


def test_distilled_neighbor_index_batch_and_note_superseded(tmp_path):
    from cairn.cli import _DistilledNeighborIndex

    class FakeEmbedder:
        dim = 2

        def embed(self, texts):
            return [[1.0, 0.0] if "ram" in t.lower() else [0.0, 1.0] for t in texts]

    (tmp_path / "memories").mkdir()
    nidx = _DistilledNeighborIndex(vault_root=tmp_path, subdir="memories", embedder=FakeEmbedder())
    assert nidx.nearest("ram 4gb") is None  # empty vault
    nidx.add("ram-2gb", "scale ram to 2gb", "t0", str(tmp_path / "memories" / "ram-2gb.md"))
    hit = nidx.nearest("scale ram to 4gb")
    assert hit is not None and hit[0].permalink == "ram-2gb"
    nidx.note_superseded("ram-2gb")
    assert nidx.nearest("scale ram to 4gb") is None  # flagged -> skipped


def test_distilled_neighbor_index_batches_beyond_embed_batch(tmp_path):
    """Construction batches embedding in _EMBED_BATCH chunks; >64 notes all load."""
    from cairn.cli import _DistilledNeighborIndex
    from cairn.ingest.judge import _EMBED_BATCH

    mem = tmp_path / "memories"
    mem.mkdir()
    n = _EMBED_BATCH + 1  # force a second batch
    for i in range(n):
        body = (
            f"---\npermalink: note-{i}\ntype: memory\n---\n\n"
            f"- [context] distinct fact number {i} #ingested\n"
        )
        (mem / f"note-{i}.md").write_text(body, encoding="utf-8")

    class FakeEmbedder:
        dim = 2

        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]  # all identical -> any query matches one

    nidx = _DistilledNeighborIndex(vault_root=tmp_path, subdir="memories", embedder=FakeEmbedder())
    assert len(nidx._live) == n  # every note across both batches loaded
    hit = nidx.nearest("anything")
    assert hit is not None and hit[1] >= 0.99  # identical vectors -> cosine ~1


def test_distilled_neighbor_index_skips_malformed_note(tmp_path):
    """A note that parse_note chokes on is skipped at load, not fatal."""
    from cairn.cli import _DistilledNeighborIndex

    mem = tmp_path / "memories"
    mem.mkdir()
    (mem / "good.md").write_text(
        "---\npermalink: good\ntype: memory\n---\n\n- [context] a good fact #ingested\n",
        encoding="utf-8",
    )
    (mem / "bad.md").write_bytes(b"\x00\xff not valid utf-8 or frontmatter \x00")

    class FakeEmbedder:
        dim = 2

        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    nidx = _DistilledNeighborIndex(vault_root=tmp_path, subdir="memories", embedder=FakeEmbedder())
    perms = {row[0] for row in nidx._live}
    assert "good" in perms and "bad" not in perms  # malformed skipped, construction succeeded


def test_transcripts_dir_requires_single_harness(tmp_path, monkeypatch):
    monkeypatch.delenv("CAIRN_HARNESSES", raising=False)
    vault = tmp_path / "vault"
    vault.mkdir()
    # Render the error wide + uncolored so Typer's Rich panel doesn't wrap the
    # message across lines (which breaks a naive substring match at CI's 80-col
    # default). Strip residual ANSI, then collapse whitespace before matching.
    res = runner.invoke(
        app,
        ["sweep", "--vault", str(vault), "--transcripts-dir", str(tmp_path), "--embedder", "fake"],
        env={"COLUMNS": "200", "NO_COLOR": "1"},
    )
    assert res.exit_code != 0
    clean = " ".join(re.sub(r"\x1b\[[0-9;]*m", "", res.output).split())
    assert "exactly one --harness" in clean


def test_sweep_default_index_is_vault_scoped(tmp_path, monkeypatch):
    """With no --index and no CAIRN_INDEX, sweep writes the vault-derived index."""
    from cairn import paths

    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    monkeypatch.delenv("CAIRN_INDEX", raising=False)
    projects = tmp_path / "projects"
    cwd = "/Users/x/proj"
    _seed_transcript(
        projects,
        cwd,
        "s1",
        [("user", "We decided to always escape the ATTACH path before interpolating it.")],
    )
    vault = tmp_path / "vault"
    vault.mkdir()
    r = runner.invoke(
        app,
        [
            "sweep",
            "--vault",
            str(vault),
            "--transcripts-dir",
            str(projects),
            "--harness",
            "claude-code",
            "--project",
            cwd,
            "--embedder",
            "fake",
            "--ledger",
            str(tmp_path / "led.sha256"),
        ],
        env={"CAIRN_JUDGE": "none"},
    )
    assert r.exit_code == 0, r.output
    assert paths.default_index(vault).exists()  # vault-scoped, not the global path


def test_recall_derives_index_from_vault(tmp_path, monkeypatch):
    """recall with --vault (no --index) reads the vault-derived index sweep wrote."""
    from cairn import paths

    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    monkeypatch.delenv("CAIRN_INDEX", raising=False)
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha apple brewing\n")
    idx = paths.default_index(vault)
    r = runner.invoke(app, ["reindex", str(vault), "--embedder", "fake"])  # derives same path
    assert r.exit_code == 0, r.output
    assert idx.exists()
    s = runner.invoke(
        app, ["recall", "apple brewing", "--vault", str(vault), "--embedder", "fake", "--no-rerank"]
    )
    assert s.exit_code == 0, s.output
    assert "a" in s.output


def test_sweep_auto_detects_both_harnesses(tmp_path, monkeypatch):
    """cairn sweep with NO --harness ingests Claude Code AND Codex transcripts in one
    auto-detect run — the headline seam of feature #36.

    Strategy: monkeypatch the two module-level root constants that back
    default_root()/is_present()/find() for each adapter, then lay down one
    keeper-grade user turn for each harness and assert that a full sweep
    (no --harness, no --transcripts-dir) writes at least one note from each.

    Determinism: the importance scorer is a pure keyword heuristic (no model).
    Both sample turns use "We decided to always …" prose that scores 0.61 —
    reliably above the 0.5 default threshold with CAIRN_JUDGE=none (embedding
    judge disabled).  No randomness; result is fully deterministic.
    """
    import cairn.ingest.harness.claude_code as cc_mod
    import cairn.ingest.harness.codex as cx_mod

    # ---- Claude Code fixture ------------------------------------------------
    claude_root = tmp_path / "claude" / "projects"
    enc_cwd = "-Users-x-proj"
    (claude_root / enc_cwd).mkdir(parents=True)
    cc_turn = json.dumps(
        {
            "type": "user",
            "sessionId": "sess-cc",
            "cwd": "/Users/x/proj",
            "gitBranch": "main",
            "timestamp": "2026-06-08T10:00:00Z",
            "message": {
                "role": "user",
                "content": "We decided to always escape the ATTACH path before interpolating it.",
            },
        }
    )
    (claude_root / enc_cwd / "sess-cc.jsonl").write_text(cc_turn + "\n")

    # ---- Codex fixture -------------------------------------------------------
    codex_root = tmp_path / "codex" / "sessions"
    day_dir = codex_root / "2026" / "03" / "08"
    day_dir.mkdir(parents=True)
    codex_session_meta = json.dumps(
        {
            "type": "session_meta",
            "payload": {"id": "sess-codex-36", "cwd": "/Users/x/insights"},
            "timestamp": "2026-03-08T09:00:00Z",
        }
    )
    codex_user_turn = json.dumps(
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "We decided to always rebase-merge the codex branch before ship.",
                    }
                ],
            },
            "timestamp": "2026-03-08T09:35:29Z",
        }
    )
    (day_dir / "rollout-x.jsonl").write_text(codex_session_meta + "\n" + codex_user_turn + "\n")

    # ---- Monkeypatch both adapter roots -------------------------------------
    monkeypatch.setattr(cc_mod, "_CLAUDE_ROOT", claude_root)
    monkeypatch.setattr(cx_mod, "_CODEX_ROOT", codex_root)

    # ---- Run auto-detect sweep (no --harness, no --transcripts-dir) ---------
    vault = tmp_path / "vault"
    vault.mkdir()
    idx = tmp_path / "i.duckdb"
    result = runner.invoke(
        app,
        [
            "sweep",
            "--vault",
            str(vault),
            "--embedder",
            "fake",
            "--index",
            str(idx),
            "--ledger",
            str(tmp_path / "led.sha256"),
        ],
        env={"CAIRN_JUDGE": "none"},  # hermetic: no fastembed judge
    )
    assert result.exit_code == 0, result.output

    # ---- Assert notes from BOTH harnesses were written ----------------------
    notes = list(vault.rglob("*.md"))
    assert notes, "sweep wrote no notes at all"

    all_content = "\n".join(n.read_text() for n in notes)
    assert "ATTACH path" in all_content, "no Claude Code note found (expected 'ATTACH path' phrase)"
    assert "rebase-merge the codex branch" in all_content, (
        "no Codex note found (expected 'rebase-merge the codex branch' phrase)"
    )


def test_install_cursor_omits_cairn_index(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".cursor").mkdir()
    r = runner.invoke(app, ["install", "cursor", "--vault", str(tmp_path / "v")])
    assert r.exit_code == 0, r.output
    import json as _j

    env = _j.loads((tmp_path / ".cursor" / "mcp.json").read_text())["mcpServers"]["agentcairn"][
        "env"
    ]
    assert env["CAIRN_VAULT"] == str((tmp_path / "v").resolve())
    assert "CAIRN_INDEX" not in env  # derived from the vault now


def test_migrate_stale_cairn_index_strips_json(tmp_path):
    import json as _j

    from cairn.hosts.plugins import migrate_stale_cairn_index

    cfg = tmp_path / "mcp.json"
    cfg.write_text(
        _j.dumps(
            {
                "mcpServers": {
                    "agentcairn": {
                        "command": "uvx",
                        "args": ["agentcairn"],
                        "env": {"CAIRN_VAULT": "/v", "CAIRN_INDEX": "/old/i.duckdb"},
                    }
                }
            }
        )
    )
    changed = migrate_stale_cairn_index(cfg, fmt="json")
    assert changed is True
    env = _j.loads(cfg.read_text())["mcpServers"]["agentcairn"]["env"]
    assert "CAIRN_INDEX" not in env and env["CAIRN_VAULT"] == "/v"


def test_migrate_stale_cairn_index_strips_vscode_servers_key(tmp_path):
    """VS Code's MCP JSON uses the top-level `servers` key, not `mcpServers`."""
    import json as _j

    from cairn.hosts.plugins import migrate_stale_cairn_index

    cfg = tmp_path / "mcp.json"
    cfg.write_text(
        _j.dumps(
            {
                "servers": {
                    "agentcairn": {
                        "command": "uvx",
                        "args": ["agentcairn"],
                        "env": {"CAIRN_VAULT": "/v", "CAIRN_INDEX": "/old/i.duckdb"},
                    }
                }
            }
        )
    )
    assert migrate_stale_cairn_index(cfg, fmt="json", root_key="servers") is True
    env = _j.loads(cfg.read_text())["servers"]["agentcairn"]["env"]
    assert "CAIRN_INDEX" not in env and env["CAIRN_VAULT"] == "/v"


def test_doctor_no_false_drift_when_index_decoupled_from_vault(tmp_path, monkeypatch):
    """An explicit --index pointing at another vault's index must NOT report DRIFT
    against an unrelated --vault."""
    from cairn import paths

    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    vault_a = tmp_path / "vaultA"
    vault_a.mkdir()
    (vault_a / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha body\n")
    idx_a = tmp_path / "a.duckdb"
    assert (
        runner.invoke(
            app, ["reindex", str(vault_a), "--index", str(idx_a), "--embedder", "fake"]
        ).exit_code
        == 0
    )
    vault_b = tmp_path / "vaultB"
    vault_b.mkdir()
    (vault_b / "b.md").write_text("---\ntitle: B\npermalink: b\n---\nbeta body\n")
    r = runner.invoke(app, ["doctor", "--index", str(idx_a), "--vault", str(vault_b)])
    assert r.exit_code == 0, r.output
    assert "DRIFT" not in r.output
    assert "status: OK" in r.output


def test_migrate_stale_cairn_index_strips_toml(tmp_path):
    import tomlkit

    from cairn.hosts.plugins import migrate_stale_cairn_index

    cfg = tmp_path / "config.toml"
    # A foreign tool also has a CAIRN_INDEX in a different section; only agentcairn's
    # must be removed (section-scoped, not a blind line strip).
    cfg.write_text(
        "[other_tool.env]\n"
        'CAIRN_INDEX = "/keep/me.duckdb"\n\n'
        "[mcp_servers.agentcairn.env]\n"
        'CAIRN_VAULT = "/v"\n'
        'CAIRN_INDEX = "/old/i.duckdb"\n'
    )
    changed = migrate_stale_cairn_index(cfg, fmt="toml")
    assert changed is True
    doc = tomlkit.parse(cfg.read_text())
    env = doc["mcp_servers"]["agentcairn"]["env"]
    assert "CAIRN_INDEX" not in env and env["CAIRN_VAULT"] == "/v"
    assert doc["other_tool"]["env"]["CAIRN_INDEX"] == "/keep/me.duckdb"  # foreign key untouched


def test_install_cursor_strips_stale_cairn_index(tmp_path, monkeypatch):
    """E2E: a host config that already pins CAIRN_INDEX gets it stripped on re-install."""
    import json as _j

    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / ".cursor" / "mcp.json"
    cfg.parent.mkdir()
    cfg.write_text(
        _j.dumps(
            {
                "mcpServers": {
                    "agentcairn": {
                        "command": "uvx",
                        "args": ["agentcairn"],
                        "env": {"CAIRN_VAULT": "/old/v", "CAIRN_INDEX": "/old/i.duckdb"},
                    }
                }
            }
        )
    )
    r = runner.invoke(app, ["install", "cursor", "--vault", str(tmp_path / "v")])
    assert r.exit_code == 0, r.output
    env = _j.loads(cfg.read_text())["mcpServers"]["agentcairn"]["env"]
    assert "CAIRN_INDEX" not in env
    assert env["CAIRN_VAULT"] == str((tmp_path / "v").resolve())


def test_ingest_default_ledger_is_vault_scoped(tmp_path, monkeypatch):
    """ingest with no --ledger writes the dedup ledger to the vault-scoped path."""
    from cairn import paths

    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    monkeypatch.delenv("CAIRN_INDEX", raising=False)
    projects = tmp_path / "projects"
    cwd = "/Users/x/proj"
    _seed_transcript(
        projects,
        cwd,
        "s1",
        [("user", "We decided to always escape the ATTACH path before interpolating it.")],
    )
    vault = tmp_path / "vault"
    vault.mkdir()
    r = runner.invoke(
        app,
        [
            "ingest",
            "--vault",
            str(vault),
            "--transcripts-dir",
            str(projects),
            "--harness",
            "claude-code",
            "--project",
            cwd,
        ],
        env={"CAIRN_JUDGE": "none"},
    )
    assert r.exit_code == 0, r.output
    assert paths.default_ledger(vault).exists()  # vault-scoped ledger, not the global one


def test_ingest_report_reconciles_compaction_counts(tmp_path):
    """2 compaction-summary events in one session → 1 promoted (latest). Headline shows
    `1 summaries`; the skipped line shows `1 compact_summary` (2 − 1), not 2."""
    import json as _j

    proj = tmp_path / "projects" / "-Users-x-proj"
    proj.mkdir(parents=True)

    def rec(text, ts, compact=False):
        d = {
            "type": "user",
            "sessionId": "s",
            "cwd": "/Users/x/proj",
            "timestamp": ts,
            "message": {"role": "user", "content": text},
        }
        if compact:
            d["isCompactSummary"] = True
        return _j.dumps(d)

    (proj / "t.jsonl").write_text(
        "\n".join(
            [
                rec("We decided to always rebase-merge the branch", "2026-06-17T00:00:00Z"),
                rec("first compaction summary text", "2026-06-17T01:00:00Z", compact=True),
                rec(
                    "second (latest) compaction summary text", "2026-06-17T02:00:00Z", compact=True
                ),
            ]
        )
        + "\n"
    )
    vault = tmp_path / "vault"
    r = runner.invoke(
        app,
        [
            "ingest",
            "--vault",
            str(vault),
            "--transcripts-dir",
            str(tmp_path / "projects"),
            "--harness",
            "claude-code",
            "--ledger",
            str(tmp_path / "led.sha256"),
        ],
        env={"CAIRN_JUDGE": "none"},
    )
    assert r.exit_code == 0, r.output
    assert "1 summaries" in r.output  # the promoted compaction is surfaced
    assert "1 compact_summary" in r.output  # 2 events − 1 promoted
    assert "2 compact_summary" not in r.output  # the old miscount is gone


def test_relink_note_writes_related_when_changed(tmp_path):
    from cairn.cli import _relink_note

    p = tmp_path / "a.md"
    p.write_text("---\ntitle: A\npermalink: a\n---\nalpha body\n")
    status = _relink_note(p, ["[[b]]", "[[c]]"])
    assert status == "linked"
    text = p.read_text()
    assert "related:" in text and "[[b]]" in text and "[[c]]" in text
    assert "alpha body" in text  # body preserved


def test_relink_note_unchanged_is_noop(tmp_path):
    from cairn.cli import _relink_note

    p = tmp_path / "a.md"
    p.write_text("---\ntitle: A\npermalink: a\n---\nalpha body\n")
    _relink_note(p, ["[[b]]"])  # first write
    mtime1 = p.stat().st_mtime_ns
    time.sleep(0.01)
    status = _relink_note(p, ["[[b]]"])  # same desired -> no rewrite
    assert status == "unchanged"
    assert p.stat().st_mtime_ns == mtime1  # file untouched


def test_relink_note_clears_stale_related(tmp_path):
    from cairn.cli import _relink_note

    p = tmp_path / "a.md"
    p.write_text("---\ntitle: A\npermalink: a\nrelated:\n- '[[b]]'\n---\nalpha body\n")
    status = _relink_note(p, [])  # no neighbors now -> clear
    assert status == "cleared"
    assert "related:" not in p.read_text()


def test_relink_note_empty_and_absent_is_unchanged(tmp_path):
    from cairn.cli import _relink_note

    p = tmp_path / "a.md"
    p.write_text("---\ntitle: A\npermalink: a\n---\nalpha body\n")
    mtime1 = p.stat().st_mtime_ns
    time.sleep(0.01)
    assert _relink_note(p, []) == "unchanged"  # no related, none desired
    assert p.stat().st_mtime_ns == mtime1


def test_relink_note_dry_run_writes_nothing(tmp_path):
    from cairn.cli import _relink_note

    p = tmp_path / "a.md"
    p.write_text("---\ntitle: A\npermalink: a\n---\nalpha body\n")
    mtime1 = p.stat().st_mtime_ns
    time.sleep(0.01)
    assert _relink_note(p, ["[[b]]"], dry_run=True) == "linked"  # reports intent
    assert p.stat().st_mtime_ns == mtime1  # but writes nothing
    assert "related:" not in p.read_text()


def test_relink_note_dry_run_clear_writes_nothing(tmp_path):
    from cairn.cli import _relink_note

    p = tmp_path / "a.md"
    p.write_text("---\ntitle: A\npermalink: a\nrelated:\n- '[[b]]'\n---\nalpha body\n")
    mtime1 = p.stat().st_mtime_ns
    time.sleep(0.01)
    assert _relink_note(p, [], dry_run=True) == "cleared"  # reports intent
    assert p.stat().st_mtime_ns == mtime1  # but writes nothing
    assert "[[b]]" in p.read_text()  # stale value untouched


# ---------------------------------------------------------------------------
# cairn link
# ---------------------------------------------------------------------------


def _seed_vault_indexed(tmp_path, monkeypatch, notes):
    """notes: list of (permalink, body). Build a vault + vault-scoped index (fake embedder)."""
    from cairn import paths

    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    monkeypatch.delenv("CAIRN_INDEX", raising=False)
    v = tmp_path / "vault"
    v.mkdir()
    for permalink, body in notes:
        (v / f"{permalink}.md").write_text(
            f"---\ntitle: {permalink}\npermalink: {permalink}\n---\n{body}\n"
        )
    assert runner.invoke(app, ["reindex", str(v), "--embedder", "fake"]).exit_code == 0
    return v


def test_link_writes_related_for_near_notes(tmp_path, monkeypatch):
    v = _seed_vault_indexed(
        tmp_path,
        monkeypatch,
        [
            ("ram", "scale the RAM to 4 gigabytes for the build"),
            ("ram2", "increase memory RAM to 8 gigabytes"),
            ("coffee", "pour over coffee brewing beans"),
        ],
    )
    r = runner.invoke(app, ["link", "--vault", str(v), "--top", "2", "--min-score", "0.0"])
    assert r.exit_code == 0, r.output
    ram = (v / "ram.md").read_text()
    assert "related:" in ram and "[[ram2]]" in ram  # near neighbor linked
    assert "[[ram]]" not in ram  # never links to self


def test_link_is_idempotent(tmp_path, monkeypatch):
    v = _seed_vault_indexed(
        tmp_path,
        monkeypatch,
        [
            ("ram", "scale the RAM to 4 gigabytes"),
            ("ram2", "increase memory RAM to 8 gigabytes"),
        ],
    )
    assert runner.invoke(app, ["link", "--vault", str(v), "--min-score", "0.0"]).exit_code == 0
    mtimes = {p.name: p.stat().st_mtime_ns for p in v.glob("*.md")}
    import time as _t

    _t.sleep(0.01)
    r = runner.invoke(app, ["link", "--vault", str(v), "--min-score", "0.0"])
    assert r.exit_code == 0, r.output
    assert {p.name: p.stat().st_mtime_ns for p in v.glob("*.md")} == mtimes  # nothing rewritten


def test_link_dry_run_writes_nothing(tmp_path, monkeypatch):
    v = _seed_vault_indexed(
        tmp_path,
        monkeypatch,
        [
            ("ram", "scale the RAM to 4 gigabytes"),
            ("ram2", "increase memory RAM to 8 gigabytes"),
        ],
    )
    r = runner.invoke(app, ["link", "--vault", str(v), "--min-score", "0.0", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert all("related:" not in p.read_text() for p in v.glob("*.md"))  # nothing written


def test_link_missing_index_exits_1(tmp_path, monkeypatch):
    from cairn import paths

    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    monkeypatch.delenv("CAIRN_INDEX", raising=False)
    v = tmp_path / "vault"
    v.mkdir()
    r = runner.invoke(app, ["link", "--vault", str(v)])
    assert r.exit_code == 1
    assert "no index" in r.output.lower()


def test_schedule_install_print_writes_nothing(tmp_path, monkeypatch):
    import sys

    monkeypatch.setattr(sys, "platform", "linux")
    res = runner.invoke(
        app, ["schedule", "install", "--interval", "30m", "--vault", str(tmp_path / "v"), "--print"]
    )
    assert res.exit_code == 0
    assert "# agentcairn-sweep" in res.output and "*/30 * * * *" in res.output


def test_schedule_status_not_installed(tmp_path, monkeypatch):
    import sys

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        "cairn.schedule._run",
        lambda cmd, stdin=None: type("R", (), {"returncode": 1, "stdout": ""})(),
    )
    res = runner.invoke(app, ["schedule", "status"])
    assert res.exit_code == 0 and "not installed" in res.output.lower()


def test_schedule_install_print_resolves_relative_vault(tmp_path, monkeypatch):
    import sys

    monkeypatch.setattr(sys, "platform", "linux")
    res = runner.invoke(app, ["schedule", "install", "--vault", "relvault", "--print"])
    assert res.exit_code == 0
    # The printed cron line must carry an absolute --vault path, not "relvault".
    m = re.search(r"--vault\s+(\S+)", res.output)
    assert m is not None
    vault_arg = m.group(1).strip("'\"")
    assert vault_arg.startswith("/"), f"vault not absolute: {vault_arg!r}"
    assert "relvault" in vault_arg  # resolved, not replaced


def test_schedule_install_uncronable_interval_clean_error(tmp_path, monkeypatch):
    import sys

    monkeypatch.setattr(sys, "platform", "linux")
    # 90m can't be expressed in cron (not <60 and not a whole number of hours).
    res = runner.invoke(
        app, ["schedule", "install", "--interval", "90m", "--vault", str(tmp_path / "v")]
    )
    assert res.exit_code != 0
    assert "cron" in res.output.lower() or "interval" in res.output.lower()
    # No raw traceback leaked.
    assert "Traceback" not in res.output


def test_schedule_install_unsupported_platform_clean_error(tmp_path, monkeypatch):
    import sys

    from cairn import schedule

    monkeypatch.setattr(sys, "platform", "win32")
    # resolve_cairn -> shutil.which behaves oddly under a faked win32 platform on
    # non-Windows hosts; stub it so we reach schedule.install()'s real backend,
    # which raises RuntimeError on an unsupported platform.
    monkeypatch.setattr(schedule, "resolve_cairn", lambda: "/usr/local/bin/cairn")
    res = runner.invoke(app, ["schedule", "install", "--vault", str(tmp_path / "v")])
    assert res.exit_code != 0
    combined = (res.stdout + (res.stderr if res.stderr_bytes else "")).lower()
    assert "not supported" in combined or "error" in combined
    assert "Traceback" not in res.stdout
    assert res.exception is None or isinstance(res.exception, SystemExit)
