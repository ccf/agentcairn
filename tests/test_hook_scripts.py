# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

_SCRIPTS = list(Path("plugin").rglob("scripts/session-end.sh")) + list(
    Path("plugin").rglob("scripts/session-start.sh")
)


def test_hook_scripts_pass_vault_not_index():
    assert _SCRIPTS, "hook scripts not found"
    for s in _SCRIPTS:
        body = s.read_text(encoding="utf-8")
        # cairn invocations use --vault and never --index (index is vault-derived)
        assert "--vault" in body
        assert "--index" not in body
