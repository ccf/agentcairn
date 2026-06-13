# scripts/eval_consolidate.py
# SPDX-License-Identifier: Apache-2.0
"""Validate _CONSOLIDATE_GATE on the real vault: embed all memory notes and report
the top-1 nearest-neighbor cosine for each, so a human can confirm the gate
separates genuine duplicates from distinct neighbors. Run:
    uv run python scripts/eval_consolidate.py [vault]
This is an analysis tool — it never edits the vault."""

from __future__ import annotations

import math
import sys
from pathlib import Path

from cairn.embed import get_embedder
from cairn.ingest.consolidate import _CONSOLIDATE_GATE


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


def main() -> None:
    vault = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "agentcairn"
    notes = sorted((vault / "memories").glob("*.md"))
    if not notes:
        print(f"no notes under {vault}/memories")
        return
    texts = [p.read_text(encoding="utf-8") for p in notes]
    vecs = get_embedder("fastembed").embed(texts)

    sims = []
    for i in range(len(vecs)):
        best, best_j = 0.0, -1
        for j in range(len(vecs)):
            if j == i:
                continue
            c = _cos(vecs[i], vecs[j])
            if c > best:
                best, best_j = c, j
        sims.append((best, notes[i].name, notes[best_j].name if best_j >= 0 else "-"))
    sims.sort(reverse=True)
    above = [s for s in sims if s[0] >= _CONSOLIDATE_GATE]
    print(f"notes={len(notes)} gate={_CONSOLIDATE_GATE}")
    print(f"pairs at/above gate (consolidation candidates): {len(above)}")
    for c, a, b in above[:40]:
        print(f"  {c:.3f}  {a}  ~  {b}")


if __name__ == "__main__":
    main()
