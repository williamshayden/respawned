"""Serve the bundled browser interface without a frontend runtime."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles


BUNDLED_UI_DIR = Path(__file__).resolve().parents[1] / "web_assets"


def mount_review_assets(app: FastAPI, directory: str | None = None) -> None:
    """Mount after API routes; an absolute override or ``off`` is optional."""
    selected = directory.strip() if directory is not None else ""
    if selected.lower() == "off":
        return
    path = Path(selected) if selected else BUNDLED_UI_DIR
    if not path.is_absolute():
        raise RuntimeError("RESPAWNED_UI_DIST must be an absolute path to the built UI directory, or off")
    if not path.is_dir() or not (path / "index.html").is_file():
        if not selected:
            raise RuntimeError("Bundled Respawned UI is missing; rebuild it with npm run bundle in web/")
        raise RuntimeError("RESPAWNED_UI_DIST must be a directory containing the built UI index.html")
    app.mount("/", StaticFiles(directory=path, html=True), name="review-assets")
