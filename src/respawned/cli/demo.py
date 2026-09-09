"""Load bundled or explicitly selected fixtures through canonical ingestion."""

from pathlib import Path

from respawned.adapters import load_legacy_seed
from respawned.core.ingest import IngestResult, ingest_records
from respawned.db.helpers.pg_connect import create_tables, get_engine


def load_demo(seed_dir: Path) -> IngestResult:
    batch = load_legacy_seed(
        seed_dir / "quotes.json",
        seed_dir / "events.jsonl",
    )
    engine = get_engine()
    try:
        create_tables(engine)
        with engine.begin() as connection:
            return ingest_records(
                connection,
                opportunities=batch.opportunities,
                activities=batch.activities,
            )
    finally:
        engine.dispose()
