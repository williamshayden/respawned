"""Optional source adapters for canonical ingestion."""

from respawned.adapters.legacy_seed import (
    LegacySeedBatch,
    LegacySeedError,
    load_legacy_seed,
)

__all__ = ["LegacySeedBatch", "LegacySeedError", "load_legacy_seed"]
