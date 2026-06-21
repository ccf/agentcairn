"""agentcairn as a Hermes Agent MemoryProvider — local-first, vault-native memory.
Install: copy this dir to ~/.hermes/plugins/memory/agentcairn and `pip install agentcairn`."""

from __future__ import annotations

import sys
import threading
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
        # Per-session turn buffers. GIL-protected; sync_turn is assumed to be called
        # serially per session (no caller-level threading) so list.append needs no lock.
        self._buffers: dict[str, list[dict]] = {}

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

    def get_tool_schemas(self):
        return [
            {
                "name": "memory_save",
                "description": "Save a durable memory to the agentcairn vault.",
                "parameters": {
                    "type": "object",
                    "required": ["text"],
                    "properties": {
                        "text": {"type": "string"},
                        "title": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            {
                "name": "memory_recall",
                "description": "Recall full memories relevant to a query.",
                "parameters": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string"},
                        "k": {"type": "integer"},
                    },
                },
            },
            {
                "name": "memory_search",
                "description": "Search memories (compact id+snippet index).",
                "parameters": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string"},
                        "k": {"type": "integer"},
                    },
                },
            },
        ]

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs):
        from cairn.mcp.tools import recall_tool, remember_tool, search_tool

        try:
            if tool_name == "memory_save":
                out = remember_tool(
                    str(self._vault),
                    args["text"],
                    title=args.get("title"),
                    tags=args.get("tags"),
                )
                _reindex(self._vault, self._embedder)
                return out
            if tool_name == "memory_recall":
                return recall_tool(
                    self._index,
                    args["query"],
                    embedder=self._embedder,
                    k=int(args.get("k", 5)),
                    rerank=self._rerank,
                )
            if tool_name == "memory_search":
                return search_tool(
                    self._index,
                    args["query"],
                    embedder=self._embedder,
                    k=int(args.get("k", 10)),
                    rerank=self._rerank,
                )
        except Exception as e:
            _log(f"tool {tool_name} failed: {e}")
            return {"error": str(e)}
        return {"error": f"unknown tool {tool_name}"}

    def sync_turn(self, user: str, assistant: str, *, session_id: str = "") -> None:
        buf = self._buffers.setdefault(session_id, [])
        if user:
            buf.append({"role": "user", "content": user})
        if assistant:
            buf.append({"role": "assistant", "content": assistant})

    def _capture(self, messages: list[dict], session_id: str) -> None:
        try:
            import cairn.ingest as ci

            t = ci.transcript_from_messages(messages, session_id=session_id)
            ledger_path = Path(self._hermes_home) / "agentcairn" / "dedup.jsonl"
            ledger = ci.DedupLedger(ledger_path)
            ci.ingest_transcript(t, vault_root=self._vault, ledger=ledger, subdir="memories")
            _reindex(self._vault, self._embedder)
        except Exception as e:
            _log(f"capture failed (dropped): {e}")

    def on_session_end(self, messages) -> None:
        msgs = list(messages) if messages else self._buffers.get("", [])
        t = threading.Thread(target=self._capture, args=(msgs, "hermes"), daemon=True)
        t.start()
        self._threads = getattr(self, "_threads", [])
        self._threads.append(t)

    def shutdown(self) -> None:
        for t in getattr(self, "_threads", []):
            t.join(timeout=30)
