from io import StringIO

from rich.console import Console


def _plain_console(output: StringIO) -> Console:
    return Console(
        file=output,
        record=True,
        force_terminal=False,
        no_color=True,
        width=100,
    )


def test_prompt_choice_can_hide_metadata_and_normalize_case():
    from respawned.cli.ui import prompt_choice

    for raw_answer, expected in (("E\n", "e"), ("", "s")):
        output = StringIO()
        answer = prompt_choice(
            "Action",
            choices=("a", "r", "e", "s"),
            default="s",
            console=_plain_console(output),
            stream=StringIO(raw_answer),
            case_sensitive=False,
            show_choices=False,
            show_default=False,
        )

        assert answer == expected
        assert output.getvalue() == "Action: "
