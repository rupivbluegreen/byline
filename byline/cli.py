"""Typer CLI surface exposing `audit`, `baseline`, and `scan`. See spec §4."""

import typer

app = typer.Typer(
    name="byline",
    help="Comparative attribution analysis for take-home submissions.",
    no_args_is_help=True,
)
