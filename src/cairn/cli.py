# SPDX-License-Identifier: Apache-2.0
"""The `cairn` command-line interface."""

from __future__ import annotations

import dataclasses
import json
import math
import os
import sys
from collections.abc import Mapping
from contextlib import nullcontext
from pathlib import Path

import typer

from cairn import __version__, paths
from cairn.config import cairn_env, resolve_rerank
from cairn.embed import get_embedder
from cairn.index import get_meta, open_index, reconcile
from cairn.ingest import find_transcripts, parse_transcript
from cairn.ingest.consolidate import (
    _CONSOLIDATE_GATE,
    Neighbor,
    extract_context,
    resolve_consolidator,
)
from cairn.ingest.dedup import DedupLedger
from cairn.ingest.judge import _EMBED_BATCH, JudgedCache, resolve_judge
from cairn.ingest.pipeline import ingest_transcripts
from cairn.locking import VaultBusyError, vault_writer_lock
from cairn.search import open_search, resolve_current_project, search
from cairn.search.engine import semantic_neighbors
from cairn.storage import atomic_write_text, ensure_private_dir
from cairn.vault import parse_note, write_note


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


class _DistilledNeighborIndex:
    """NeighborIndex over the DISTILLED `[context]` text of live vault notes (loaded
    and embedded at construction), unioned with this-sweep's writes. No DuckDB: the
    recall chunk embeddings include the `[verbatim]` turn and cluster by conversational
    genre (useless for dedup); distilled-vs-distilled separates better (0.10.1)."""

    def __init__(self, *, vault_root: Path, subdir: str, embedder) -> None:
        self._embedder = embedder
        # (permalink, vec, distilled_text, created_ts, path)
        self._batch: list[tuple[str, list[float], str, str | None, str | None]] = []
        self._superseded: set[str] = set()
        loaded: list[tuple[str, str, str | None, str]] = []  # perm, ctx, created, path
        for p in sorted((vault_root / subdir).glob("*.md")):
            try:
                note = parse_note(p.read_text(encoding="utf-8"))
            except Exception:
                continue  # a malformed note must not abort the sweep
            if note.frontmatter.get("superseded_by"):
                continue  # already demoted — never match against it
            ctx = extract_context(note.body)
            if not ctx:
                continue
            perm = note.permalink or note.frontmatter.get("permalink") or p.stem
            loaded.append((perm, ctx, note.frontmatter.get("created"), str(p.resolve())))
        self._live: list[tuple[str, list[float], str, str | None, str | None]] = []
        for i in range(0, len(loaded), _EMBED_BATCH):  # batch -> no OOM on big vaults
            batch = loaded[i : i + _EMBED_BATCH]
            for (perm, ctx, created, path), vec in zip(
                batch, embedder.embed([b[1] for b in batch]), strict=True
            ):
                self._live.append((perm, vec, ctx, created, path))

    def _embed(self, text: str) -> list[float]:
        return self._embedder.embed([text])[0]

    def nearest(self, text: str):
        vec = self._embed(text)
        best = None  # (Neighbor, cosine)
        for perm, nvec, ntext, nts, npath in (*self._live, *self._batch):
            if perm in self._superseded:
                continue
            cos = _cosine(vec, nvec)
            if best is None or cos > best[1]:
                best = (Neighbor(permalink=perm, text=ntext, timestamp=nts, path=npath), cos)
        if best is None or best[1] < _CONSOLIDATE_GATE:
            return None
        return best

    def add(
        self, permalink: str, text: str, timestamp: str | None, path: str | None = None
    ) -> None:
        self._batch.append((permalink, self._embed(text), text, timestamp, path))

    def note_superseded(self, permalink: str) -> None:
        self._superseded.add(permalink)


app = typer.Typer(
    no_args_is_help=True, add_completion=False, help="agentcairn — local-first agent memory."
)

schedule_app = typer.Typer(
    help="Manage a background schedule that runs `cairn sweep` periodically "
    "(the host-agnostic capture backstop)."
)
app.add_typer(schedule_app, name="schedule")


def _exit_vault_busy(exc: VaultBusyError) -> None:
    typer.secho(f"busy: {exc}", fg=typer.colors.YELLOW, err=True)
    raise typer.Exit(75) from exc  # EX_TEMPFAIL: retrying later is the right action


@schedule_app.command("install")
def schedule_install(
    interval: str = typer.Option("30m", "--interval", help="e.g. 30m, 1h, or minutes."),
    vault: Path = typer.Option(None, "--vault"),
    print_only: bool = typer.Option(False, "--print", help="Render only; write nothing."),
) -> None:
    from cairn import schedule
    from cairn.paths import resolve_vault

    try:
        mins = schedule.parse_interval(interval)
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint="--interval") from e
    v = resolve_vault(vault).resolve()
    log = str(schedule.log_path())
    cairn = schedule.resolve_cairn()
    try:
        if print_only:
            rendered = (
                schedule.render_plist(cairn, str(v), mins, log)
                if sys.platform == "darwin"
                else schedule.render_cron_line(cairn, str(v), mins, log)
            )
            typer.echo(rendered)
            return
        schedule.install(mins, v)
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint="--interval") from e
    except RuntimeError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(f"Scheduled `cairn sweep` every {mins}m for vault {v}.")


@schedule_app.command("uninstall")
def schedule_uninstall() -> None:
    from cairn import schedule

    try:
        removed = schedule.uninstall()
    except RuntimeError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo("Removed agentcairn schedule." if removed else "No agentcairn schedule found.")


@schedule_app.command("status")
def schedule_status() -> None:
    from cairn import schedule

    st = schedule.status()
    typer.echo(
        f"installed: runs `cairn sweep` every {st['interval_min']}m" if st else "not installed"
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


def _resolve_harnesses(harness_opt: str | None, env: Mapping[str, str]) -> list[str] | None:
    """Resolve which harnesses to ingest. --harness flag wins, else
    CAIRN_HARNESSES, else None (auto-detect every present harness). A comma list
    is split and trimmed; an all-whitespace/empty value is treated as unset."""
    raw = harness_opt if (harness_opt and harness_opt.strip()) else env.get("CAIRN_HARNESSES")
    if not raw or not raw.strip():
        return None
    return [h.strip() for h in raw.split(",") if h.strip()]


def _relink_note(path: Path, desired: list[str], *, dry_run: bool = False) -> str:
    """Set/clear a note's `related:` frontmatter to `desired` (a list of "[[permalink]]"
    strings). Writes only when it differs from the current value. Returns one of
    "linked" (set/changed), "cleared" (removed a stale list), or "unchanged". The tool
    owns the `related:` field; body and other frontmatter are preserved via the
    parse_note->write_note fixpoint."""
    note = parse_note(path.read_text(encoding="utf-8"))
    current = note.frontmatter.get("related")
    if desired:
        if current == desired:
            return "unchanged"
        note.frontmatter["related"] = desired
        if not dry_run:
            atomic_write_text(path, write_note(note))
        return "linked"
    # desired is empty
    if "related" in note.frontmatter:
        if not dry_run:
            del note.frontmatter["related"]
            atomic_write_text(path, write_note(note))
        return "cleared"
    return "unchanged"


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
        None,
        "--index",
        help="Index .duckdb path (default: ~/.cache/agentcairn/indexes/<vault_key>.duckdb).",
    ),
    embedder: str = typer.Option(
        None,
        "--embedder",
        help="'fastembed' or 'fake'; 'ollama' (CAIRN_EMBED_MODEL/OLLAMA_HOST) "
        "(default: CAIRN_EMBEDDER setting or fastembed).",
    ),
) -> None:
    """Reconcile the DuckDB index with the vault (incremental)."""
    embedder = embedder or cairn_env().get("CAIRN_EMBEDDER") or "fastembed"
    idx = paths.index_for(index, vault)
    ensure_private_dir(idx.parent)
    emb = get_embedder(embedder)
    try:
        with vault_writer_lock(vault, operation="cli-reindex"):
            con = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
            try:
                stats = reconcile(con, str(vault), emb)
            finally:
                con.close()  # release DuckDB even if reconcile fails
    except VaultBusyError as exc:
        _exit_vault_busy(exc)
    typer.echo(
        f"reindexed: {stats.added} note(s) added, {stats.updated} updated, "
        f"{stats.deleted} removed{' (full rebuild)' if stats.rebuilt else ''}"
    )


@app.command(name="index-status")
def index_status(
    index: Path = typer.Option(None, "--index", help="Index .duckdb path."),
    vault: Path = typer.Option(
        None,
        "--vault",
        help="Vault dir; the index is derived from it (default: CAIRN_VAULT or ~/agentcairn).",
    ),
) -> None:
    """Show index location, embedding model, and row counts."""
    idx = paths.index_for(index, paths.resolve_vault(vault))
    if not idx.exists():
        typer.echo(f"no index at {idx}")
        raise typer.Exit(1)
    import duckdb

    con = duckdb.connect(str(idx), read_only=True)
    try:
        n = con.execute("SELECT count(*) FROM notes").fetchone()[0]
        c = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
        typer.echo(f"index: {idx}")
        typer.echo(
            f"model: {get_meta(con, 'embedding_model')} (dim {get_meta(con, 'embedding_dim')})"
        )
        typer.echo(f"notes: {n}")
        typer.echo(f"chunks: {c}")
    finally:
        con.close()


@app.command()
def recall(
    query: str = typer.Argument(..., help="What to search for."),
    index: Path = typer.Option(None, "--index", help="Index .duckdb path."),
    vault: Path = typer.Option(
        None,
        "--vault",
        help="Vault dir; the index is derived from it (default: CAIRN_VAULT or ~/agentcairn).",
    ),
    embedder: str = typer.Option(
        None,
        "--embedder",
        help="'fastembed' (hybrid) or 'fake'; 'none' = BM25-only; 'ollama' (CAIRN_EMBED_MODEL/OLLAMA_HOST) (default: CAIRN_EMBEDDER setting or fastembed).",  # noqa: E501
    ),
    k: int = typer.Option(10, "--k", help="Number of results."),
    rerank: bool | None = typer.Option(
        None,
        "--rerank/--no-rerank",
        help="Cross-encoder rerank (default on; or set CAIRN_RERANK=0).",
    ),
    project: str = typer.Option(
        None, "--project", help="Boost this project's memories (default: current dir)."
    ),
    scope: str = typer.Option(
        "all", "--scope", help="'all' (boost, non-lossy) or 'project' (hard-filter)."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit results as JSON (for tooling/plugins)."
    ),
) -> None:
    """Hybrid recall over the index (semantic + BM25 + graph-boost).

    Validity-aware: current facts rank above superseded/expired ones (set
    `superseded_by`/`valid_until` in note frontmatter). Reranked by default
    (`CAIRN_RERANK=0` to disable).
    """
    embedder = embedder or cairn_env().get("CAIRN_EMBEDDER") or "fastembed"
    idx = paths.index_for(index, paths.resolve_vault(vault))
    if not idx.exists():
        typer.echo(f"no index at {idx} — run `cairn reindex <vault>` first")
        raise typer.Exit(1)
    emb = None if embedder == "none" else get_embedder(embedder)
    current = resolve_current_project(project)
    con = open_search(str(idx))
    hits = search(
        con, query, embedder=emb, k=k, rerank=resolve_rerank(rerank), project=current, scope=scope
    )
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
    if json_out:
        typer.echo(
            json.dumps(
                [
                    {
                        "permalink": h.permalink,
                        "title": h.heading_path,
                        "text": h.snippet,
                        "score": h.score,
                        "project": h.project,
                    }
                    for h in hits
                ],
                ensure_ascii=False,
            )
        )
        return
    if not hits:
        typer.echo("(no results)")
        return
    for h in hits:
        mark = f"  [from: {h.project}]" if h.project and h.project != current else ""
        typer.echo(f"[{h.score:.3f}] {h.permalink}  ·  {h.heading_path}{mark}")
        typer.echo(f"        {h.snippet.strip()[:160]}")


@app.command("recall-hook")
def recall_hook(
    vault: Path = typer.Option(
        None, "--vault", help="Vault dir (default: CAIRN_VAULT or ~/agentcairn)."
    ),
    index: Path = typer.Option(
        None, "--index", help="Index .duckdb path (default: derived from vault)."
    ),
    embedder: str = typer.Option(
        None, "--embedder", help="'fastembed' (default), 'fake' (tests), or 'none' (BM25)."
    ),
) -> None:
    """Auto-recall for the Claude Code UserPromptSubmit hook (internal).

    Reads the hook JSON payload from stdin, runs a hybrid recall against the
    prompt, and prints the additionalContext envelope (or nothing). Always
    exits 0 — never blocks or breaks a prompt.
    """
    try:
        import sys

        from cairn import recall_hook as _rh

        out = _rh.run(sys.stdin.read(), vault=vault, index=index, embedder_name=embedder)
        if out:
            typer.echo(out)
    except Exception:
        pass


@app.command()
def recent(
    index: Path = typer.Option(None, "--index", help="Index .duckdb path."),
    vault: Path = typer.Option(
        None,
        "--vault",
        help="Vault dir; the index is derived from it (default: CAIRN_VAULT or ~/agentcairn).",
    ),
    project: str = typer.Option(
        None, "--project", help="Only notes whose path contains this substring."
    ),
    n: int = typer.Option(10, "-n", "--num", help="Number of notes."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for machine parsing."),
) -> None:
    """Most-recently-modified notes (optionally filtered to a project path substring)."""
    idx = paths.index_for(index, paths.resolve_vault(vault))
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
    target = path or Path(cairn_env().get("CAIRN_VAULT") or (Path.home() / "agentcairn"))
    target = target.expanduser()
    try:
        with vault_writer_lock(target, operation="cli-init"):
            ensure_private_dir(target)
            obs = target / ".obsidian"
            ensure_private_dir(obs)
            app_json = obs / "app.json"
            if not app_json.exists():
                atomic_write_text(app_json, "{}\n")
            welcome = target / "welcome.md"
            existed = welcome.exists()
            if not existed:
                atomic_write_text(welcome, _WELCOME)
    except VaultBusyError as exc:
        _exit_vault_busy(exc)
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
    embedder = cairn_env().get("CAIRN_EMBEDDER") or "fastembed"
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
        None,
        help="Host id: claude-code / codex / antigravity (plugins) · cursor / claude-desktop / "
        "vscode / gemini / opencode (mcp).",
    ),
    all_hosts: bool = typer.Option(False, "--all", help="Configure every detected host."),
    print_only: bool = typer.Option(
        False, "--print", help="Print the config/commands; write nothing."
    ),
    vault: Path = typer.Option(
        None,
        "--vault",
        help="Vault path (mcp hosts; default ~/agentcairn). The index is derived from the vault.",
    ),
    source: str = typer.Option(
        None,
        "--source",
        help="Plugin source for plugin hosts (default the marketplace; Antigravity's "
        "`agy plugin install` needs a local dir, so pass --source <path>/plugin).",
    ),
) -> None:
    """Install agentcairn into another agent: the plugin for plugin hosts
    (Claude Code, Codex, Antigravity), or the MCP server config for MCP hosts (Cursor, …)."""
    from cairn.hosts import HOSTS, detected_hosts, get_host
    from cairn.hosts.entry import mcp_entry, opencode_mcp_entry
    from cairn.hosts.plugins import (
        install_plugin,
        migrate_antigravity_mcp_block,
        migrate_codex_mcp_block,
    )
    from cairn.hosts.skills import install_skill
    from cairn.hosts.writers import write_host

    settings = cairn_env()
    default_vault = Path(settings.get("CAIRN_VAULT") or (Path.home() / "agentcairn"))
    v = str((vault or default_vault).expanduser().resolve())
    ids = ", ".join(h.id for h in HOSTS)

    if host is None and not all_hosts:  # detect + preview, write nothing
        present = detected_hosts()
        if not present:
            typer.echo(f"No supported agents detected. Supported: {ids}")
            return
        typer.echo("Detected — run `cairn install <id>` (or `--all`):")
        for h in present:
            where = f"plugin via `{h.cli}`" if h.kind == "plugin" else str(h.config_path())
            typer.echo(f"  {h.id:15} {h.label}  → {where}")
        return

    if all_hosts:
        targets = detected_hosts()
        if not targets:
            typer.echo(f"No supported agents detected. Supported: {ids}")
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
            if h.kind == "plugin":
                if vault:
                    typer.echo(
                        f"  note: --vault doesn't apply to {h.label} (set in the plugin's config)"
                    )
                # Codex/Claude accept the git marketplace ref as a default; Antigravity's
                # `agy plugin install` cannot fetch a git repo, so it requires --source.
                plugin_source = source or (None if h.id == "antigravity" else "ccf/agentcairn")
                if plugin_source is None:
                    raise ValueError(
                        f"{h.label} needs --source <dir>: `agy plugin install` takes a local "
                        "directory (or a registered marketplace), not a git repo. Clone the "
                        "repo and run `cairn install antigravity --source <path>/plugin`."
                    )
                out = install_plugin(h, source=plugin_source, dry=print_only)
                header = f"# {h.label} (plugin via `{h.cli}`)" if print_only else f"✓ {h.label}:"
                typer.echo(header)
                typer.echo(out)
                # Strip a stale agentcairn MCP entry only AFTER a successful install
                # (install_plugin raises on failure), so an aborted install leaves the
                # user's existing MCP wiring intact rather than half-removed.
                migrators = {
                    "codex": migrate_codex_mcp_block,
                    "antigravity": migrate_antigravity_mcp_block,
                }
                migrate = migrators.get(h.id)
                if migrate is not None:
                    note = migrate(h.config_path(), dry=print_only)
                    if note:
                        typer.echo(f"  {note}")
            else:
                entry = opencode_mcp_entry(v) if h.id == "opencode" else mcp_entry(v)
                out = write_host(h, entry, dry=print_only)
                if print_only:
                    typer.echo(f"# {h.label} ({h.config_path()})")
                    typer.echo(out)
                else:
                    typer.echo(f"✓ {h.label}: {out}")
                if h.skill_dir is not None:
                    note = install_skill(Path(h.skill_dir).expanduser(), dry=print_only)
                    typer.echo(f"  {note}")
                if h.id == "opencode":
                    from cairn.hosts.opencode import install_opencode_plugin

                    note = install_opencode_plugin(h.config_path().parent, vault=v, dry=print_only)
                    typer.echo(f"  {note}")
                if h.kind != "plugin":
                    from cairn.hosts.plugins import migrate_stale_cairn_index

                    fmt = "toml" if h.format == "toml" else "json"
                    if not print_only:
                        migrate_stale_cairn_index(h.config_path(), fmt=fmt, root_key=h.root_key)
        except Exception as e:  # best-effort per host; continue under --all
            failures += 1
            typer.echo(f"✗ {h.label}: {e}")
    if not print_only and failures < len(targets):
        typer.echo(
            "Tip: run `cairn schedule install` to capture sessions periodically in "
            "the background. Useful for more timely ingestion of memories from "
            "long-running sessions."
        )
    if failures:
        raise typer.Exit(1)


@app.command()
def config(
    init: bool = typer.Option(False, "--init", help="Write a commented template config file."),
) -> None:
    """Show every setting's effective value and source (env / file / default),
    or scaffold ~/.agentcairn/config.toml with --init."""
    from cairn.config import KNOBS, _config_path, config_file_values

    path = _config_path()
    if init:
        if path.exists():
            typer.echo(f"config file already exists: {path}")
            return
        lines = [
            "# agentcairn configuration — env vars override these values.",
            "# Uncomment a line to set it. Docs: https://github.com/ccf/agentcairn",
            "",
        ]

        def _bare(default: str) -> bool:
            """TOML booleans/numbers must be emitted unquoted to stay valid."""
            if default in ("true", "false"):
                return True
            try:
                float(default)
                return True
            except ValueError:
                return False

        for k in KNOBS:
            lines.append(f"# {k.description}")
            if _bare(k.default):
                lines.append(f"# {k.key} = {k.default}")
            else:
                lines.append(f'# {k.key} = "{k.default}"')
            lines.append("")
        atomic_write_text(path, "\n".join(lines))
        import cairn.config as _cfg

        _cfg._reset()
        typer.echo(f"wrote {path} (mode 0600) — uncomment lines to configure")
        return

    file_vals = config_file_values()
    typer.echo(f"config file: {path}{'' if path.exists() else ' (not present)'}")
    for k in KNOBS:
        if k.env in os.environ:
            value, source = os.environ[k.env], "env"
        elif k.env in file_vals:
            value, source = file_vals[k.env], "file"
        else:
            value, source = k.default, "default"
        if k.secret and value:
            value = f"{value[:7]}…{value[-4:]}" if len(value) > 20 else "…set…"
        typer.echo(f"  {k.key:18} = {value:42} [{source}]")


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
    """Surface a degraded LLM judge run. Two failure modes, both easy to miss:
    the tier never resolved (no key), or it resolved but every batch failed and
    fell back (e.g. the timeout was too low) — the latter kept the tier "llm" so
    the old tier-only check stayed silent while the whole run used the fallback."""
    if cairn_env().get("CAIRN_JUDGE") != "anthropic":
        return
    if rep.judge_tier != "llm":
        # Source-agnostic wording: the setting may come from env OR config file.
        typer.echo(
            "  note: judge=anthropic configured but LLM tier unavailable (missing key?) "
            f"— used {rep.judge_tier}"
        )
    elif rep.judge_degraded:
        typer.secho(
            f"  ⚠ LLM judge degraded: {rep.judge_degraded} candidate(s) fell back to a "
            "weaker tier (the batch call failed — raise judge_timeout or check API "
            "connectivity). Those turns were NOT distilled.",
            fg=typer.colors.YELLOW,
        )


@app.command()
def sweep(
    vault: Path = typer.Option(..., "--vault", help="Vault root."),
    transcripts_dir: Path = typer.Option(
        None, "--transcripts-dir", help="Override the ~/.claude/projects root."
    ),
    project: str = typer.Option(None, "--project", help="Absolute cwd filter (default: all)."),
    harness: str = typer.Option(
        None,
        "--harness",
        help="Comma list of harnesses to ingest (default: CAIRN_HARNESSES or "
        "auto-detect every present harness, e.g. 'claude-code,codex').",
    ),
    threshold: float = typer.Option(0.5, "--threshold", help="Importance keep-threshold."),
    index: Path = typer.Option(
        None,
        "--index",
        help="Index .duckdb path (default: ~/.cache/agentcairn/indexes/<vault_key>.duckdb).",
    ),
    embedder: str = typer.Option(
        None,
        "--embedder",
        help="'fastembed' or 'fake'; 'ollama' (CAIRN_EMBED_MODEL/OLLAMA_HOST) "
        "(default: CAIRN_EMBEDDER setting or fastembed).",
    ),
    ledger: Path = typer.Option(
        None,
        "--ledger",
        help="Dedup ledger path (default: ~/.cache/agentcairn/ledgers/<hash>.sha256).",
    ),
) -> None:
    """Batch-ingest transcripts into the vault, then reindex (cron maintenance).

    Run `cairn schedule install` to run this automatically on a schedule.
    """
    embedder = embedder or cairn_env().get("CAIRN_EMBEDDER") or "fastembed"
    led_path = ledger if ledger is not None else paths.default_ledger(vault)
    selected = _resolve_harnesses(harness, cairn_env())
    if transcripts_dir is not None and (selected is None or len(selected) != 1):
        raise typer.BadParameter("--transcripts-dir requires exactly one --harness")
    if transcripts_dir is not None:
        refs = find_transcripts(harness=selected[0], root=transcripts_dir, project=project)
    else:
        refs = find_transcripts(harness=None, harnesses=selected, project=project)
    transcripts = [parse_transcript(ref) for ref in refs]
    # One embedder serves the judge, consolidation neighbor queries, and reindex
    # (avoid a double model load).
    emb = get_embedder(embedder)
    idx = paths.index_for(index, vault)
    ensure_private_dir(idx.parent)
    consolidator = resolve_consolidator()
    try:
        # Serialize the complete vault+ledger+index mutation. A second sweep
        # must not distill from a half-written first sweep.
        with vault_writer_lock(vault, operation="cli-sweep"):
            # Load the ledger only after acquiring the lock. Otherwise a process
            # that began while another sweep was active could later acquire the
            # lock with a stale in-memory hash set and duplicate its writes.
            led = DedupLedger(led_path)
            # This subdir must match the one ingest_transcripts writes.
            neighbor_index = (
                _DistilledNeighborIndex(vault_root=vault, subdir="memories", embedder=emb)
                if consolidator is not None
                else None
            )
            rep = ingest_transcripts(
                transcripts,
                vault_root=vault,
                ledger=led,
                threshold=threshold,
                judge=resolve_judge(embedder=emb),
                judged_cache=JudgedCache(
                    led_path.parent / f"{paths.vault_key(vault)}.judged.jsonl"
                ),
                consolidator=consolidator,
                neighbor_index=neighbor_index,
            )
            con = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
            try:
                stats = reconcile(con, str(vault), emb)
            finally:
                con.close()  # release DuckDB even if reconcile fails
    except VaultBusyError as exc:
        _exit_vault_busy(exc)
    extra = ""
    if rep.semantic_deduped or rep.superseded:
        extra = f"; {rep.semantic_deduped} deduped, {rep.superseded} superseded"
    typer.echo(
        f"swept: {len(rep.written)} memory note(s) written{extra}; reindexed "
        f"{stats.added} added, {stats.updated} updated, {stats.deleted} removed"
    )
    _warn_if_llm_tier_unavailable(rep)


@app.command()
def doctor(
    index: Path = typer.Option(
        None,
        "--index",
        help="Index .duckdb path (default: ~/.cache/agentcairn/indexes/<vault_key>.duckdb).",
    ),
    vault: Path = typer.Option(
        None,
        "--vault",
        help="Vault dir; the index is derived from it (default: CAIRN_VAULT or ~/agentcairn).",
    ),
) -> None:
    """Health-check the index: model/dim, row counts, embedding/chunk parity."""
    import duckdb

    vault_dir = paths.resolve_vault(vault)
    idx = paths.index_for(index, vault_dir)
    if not idx.exists():
        typer.echo(f"no index at {idx} — run `cairn reindex <vault>` first")
        raise typer.Exit(1)
    con = duckdb.connect(str(idx), read_only=True)
    notes = con.execute("SELECT count(*) FROM notes").fetchone()[0]
    chunks = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
    embs = con.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0]
    model = get_meta(con, "embedding_model")
    dim = get_meta(con, "embedding_dim")
    indexed_paths = [row[0] for row in con.execute("SELECT path FROM notes").fetchall()]
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
    # Drift: index vs on-disk vault. Dead paths or unindexed notes mean the index
    # was built against a different/stale vault (the 2026-06-17 footgun).
    # The dead-path check is index-intrinsic (always valid). The on-disk-unindexed
    # half assumes the index belongs to `vault_dir` — only true when the index is the
    # vault-derived default. With an explicit/decoupled --index (or CAIRN_INDEX)
    # pointing elsewhere, comparing against vault_dir would report misleading DRIFT,
    # so we skip that half and don't suggest reindexing the wrong vault.
    coupled = idx == paths.default_index(vault_dir)
    indexed_missing = sum(1 for p in indexed_paths if p and not Path(p).exists())
    disk_unindexed = 0
    if coupled and vault_dir.exists():
        on_disk = {str(p.resolve()) for p in vault_dir.rglob("*.md")}
        indexed_set = {str(Path(p).resolve()) for p in indexed_paths if p}
        disk_unindexed = len(on_disk - indexed_set)
    if indexed_missing or disk_unindexed:
        parts = [f"{indexed_missing} indexed note(s) missing on disk"]
        if coupled:
            parts.append(f"{disk_unindexed} on-disk note(s) unindexed")
        remedy = f"cairn reindex {vault_dir}" if coupled else "cairn reindex <the index's vault>"
        typer.echo(f"status: DRIFT — {', '.join(parts)}. Fix: {remedy}")
        raise typer.Exit(1)
    ok = "status: OK" if coupled else "status: OK (index/vault decoupled — coverage check skipped)"
    typer.echo(ok)


@app.command()
def link(
    vault: Path = typer.Option(
        None,
        "--vault",
        help="Vault dir; the index is derived from it (default: CAIRN_VAULT or ~/agentcairn).",
    ),
    index: Path = typer.Option(
        None,
        "--index",
        help="Index .duckdb path (default: derived from the vault).",
    ),
    top: int = typer.Option(5, "--top", help="Max neighbors to link per note."),
    min_score: float = typer.Option(0.6, "--min-score", help="Minimum cosine to link a neighbor."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would change; write nothing."
    ),
) -> None:
    """Write each note's top semantic neighbors into a `related:` frontmatter list of
    [[wikilinks]] (populates the Obsidian graph). Opt-in and idempotent; re-run to refresh.
    Reads the current index — run `cairn sweep`/`reindex` first for fresh results."""
    vault_dir = paths.resolve_vault(vault)
    idx = paths.index_for(index, vault_dir)
    if not idx.exists():
        typer.echo(f"no index at {idx} — run `cairn reindex <vault>` first")
        raise typer.Exit(1)
    linked = unchanged = cleared = errors = 0
    try:
        # A real link run mutates many notes and must serialize with sweep,
        # remember, ingest, and reconciliation. A dry run remains read-only.
        lock = nullcontext() if dry_run else vault_writer_lock(vault_dir, operation="cli-link")
        with lock:
            con = open_search(str(idx))
            try:
                rows = con.execute(
                    "SELECT permalink, path FROM notes WHERE superseded_by IS NULL"
                ).fetchall()
                for permalink, path in rows:
                    if not path:
                        continue
                    try:
                        nbrs = semantic_neighbors(con, permalink, k=top, min_score=min_score)
                        desired = [f"[[{n['permalink']}]]" for n in nbrs]
                        status = _relink_note(Path(path), desired, dry_run=dry_run)
                    except Exception as exc:  # best-effort per note
                        errors += 1
                        typer.echo(f"  skip {permalink}: {exc}")
                        continue
                    if status == "linked":
                        linked += 1
                    elif status == "cleared":
                        cleared += 1
                    else:
                        unchanged += 1
            finally:
                con.close()
    except VaultBusyError as exc:
        _exit_vault_busy(exc)
    prefix = "[dry-run] " if dry_run else ""
    suffix = f" · {errors} errors" if errors else ""
    typer.echo(f"{prefix}linked {linked} · unchanged {unchanged} · cleared {cleared}{suffix}")


@app.command()
def ingest(
    vault: Path = typer.Option(..., "--vault", help="Vault root to write derived notes into."),
    transcripts_dir: Path = typer.Option(
        None, "--transcripts-dir", help="Override the ~/.claude/projects root."
    ),
    project: str = typer.Option(
        None, "--project", help="Absolute cwd to filter transcripts to (default: all)."
    ),
    harness: str = typer.Option(
        None,
        "--harness",
        help="Comma list of harnesses to ingest (default: CAIRN_HARNESSES or "
        "auto-detect every present harness, e.g. 'claude-code,codex').",
    ),
    threshold: float = typer.Option(0.5, "--threshold", help="Importance keep-threshold."),
    ledger: Path = typer.Option(
        None, "--ledger", help="Dedup ledger path (default: <vault>/.cairn/ingested.sha256)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report without writing (LLM judge is skipped on dry runs)."
    ),
    embedder: str = typer.Option(
        None,
        "--embedder",
        help="Embedder for the durability judge (mirrors sweep) "
        "(default: CAIRN_EMBEDDER setting or fastembed).",
    ),
) -> None:
    """Ingest Claude Code transcripts into non-lossy derived memory notes."""
    embedder = embedder or cairn_env().get("CAIRN_EMBEDDER") or "fastembed"
    # Keep ledger OUTSIDE the vault (dedup.py docstring + spec). Namespace
    # by vault path so different vaults use separate ledgers.
    led_path = ledger if ledger is not None else paths.default_ledger(vault)
    selected = _resolve_harnesses(harness, cairn_env())
    if transcripts_dir is not None and (selected is None or len(selected) != 1):
        raise typer.BadParameter("--transcripts-dir requires exactly one --harness")
    if transcripts_dir is not None:
        refs = find_transcripts(harness=selected[0], root=transcripts_dir, project=project)
    else:
        refs = find_transcripts(harness=None, harnesses=selected, project=project)
    if not refs:
        typer.echo("No transcripts found.")
        return
    transcripts = [parse_transcript(ref) for ref in refs]
    # Same --embedder flag as sweep, so the judge scores in the same embedding
    # space regardless of which command ingests (lazy: tier "none" loads nothing).
    loader = lambda: get_embedder(embedder)  # noqa: E731
    if dry_run:
        # A preview must not spend LLM tokens: force the judge tier below anthropic
        # (embedding unless explicitly disabled).
        env = dict(cairn_env())
        if env.get("CAIRN_JUDGE", "embedding") != "none":
            env["CAIRN_JUDGE"] = "embedding"
        judge = resolve_judge(env=env, embedder_loader=loader)
    else:
        judge = resolve_judge(embedder_loader=loader)

    def _run_ingest():
        # Both caches are read only after the writer lock is held. Loading them
        # before waiting could let a second process ingest against stale state.
        return ingest_transcripts(
            transcripts,
            vault_root=vault,
            ledger=DedupLedger(led_path),
            threshold=threshold,
            judge=judge,
            judged_cache=JudgedCache(led_path.parent / f"{paths.vault_key(vault)}.judged.jsonl"),
            dry_run=dry_run,
        )

    if dry_run:
        rep = _run_ingest()
    else:
        try:
            with vault_writer_lock(vault, operation="cli-ingest"):
                rep = _run_ingest()
        except VaultBusyError as exc:
            _exit_vault_busy(exc)
    prefix = "[dry-run] " if dry_run else ""
    summaries_part = f"{rep.summaries} summaries · " if rep.summaries else ""
    typer.echo(
        f"{prefix}{rep.authored} authored · {summaries_part}{rep.candidates} candidates · "
        f"{rep.redactions} redactions · {rep.deduped} deduped · "
        f"{rep.gated_out} gated · {len(rep.written)} written · judge: {rep.judge_tier}"
        + (f" ({rep.judge_degraded} degraded)" if rep.judge_degraded else "")
    )
    skipped = {k: v for k, v in rep.event_kinds.items() if k != "authored_user"}
    # `compact_summary` events that were promoted to session-summary notes aren't skips.
    if "compact_summary" in skipped:
        remaining = skipped["compact_summary"] - rep.summaries
        if remaining > 0:
            skipped["compact_summary"] = remaining
        else:
            del skipped["compact_summary"]
    if skipped:
        breakdown = ", ".join(f"{v} {k}" for k, v in sorted(skipped.items(), key=lambda kv: -kv[1]))
        typer.echo(f"  skipped (non-authored): {breakdown}")
    if not dry_run:  # dry runs force the tier down on purpose — no warning
        _warn_if_llm_tier_unavailable(rep)
