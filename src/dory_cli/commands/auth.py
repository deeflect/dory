"""Auth command group for Dory CLI — manage bearer tokens."""
from __future__ import annotations

import typer

from dory_cli._internals import _get_config
from dory_http.auth import issue_token


def register(auth_app: typer.Typer) -> None:
    """Register auth commands on the given Typer sub-app."""

    @auth_app.command("new")
    def auth_new(
        ctx: typer.Context,
        name: str = typer.Argument(...),
    ) -> None:
        config = _get_config(ctx)
        token = issue_token(name, config.auth_tokens_path)
        typer.echo(token)
