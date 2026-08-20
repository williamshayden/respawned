import os
from pathlib import Path

from follow_up_engine.cli.load_data import load_data


def main() -> None:
    seed_dir = Path(os.getenv("SEED_DIR", "seed"))
    quotes_filename = os.getenv("QUOTES_FILENAME", "quotes.json")
    events_filename = os.getenv("EVENTS_FILENAME", "events.jsonl")

    load_data(
        quotes_source={"type": "json", "path": str(seed_dir / quotes_filename)},
        events_source={"type": "json", "path": str(seed_dir / events_filename)},
    )


if __name__ == "__main__":
    main()
