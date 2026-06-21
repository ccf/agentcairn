"""agentcairn as a Hermes Agent MemoryProvider — local-first, vault-native memory.
Install: copy this dir to ~/.hermes/plugins/memory/agentcairn and `pip install agentcairn`."""

from __future__ import annotations

import sys
from pathlib import Path


def _base():
    try:
        from agent.memory_provider import MemoryProvider  # type: ignore

        return MemoryProvider
    except Exception:
        return object


def register(ctx) -> None:
    ctx.register_memory_provider(CairnMemoryProvider())


def _log(msg: str) -> None:
    print(f"[agentcairn] {msg}", file=sys.stderr)


def _resolve(cfg: dict):
    from cairn import paths

    vault = paths.resolve_vault(cfg.get("vault_path"))
    index = str(paths.index_for(None, vault))
    embedder = cfg.get("embedder") or "fastembed"
    return vault, index, embedder


def _reindex(vault: Path, embedder: str) -> None:
    from cairn import paths
    from cairn.embed import get_embedder
    from cairn.index import open_index, reconcile

    emb = get_embedder(embedder)
    idx = paths.index_for(None, vault)
    idx.parent.mkdir(parents=True, exist_ok=True)
    con = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    try:
        reconcile(con, str(vault), emb)
    finally:
        con.close()


class CairnMemoryProvider(_base()):
    name = "agentcairn"

    def __init__(self) -> None:
        self._cfg: dict = {}
        self._vault: Path | None = None
        self._index: str | None = None
        self._embedder = "fastembed"
        self._rerank = False

    def is_available(self) -> bool:
        try:
            self._vault, self._index, self._embedder = _resolve(self._cfg)
            return True
        except Exception:
            return False

    def initialize(self, session_id: str, **kwargs) -> None:
        self._hermes_home = kwargs.get("hermes_home", str(Path.home() / ".hermes"))
        self._vault, self._index, self._embedder = _resolve(self._cfg)
        self._rerank = bool(self._cfg.get("rerank", False))
        self._vault.mkdir(parents=True, exist_ok=True)

    def system_prompt_block(self) -> str:
        return (
            f"agentcairn memory is active. Your durable memories live as plain Markdown in "
            f"{self._vault}. Relevant ones are recalled automatically each turn."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        try:
            from cairn.mcp.tools import recall_tool

            res = recall_tool(self._index, query, embedder=self._embedder, k=5, rerank=self._rerank)
            notes = res.get("notes") or []
            chunks = [str(n.get("text") or "") for n in notes]
            chunks = [c for c in chunks if c]
            if not chunks:
                return ""
            return "## Relevant memories (agentcairn)\n\n" + "\n\n---\n\n".join(chunks)
        except Exception as e:
            _log(f"prefetch failed: {e}")
            return ""

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs):
        # Minimal here; full tool surface in Task 3.
        if tool_name == "memory_save":
            from cairn.mcp.tools import remember_tool

            out = remember_tool(
                str(self._vault), args["text"], title=args.get("title"), tags=args.get("tags")
            )
            _reindex(self._vault, self._embedder)
            return out
        return {"error": f"unknown tool {tool_name}"}
