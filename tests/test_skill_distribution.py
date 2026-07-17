# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETUP = ROOT / "skills" / "agentcairn-setup"
RUNTIME = ROOT / "plugin" / "skills" / "using-agentcairn-memory" / "SKILL.md"
RUNTIME_PACKAGE_COPY = ROOT / "src" / "cairn" / "assets" / "using-agentcairn-memory" / "SKILL.md"


def _frontmatter(text: str) -> dict[str, str]:
    assert text.startswith("---\n")
    raw = text.split("---\n", 2)[1]
    fields: dict[str, str] = {}
    for line in raw.splitlines():
        if line and not line.startswith(" ") and ":" in line:
            key, value = line.split(":", 1)
            fields[key] = value.strip()
    return fields


def test_public_setup_skill_has_a_valid_interface():
    text = (SETUP / "SKILL.md").read_text(encoding="utf-8")
    fields = _frontmatter(text)
    assert fields.keys() == {"name", "description"}
    assert fields["name"] == "agentcairn-setup"
    assert all(word in fields["description"].lower() for word in ("install", "verify", "repair"))

    interface = (SETUP / "agents" / "openai.yaml").read_text(encoding="utf-8")
    assert 'display_name: "AgentCairn Setup"' in interface
    assert "$agentcairn-setup" in interface
    files = {path.relative_to(SETUP).as_posix() for path in SETUP.rglob("*") if path.is_file()}
    assert files == {"SKILL.md", "agents/openai.yaml"}


def test_public_setup_skill_delegates_instead_of_bundling_an_installer():
    text = (SETUP / "SKILL.md").read_text(encoding="utf-8")
    prose = " ".join(text.split())
    assert "installing this skill does not install" in prose
    assert "uvx --from agentcairn cairn install" in text
    assert "uvx --from agentcairn cairn install <host> --print" in text
    assert "Do not copy plugin files, write MCP configuration by hand" in prose
    assert "Do not install or repair every detected host" in prose
    assert "may include credentials from unrelated MCP servers" in prose
    assert not (SETUP / "scripts").exists()


def test_setup_skill_is_not_loaded_as_native_runtime_behavior():
    assert not (ROOT / "plugin" / "skills" / "agentcairn-setup").exists()


def test_runtime_skill_is_internal_and_fails_closed_without_tools():
    text = RUNTIME.read_text(encoding="utf-8")
    assert "metadata:\n  internal: true" in text
    assert "confirm that `recall` and `remember` are available" in text
    assert "Do not invent tool calls" in text
    assert "$agentcairn-setup" in text
    assert "restarted" in text
    assert RUNTIME.read_bytes() == RUNTIME_PACKAGE_COPY.read_bytes()
