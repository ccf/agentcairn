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
from cairn.ingest.judge import JudgedCache, resolve_judge
from cairn.ingest.pipeline import ingest_transcripts
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
    # Honor CAIRN_INDEX (expanding a leading "~") so the CLI, hooks, and MCP
    # server all target the same index when it's customized via env/user_config.
    env = os.environ.get("CAIRN_INDEX")
    if env:
        return Path(env).expanduser()
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
    # Best-effort savings ledger — must never break recall. Note: this CLI path
    # returns snippets, so `recalled` is the snippet payload (smaller than the
    # MCP recall_tool's full-note payload); both honestly reflect what each
    # surface actually returned.
    try:
        from cairn import usage
        from cairn.index.schema import cached_haystack_tokens

        full = cached_haystack_tokens(con)
        recalled = sum(usage.estimate_tokens(h.snippet) for h in hits)
        usage.record("recall", full=full, recalled=recalled, k=k)
    except Exception:
        pass
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
def savings(
    as_json: bool = typer.Option(False, "--json", help="Emit the summary as JSON."),
    oneline: bool = typer.Option(
        False, "--oneline", help="One-line digest string (empty when no data)."
    ),
) -> None:
    """How much context your recalls have saved (local, estimated, no telemetry)."""
    from cairn import usage

    s = usage.summarize()
    if oneline:
        line = usage.oneline(s)
        if line:
            typer.echo(line)
        return
    if as_json:
        typer.echo(json.dumps(s))
        return
    if not usage.enabled():
        typer.echo("Usage tracking is OFF (CAIRN_USAGE=0).")
    if s["recalls"] == 0:
        typer.echo("No recalls recorded yet — use recall and check back.")
        typer.echo(f"(local ledger: {usage.ledger_path()})")
        return
    typer.echo(f"Tokens saved:  ~{s['total_saved']:,}  (estimated, ~4 chars/token)")
    typer.echo(f"Recalls:       {s['recalls']}")
    typer.echo(
        f"Reduction:     {s['lifetime_factor']:.1f}x lifetime  "
        f"({s['mean_factor']:.1f}x mean / {s['median_factor']:.1f}x median per recall)"
    )
    if s["first_ts"] and s["last_ts"]:
        typer.echo(f"Span:          {s['first_ts'][:10]} -> {s['last_ts'][:10]}")
    typer.echo("")
    typer.echo("vs. dumping your whole vault — a model of context size, not a measured cost.")
    typer.echo(f"Local ledger:  {usage.ledger_path()}  (disable with CAIRN_USAGE=0)")


@app.command()
def warm() -> None:
    """Pre-download the configured embedder + reranker models (best-effort).

    Reads CAIRN_EMBEDDER (default 'fastembed') and CAIRN_RERANK. Idempotent —
    near-instant once the models are cached. The plugin's detached first-run job
    calls this so the first real sweep/recall isn't slow; also handy before
    first CLI use.
    """
    embedder = os.environ.get("CAIRN_EMBEDDER") or "fastembed"
    if embedder in ("fastembed", "ollama"):
        try:
            # Touch `.dim` to force the actual load: fastembed downloads in its
            # constructor, but ollama probes the server lazily on first dim/embed
            # — so without this, warming ollama would validate/load nothing.
            _ = get_embedder(embedder).dim
            typer.echo(f"embedder ready: {embedder}")
        except Exception as exc:  # best-effort pre-fetch — never crash
            typer.echo(f"embedder warm failed ({embedder}): {exc}")
    else:
        typer.echo(f"embedder: nothing to warm ({embedder})")

    if resolve_rerank():
        try:
            from cairn.search import rerank_candidates

            rerank_candidates("warm", [{"text": "hello"}])
            typer.echo("reranker ready")
        except Exception as exc:  # best-effort pre-fetch — never crash
            typer.echo(f"reranker warm failed: {exc}")
    else:
        typer.echo("reranker: skipped (CAIRN_RERANK=0)")


@app.command()
def install(
    host: str = typer.Argument(
        None, help="Host id: cursor / claude-desktop / windsurf / gemini / codex."
    ),
    all_hosts: bool = typer.Option(False, "--all", help="Configure every detected host."),
    print_only: bool = typer.Option(False, "--print", help="Print the config; write nothing."),
    vault: Path = typer.Option(None, "--vault", help="Vault path (default ~/agentcairn)."),
    index: Path = typer.Option(
        None, "--index", help="Index path (default ~/.cache/agentcairn/index.duckdb)."
    ),
) -> None:
    """Wire the agentcairn MCP server into another MCP host (Cursor, Codex, …)."""
    from cairn.hosts import HOSTS, detected_hosts, get_host
    from cairn.hosts.entry import mcp_entry
    from cairn.hosts.writers import write_host

    v = str((vault or (Path.home() / "agentcairn")).expanduser().resolve())
    idx = str(
        (index or (Path.home() / ".cache" / "agentcairn" / "index.duckdb")).expanduser().resolve()
    )
    entry = mcp_entry(v, idx)
    ids = ", ".join(h.id for h in HOSTS)

    if host is None and not all_hosts:  # detect + preview, write nothing
        present = detected_hosts()
        if not present:
            typer.echo(f"No supported MCP hosts detected. Supported: {ids}")
            return
        typer.echo("Detected hosts — run `cairn install <id>` (or `--all`):")
        for h in present:
            typer.echo(f"  {h.id:15} {h.label}  → {h.config_path()}")
        return

    if all_hosts:
        targets = detected_hosts()
        if not targets:
            typer.echo(f"No supported MCP hosts detected. Supported: {ids}")
            return
    else:
        h = get_host(host)
        if h is None:
            typer.echo(f"unknown host '{host}'. Supported: {ids}")
            raise typer.Exit(1)
        targets = [h]

    failures = 0
    for h in targets:
        try:
            out = write_host(h, entry, dry=print_only)
            if print_only:
                typer.echo(f"# {h.label} ({h.config_path()})")
                typer.echo(out)
            else:
                typer.echo(f"✓ {h.label}: {out}")
        except Exception as e:  # best-effort per host; continue under --all
            failures += 1
            typer.echo(f"✗ {h.label}: {e}")
    if failures:
        raise typer.Exit(1)


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


def _warn_if_llm_tier_unavailable(rep) -> None:
    """CAIRN_JUDGE=anthropic but the run didn't use the LLM tier — say so once."""
    if os.environ.get("CAIRN_JUDGE") == "anthropic" and rep.judge_tier != "llm":
        typer.echo(
            "  note: CAIRN_JUDGE=anthropic but LLM tier unavailable (missing key?) "
            f"— used {rep.judge_tier}"
        )


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
    vault_key = hashlib.sha256(str(vault.resolve()).encode()).hexdigest()[:16]
    if ledger is not None:
        led_path = ledger
    else:
        led_path = Path.home() / ".cache" / "agentcairn" / "ledgers" / f"{vault_key}.sha256"
    led = DedupLedger(led_path)
    paths = find_transcripts(root=transcripts_dir, project=project)
    transcripts = [parse_transcript(tp) for tp in paths]
    # One embedder serves both the judge and the reindex (avoid a double model load).
    emb = get_embedder(embedder)
    rep = ingest_transcripts(
        transcripts,
        vault_root=vault,
        ledger=led,
        threshold=threshold,
        judge=resolve_judge(embedder=emb),
        judged_cache=JudgedCache(led_path.parent / f"{vault_key}.judged.jsonl"),
    )
    idx = index or _default_index()
    idx.parent.mkdir(parents=True, exist_ok=True)
    con = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    try:
        stats = reconcile(con, str(vault), emb)
    finally:
        con.close()  # release the write lock even if reconcile fails
    typer.echo(
        f"swept: {len(rep.written)} memory note(s) written; reindexed "
        f"{stats.added} added, {stats.updated} updated, {stats.deleted} removed"
    )
    _warn_if_llm_tier_unavailable(rep)


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
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report without writing (LLM judge is skipped on dry runs)."
    ),
    embedder: str = typer.Option(
        "fastembed", "--embedder", help="Embedder for the durability judge (mirrors sweep)."
    ),
) -> None:
    """Ingest Claude Code transcripts into non-lossy derived memory notes."""
    # Keep ledger OUTSIDE the vault (dedup.py docstring + spec). Namespace
    # by vault path so different vaults use separate ledgers.
    vault_key = hashlib.sha256(str(vault.resolve()).encode()).hexdigest()[:16]
    if ledger is not None:
        led_path = ledger
    else:
        led_path = Path.home() / ".cache" / "agentcairn" / "ledgers" / f"{vault_key}.sha256"
    led = DedupLedger(led_path)
    paths = find_transcripts(root=transcripts_dir, project=project)
    if not paths:
        typer.echo("No transcripts found.")
        return
    transcripts = [parse_transcript(tp) for tp in paths]
    # Same --embedder flag as sweep, so the judge scores in the same embedding
    # space regardless of which command ingests (lazy: tier "none" loads nothing).
    loader = lambda: get_embedder(embedder)  # noqa: E731
    if dry_run:
        # A preview must not spend LLM tokens: force the judge tier below anthropic
        # (embedding unless explicitly disabled).
        env = dict(os.environ)
        if env.get("CAIRN_JUDGE", "embedding") != "none":
            env["CAIRN_JUDGE"] = "embedding"
        judge = resolve_judge(env=env, embedder_loader=loader)
    else:
        judge = resolve_judge(embedder_loader=loader)
    rep = ingest_transcripts(
        transcripts,
        vault_root=vault,
        ledger=led,
        threshold=threshold,
        judge=judge,
        judged_cache=JudgedCache(led_path.parent / f"{vault_key}.judged.jsonl"),
        dry_run=dry_run,
    )
    prefix = "[dry-run] " if dry_run else ""
    typer.echo(
        f"{prefix}{rep.authored} authored · {rep.candidates} candidates · "
        f"{rep.redactions} redactions · {rep.deduped} deduped · "
        f"{rep.gated_out} gated · {len(rep.written)} written · judge: {rep.judge_tier}"
        + (f" ({rep.judge_degraded} degraded)" if rep.judge_degraded else "")
    )
    skipped = {k: v for k, v in rep.event_kinds.items() if k != "authored_user"}
    if skipped:
        breakdown = ", ".join(f"{v} {k}" for k, v in sorted(skipped.items(), key=lambda kv: -kv[1]))
        typer.echo(f"  skipped (non-authored): {breakdown}")
    if not dry_run:  # dry runs force the tier down on purpose — no warning
        _warn_if_llm_tier_unavailable(rep)
