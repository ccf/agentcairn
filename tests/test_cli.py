# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path

from typer.testing import CliRunner

from cairn.cli import app

runner = CliRunner()


def test_version_flag_prints_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.0.1" in result.stdout


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
