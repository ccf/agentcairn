# SPDX-License-Identifier: Apache-2.0
from typer.testing import CliRunner

from cairn.cli import app

runner = CliRunner()


def test_version_flag_prints_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.0.1" in result.stdout
