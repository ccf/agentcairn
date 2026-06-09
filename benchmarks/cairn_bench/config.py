# SPDX-License-Identifier: Apache-2.0
"""The ablation arms. Each arm, given (con, query_text, embedder, pool, k), returns a
ranked list of Hit-like rows with .permalink and .heading_path so gold matching works
identically across arms."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cairn.search import get_chunks, search, vector_search


@dataclass
class RankedRow:
    permalink: str
    heading_path: str


def _from_hits(hits) -> list[RankedRow]:
    return [RankedRow(h.permalink, h.heading_path) for h in hits]


def _vector_only(con, q, embedder, pool, k):
    pairs = vector_search(con, embedder.embed_query(q), dim=embedder.dim, pool=pool)
    ids = [cid for cid, _sim in pairs][:k]
    rows = {c["chunk_id"]: c for c in get_chunks(con, ids)}
    return [
        RankedRow(rows[cid]["note_permalink"], rows[cid]["heading_path"])
        for cid in ids
        if cid in rows
    ]


@dataclass
class ArmConfig:
    name: str
    rank: Callable  # (con, query_text, embedder, pool, k) -> list[RankedRow]


ARMS: list[ArmConfig] = [
    ArmConfig(
        "bm25-only",
        lambda con, q, e, pool, k: _from_hits(search(con, q, embedder=None, k=k, pool=pool)),
    ),
    ArmConfig("vector-only", _vector_only),
    ArmConfig(
        "hybrid-rrf",
        lambda con, q, e, pool, k: _from_hits(
            search(con, q, embedder=e, k=k, pool=pool, graph_boost=False)
        ),
    ),
    ArmConfig(
        "hybrid+graph-boost",
        lambda con, q, e, pool, k: _from_hits(
            search(con, q, embedder=e, k=k, pool=pool, graph_boost=True)
        ),
    ),
    ArmConfig(
        "hybrid+reranker",
        lambda con, q, e, pool, k: _from_hits(
            search(con, q, embedder=e, k=k, pool=pool, rerank=True)
        ),
    ),
]
