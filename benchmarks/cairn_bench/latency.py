# SPDX-License-Identifier: Apache-2.0
"""Retrieval-latency benchmark: measure brute-force vector search (vector arm +
end-to-end hybrid) across synthetic vault sizes, to decide whether/when an HNSW
index is worth building. Manual tool — not a CI gate. See
docs/specs/2026-06-15-retrieval-latency-benchmark-design.md."""

from __future__ import annotations

import numpy as np

from cairn.index.build import build_fts
from cairn.index.schema import open_index

_WORDS = (
    "deploy key rotation token cache schema vector index recall memory vault "
    "harness project session redact judge consolidate embed chunk note query"
).split()


def build_synthetic_index(path: str, n_chunks: int, *, dim: int, seed: int) -> None:
    """Build a temp DuckDB index of `n_chunks` synthetic notes/chunks/embeddings
    (random float32 vectors + pseudo-text, seeded). Build via open_index (writable),
    then close — queries use open_search (which carries the rrf macro + fts)."""
    rng = np.random.default_rng(seed)
    vecs = rng.standard_normal((n_chunks, dim)).astype("float32")
    con = open_index(path, dim=dim, model_id="bench")
    try:
        note_rows = []
        chunk_rows = []
        emb_rows = []
        for i in range(n_chunks):
            cid = f"c{i}"
            text = " ".join(rng.choice(_WORDS, size=6))
            note_rows.append(
                (
                    cid,
                    f"/bench/{cid}.md",
                    f"note {i}",
                    "memory",
                    cid,
                    0.0,
                    None,
                    None,
                    None,
                    None,
                    None,
                )
            )
            chunk_rows.append((cid, cid, "", 0, text))
            emb_rows.append((cid, vecs[i].tolist()))
        con.execute("BEGIN")
        con.executemany(
            "INSERT INTO notes (permalink, path, title, type, content_hash, mtime, "
            "valid_from, valid_until, superseded_by, project, harness) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            note_rows,
        )
        con.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)", chunk_rows)
        con.executemany("INSERT INTO chunk_embeddings VALUES (?, ?)", emb_rows)
        con.execute("COMMIT")
        build_fts(con)
    finally:
        con.close()
