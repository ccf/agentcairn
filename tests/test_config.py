# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

import cairn.config as cfg
from cairn.config import ollama_config, parse_bool, resolve_rerank


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on", "On"])
def test_parse_bool_true(val):
    assert parse_bool(val) is True


@pytest.mark.parametrize("val", ["0", "false", "False", "no", "off"])
def test_parse_bool_false(val):
    assert parse_bool(val) is False


def test_parse_bool_unknown_raises():
    with pytest.raises(ValueError):
        parse_bool("maybe")


def test_resolve_rerank_explicit_wins():
    assert resolve_rerank(True, env={"CAIRN_RERANK": "0"}) is True
    assert resolve_rerank(False, env={"CAIRN_RERANK": "1"}) is False


def test_resolve_rerank_env():
    assert resolve_rerank(None, env={"CAIRN_RERANK": "0"}) is False
    assert resolve_rerank(None, env={"CAIRN_RERANK": "yes"}) is True


def test_resolve_rerank_default_on_when_unset():
    assert resolve_rerank(None, env={}) is True


def test_resolve_rerank_junk_env_defaults_true():
    # a typo'd env var must not crash a query
    assert resolve_rerank(None, env={"CAIRN_RERANK": "maybe"}) is True


def test_ollama_config_defaults():
    assert ollama_config(env={}) == ("nomic-embed-text", "http://localhost:11434")


def test_ollama_config_env_override():
    env = {"CAIRN_EMBED_MODEL": "mxbai-embed-large", "OLLAMA_HOST": "http://box:11434"}
    assert ollama_config(env=env) == ("mxbai-embed-large", "http://box:11434")


def test_fastembed_model_default_and_override():
    from cairn.config import fastembed_model

    assert fastembed_model(env={}) == "nomic-ai/nomic-embed-text-v1.5"
    assert (
        fastembed_model(env={"CAIRN_EMBED_MODEL": "BAAI/bge-small-en-v1.5"})
        == "BAAI/bge-small-en-v1.5"
    )


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Never read the developer's real ~/.agentcairn; reset the cache around each test."""
    monkeypatch.setenv("CAIRN_CONFIG", str(tmp_path / "config.toml"))
    cfg._reset()
    yield
    cfg._reset()


def _write(tmp_path, body: str):
    p = tmp_path / "config.toml"
    p.write_text(body)
    cfg._reset()
    return p


def test_cairn_env_missing_file_is_env_only(monkeypatch):
    monkeypatch.setenv("CAIRN_JUDGE", "none")
    e = cfg.cairn_env()
    assert e["CAIRN_JUDGE"] == "none"
    assert "CAIRN_EMBED_MODEL" not in e


def test_cairn_env_file_layer_and_env_wins(tmp_path, monkeypatch):
    _write(tmp_path, 'judge = "anthropic"\nembed_model = "BAAI/bge-small-en-v1.5"\n')
    e = cfg.cairn_env()
    assert e["CAIRN_JUDGE"] == "anthropic"  # from file
    assert e["CAIRN_EMBED_MODEL"] == "BAAI/bge-small-en-v1.5"
    monkeypatch.setenv("CAIRN_JUDGE", "none")
    cfg._reset()
    assert cfg.cairn_env()["CAIRN_JUDGE"] == "none"  # env wins over file


def test_cairn_env_passthrough_keys(tmp_path):
    _write(tmp_path, 'anthropic_api_key = "sk-ant-test-12345678"\nollama_host = "http://x:1"\n')
    e = cfg.cairn_env()
    assert e["ANTHROPIC_API_KEY"] == "sk-ant-test-12345678"
    assert e["OLLAMA_HOST"] == "http://x:1"


def test_cairn_env_type_coercion(tmp_path):
    _write(tmp_path, "rerank = false\njudge_timeout = 10\nusage = true\n")
    e = cfg.cairn_env()
    assert e["CAIRN_RERANK"] == "false"  # bool -> lowercase string
    assert e["CAIRN_JUDGE_TIMEOUT"] == "10"  # int -> string
    assert e["CAIRN_USAGE"] == "true"


def test_cairn_env_unknown_key_warns_once(tmp_path, capsys):
    _write(tmp_path, 'judg_model = "typo"\n')
    cfg.cairn_env()
    cfg.cairn_env()  # second call: no second warning
    err = capsys.readouterr().err
    assert err.count("judg_model") == 1
    assert "unknown" in err.lower()


def test_cairn_env_malformed_file_degrades(tmp_path, capsys):
    _write(tmp_path, "this is = = not toml")
    e = cfg.cairn_env()
    assert "CAIRN_JUDGE" not in e  # treated as empty
    assert "config" in capsys.readouterr().err.lower()


def test_resolvers_pick_up_file(tmp_path):
    _write(tmp_path, 'judge = "none"\nrerank = false\nembed_model = "m-x"\n')
    mode, _, _ = cfg.judge_config()
    assert mode == "none"
    assert cfg.resolve_rerank(None) is False
    assert cfg.fastembed_model() == "m-x"


def test_default_judge_timeout_covers_a_full_batch():
    """The default timeout must comfortably cover a full LLM batch — a 10s
    default silently degraded every batch (each ~30s) to the embedding tier."""
    from cairn.ingest.judge import _BATCH_SIZE, _TIMEOUT_PER_MSG_S

    _, _, timeout = cfg.judge_config({"CAIRN_JUDGE": "anthropic"})
    assert timeout >= _TIMEOUT_PER_MSG_S * _BATCH_SIZE  # >= a full batch's budget


def test_config_file_values_exposes_file_layer(tmp_path):
    _write(tmp_path, 'judge = "anthropic"\n')
    assert cfg.config_file_values()["CAIRN_JUDGE"] == "anthropic"


def test_consolidate_knob_default_on_and_disablable():
    assert cfg.resolve_consolidate({}) is True  # default on
    assert cfg.resolve_consolidate({"CAIRN_CONSOLIDATE": "false"}) is False
    assert cfg.resolve_consolidate({"CAIRN_CONSOLIDATE": "0"}) is False
    assert cfg.resolve_consolidate({"CAIRN_CONSOLIDATE": "true"}) is True
