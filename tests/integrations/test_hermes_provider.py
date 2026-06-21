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


def test_tool_schemas_declare_three_tools(provider):
    names = {t["name"] for t in provider.get_tool_schemas()}
    assert {"memory_save", "memory_recall", "memory_search"} <= names


def test_memory_save_then_recall_finds_it(provider):
    out = provider.handle_tool_call(
        "memory_save", {"text": "Prefer tabs in Go.", "tags": ["style"]}
    )
    assert out.get("permalink") or out.get("path")
    rec = provider.handle_tool_call("memory_recall", {"query": "Go formatting"})
    assert any("Go" in str(n.get("text", "")) for n in rec.get("notes", []))


def test_memory_search_returns_without_error(provider):
    provider.handle_tool_call("memory_save", {"text": "Deploy with make ship."})
    res = provider.handle_tool_call("memory_search", {"query": "deploy"})
    # search_tool returns {"query": ..., "as_of": ..., "hits": [...]}
    assert "hits" in res


def test_redaction_on_save(provider):
    provider.handle_tool_call(
        "memory_save", {"text": "token sk-ant-api03-SECRETSECRETSECRET deploy"}
    )
    assert "SECRETSECRET" not in provider.prefetch("deploy")


def test_unknown_tool_returns_error(provider):
    assert "error" in provider.handle_tool_call("nope", {})


def test_session_end_distills_user_facts_then_recall_finds_them(provider):
    msgs = [
        {
            "role": "user",
            "content": (
                "Decision: we always deploy this repo using make ship instead of "
                "npm publish, because the Makefile handles CI versioning. Never run "
                "npm publish directly."
            ),
        },
        {"role": "assistant", "content": "Understood, noted."},
    ]
    provider._capture(msgs, "sess-1")  # run capture inline (no daemon thread)
    assert "make ship" in provider.prefetch("how do we deploy?")


def test_capture_failure_is_swallowed(provider, monkeypatch):
    import cairn.ingest as ci

    monkeypatch.setattr(
        ci, "ingest_transcript", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    provider._capture([{"role": "user", "content": "x"}], "s")  # must NOT raise


def test_on_session_end_is_nonblocking_and_persists(provider):
    content = (
        "The production region is always us-east-1; we should never switch this "
        "service to us-west-2 for latency reasons."
    )
    provider.on_session_end([{"role": "user", "content": content}])
    provider.shutdown()  # joins the daemon thread
    assert "us-east-1" in provider.prefetch("which region is prod?")


def test_sync_turn_buffers(provider):
    provider.sync_turn("hello", "hi there", session_id="s9")
    assert len(provider._buffers["s9"]) == 2
