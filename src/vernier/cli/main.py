"""CLI entrypoint for the ``vern`` console script."""

import typer

app = typer.Typer(
    name="vern",
    help="Vernier: deterministic capture, replay, and divergence classification.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Vernier: deterministic capture, replay, and divergence classification."""
