# SPDX-License-Identifier: Apache-2.0
import os
import subprocess
import sys
from pathlib import Path


def test_eval_consolidate_smoke(tmp_path):
    """The eval script runs to completion on a tiny vault without OOM/crash, using
    the fake embedder (no model download). Guards the OOM regression + the hook."""
    mem = tmp_path / "memories"
    mem.mkdir()
    for i, ctx in enumerate(["scale RAM to 2GB", "scale RAM to 4GB", "deploy the website"]):
        (mem / f"n{i}.md").write_text(
            f"---\npermalink: n{i}\ntype: memory\n---\n\n- [context] {ctx} #ingested\n",
            encoding="utf-8",
        )
    script = Path(__file__).resolve().parents[1] / "scripts" / "eval_consolidate.py"
    r = subprocess.run(
        [sys.executable, str(script), str(tmp_path)],
        capture_output=True,
        text=True,
        env={**os.environ, "CAIRN_EVAL_EMBEDDER": "fake"},
    )
    assert r.returncode == 0, r.stderr
    assert "gate=" in r.stdout
