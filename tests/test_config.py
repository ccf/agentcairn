# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

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
