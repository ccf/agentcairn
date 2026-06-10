# SPDX-License-Identifier: Apache-2.0
import json

from typer.testing import CliRunner

from cairn.cli import app
from cairn.mcp.tools import recall_tool

runner = CliRunner()


def _build_index(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    (v / "a.md").write_text("---\ntitle: Alpha\npermalink: a\n---\nalpha apple brewing notes\n")
    idx = tmp_path / "i.duckdb"
    r = runner.invoke(app, ["reindex", str(v), "--index", str(idx), "--embedder", "fake"])
    assert r.exit_code == 0, r.output
    return idx


def test_recall_tool_records_one_row(tmp_path, monkeypatch):
    idx = _build_index(tmp_path)
    led = tmp_path / "usage.jsonl"
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(led))
    monkeypatch.delenv("CAIRN_USAGE", raising=False)
    out = recall_tool(str(idx), "apple brewing", embedder="fake", k=3)
    assert out["notes"]  # recall succeeded
    rows = [json.loads(x) for x in led.read_text().splitlines() if x.strip()]
    assert len(rows) == 1
    assert rows[0]["event"] == "recall"
    assert rows[0]["full"] > 0
    assert rows[0]["recalled"] > 0
    assert rows[0]["recalled"] <= rows[0]["full"]


def test_recall_tool_survives_unwritable_ledger(tmp_path, monkeypatch):
    idx = _build_index(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(blocker / "no" / "usage.jsonl"))
    monkeypatch.delenv("CAIRN_USAGE", raising=False)
    # Recall must still succeed even though the ledger write fails.
    out = recall_tool(str(idx), "apple", embedder="fake", k=3)
    assert "notes" in out
