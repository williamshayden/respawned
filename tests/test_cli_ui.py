from io import StringIO

from rich.console import Console


def _plain_console(output: StringIO) -> Console:
    return Console(
        file=output,
        force_terminal=False,
        no_color=True,
        width=100,
    )


def test_print_table_renders_heading_columns_and_rows():
    from follow_up_engine.cli.ui import print_table

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
    from follow_up_engine.cli.ui import prompt_choice

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
