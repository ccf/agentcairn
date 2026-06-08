# SPDX-License-Identifier: Apache-2.0
"""The `cairn` command-line interface."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import typer

from cairn import __version__
from cairn.embed import get_embedder
from cairn.index import get_meta, open_index, reconcile
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
