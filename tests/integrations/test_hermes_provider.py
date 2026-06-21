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


def test_get_config_schema_declares_fields(provider):
    keys = {f["key"] for f in provider.get_config_schema()}
    assert {"vault_path", "embedder", "rerank"} <= keys


def test_saved_config_is_honored_on_initialize(tmp_path):
    import importlib.util
    from pathlib import Path

    PLUGIN = Path(__file__).resolve().parents[2] / "integrations" / "hermes" / "__init__.py"
    spec = importlib.util.spec_from_file_location("cairn_hermes_plugin2", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    custom = tmp_path / "custom_vault"
    hhome = str(tmp_path / "hh")
    p = mod.CairnMemoryProvider()
    p.save_config({"vault_path": str(custom)}, hhome)
    p2 = mod.CairnMemoryProvider()
    p2.initialize("s", hermes_home=hhome)
    assert str(custom) in str(p2._vault)  # Hermes-set vault_path is honored


def test_on_session_end_empty_falls_back_to_buffered_turns(provider):
    # provider fixture initialized with session_id="sess-1"; buffer a durable fact
    # under that real session id (no explicit session_id passed to sync_turn).
    provider.sync_turn(
        (
            "Decision: we always deploy this repo using make ship instead of npm "
            "publish, because the Makefile handles CI versioning. Never run npm "
            "publish directly."
        ),
        "Understood, noted.",
    )
    provider.on_session_end([])  # empty -> must fall back to buffered turns
    provider.shutdown()  # join the daemon capture thread
    assert "make ship" in provider.prefetch("how do we deploy?")


_DURABLE = (
    "Decision: we always deploy this repo using make ship instead of npm publish, "
    "because the Makefile handles CI versioning. Never run npm publish directly."
)


def test_initialize_clears_stale_buffers(tmp_path, monkeypatch):
    mod = load_plugin()

    # Sanity: a durable fact buffered then flushed via on_session_end([]) IS recalled,
    # so absence in the main assertion is due to buffer-clearing, not the importance gate.
    monkeypatch.setenv("CAIRN_VAULT", str(tmp_path / "sanity_vault"))
    sanity = mod.CairnMemoryProvider()
    sanity.initialize("only", hermes_home=str(tmp_path / "hh_sanity"))
    sanity.sync_turn(_DURABLE, "Understood, noted.", session_id="old")
    sanity.on_session_end([])
    sanity.shutdown()
    assert "make ship" in sanity.prefetch("how do we deploy?")

    # Main: buffer under "old", then re-initialize the SAME provider for "new".
    # initialize() must clear the stale buffer so the later on_session_end([]) is a no-op.
    monkeypatch.setenv("CAIRN_VAULT", str(tmp_path / "main_vault"))
    p = mod.CairnMemoryProvider()
    p.initialize("old", hermes_home=str(tmp_path / "hh_main"))
    p.sync_turn(_DURABLE, "Understood, noted.", session_id="old")
    p.initialize("new", hermes_home=str(tmp_path / "hh_main"))  # must clear stale buffer
    assert p._buffers == {}
    p.on_session_end([])  # empty + cleared buffer -> nothing captured
    p.shutdown()
    assert "make ship" not in p.prefetch("how do we deploy?")


def test_save_config_updates_cfg_so_is_available_sees_new_vault(provider, tmp_path):
    new_vault = tmp_path / "switched_vault"
    provider.save_config({"vault_path": str(new_vault)}, hermes_home=str(tmp_path / "hh_cfg"))
    # save_config updates _cfg AND re-resolves the cached vault immediately — writes/recall
    # must honor the new vault without needing a re-initialize or an is_available() call.
    assert provider._cfg.get("vault_path") == str(new_vault)
    assert str(new_vault) in str(provider._vault)  # cached vault updated by save_config itself
    assert provider.is_available() is True


def test_on_session_end_none_is_failsafe(provider):
    # Hermes may hand us None; list(None) would raise outside the capture wrapper.
    provider.on_session_end(None)  # must NOT raise
    provider.shutdown()
