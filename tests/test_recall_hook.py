# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path

from cairn.embed import get_embedder
from cairn.recall_hook import build_hook_output, format_block, run, should_recall
from tests.search.test_engine import build_index


def _idx(tmp_path) -> Path:
    return Path(build_index(tmp_path, get_embedder("fake")))


def test_should_recall_gate():
    assert should_recall("how do I brew coffee beans?", env={}) is True
    assert should_recall("go", env={}) is False
    assert should_recall("  yes  ", env={}) is False
    assert should_recall("how do I brew coffee?", env={"CAIRN_AUTO_RECALL": "0"}) is False


def test_format_block_empty_returns_empty():
    assert format_block([]) == ""
    assert format_block([{"permalink": "x", "text": "   "}]) == ""


def test_format_block_includes_permalink():
    block = format_block([{"permalink": "coffee", "text": "Arabica beans."}])
    assert block.startswith("## Relevant memories (agentcairn)")
    assert "Arabica beans." in block
    assert "[[coffee]]" in block


def test_build_hook_output_shape():
    assert build_hook_output("hi") == {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "hi",
        }
    }


def test_run_injects_relevant_memory(tmp_path):
    out = run(
        json.dumps({"prompt": "how do I brew coffee beans?"}),
        index=_idx(tmp_path),
        embedder_name="fake",
        env={},
    )
    assert out
    data = json.loads(out)
    assert data["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "coffee" in data["hookSpecificOutput"]["additionalContext"].lower()


def test_run_skips_trivial_prompt(tmp_path):
    out = run(json.dumps({"prompt": "go"}), index=_idx(tmp_path), embedder_name="fake", env={})
    assert out == ""


def test_run_disabled_via_env(tmp_path):
    out = run(
        json.dumps({"prompt": "how do I brew coffee beans?"}),
        index=_idx(tmp_path),
        embedder_name="fake",
        env={"CAIRN_AUTO_RECALL": "0"},
    )
    assert out == ""


def test_run_no_index_is_silent(tmp_path):
    out = run(
        json.dumps({"prompt": "how do I brew coffee beans?"}),
        index=tmp_path / "missing.duckdb",
        embedder_name="fake",
        env={},
    )
    assert out == ""


def test_run_malformed_stdin_is_silent(tmp_path):
    out = run("not json at all", index=_idx(tmp_path), embedder_name="fake", env={})
    assert out == ""


def test_run_honors_cairn_embedder_env(tmp_path):
    """No explicit embedder_name → CAIRN_EMBEDDER env var is used."""
    out = run(
        json.dumps({"prompt": "how do I brew coffee beans?"}),
        index=_idx(tmp_path),
        env={"CAIRN_EMBEDDER": "fake"},
    )
    assert out
    data = json.loads(out)
    assert "coffee" in data["hookSpecificOutput"]["additionalContext"].lower()


def test_run_valid_non_dict_json_is_silent(tmp_path):
    """Valid JSON that is not a dict (string, list) should return ''."""
    idx = _idx(tmp_path)
    assert run('"hi"', index=idx, embedder_name="fake", env={}) == ""
    assert run("[1, 2]", index=idx, embedder_name="fake", env={}) == ""


def test_run_bm25_fallback_when_embedder_none(tmp_path):
    """embedder_name='none' → BM25-only path; still returns hits for a keyword query."""
    out = run(
        json.dumps({"prompt": "coffee beans"}),
        index=_idx(tmp_path),
        embedder_name="none",
        env={},
    )
    assert out != ""
