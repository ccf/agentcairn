# SPDX-License-Identifier: Apache-2.0
"""The `cairn` command-line interface."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path

import typer

from cairn import __version__
from cairn.config import resolve_rerank
from cairn.embed import get_embedder
from cairn.index import get_meta, open_index, reconcile
from cairn.ingest import find_transcripts, parse_transcript
from cairn.ingest.dedup import DedupLedger
from cairn.ingest.pipeline import ingest_transcript
from cairn.search import open_search, search
from cairn.vault import parse_note

app = typer.Typer(
    no_args_is_help=True, add_completion=False, help="agentcairn — local-first agent memory."
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
) -> None:
    """agentcairn — local-first agent memory."""


def _default_index() -> Path:
    return Path.home() / ".cache" / "agentcairn" / "index.duckdb"


@app.command()
def parse(
    file: Path = typer.Argument(..., exists=True, readable=True, help="Markdown note to parse."),
) -> None:
    """Parse a markdown note and print its structured form as JSON."""
    note = parse_note(file.read_text())
    typer.echo(json.dumps(dataclasses.asdict(note), indent=2, default=str))


@app.command()
def reindex(
    vault: Path = typer.Argument(..., exists=True, file_okay=False, help="Vault directory."),
    index: Path = typer.Option(
        None, "--index", help="Index .duckdb path (default ~/.cache/agentcairn/index.duckdb)."
    ),
    embedder: str = typer.Option(
        "fastembed",
        "--embedder",
        help="'fastembed' or 'fake'; 'ollama' (CAIRN_EMBED_MODEL/OLLAMA_HOST).",
    ),
) -> None:
    """Reconcile the DuckDB index with the vault (incremental)."""
    idx = index or _default_index()
    idx.parent.mkdir(parents=True, exist_ok=True)
    emb = get_embedder(embedder)
    con = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    try:
        stats = reconcile(con, str(vault), emb)
    finally:
        con.close()  # release the write lock even if reconcile fails
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


@app.command()
def recall(
    query: str = typer.Argument(..., help="What to search for."),
    index: Path = typer.Option(None, "--index", help="Index .duckdb path."),
    embedder: str = typer.Option(
        "fastembed",
        "--embedder",
        help="'fastembed' (hybrid) or 'fake'; 'none' = BM25-only; 'ollama' (CAIRN_EMBED_MODEL/OLLAMA_HOST).",  # noqa: E501
    ),
    k: int = typer.Option(10, "--k", help="Number of results."),
    rerank: bool | None = typer.Option(
        None,
        "--rerank/--no-rerank",
        help="Cross-encoder rerank (default on; or set CAIRN_RERANK=0).",
    ),
) -> None:
    """Hybrid recall over the index (semantic + BM25 + graph-boost).

    Validity-aware: current facts rank above superseded/expired ones (set
    `superseded_by`/`valid_until` in note frontmatter). Reranked by default
    (`CAIRN_RERANK=0` to disable).
    """
    idx = index or _default_index()
    if not idx.exists():
        typer.echo(f"no index at {idx} — run `cairn reindex <vault>` first")
        raise typer.Exit(1)
    emb = None if embedder == "none" else get_embedder(embedder)
    con = open_search(str(idx))
    hits = search(con, query, embedder=emb, k=k, rerank=resolve_rerank(rerank))
    if not hits:
        typer.echo("(no results)")
        return
    for h in hits:
        typer.echo(f"[{h.score:.3f}] {h.permalink}  ·  {h.heading_path}")
        typer.echo(f"        {h.snippet.strip()[:160]}")


@app.command()
def recent(
    index: Path = typer.Option(None, "--index", help="Index .duckdb path."),
    project: str = typer.Option(
        None, "--project", help="Only notes whose path contains this substring."
    ),
    n: int = typer.Option(10, "-n", "--num", help="Number of notes."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for machine parsing."),
) -> None:
    """Most-recently-modified notes (optionally filtered to a project path substring)."""
    idx = index or _default_index()
    if not idx.exists():
        typer.echo(json.dumps({"notes": []}) if as_json else f"no index at {idx}")
        return
    con = open_search(str(idx))
    try:
        if project:
            rows = con.execute(
                "SELECT permalink, title, path FROM notes "
                "WHERE path LIKE '%' || ? || '%' ORDER BY mtime DESC LIMIT ?",
                [project, n],
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT permalink, title, path FROM notes ORDER BY mtime DESC LIMIT ?", [n]
            ).fetchall()
    finally:
        con.close()
    notes = [{"permalink": r[0], "title": r[1], "path": r[2]} for r in rows]
    if as_json:
        typer.echo(json.dumps({"notes": notes}))
    else:
        for nt in notes:
            typer.echo(f"{nt['permalink']}  ·  {nt['title']}")


_WELCOME = (
    "---\ntitle: Welcome to your agentcairn vault\npermalink: welcome\n---\n\n"
    "This is your **agentcairn** memory vault. Your coding agent writes distilled, redacted "
    "memories here as plain Markdown — you can read, edit, or delete any of it by hand. "
    "Open this folder in Obsidian to browse the graph.\n"
)


@app.command()
def init(
    path: Path = typer.Argument(None, help="Vault path (default: $CAIRN_VAULT or ~/agentcairn)."),
) -> None:
    """Scaffold an Obsidian-ready agentcairn vault. Idempotent and non-destructive."""
    target = path or Path(os.environ.get("CAIRN_VAULT") or (Path.home() / "agentcairn"))
    target = target.expanduser()
    target.mkdir(parents=True, exist_ok=True)
    obs = target / ".obsidian"
    obs.mkdir(exist_ok=True)
    app_json = obs / "app.json"
    if not app_json.exists():
        app_json.write_text("{}\n")
    welcome = target / "welcome.md"
    existed = welcome.exists()
    if not existed:
        welcome.write_text(_WELCOME)
    suffix = "" if not existed else " (existing — left intact)"
    typer.echo(f"agentcairn vault ready at {target}{suffix}")


@app.command()
def serve(
    vault: Path = typer.Option(None, "--vault", help="Vault root (enables `remember`)."),
    index: Path = typer.Option(None, "--index", help="Index .duckdb path."),
    embedder: str = typer.Option(
        None,
        "--embedder",
        help="'fastembed', 'fake', or 'ollama' (CAIRN_EMBED_MODEL/OLLAMA_HOST); "
        "defaults to CAIRN_EMBEDDER or fastembed.",
    ),
) -> None:
    """Launch the agentcairn MCP server (stdio)."""
    from cairn.mcp.server import build_server

    build_server(
        vault=str(vault) if vault else None,
        index=str(index) if index else None,
        embedder=embedder,
    ).run()


@app.command()
def sweep(
    vault: Path = typer.Option(..., "--vault", help="Vault root."),
    transcripts_dir: Path = typer.Option(
        None, "--transcripts-dir", help="Override the ~/.claude/projects root."
    ),
    project: str = typer.Option(None, "--project", help="Absolute cwd filter (default: all)."),
    threshold: float = typer.Option(0.5, "--threshold", help="Importance keep-threshold."),
    index: Path = typer.Option(
        None, "--index", help="Index .duckdb path (default ~/.cache/agentcairn/index.duckdb)."
    ),
    embedder: str = typer.Option(
        "fastembed",
        "--embedder",
        help="'fastembed' or 'fake'; 'ollama' (CAIRN_EMBED_MODEL/OLLAMA_HOST).",
    ),
    ledger: Path = typer.Option(
        None,
        "--ledger",
        help="Dedup ledger path (default: ~/.cache/agentcairn/ledgers/<hash>.sha256).",
    ),
) -> None:
    """Batch-ingest transcripts into the vault, then reindex (cron maintenance)."""
    if ledger is not None:
        led_path = ledger
    else:
        vault_key = hashlib.sha256(str(vault.resolve()).encode()).hexdigest()[:16]
        led_path = Path.home() / ".cache" / "agentcairn" / "ledgers" / f"{vault_key}.sha256"
    led = DedupLedger(led_path)
    paths = find_transcripts(root=transcripts_dir, project=project)
    written = 0
    for tp in paths:
        rep = ingest_transcript(
            parse_transcript(tp),
            vault_root=vault,
            ledger=led,
            threshold=threshold,
        )
        written += len(rep.written)
    idx = index or _default_index()
    idx.parent.mkdir(parents=True, exist_ok=True)
    emb = get_embedder(embedder)
    con = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    try:
        stats = reconcile(con, str(vault), emb)
    finally:
        con.close()  # release the write lock even if reconcile fails
    typer.echo(
        f"swept: {written} memory note(s) written; reindexed "
        f"{stats.added} added, {stats.updated} updated, {stats.deleted} removed"
    )


@app.command()
def doctor(
    index: Path = typer.Option(
        None, "--index", help="Index .duckdb path (default ~/.cache/agentcairn/index.duckdb)."
    ),
) -> None:
    """Health-check the index: model/dim, row counts, embedding/chunk parity."""
    import duckdb

    idx = index or _default_index()
    if not idx.exists():
        typer.echo(f"no index at {idx} — run `cairn reindex <vault>` first")
        raise typer.Exit(1)
    con = duckdb.connect(str(idx), read_only=True)
    notes = con.execute("SELECT count(*) FROM notes").fetchone()[0]
    chunks = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
    embs = con.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0]
    model = get_meta(con, "embedding_model")
    dim = get_meta(con, "embedding_dim")
    con.close()
    typer.echo(f"index:  {idx}")
    typer.echo(f"model:  {model} (dim {dim})")
    typer.echo(f"notes:  {notes}")
    typer.echo(f"chunks: {chunks}")
    typer.echo(f"embeds: {embs}")
    problems: list[str] = []
    if chunks != embs:
        problems.append(f"chunk/embedding mismatch: {chunks} chunks vs {embs} embeddings")
    if notes > 0 and chunks == 0:
        problems.append("notes present but no chunks indexed")
    if problems:
        for p in problems:
            typer.echo(f"PROBLEM: {p}")
        raise typer.Exit(1)
    typer.echo("status: OK")


@app.command()
def ingest(
    vault: Path = typer.Option(..., "--vault", help="Vault root to write derived notes into."),
    transcripts_dir: Path = typer.Option(
        None, "--transcripts-dir", help="Override the ~/.claude/projects root."
    ),
    project: str = typer.Option(
        None, "--project", help="Absolute cwd to filter transcripts to (default: all)."
    ),
    threshold: float = typer.Option(0.5, "--threshold", help="Importance keep-threshold."),
    ledger: Path = typer.Option(
        None, "--ledger", help="Dedup ledger path (default: <vault>/.cairn/ingested.sha256)."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report without writing."),
) -> None:
    """Ingest Claude Code transcripts into non-lossy derived memory notes."""
    if ledger is not None:
        led_path = ledger
    else:
        # Keep ledger OUTSIDE the vault (dedup.py docstring + spec). Namespace
        # by vault path so different vaults use separate ledgers.
        vault_key = hashlib.sha256(str(vault.resolve()).encode()).hexdigest()[:16]
        led_path = Path.home() / ".cache" / "agentcairn" / "ledgers" / f"{vault_key}.sha256"
    led = DedupLedger(led_path)
    paths = find_transcripts(root=transcripts_dir, project=project)
    if not paths:
        typer.echo("No transcripts found.")
        return
    totals: dict[str, int] = {
        "candidates": 0,
        "redactions": 0,
        "deduped": 0,
        "gated_out": 0,
        "written": 0,
    }
    for tp in paths:
        rep = ingest_transcript(
            parse_transcript(tp),
            vault_root=vault,
            ledger=led,
            threshold=threshold,
            dry_run=dry_run,
        )
        totals["candidates"] += rep.candidates
        totals["redactions"] += rep.redactions
        totals["deduped"] += rep.deduped
        totals["gated_out"] += rep.gated_out
        totals["written"] += len(rep.written)
    prefix = "[dry-run] " if dry_run else ""
    typer.echo(
        f"{prefix}{totals['candidates']} candidates · {totals['redactions']} redactions · "
        f"{totals['deduped']} deduped · {totals['gated_out']} gated · "
        f"{totals['written']} written"
    )
