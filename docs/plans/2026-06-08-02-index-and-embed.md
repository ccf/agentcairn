# Plan 2 — `cairn.embed` + `cairn.index` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the embedding layer (`cairn.embed`) and the rebuildable DuckDB index (`cairn.index`) that turns a vault of parsed `Note`s into queryable tables — chunks, embeddings, a BM25 full-text index, and a link graph — with reconcile-on-spawn so only changed notes are re-processed.

**Architecture:** `cairn.vault` (Plan 1, merged) parses markdown → `Note`. Plan 2 adds: a pluggable `Embedder` (FastEmbed `bge-small` default; a deterministic `FakeEmbedder` for fast offline tests); structure-aware chunking; a DuckDB schema (`notes`/`chunks`/`chunk_embeddings`/`links`/`meta`); a vault indexer that populates those tables; a persistent **FTS BM25** index; and reconcile-on-spawn (mtime/content-hash diff + embedding-model guard). **Out of scope (Plan 3):** the HNSW vector index and the hybrid RRF query — Plan 2 only *stores* embeddings and proves BM25 works.

**Tech Stack:** Python 3.12, `duckdb` (+`vss`,`fts` extensions), `fastembed` (ONNX, no PyTorch), existing `cairn.vault`, `pytest`. uv for everything.

---

## v1 decomposition note
This is **Plan 2 of 5** (spec §15). Depends on `cairn.vault.{Note, Observation, Relation, parse_note}` (merged on `main`). Plan 3 (`cairn.search`) will build the in-memory HNSW index and the hybrid query on top of the tables this plan creates — **keep `chunk_embeddings.vec` as a `FLOAT[N]` column and store the model id+dim in `meta`** so Plan 3 can add HNSW without a migration.

## File structure
```
src/cairn/
├── embed/
│   ├── __init__.py        # re-exports: Embedder, FakeEmbedder, FastEmbedEmbedder, get_embedder
│   ├── base.py            # Embedder Protocol
│   ├── fake.py            # FakeEmbedder (deterministic, dep-free; default for tests)
│   └── fastembed_embedder.py  # FastEmbedEmbedder (bge-small; real default)
└── index/
    ├── __init__.py        # re-exports: open_index, index_vault, reconcile, IndexStats
    ├── chunk.py           # chunk_note(note) -> list[Chunk]
    ├── schema.py          # connect/open_index + DDL + meta helpers
    └── build.py           # index_vault(), reconcile(), FTS build, bm25 sanity query
tests/
├── embed/{test_fake.py, test_fastembed_integration.py}
└── index/{test_chunk.py, test_schema.py, test_build.py, test_reconcile.py}
```

---

### Task 1: `Embedder` protocol + `FakeEmbedder`

**Files:** Create `src/cairn/embed/base.py`, `src/cairn/embed/fake.py`, `src/cairn/embed/__init__.py`; Test `tests/embed/test_fake.py`, `tests/embed/__init__.py`.

- [ ] **Step 1: Write the failing test** — `tests/embed/test_fake.py`
```python
# SPDX-License-Identifier: Apache-2.0
from cairn.embed import FakeEmbedder


def test_fake_embedder_shape_and_determinism():
    e = FakeEmbedder(dim=8)
    assert e.dim == 8
    assert e.model_id == "fake-8"
    v1 = e.embed(["hello", "world"])
    assert len(v1) == 2 and all(len(v) == 8 for v in v1)
    # deterministic: same text -> same vector
    assert e.embed(["hello"])[0] == v1[0]
    # query path returns one vector of the right dim
    q = e.embed_query("hello")
    assert len(q) == 8 and q == v1[0]
    # roughly unit-normalized
    assert abs(sum(x * x for x in q) - 1.0) < 1e-6
```

- [ ] **Step 2: Run it — expect FAIL** (`ModuleNotFoundError: cairn.embed`). Run: `uv run pytest tests/embed/test_fake.py -v`

- [ ] **Step 3: Implement `src/cairn/embed/base.py`**
```python
# SPDX-License-Identifier: Apache-2.0
"""The Embedder interface. Implementations turn text into fixed-dimension
float vectors. Keep this surface small and stable — the index and (Plan 3)
search both depend on it."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    @property
    def model_id(self) -> str: ...
    @property
    def dim(self) -> int: ...
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...
```

- [ ] **Step 4: Implement `src/cairn/embed/fake.py`**
```python
# SPDX-License-Identifier: Apache-2.0
"""Deterministic, dependency-free embedder for fast offline tests. NOT for
real retrieval — vectors are hash-derived, not semantic."""

from __future__ import annotations

import hashlib
import math


class FakeEmbedder:
    def __init__(self, dim: int = 8) -> None:
        self._dim = dim

    @property
    def model_id(self) -> str:
        return f"fake-{self._dim}"

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    def _vec(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [digest[i % len(digest)] / 255.0 for i in range(self._dim)]
        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        return [v / norm for v in raw]
```

- [ ] **Step 5: Implement `src/cairn/embed/__init__.py`**
```python
# SPDX-License-Identifier: Apache-2.0
from cairn.embed.base import Embedder
from cairn.embed.fake import FakeEmbedder

__all__ = ["Embedder", "FakeEmbedder"]
```

- [ ] **Step 6: Run — expect PASS.** `uv run pytest tests/embed/test_fake.py -v`
- [ ] **Step 7: Commit**
```bash
git add src/cairn/embed/base.py src/cairn/embed/fake.py src/cairn/embed/__init__.py tests/embed/
git commit -m "feat(embed): Embedder protocol + deterministic FakeEmbedder"
```

---

### Task 2: `FastEmbedEmbedder` (real default) + `get_embedder`

**Files:** Create `src/cairn/embed/fastembed_embedder.py`; Modify `src/cairn/embed/__init__.py`; Modify `pyproject.toml` (add `fastembed`); Test `tests/embed/test_fastembed_integration.py`.

- [ ] **Step 1: Add the dependency.** In `pyproject.toml` `dependencies`, add `"fastembed>=0.4"`. Run `uv sync`.

- [ ] **Step 2: Write the integration test (guarded so the default suite stays offline/fast)** — `tests/embed/test_fastembed_integration.py`
```python
# SPDX-License-Identifier: Apache-2.0
"""Real-model test — downloads ~32MB on first run. Skipped unless
CAIRN_RUN_INTEGRATION=1 so the default suite stays fast and offline."""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CAIRN_RUN_INTEGRATION") != "1",
    reason="set CAIRN_RUN_INTEGRATION=1 to run (downloads model)",
)


def test_fastembed_bge_small_dims_and_determinism():
    from cairn.embed import FastEmbedEmbedder

    e = FastEmbedEmbedder()
    assert e.model_id == "BAAI/bge-small-en-v1.5"
    assert e.dim == 384
    vecs = e.embed(["the cat sat", "the cat sat"])
    assert len(vecs) == 2 and all(len(v) == 384 for v in vecs)
    assert vecs[0] == vecs[1]  # deterministic for identical input
    assert len(e.embed_query("a query")) == 384
```

- [ ] **Step 3: Implement `src/cairn/embed/fastembed_embedder.py`**
```python
# SPDX-License-Identifier: Apache-2.0
"""FastEmbed (ONNX) embedder — the real default. Derives `dim` from the model
at init (no hardcoded width) and exposes an asymmetric query path when the
backend supports one."""

from __future__ import annotations


class FastEmbedEmbedder:
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        from fastembed import TextEmbedding

        self._name = model_name
        self._model = TextEmbedding(model_name=model_name)
        # Probe one embedding to learn the dimension rather than hardcoding it.
        self._dim = len(next(iter(self._model.embed(["probe"]))).tolist())

    @property
    def model_id(self) -> str:
        return self._name

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        query_embed = getattr(self._model, "query_embed", None)
        if query_embed is not None:
            return list(query_embed([text]))[0].tolist()
        return self.embed([text])[0]
```

- [ ] **Step 4: Add `get_embedder` factory + re-export.** Update `src/cairn/embed/__init__.py`
```python
# SPDX-License-Identifier: Apache-2.0
from cairn.embed.base import Embedder
from cairn.embed.fake import FakeEmbedder


def get_embedder(name: str = "fastembed") -> Embedder:
    """Return an Embedder by name. 'fake' for tests; 'fastembed' (default) for real use."""
    if name == "fake":
        return FakeEmbedder()
    if name == "fastembed":
        from cairn.embed.fastembed_embedder import FastEmbedEmbedder

        return FastEmbedEmbedder()
    raise ValueError(f"unknown embedder: {name!r}")


__all__ = ["Embedder", "FakeEmbedder", "get_embedder"]
```
(Note: `FastEmbedEmbedder` is imported lazily inside `get_embedder` so importing `cairn.embed` never forces `fastembed`/ONNX to load — keeps the fake-only test path fast.)

- [ ] **Step 5: Run.** `uv run pytest tests/embed -v` → the integration test SKIPS (reason shown); fake tests pass. Optionally verify the real path once: `CAIRN_RUN_INTEGRATION=1 uv run pytest tests/embed/test_fastembed_integration.py -v` (downloads model). Report whether you ran it.
- [ ] **Step 6: Commit**
```bash
git add pyproject.toml uv.lock src/cairn/embed/fastembed_embedder.py src/cairn/embed/__init__.py tests/embed/test_fastembed_integration.py
git commit -m "feat(embed): FastEmbedEmbedder (bge-small) + get_embedder factory"
```

---

### Task 3: Structure-aware chunking

**Files:** Create `src/cairn/index/chunk.py`, `src/cairn/index/__init__.py`, `tests/index/__init__.py`; Test `tests/index/test_chunk.py`.

Chunk by markdown header sections; within a section, split the body into windows of at most `max_chars` (default 1500 ≈ ~512 tokens) on paragraph/line boundaries. Each chunk is prefixed with a semantic anchor (`Title: … | Section: …`) and records provenance (note permalink + heading path + ordinal).

- [ ] **Step 1: Write the failing test** — `tests/index/test_chunk.py`
```python
# SPDX-License-Identifier: Apache-2.0
from cairn.vault import parse_note
from cairn.index.chunk import chunk_note

NOTE = """---
title: Coffee
permalink: coffee
---
Intro paragraph about coffee.

## Brewing
Pour over is great.

## Storage
Keep beans sealed.
"""


def test_chunks_have_anchor_and_provenance():
    note = parse_note(NOTE)
    chunks = chunk_note(note, max_chars=1500)
    assert len(chunks) >= 3  # intro + Brewing + Storage
    # each chunk carries a semantic-anchor prefix and provenance
    brewing = next(c for c in chunks if c.heading_path.endswith("Brewing"))
    assert brewing.text.startswith("Title: Coffee | Section: Brewing |")
    assert "Pour over is great." in brewing.text
    assert brewing.note_permalink == "coffee"
    assert all(c.chunk_id and c.note_permalink == "coffee" for c in chunks)
    # ordinals are unique and contiguous from 0
    assert sorted(c.ordinal for c in chunks) == list(range(len(chunks)))


def test_long_section_is_split_by_max_chars():
    big = "para. " * 1000  # ~6000 chars
    note = parse_note(f"---\ntitle: T\npermalink: t\n---\n## S\n{big}\n")
    chunks = chunk_note(note, max_chars=1500)
    seg = [c for c in chunks if c.heading_path.endswith("S")]
    assert len(seg) >= 3
    assert all(len(c.text) <= 1500 + 200 for c in seg)  # anchor prefix adds a little
```

- [ ] **Step 2: Run — expect FAIL.** `uv run pytest tests/index/test_chunk.py -v`

- [ ] **Step 3: Implement `src/cairn/index/chunk.py`**
```python
# SPDX-License-Identifier: Apache-2.0
"""Split a Note's body into retrieval chunks: one or more per markdown header
section, each prefixed with a semantic anchor and carrying provenance back to
the source note + heading."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from cairn.vault import Note


@dataclass
class Chunk:
    chunk_id: str
    note_permalink: str | None
    heading_path: str  # e.g. "Coffee > Brewing"
    ordinal: int
    text: str  # anchor-prefixed, ready to embed/index


def _sections(body: str) -> list[tuple[str, str]]:
    """Yield (heading_path_tail, section_body). Text before the first header
    goes under heading ''. Only ATX headers (#..######) split sections."""
    sections: list[tuple[str, str]] = []
    current_head = ""
    buf: list[str] = []
    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#") and stripped.lstrip("#").startswith(" "):
            sections.append((current_head, "\n".join(buf).strip()))
            current_head = stripped.lstrip("#").strip()
            buf = []
        else:
            buf.append(line)
    sections.append((current_head, "\n".join(buf).strip()))
    return [(h, b) for h, b in sections if b]


def _windows(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    out: list[str] = []
    cur = ""
    for para in text.split("\n"):
        if cur and len(cur) + len(para) + 1 > max_chars:
            out.append(cur.strip())
            cur = ""
        cur = f"{cur}\n{para}" if cur else para
    if cur.strip():
        out.append(cur.strip())
    # hard-split any window still over the limit
    final: list[str] = []
    for w in out:
        while len(w) > max_chars:
            final.append(w[:max_chars])
            w = w[max_chars:]
        if w:
            final.append(w)
    return final


def chunk_note(note: Note, max_chars: int = 1500) -> list[Chunk]:
    title = str(note.frontmatter.get("title") or note.permalink or "")
    chunks: list[Chunk] = []
    ordinal = 0
    for head_tail, section_body in _sections(note.body):
        heading_path = f"{title} > {head_tail}".strip(" >") if head_tail else title
        section_label = head_tail or title or "note"
        for window in _windows(section_body, max_chars):
            anchor = f"Title: {title} | Section: {section_label} | "
            cid = hashlib.sha256(
                f"{note.permalink}\x00{ordinal}\x00{window}".encode()
            ).hexdigest()[:16]
            chunks.append(
                Chunk(
                    chunk_id=cid,
                    note_permalink=note.permalink,
                    heading_path=heading_path or section_label,
                    ordinal=ordinal,
                    text=anchor + window,
                )
            )
            ordinal += 1
    return chunks
```

- [ ] **Step 4: Implement `src/cairn/index/__init__.py`** (stub re-export, expanded in later tasks)
```python
# SPDX-License-Identifier: Apache-2.0
from cairn.index.chunk import Chunk, chunk_note

__all__ = ["Chunk", "chunk_note"]
```

- [ ] **Step 5: Run — expect PASS.** `uv run pytest tests/index/test_chunk.py -v`
- [ ] **Step 6: Commit**
```bash
git add src/cairn/index/chunk.py src/cairn/index/__init__.py tests/index/
git commit -m "feat(index): structure-aware chunking with semantic anchors + provenance"
```

---

### Task 4: DuckDB schema + connection

**Files:** Create `src/cairn/index/schema.py`; Modify `pyproject.toml` (add `duckdb`); Test `tests/index/test_schema.py`.

- [ ] **Step 1: Add dependency.** Add `"duckdb>=1.1"` to `pyproject.toml` `dependencies`; `uv sync`.

- [ ] **Step 2: Write the failing test** — `tests/index/test_schema.py`
```python
# SPDX-License-Identifier: Apache-2.0
from cairn.index.schema import open_index, get_meta, set_meta


def test_open_index_creates_tables_and_meta(tmp_path):
    con = open_index(str(tmp_path / "i.duckdb"), dim=8, model_id="fake-8")
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    assert {"notes", "chunks", "chunk_embeddings", "links", "meta"} <= tables
    assert get_meta(con, "embedding_model") == "fake-8"
    assert get_meta(con, "embedding_dim") == "8"
    set_meta(con, "k", "v")
    assert get_meta(con, "k") == "v"


def test_embedding_vec_column_is_fixed_width(tmp_path):
    con = open_index(str(tmp_path / "i.duckdb"), dim=8, model_id="fake-8")
    # inserting a wrong-width vector must fail (fixed FLOAT[8])
    con.execute("INSERT INTO chunk_embeddings VALUES ('c1', [0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8])")
    import pytest
    with pytest.raises(Exception):
        con.execute("INSERT INTO chunk_embeddings VALUES ('c2', [0.1,0.2,0.3])")
```

- [ ] **Step 3: Run — expect FAIL.** `uv run pytest tests/index/test_schema.py -v`

- [ ] **Step 4: Implement `src/cairn/index/schema.py`**
```python
# SPDX-License-Identifier: Apache-2.0
"""DuckDB index schema. The .duckdb file is a DISPOSABLE, rebuildable cache —
never the source of truth (that is the markdown vault). `meta` records the
embedding model + dim so a model/dim mismatch can trigger a rebuild."""

from __future__ import annotations

import duckdb


def open_index(path: str, *, dim: int, model_id: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(path)
    con.execute("INSTALL vss; LOAD vss;")
    con.execute("INSTALL fts; LOAD fts;")
    con.execute(
        "CREATE TABLE IF NOT EXISTS notes ("
        "  permalink VARCHAR PRIMARY KEY, path VARCHAR, title VARCHAR, type VARCHAR,"
        "  content_hash VARCHAR, mtime DOUBLE)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS chunks ("
        "  chunk_id VARCHAR PRIMARY KEY, note_permalink VARCHAR,"
        "  heading_path VARCHAR, ordinal INTEGER, text VARCHAR)"
    )
    con.execute(
        f"CREATE TABLE IF NOT EXISTS chunk_embeddings ("
        f"  chunk_id VARCHAR PRIMARY KEY, vec FLOAT[{dim}])"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS links ("
        "  src_permalink VARCHAR, dst_permalink VARCHAR, edge_type VARCHAR)"
    )
    con.execute("CREATE TABLE IF NOT EXISTS meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
    set_meta(con, "embedding_model", model_id)
    set_meta(con, "embedding_dim", str(dim))
    return con


def set_meta(con: duckdb.DuckDBPyConnection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO meta VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        [key, value],
    )


def get_meta(con: duckdb.DuckDBPyConnection, key: str) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key = ?", [key]).fetchone()
    return row[0] if row else None
```

- [ ] **Step 5: Run — expect PASS.** `uv run pytest tests/index/test_schema.py -v`
- [ ] **Step 6: Commit**
```bash
git add pyproject.toml uv.lock src/cairn/index/schema.py tests/index/test_schema.py
git commit -m "feat(index): DuckDB schema (notes/chunks/embeddings/links/meta)"
```

---

### Task 5: Index a vault (populate tables + embeddings)

**Files:** Create `src/cairn/index/build.py`; Modify `src/cairn/index/__init__.py`; Test `tests/index/test_build.py`.

- [ ] **Step 1: Write the failing test** — `tests/index/test_build.py`
```python
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

from cairn.embed import FakeEmbedder
from cairn.index import index_vault, open_index


def _vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    (v / "coffee.md").write_text(
        "---\ntitle: Coffee\npermalink: coffee\n---\n"
        "About [[Tea]].\n\n## Brewing\nPour over. \n\n- pairs_with [[Tea]]\n"
    )
    (v / "tea.md").write_text("---\ntitle: Tea\npermalink: tea\n---\nGreen tea.\n")
    return v


def test_index_vault_populates_rows_and_embeddings(tmp_path):
    v = _vault(tmp_path)
    emb = FakeEmbedder(dim=8)
    con = open_index(str(tmp_path / "i.duckdb"), dim=emb.dim, model_id=emb.model_id)
    stats = index_vault(con, str(v), emb)
    assert stats.notes == 2
    assert stats.chunks >= 2
    # every chunk has an embedding of the right width
    n_emb = con.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0]
    n_chunks = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
    assert n_emb == n_chunks
    # link graph captured (coffee -> tea, both wikilink and pairs_with)
    edges = con.execute(
        "SELECT src_permalink, dst_permalink, edge_type FROM links ORDER BY edge_type"
    ).fetchall()
    assert ("coffee", "Tea", "links_to") in edges
    assert ("coffee", "Tea", "pairs_with") in edges
```

- [ ] **Step 2: Run — expect FAIL.** `uv run pytest tests/index/test_build.py -v`

- [ ] **Step 3: Implement `src/cairn/index/build.py`**
```python
# SPDX-License-Identifier: Apache-2.0
"""Populate the DuckDB index from a markdown vault. Idempotent per note:
re-indexing a note replaces its prior rows."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import duckdb

from cairn.embed.base import Embedder
from cairn.index.chunk import chunk_note
from cairn.vault import parse_note


@dataclass
class IndexStats:
    notes: int = 0
    chunks: int = 0


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _delete_note(con: duckdb.DuckDBPyConnection, permalink: str) -> None:
    con.execute(
        "DELETE FROM chunk_embeddings WHERE chunk_id IN "
        "(SELECT chunk_id FROM chunks WHERE note_permalink = ?)",
        [permalink],
    )
    con.execute("DELETE FROM chunks WHERE note_permalink = ?", [permalink])
    con.execute("DELETE FROM links WHERE src_permalink = ?", [permalink])
    con.execute("DELETE FROM notes WHERE permalink = ?", [permalink])


def index_note(
    con: duckdb.DuckDBPyConnection, path: Path, embedder: Embedder
) -> int:
    """(Re)index a single note file. Returns number of chunks. Permalink falls
    back to the file stem when frontmatter omits it."""
    text = path.read_text()
    note = parse_note(text)
    permalink = note.permalink or path.stem
    note.permalink = permalink  # ensure downstream rows are keyed consistently

    _delete_note(con, permalink)
    con.execute(
        "INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?)",
        [
            permalink,
            str(path),
            str(note.frontmatter.get("title") or ""),
            str(note.frontmatter.get("type") or ""),
            _content_hash(text),
            path.stat().st_mtime,
        ],
    )
    chunks = chunk_note(note)
    if chunks:
        vecs = embedder.embed([c.text for c in chunks])
        for c, vec in zip(chunks, vecs):
            con.execute(
                "INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                [c.chunk_id, permalink, c.heading_path, c.ordinal, c.text],
            )
            con.execute(
                "INSERT INTO chunk_embeddings VALUES (?, ?)", [c.chunk_id, vec]
            )
    for t in note.wikilinks:
        con.execute("INSERT INTO links VALUES (?, ?, ?)", [permalink, t, "links_to"])
    for rel in note.relations:
        if rel.rel_type != "links_to":
            con.execute(
                "INSERT INTO links VALUES (?, ?, ?)", [permalink, rel.target, rel.rel_type]
            )
    return len(chunks)


def index_vault(
    con: duckdb.DuckDBPyConnection, vault_dir: str, embedder: Embedder
) -> IndexStats:
    stats = IndexStats()
    for path in sorted(Path(vault_dir).rglob("*.md")):
        stats.chunks += index_note(con, path, embedder)
        stats.notes += 1
    return stats
```

- [ ] **Step 4: Update `src/cairn/index/__init__.py`**
```python
# SPDX-License-Identifier: Apache-2.0
from cairn.index.build import IndexStats, index_note, index_vault
from cairn.index.chunk import Chunk, chunk_note
from cairn.index.schema import get_meta, open_index, set_meta

__all__ = [
    "Chunk", "chunk_note", "open_index", "get_meta", "set_meta",
    "IndexStats", "index_note", "index_vault",
]
```

- [ ] **Step 5: Run — expect PASS.** `uv run pytest tests/index/test_build.py -v`
- [ ] **Step 6: Commit**
```bash
git add src/cairn/index/build.py src/cairn/index/__init__.py tests/index/test_build.py
git commit -m "feat(index): index_vault populates notes/chunks/embeddings/links"
```

---

### Task 6: Build the FTS (BM25) index + sanity query

**Files:** Modify `src/cairn/index/build.py`, `src/cairn/index/__init__.py`; Test `tests/index/test_build.py` (extend).

- [ ] **Step 1: Add the failing test** — append to `tests/index/test_build.py`
```python
from cairn.index import build_fts, bm25_search


def test_fts_bm25_finds_chunk(tmp_path):
    v = _vault(tmp_path)
    emb = FakeEmbedder(dim=8)
    con = open_index(str(tmp_path / "i.duckdb"), dim=emb.dim, model_id=emb.model_id)
    index_vault(con, str(v), emb)
    build_fts(con)
    hits = bm25_search(con, "pour over brewing", limit=5)
    assert hits, "expected at least one BM25 hit"
    assert any("Brewing" in h[1] for h in hits)  # (chunk_id, heading_path, score)
```

- [ ] **Step 2: Run — expect FAIL** (`ImportError: build_fts`). `uv run pytest tests/index/test_build.py::test_fts_bm25_finds_chunk -v`

- [ ] **Step 3: Add `build_fts` + `bm25_search` to `src/cairn/index/build.py`**
```python
def build_fts(con: duckdb.DuckDBPyConnection) -> None:
    """(Re)build the BM25 full-text index over chunk text. Must be called after
    any change to `chunks` — DuckDB's FTS index does not auto-update."""
    con.execute("PRAGMA create_fts_index('chunks', 'chunk_id', 'text', overwrite=1)")


def bm25_search(
    con: duckdb.DuckDBPyConnection, query: str, limit: int = 10
) -> list[tuple[str, str, float]]:
    """Return [(chunk_id, heading_path, score)] ranked by BM25. Empty if the FTS
    index has not been built."""
    rows = con.execute(
        """
        WITH scored AS (
            SELECT c.chunk_id, c.heading_path,
                   fts_main_chunks.match_bm25(c.chunk_id, ?) AS score
            FROM chunks c
        )
        SELECT chunk_id, heading_path, score FROM scored
        WHERE score IS NOT NULL ORDER BY score DESC LIMIT ?
        """,
        [query, limit],
    ).fetchall()
    return [(r[0], r[1], float(r[2])) for r in rows]
```

- [ ] **Step 4: Re-export** `build_fts`, `bm25_search` in `src/cairn/index/__init__.py` (`__all__`).
- [ ] **Step 5: Run — expect PASS.** `uv run pytest tests/index/test_build.py -v`
- [ ] **Step 6: Commit**
```bash
git add src/cairn/index/build.py src/cairn/index/__init__.py tests/index/test_build.py
git commit -m "feat(index): BM25 full-text index build + bm25_search"
```

---

### Task 7: Reconcile-on-spawn (incremental + model-mismatch rebuild)

**Files:** Modify `src/cairn/index/build.py`, `src/cairn/index/__init__.py`; Test `tests/index/test_reconcile.py`.

- [ ] **Step 1: Write the failing test** — `tests/index/test_reconcile.py`
```python
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

from cairn.embed import FakeEmbedder
from cairn.index import open_index, reconcile


def _seed(tmp_path: Path) -> Path:
    v = tmp_path / "vault"; v.mkdir()
    (v / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha\n")
    (v / "b.md").write_text("---\ntitle: B\npermalink: b\n---\nbeta\n")
    return v


def test_reconcile_only_touches_changed_notes(tmp_path):
    v = _seed(tmp_path)
    emb = FakeEmbedder(dim=8)
    con = open_index(str(tmp_path / "i.duckdb"), dim=emb.dim, model_id=emb.model_id)
    r1 = reconcile(con, str(v), emb)
    assert r1.added == 2 and r1.updated == 0 and r1.deleted == 0

    # no changes -> nothing re-indexed
    r2 = reconcile(con, str(v), emb)
    assert (r2.added, r2.updated, r2.deleted) == (0, 0, 0)

    # edit one note, delete another
    (v / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha CHANGED\n")
    (v / "b.md").unlink()
    r3 = reconcile(con, str(v), emb)
    assert r3.updated == 1 and r3.deleted == 1 and r3.added == 0
    assert con.execute("SELECT count(*) FROM notes").fetchone()[0] == 1


def test_reconcile_rebuilds_on_model_mismatch(tmp_path):
    v = _seed(tmp_path)
    con = open_index(str(tmp_path / "i.duckdb"), dim=8, model_id="fake-8")
    reconcile(con, str(v), FakeEmbedder(dim=8))
    # a different model id at same dim must force a full rebuild (semantic mismatch)
    r = reconcile(con, str(v), FakeEmbedder(dim=8), model_id_override="other-8")
    assert r.rebuilt is True
    assert r.added == 2
```

- [ ] **Step 2: Run — expect FAIL.** `uv run pytest tests/index/test_reconcile.py -v`

- [ ] **Step 3: Add `reconcile` (+ `ReconcileStats`) to `src/cairn/index/build.py`**
```python
@dataclass
class ReconcileStats:
    added: int = 0
    updated: int = 0
    deleted: int = 0
    rebuilt: bool = False


def reconcile(
    con: duckdb.DuckDBPyConnection,
    vault_dir: str,
    embedder: Embedder,
    *,
    model_id_override: str | None = None,
) -> ReconcileStats:
    """Bring the index in sync with the vault, re-processing only changed notes.
    A change in embedding model/dim triggers a full rebuild (vectors from
    different models are not comparable). Always rebuilds the FTS index when any
    content changed."""
    from cairn.index.schema import get_meta, set_meta

    model_id = model_id_override or embedder.model_id
    stats = ReconcileStats()

    if get_meta(con, "embedding_model") != model_id or get_meta(con, "embedding_dim") != str(embedder.dim):
        con.execute("DELETE FROM chunk_embeddings")
        con.execute("DELETE FROM chunks")
        con.execute("DELETE FROM links")
        con.execute("DELETE FROM notes")
        set_meta(con, "embedding_model", model_id)
        set_meta(con, "embedding_dim", str(embedder.dim))
        stats.rebuilt = True

    on_disk = {p.stem: p for p in sorted(Path(vault_dir).rglob("*.md"))}
    # map permalink->(path, hash, mtime) currently in the index
    indexed = {
        row[0]: (row[1], row[2], row[3])
        for row in con.execute("SELECT permalink, path, content_hash, mtime FROM notes").fetchall()
    }

    # deletions: indexed notes whose file no longer exists
    seen_permalinks: set[str] = set()
    for path in on_disk.values():
        text = path.read_text()
        permalink = parse_note(text).permalink or path.stem
        seen_permalinks.add(permalink)
        prev = indexed.get(permalink)
        cur_hash = _content_hash(text)
        if prev is None:
            index_note(con, path, embedder)
            stats.added += 1
        elif prev[1] != cur_hash:
            index_note(con, path, embedder)
            stats.updated += 1
    for permalink in set(indexed) - seen_permalinks:
        _delete_note(con, permalink)
        stats.deleted += 1

    if stats.added or stats.updated or stats.deleted or stats.rebuilt:
        build_fts(con)
    return stats
```

- [ ] **Step 4: Re-export** `ReconcileStats`, `reconcile` in `src/cairn/index/__init__.py`.
- [ ] **Step 5: Run — expect PASS.** `uv run pytest tests/index/test_reconcile.py -v`
- [ ] **Step 6: Commit**
```bash
git add src/cairn/index/build.py src/cairn/index/__init__.py tests/index/test_reconcile.py
git commit -m "feat(index): reconcile-on-spawn (incremental + model-mismatch rebuild)"
```

---

### Task 8: `cairn reindex` + `cairn index-status` CLI

**Files:** Modify `src/cairn/cli.py`; Test `tests/test_cli.py` (extend).

Default index path: `~/.cache/agentcairn/index.duckdb` (local disk, never inside the vault). Vault path arg required.

- [ ] **Step 1: Add the failing test** — append to `tests/test_cli.py`
```python
def test_reindex_and_status(tmp_path):
    v = tmp_path / "vault"; v.mkdir()
    (v / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha [[B]]\n")
    idx = tmp_path / "i.duckdb"
    r = runner.invoke(app, ["reindex", str(v), "--index", str(idx), "--embedder", "fake"])
    assert r.exit_code == 0, r.output
    assert "1 note" in r.output
    s = runner.invoke(app, ["index-status", "--index", str(idx)])
    assert s.exit_code == 0
    assert "notes: 1" in s.output
```

- [ ] **Step 2: Run — expect FAIL.** `uv run pytest tests/test_cli.py::test_reindex_and_status -v`

- [ ] **Step 3: Add commands to `src/cairn/cli.py`**
```python
# add imports at top
from pathlib import Path

from cairn.embed import get_embedder
from cairn.index import open_index, reconcile, get_meta


def _default_index() -> Path:
    return Path.home() / ".cache" / "agentcairn" / "index.duckdb"


@app.command()
def reindex(
    vault: Path = typer.Argument(..., exists=True, file_okay=False, help="Vault directory."),
    index: Path = typer.Option(None, "--index", help="Index .duckdb path (default ~/.cache/agentcairn/index.duckdb)."),
    embedder: str = typer.Option("fastembed", "--embedder", help="'fastembed' or 'fake'."),
) -> None:
    """Reconcile the DuckDB index with the vault (incremental)."""
    idx = index or _default_index()
    idx.parent.mkdir(parents=True, exist_ok=True)
    emb = get_embedder(embedder)
    con = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    stats = reconcile(con, str(vault), emb)
    typer.echo(
        f"reindexed: {stats.added} note(s) added, {stats.updated} updated, "
        f"{stats.deleted} removed{' (full rebuild)' if stats.rebuilt else ''}"
    )


@app.command(name="index-status")
def index_status(
    index: Path = typer.Option(None, "--index", help="Index .duckdb path."),
) -> None:
    """Show index location, embedding model, and row counts."""
    idx = index or _default_index()
    if not idx.exists():
        typer.echo(f"no index at {idx}")
        raise typer.Exit(1)
    import duckdb

    con = duckdb.connect(str(idx))
    n = con.execute("SELECT count(*) FROM notes").fetchone()[0]
    c = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
    typer.echo(f"index: {idx}")
    typer.echo(f"model: {get_meta(con, 'embedding_model')} (dim {get_meta(con, 'embedding_dim')})")
    typer.echo(f"notes: {n}")
    typer.echo(f"chunks: {c}")
```
(Wording note: `reindex` prints "1 note(s) added" — the test asserts the substring "1 note", which matches.)

- [ ] **Step 4: Run — expect PASS.** `uv run pytest tests/test_cli.py -v`
- [ ] **Step 5: Full suite + pre-commit.** `uv run pytest -q` (all green) and `uv run pre-commit run --all-files`.
- [ ] **Step 6: Commit**
```bash
git add src/cairn/cli.py tests/test_cli.py
git commit -m "feat(cli): cairn reindex + index-status"
```

---

## Done criteria
- `uv run pytest -q` all green (fake-embedder tests; the FastEmbed integration test skips unless `CAIRN_RUN_INTEGRATION=1`).
- `uv run cairn reindex <vault> --embedder fake --index /tmp/i.duckdb` populates notes/chunks/embeddings/links + BM25 index; `cairn index-status` reports counts + model.
- Re-running `reindex` with no vault changes re-processes nothing; editing/removing notes updates only those; changing embedder model/dim triggers a full rebuild.
- Public surface for Plan 3: `cairn.index.{open_index, index_vault, reconcile, build_fts, bm25_search, get_meta}` + `cairn.embed.{Embedder, get_embedder}`; `chunk_embeddings.vec` is `FLOAT[dim]` ready for an HNSW index.

## Self-review (plan author)
- **Spec coverage (Plan 2 slice):** embedding layer w/ local default + pluggable ✓ (spec §10); DuckDB schema notes/chunks/embeddings/links/meta ✓ (§7); structure-aware chunking + semantic anchors + provenance ✓ (§6); FTS BM25 ✓; reconcile-on-spawn + model-mismatch guard ✓ (§4); per-model dim recorded ✓. Deferred by design: HNSW + hybrid RRF query (Plan 3), redaction/ingest (Plan 4), MCP (Plan 5).
- **No placeholders:** every code step is complete and runnable; the only "later" references point to Plan 3/4/5, not within this plan.
- **Type consistency:** `Embedder(model_id, dim, embed, embed_query)`, `Chunk(chunk_id, note_permalink, heading_path, ordinal, text)`, `open_index(path, *, dim, model_id)`, `index_note`/`index_vault(con, vault_dir, embedder)`, `build_fts(con)`, `bm25_search(con, query, limit)`, `reconcile(con, vault_dir, embedder, *, model_id_override)` are used identically across Tasks 1–8.
- **Risk to watch during execution:** exact DuckDB array-insertion form (passing a Python `list[float]` for a `FLOAT[N]` column) and the `match_bm25`/`fts_main_chunks` call shape are the most version-sensitive bits — if a step errors, that's a normal TDD fix (adjust the binding form), not a redesign.
