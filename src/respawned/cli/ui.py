"""Small Rich primitives shared by interactive CLI commands."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TextIO

from rich.console import Console
from rich.prompt import Prompt


DEFAULT_CONSOLE = Console()


def prompt_choice(
    prompt: str,
    *,
    choices: Sequence[str],
    default: str | None = None,
    console: Console = DEFAULT_CONSOLE,
    stream: TextIO | None = None,
    case_sensitive: bool = True,
    show_choices: bool = True,
    show_default: bool = True,
) -> str:
    """Ask for one of ``choices`` using Rich's validated prompt."""
    arguments = {
        "choices": list(choices),
        "console": console,
        "stream": stream,
        "case_sensitive": case_sensitive,
        "show_choices": show_choices,
        "show_default": show_default,
    }
    if default is not None:
        arguments["default"] = default
    return str(Prompt.ask(prompt, **arguments))
