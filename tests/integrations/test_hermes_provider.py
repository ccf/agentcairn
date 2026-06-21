import importlib.util
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2] / "integrations" / "hermes" / "__init__.py"


def load_plugin():
    spec = importlib.util.spec_from_file_location("cairn_hermes_plugin", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def provider(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_VAULT", str(tmp_path / "vault"))
    mod = load_plugin()
    p = mod.CairnMemoryProvider()
    p.initialize("sess-1", hermes_home=str(tmp_path / "hhome"))
    return p


def test_name_and_availability(provider):
    assert provider.name == "agentcairn"
    assert provider.is_available() is True


def test_register_registers_one_provider():
    mod = load_plugin()
    seen = []

    class Ctx:
        def register_memory_provider(self, p):
            seen.append(p)

    mod.register(Ctx())
    assert len(seen) == 1 and seen[0].name == "agentcairn"


def test_prefetch_returns_a_saved_memory(provider):
    provider.handle_tool_call("memory_save", {"text": "I deploy with make ship."})
    block = provider.prefetch("how do I deploy?")
    assert "make ship" in block


def test_prefetch_empty_vault_is_safe(provider):
    assert isinstance(provider.prefetch("anything"), str)
