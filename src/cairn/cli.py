# SPDX-License-Identifier: Apache-2.0
"""The `cairn` command-line interface."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import typer

from cairn import __version__
from cairn.vault import parse_note

app = typer.Typer(no_args_is_help=True, add_completion=False, help="agentcairn — local-first agent memory.")


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


@app.command()
def parse(file: Path = typer.Argument(..., exists=True, readable=True, help="Markdown note to parse.")) -> None:
    """Parse a markdown note and print its structured form as JSON."""
    note = parse_note(file.read_text())
    typer.echo(json.dumps(dataclasses.asdict(note), indent=2, default=str))
