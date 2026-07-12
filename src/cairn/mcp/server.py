# SPDX-License-Identifier: Apache-2.0
"""FastMCP server exposing agentcairn's memory tools. Reads config from env so
`uvx agentcairn` / MCP clients can point it at a vault + index. Thin wrapper —
real logic lives in cairn.mcp.tools."""

from __future__ import annotations

import os
import threading

from mcp.server.fastmcp import FastMCP

from cairn import paths
from cairn.config import cairn_env, resolve_rerank
from cairn.locking import VaultBusyError
from cairn.mcp import tools
from cairn.search import resolve_current_project

_DEFAULT_EMBEDDER = "fastembed"
_DUCKDB_LOCK_MARKERS = (
    "could not set lock",
    "conflicting lock is held",
    "database is locked",
)


def _transient_index_contention(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _DUCKDB_LOCK_MARKERS)


def resolve_config(
    *,
    vault: str | None = None,
    index: str | None = None,
    embedder: str | None = None,
) -> tuple[str, str, str]:
    """Resolve (vault, index, embedder) from explicit args → env → defaults.

    Returns a 3-tuple:
      vault    — explicit arg → CAIRN_VAULT env → ~/agentcairn
      index    — explicit arg → CAIRN_INDEX env → derived from vault (paths.index_for)
      embedder — explicit arg → CAIRN_EMBEDDER env → "fastembed"
                 valid values: "fastembed", "fake", "ollama"
                 (ollama: model via CAIRN_EMBED_MODEL, host via OLLAMA_HOST)
    """
    settings = cairn_env()
    resolved_vault = vault or settings.get("CAIRN_VAULT")
    # Expand a leading "~": plugin user_config defaults like "~/agentcairn" may
    # reach us unnormalized, and DuckDB/open() treat a literal "~" as a relative
    # dir — so recall would miss the index and remember would write outside the vault.
    if resolved_vault:
        resolved_vault = os.path.expanduser(resolved_vault)
    vault_path = paths.resolve_vault(resolved_vault, env=settings)
    resolved_index = str(paths.index_for(index, vault_path, env=settings))
    resolved_embedder = embedder or settings.get("CAIRN_EMBEDDER") or _DEFAULT_EMBEDDER
    # Return the resolved default too: standalone `uvx agentcairn` must be able
    # to remember into the same default vault used to derive its index.
    return str(vault_path), resolved_index, resolved_embedder


class _LazyReconciler:
    """Run one startup reconciliation on the first read tool invocation."""

    def __init__(self, vault: str, index: str, embedder: str) -> None:
        self.vault = vault
        self.index = index
        self.embedder = embedder
        self._guard = threading.Lock()
        self._done = False
        self._status: dict = {"status": "pending", "reason": "startup_reconcile_pending"}

    def ensure(self) -> dict:
        if self._done:
            return self._status
        with self._guard:
            if self._done:
                return self._status
            try:
                self._status = tools.reconcile_index_tool(
                    self.vault, self.index, embedder=self.embedder
                )
                self._status["source"] = "startup_reconcile"
                self._done = True
            except VaultBusyError as exc:
                # A writer is already making progress. Serve the last good index
                # when one exists, and retry reconciliation on the next read.
                self._status = {
                    "status": "degraded",
                    "reason": "writer_busy",
                    "error": str(exc),
                    "source": "startup_reconcile",
                }
            except Exception as exc:
                # A short-lived read connection in another process can hold a
                # DuckDB lock without holding our writer lock. Retry that case on
                # the next tool call; model/config failures stay one-shot.
                retryable = _transient_index_contention(exc)
                self._status = {
                    "status": "degraded",
                    "reason": "index_reader_busy" if retryable else "reconcile_failed",
                    "error": str(exc),
                    "source": "startup_reconcile",
                }
                self._done = not retryable
            return self._status

    def update(self, status: dict) -> None:
        """Let a write-through remember supersede startup freshness state."""
        with self._guard:
            self._status = {**status, "source": "remember"}
            self._done = status.get("status") == "current"


def build_server(
    *,
    vault: str | None = None,
    index: str | None = None,
    embedder: str | None = None,
) -> FastMCP:
    vault, index, embedder = resolve_config(vault=vault, index=index, embedder=embedder)
    rerank_default = resolve_rerank(None)
    mcp = FastMCP("agentcairn")
    freshness = _LazyReconciler(vault, index, embedder)

    def _read(call) -> dict:
        status = freshness.ensure()
        try:
            result = call()
        except Exception as exc:
            if status.get("status") == "degraded":
                message = f"{exc}; automatic index reconciliation failed: {status['error']}"
                raise RuntimeError(message) from exc
            raise
        result["freshness"] = status
        return result

    @mcp.tool()
    def search(
        query: str,
        k: int = 10,
        rerank: bool = rerank_default,
        project: str | None = None,
        scope: str = "all",
    ) -> dict:
        """Hybrid search over memory; returns a compact id+snippet index.
        Reranks by default (set CAIRN_RERANK=0 to disable, or pass rerank=false).
        Recall prefers your current project's memories (boosted, non-lossy): pass
        `project` (a repo name) to target another project, else the server's working
        directory is used. `scope="project"` hard-limits results to that project."""
        return _read(
            lambda: tools.search_tool(
                index,
                query,
                embedder=embedder,
                k=k,
                rerank=rerank,
                project=project,
                scope=scope,
            )
        )

    @mcp.tool()
    def recall(
        query: str,
        k: int = 5,
        rerank: bool = rerank_default,
        project: str | None = None,
        scope: str = "all",
    ) -> dict:
        """Search then hydrate the top-k notes' full text.
        Reranks by default (set CAIRN_RERANK=0 to disable, or pass rerank=false).
        Recall prefers your current project's memories (boosted, non-lossy): pass
        `project` (a repo name) to target another project, else the server's working
        directory is used. `scope="project"` hard-limits results to that project."""
        return _read(
            lambda: tools.recall_tool(
                index,
                query,
                embedder=embedder,
                k=k,
                rerank=rerank,
                project=project,
                scope=scope,
            )
        )

    @mcp.tool()
    def build_context(permalink: str) -> dict:
        """Return a note plus its 1-hop linked neighbors."""
        return _read(lambda: tools.build_context_tool(index, permalink))

    @mcp.tool()
    def recent(n: int = 10) -> dict:
        """List the most-recently-modified notes."""
        return _read(lambda: tools.recent_tool(index, n=n))

    @mcp.tool()
    def remember(text: str, title: str | None = None, tags: list[str] | None = None) -> dict:
        """Persist a distilled memory (redacted, non-lossy) into the vault."""
        result = tools.remember_tool(
            vault,
            text,
            title=title,
            tags=tags,
            index_path=index,
            embedder=embedder,
            project=resolve_current_project(None),
            harness="mcp",
        )
        freshness.update(result["index"])
        return result

    return mcp


def main() -> None:  # pragma: no cover - stdio entrypoint
    build_server().run()


if __name__ == "__main__":  # pragma: no cover
    main()
