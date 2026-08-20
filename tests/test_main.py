import importlib


def test_main_loads_seed_files_from_environment(monkeypatch):
    entrypoint = importlib.import_module("follow_up_engine.__main__")
    loaded = {}

    def fake_load_data(quotes_source, events_source):
        loaded["quotes"] = quotes_source
        loaded["events"] = events_source

    monkeypatch.setattr(entrypoint, "load_data", fake_load_data)
    monkeypatch.setenv("SEED_DIR", "/seed")
    monkeypatch.setenv("QUOTES_FILENAME", "quotes.json")
    monkeypatch.setenv("EVENTS_FILENAME", "events.jsonl")

    entrypoint.main()

    assert loaded == {
        "quotes": {"type": "json", "path": "/seed/quotes.json"},
        "events": {"type": "json", "path": "/seed/events.jsonl"},
    }
