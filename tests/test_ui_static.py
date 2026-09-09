from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from respawned.api.static import BUNDLED_UI_DIR, mount_review_assets
from respawned.api.ui import create_ui_router
from respawned.cli.common import DEFAULT_POLICY_PATH
from respawned.core.policy import load_policy


def _api():
    app = FastAPI()

    def unexpected_dependency():
        pytest.fail("Static UI or authorization accessed a database or drafting provider")

    app.include_router(create_ui_router(
        unexpected_dependency,
        lambda: load_policy(DEFAULT_POLICY_PATH),
        unexpected_dependency,
    ))
    return app


@pytest.fixture
def built_ui(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (tmp_path / "index.html").write_text(
        '<!doctype html><title>Review</title><script src="/assets/app.js"></script>',
        encoding="utf-8",
    )
    (assets / "app.js").write_text('document.title = "Review ready";', encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("directory", [None, "", "  "])
def test_unset_assets_serve_bundled_ui(directory):
    app = _api()
    mount_review_assets(app, directory)
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "<title>Respawned</title>" in response.text
        assert response.content == (BUNDLED_UI_DIR / "index.html").read_bytes()


def test_explicit_off_keeps_api_only_behavior():
    app = _api()
    mount_review_assets(app, "off")
    with TestClient(app) as client:
        assert client.get("/").status_code == 404


def test_missing_bundled_assets_fail_clearly(monkeypatch, tmp_path):
    monkeypatch.setattr("respawned.api.static.BUNDLED_UI_DIR", tmp_path / "missing")
    with pytest.raises(RuntimeError, match="Bundled Respawned UI is missing"):
        mount_review_assets(_api())


def test_build_assets_serve_without_shadowing_protected_api(built_ui, monkeypatch):
    monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", "reviewer-test-token")
    app = _api()
    mount_review_assets(app, str(built_ui))
    with TestClient(app) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert "text/html" in index.headers["content-type"]
        assert '<title>Review</title>' in index.text
        assert client.get("/assets/app.js").text == 'document.title = "Review ready";'
        assert client.get("/missing.js").status_code == 404

        blocked = client.get("/v1/ui/config")
        assert blocked.status_code == 401
        assert blocked.json() == {"detail": "Reviewer authorization required"}
        enabled = client.get("/v1/ui/config", headers={"Authorization": "Bearer reviewer-test-token"})
        assert enabled.status_code == 200
        assert enabled.json()["policy_mode"] == "human"


@pytest.mark.parametrize("case", ["relative", "missing", "file", "empty_directory"])
def test_invalid_explicit_asset_directory_fails_before_serving(tmp_path, case):
    path = tmp_path / "dist"
    if case == "relative":
        path = Path("relative/dist")
    elif case == "file":
        path.write_text("not a directory", encoding="utf-8")
    elif case == "empty_directory":
        path.mkdir()
    with pytest.raises(RuntimeError, match="RESPAWNED_UI_DIST must be"):
        mount_review_assets(_api(), str(path))
