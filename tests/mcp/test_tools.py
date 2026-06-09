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


# ---------------------------------------------------------------------------
# Fix B: title and tags must be redacted before write
# ---------------------------------------------------------------------------


def test_remember_redacts_secret_in_title(tmp_path):
    """A token in the caller-supplied title must NOT reach the written file."""
    vault = tmp_path / "vault"
    vault.mkdir()
    secret = "ghp_16C7e42F292c6912E7710c838347Ae178B4a"
    out = remember_tool(str(vault), "harmless body text here", title=f"key {secret}")
    body = Path(out["path"]).read_text()
    assert secret not in body, "secret in title leaked to disk"
    assert "[REDACTED" in body


def test_remember_redacts_secret_in_tags(tmp_path):
    """A token passed as a tag must NOT reach the written file."""
    vault = tmp_path / "vault"
    vault.mkdir()
    secret = "ghp_16C7e42F292c6912E7710c838347Ae178B4a"
    out = remember_tool(str(vault), "harmless body text here", tags=[secret])
    body = Path(out["path"]).read_text()
    assert secret not in body, "secret in tags leaked to disk"
    assert "[REDACTED" in body


def test_remember_redaction_count_includes_title_and_tags(tmp_path):
    """The reported `redactions` count must include title + tag redactions, not
    just the body — else a secret only in the title reports 0 (misrepresents)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    secret = "ghp_16C7e42F292c6912E7710c838347Ae178B4a"
    # secret ONLY in title (body is clean) -> count must still be >= 1
    out = remember_tool(str(vault), "harmless body text here", title=f"key {secret}")
    assert out["redactions"] >= 1
    # secret only in a tag -> counted too
    out2 = remember_tool(str(vault), "another harmless body", tags=[secret])
    assert out2["redactions"] >= 1


# ---------------------------------------------------------------------------
# Fix D: search_tool must not return duplicate permalinks
# ---------------------------------------------------------------------------


def _build_index_chunky(tmp_path: Path) -> tuple[Path, Path]:
    """Build an index with a note long enough to produce >=2 chunks (>1500 chars)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    paragraph = (
        "Chunking is important for retrieval. "
        "Each paragraph adds content to ensure this note exceeds the chunk size limit. "
    )
    # Repeat to exceed 1500 chars so the note produces multiple chunks.
    long_body = (paragraph * 20).strip()
    (vault / "long.md").write_text(
        f"---\ntitle: Long Note\npermalink: long-note\n---\n{long_body}\n"
    )
    idx = tmp_path / "i.duckdb"
    emb = FakeEmbedder(dim=8)
    con = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    reconcile(con, str(vault), emb)
    con.close()
    return idx, vault


def test_search_tool_no_duplicate_permalinks(tmp_path):
    """search_tool must return at most one hit per permalink (deduped by best score)."""
    idx, _vault = _build_index_chunky(tmp_path)
    out = search_tool(str(idx), "chunking retrieval", embedder="fake", k=20)
    perms = [h["permalink"] for h in out["hits"]]
    assert len(perms) == len(set(perms)), f"Duplicate permalinks in hits: {perms}"


# ---------------------------------------------------------------------------
# Fix 1+4: search_tool & recall_tool return up to k DISTINCT notes
# (over-fetch chunks, dedup by permalink)
# ---------------------------------------------------------------------------


def _build_index_multi_chunky(tmp_path: Path) -> Path:
    """Build an index where one note monopolises the top-k chunk slots.

    alpha-note has ~4 chunks (body ~4500 chars) and is highly relevant to
    the test query; beta-note and gamma-note are short (1 chunk each) with
    completely unrelated content (gardening).  Searching for the alpha topic
    with k=3 causes the naive implementation to return all 3 slots from
    alpha-note, collapsing to 1 distinct note after dedup.  The fix must
    over-fetch and then dedup to surface all 3 notes.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    alpha_phrase = "retrieval augmented generation combines dense and sparse methods for memory. "
    # alpha: ~4500 chars → 4 chunks (chunk limit is 1500 chars)
    alpha_body = (alpha_phrase * 60).strip()
    (vault / "alpha.md").write_text(
        f"---\ntitle: alpha-note\npermalink: alpha-note\n---\n{alpha_body}\n"
    )
    # beta and gamma: completely unrelated content — one chunk each, will rank below alpha
    unrelated = "Gardening is a relaxing hobby. Soil preparation is key for healthy plants. "
    unrelated_body = (unrelated * 3).strip()
    (vault / "beta.md").write_text(
        f"---\ntitle: beta-note\npermalink: beta-note\n---\n{unrelated_body}\n"
    )
    (vault / "gamma.md").write_text(
        f"---\ntitle: gamma-note\npermalink: gamma-note\n---\n{unrelated_body} extra content\n"
    )
    idx = tmp_path / "i.duckdb"
    emb = FakeEmbedder(dim=8)
    con = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    reconcile(con, str(vault), emb)
    con.close()
    # Sanity: alpha must have ≥3 chunks for the naive top-3 to be all-alpha.
    import duckdb as _ddb

    con2 = _ddb.connect(str(idx))
    alpha_chunks = con2.execute(
        "SELECT count(*) FROM chunks WHERE note_permalink = 'alpha-note'"
    ).fetchone()[0]
    con2.close()
    assert alpha_chunks >= 3, f"alpha-note has {alpha_chunks} chunks; increase alpha_body"
    return idx


def test_search_tool_returns_k_distinct_notes(tmp_path):
    """search_tool(k=3) must return 3 DISTINCT notes even when one note has ≥3 chunks."""
    idx = _build_index_multi_chunky(tmp_path)
    out = search_tool(str(idx), "retrieval augmented generation", embedder="fake", k=3)
    perms = [h["permalink"] for h in out["hits"]]
    assert len(set(perms)) == 3, f"Expected 3 distinct notes, got: {perms}"
    assert len(perms) == len(set(perms)), f"Duplicate permalinks: {perms}"


def test_recall_tool_returns_k_distinct_notes(tmp_path):
    """recall_tool(k=3) must hydrate 3 DISTINCT notes even when one note has ≥3 chunks."""
    idx = _build_index_multi_chunky(tmp_path)
    out = recall_tool(str(idx), "retrieval augmented generation", embedder="fake", k=3)
    perms = [n["permalink"] for n in out["notes"]]
    assert len(set(perms)) == 3, f"Expected 3 distinct notes, got: {perms}"
    assert len(perms) == len(set(perms)), f"Duplicate permalinks: {perms}"


# ---------------------------------------------------------------------------
# Fix 5: read tools raise clean ValueError when index is missing
# ---------------------------------------------------------------------------


def test_search_tool_missing_index_raises_valueerror(tmp_path):
    """search_tool must raise ValueError (not a cryptic crash) when index is absent."""
    missing = str(tmp_path / "nope.duckdb")
    with pytest.raises(ValueError, match="no index"):
        search_tool(missing, "anything")


def test_recent_tool_missing_index_raises_valueerror(tmp_path):
    """recent_tool must raise ValueError when index is absent."""
    missing = str(tmp_path / "nope.duckdb")
    with pytest.raises(ValueError, match="no index"):
        recent_tool(missing)
