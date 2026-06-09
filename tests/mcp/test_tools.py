# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest

from cairn.embed import FakeEmbedder
from cairn.index import open_index, reconcile
from cairn.mcp.tools import build_context_tool, recall_tool, recent_tool, remember_tool, search_tool
from cairn.vault import parse_note


def _build_index(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "coffee.md").write_text(
        "---\ntitle: Coffee\npermalink: coffee\n---\n"
        "Pour over coffee brewing.\n\nSee also [[tea]].\n"
    )
    (vault / "tea.md").write_text(
        "---\ntitle: Tea\npermalink: tea\n---\nGreen tea steeping is calming.\n"
    )
    idx = tmp_path / "i.duckdb"
    emb = FakeEmbedder(dim=8)
    con = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    reconcile(con, str(vault), emb)
    con.close()
    return idx


def test_search_tool_returns_compact_hits(tmp_path):
    idx = _build_index(tmp_path)
    out = search_tool(str(idx), "coffee brewing", embedder="fake", k=5)
    assert out["query"] == "coffee brewing"
    assert isinstance(out["hits"], list) and out["hits"]
    h = out["hits"][0]
    assert set(h) >= {"permalink", "heading_path", "snippet", "score"}
    # compact: no full note body in a search hit
    assert "text" not in h


def test_recall_tool_hydrates_full_notes(tmp_path):
    idx = _build_index(tmp_path)
    out = recall_tool(str(idx), "coffee brewing", embedder="fake", k=2)
    assert out["notes"]
    top = out["notes"][0]
    assert "permalink" in top and "text" in top  # full text hydrated
    assert "Pour over coffee" in top["text"] or "coffee" in top["text"].lower()


def test_build_context_returns_note_and_neighbors(tmp_path):
    idx = _build_index(tmp_path)
    out = build_context_tool(str(idx), "coffee")
    assert out["root"]["permalink"] == "coffee"
    # coffee links to [[tea]] -> tea resolves as an outgoing neighbor
    outgoing = {n["permalink"] for n in out["outgoing"] if n.get("permalink")}
    assert "tea" in outgoing


def test_build_context_missing_permalink(tmp_path):
    idx = _build_index(tmp_path)
    out = build_context_tool(str(idx), "nonexistent")
    assert out["root"] is None
    assert out["outgoing"] == [] and out["incoming"] == []


def test_recent_tool_lists_notes(tmp_path):
    idx = _build_index(tmp_path)
    out = recent_tool(str(idx), n=10)
    perms = {r["permalink"] for r in out["notes"]}
    assert {"coffee", "tea"} <= perms
    assert all({"permalink", "title", "path", "type"} <= set(r) for r in out["notes"])


def test_remember_writes_redacted_note(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    out = remember_tool(
        str(vault),
        "Always pin the store path. The old key was ghp_16C7e42F292c6912E7710c838347Ae178B4a.",
        title="store path rule",
        tags=["ops"],
    )
    assert out["permalink"]
    path = Path(out["path"])
    assert vault in path.resolve().parents
    assert out["redactions"] >= 1
    body = path.read_text()
    # secret never lands on disk; redaction marker present
    assert "ghp_16C7e42F292c6912E7710c838347Ae178B4a" not in body
    assert "[REDACTED" in body
    # round-trips through the real parser
    parsed = parse_note(body)
    assert parsed.frontmatter["type"] == "memory"
    assert "ops" in parsed.frontmatter["tags"]


def test_remember_rejects_empty_text(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(ValueError):
        remember_tool(str(vault), "   ")
