# SPDX-License-Identifier: Apache-2.0
"""The `cairn` command-line interface."""

from __future__ import annotations

import typer

from cairn import __version__

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
