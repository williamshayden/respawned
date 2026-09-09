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


def test_print_table_renders_heading_columns_and_rows():
    from respawned.cli.ui import print_table

    output = StringIO()
    print_table(
        title="Pending follow-ups",
        columns=("Customer", "Score"),
        rows=(("Jamie", 42),),
        console=_plain_console(output),
    )

    rendered = output.getvalue()
    assert "Pending follow-ups" in rendered
    assert "Customer" in rendered
    assert "Score" in rendered
    assert "Jamie" in rendered
    assert "42" in rendered


def test_prompt_choice_uses_rich_choices_and_returns_selected_action():
    from respawned.cli.ui import prompt_choice

    output = StringIO()
    answer = prompt_choice(
        "Action",
        choices=("a", "r", "m", "s"),
        default="s",
        console=_plain_console(output),
        stream=StringIO("a\n"),
    )

    assert answer == "a"
    assert "Action [a/r/m/s] (s):" in output.getvalue()


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
