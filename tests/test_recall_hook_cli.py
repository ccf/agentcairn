# SPDX-License-Identifier: Apache-2.0
import json

from typer.testing import CliRunner

from cairn.cli import app
from cairn.embed import get_embedder
from tests.search.test_engine import build_index

runner = CliRunner()


def test_recall_hook_cli_stdin_wiring(tmp_path):
    idx = build_index(tmp_path, get_embedder("fake"))
    r = runner.invoke(
        app,
        ["recall-hook", "--index", str(idx), "--embedder", "fake"],
        input=json.dumps({"prompt": "how do I brew coffee beans?"}),
    )
    assert r.exit_code == 0
    data = json.loads(r.output)
    assert data["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"


def test_recall_hook_cli_trivial_prompt_no_output(tmp_path):
    idx = build_index(tmp_path, get_embedder("fake"))
    r = runner.invoke(
        app,
        ["recall-hook", "--index", str(idx), "--embedder", "fake"],
        input=json.dumps({"prompt": "go"}),
    )
    assert r.exit_code == 0
    assert r.output.strip() == ""
