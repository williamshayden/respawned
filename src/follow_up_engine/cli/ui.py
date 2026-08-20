"""Small Rich primitives shared by interactive CLI commands."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TextIO

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table


DEFAULT_CONSOLE = Console()


def print_table(
    *,
    title: str,
    columns: Sequence[str],
    rows: Iterable[Sequence[object]],
    console: Console = DEFAULT_CONSOLE,
) -> Table:
    """Render a simple table and return it for optional caller customization."""
    table = Table(title=title)
    for column in columns:
        table.add_column(column)
    for row in rows:
        table.add_row(*(str(value) for value in row))
    console.print(table)
    return table


def prompt_choice(
    prompt: str,
    *,
    choices: Sequence[str],
    default: str | None = None,
    console: Console = DEFAULT_CONSOLE,
    stream: TextIO | None = None,
) -> str:
    """Ask for one of ``choices`` using Rich's validated prompt."""
    arguments = {
        "choices": list(choices),
        "console": console,
        "stream": stream,
    }
    if default is not None:
        arguments["default"] = default
    return str(Prompt.ask(prompt, **arguments))
