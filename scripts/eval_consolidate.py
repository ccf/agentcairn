# scripts/eval_consolidate.py
# SPDX-License-Identifier: Apache-2.0
"""Validate _CONSOLIDATE_GATE on the real vault: embed each live note's DISTILLED
[context] line (the production consolidation signal — NOT the full note body, which
clusters by genre) and report the top-1 nearest-neighbor cosine distribution so a
human can confirm the gate separates dups from distinct notes. Run:
    uv run python scripts/eval_consolidate.py [vault]
Set CAIRN_EVAL_EMBEDDER=fake for a model-free smoke run. Analysis tool — never edits."""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

from cairn.embed import get_embedder
from cairn.ingest.consolidate import _CONSOLIDATE_GATE, extract_context
from cairn.vault import parse_note

_EMBED_BATCH = 64


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


def main() -> None:
    vault = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "agentcairn"
    items: list[tuple[str, str]] = []  # (name, distilled_text)
    for p in sorted((vault / "memories").glob("*.md")):
        try:
            note = parse_note(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if note.frontmatter.get("superseded_by"):
            continue  # exclude already-demoted notes
        ctx = extract_context(note.body)
        if ctx:
            items.append((p.name, ctx))
    if not items:
        print(f"no live [context] notes under {vault}/memories")
        return
    emb = get_embedder(os.environ.get("CAIRN_EVAL_EMBEDDER", "fastembed"))
    texts = [c for _, c in items]
    vecs: list[list[float]] = []
    for i in range(0, len(texts), _EMBED_BATCH):  # batch -> no OOM
        vecs.extend(emb.embed(texts[i : i + _EMBED_BATCH]))
    sims = []
    for i in range(len(vecs)):
        best, bj = 0.0, -1
        for j in range(len(vecs)):
            if j == i:
                continue
            c = _cos(vecs[i], vecs[j])
            if c > best:
                best, bj = c, j
        sims.append((best, items[i][0], items[bj][0] if bj >= 0 else "-"))
    sims.sort(reverse=True)
    above = [s for s in sims if s[0] >= _CONSOLIDATE_GATE]
    print(f"live notes={len(items)} gate={_CONSOLIDATE_GATE}")
    print(f"pairs at/above gate (consolidation candidates): {len(above)}")
    for c, a, b in above[:40]:
        print(f"  {c:.3f}  {a}  ~  {b}")


if __name__ == "__main__":
    main()
