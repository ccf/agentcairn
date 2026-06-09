# SPDX-License-Identifier: Apache-2.0
"""Generate an answer from the retrieved top-k Hits (hydrated to full chunk text).

Imports `cairn.search.get_chunks` to resolve chunk IDs to text. The provider seam
(FakeProvider / AnthropicProvider) is passed in; no direct import of `anthropic` here.
"""

from __future__ import annotations

from cairn.search import get_chunks

_READER = (
    "Answer the question using ONLY the context below. If the answer is not in "
    "the context, say you don't have that information.\n\nContext:\n{ctx}\n\n"
    "Question: {q}\nAnswer:"
)


def generate_answer(con, question: str, hits, *, provider, max_chunks: int = 10) -> str:
    """Generate an answer by hydrating top-k hits to chunk text and prompting the provider.

    Args:
        con: Open DuckDB search connection (from cairn.search.open_search).
        question: The question to answer.
        hits: Ranked list of Hit objects (with .chunk_id attribute).
        provider: A Provider-compatible object (FakeProvider or AnthropicProvider).
        max_chunks: Maximum number of chunks to include in the context window.

    Returns:
        The provider's answer as a string.
    """
    ids = [h.chunk_id for h in hits[:max_chunks]]
    chunks = {c["chunk_id"]: c for c in get_chunks(con, ids)}
    ctx = "\n\n".join(
        f"[{chunks[cid]['heading_path']}] {chunks[cid]['text']}" for cid in ids if cid in chunks
    )
    return provider.complete(_READER.format(ctx=ctx, q=question), max_tokens=256, temperature=0.0)
