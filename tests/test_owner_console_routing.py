from fastapi import FastAPI
from fastapi.testclient import TestClient

from transpodcast.owner_console import OwnerConsoleStaticFiles


def _client(tmp_path) -> TestClient:
    (tmp_path / "index.html").write_text(
        "<!doctype html><title>TransPodcast</title>",
        encoding="utf-8",
    )
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('TransPodcast')", encoding="utf-8")

    app = FastAPI()

    @app.get("/api/v1/example")
    def example() -> dict[str, bool]:
        return {"ok": True}

    app.mount(
        "/",
        OwnerConsoleStaticFiles(directory=tmp_path, html=True),
        name="owner-console",
    )
    return TestClient(app)


def test_owner_console_routes_fall_back_to_index(tmp_path) -> None:
    client = _client(tmp_path)

    for route in (
        "/interests",
        "/decisions",
        "/episodes/42",
        "/episodes/42/edit",
        "/channels/7",
    ):
        response = client.get(route)

        assert response.status_code == 200
        assert "<title>TransPodcast</title>" in response.text


def test_owner_console_fallback_preserves_api_and_asset_404s(tmp_path) -> None:
    client = _client(tmp_path)

    assert client.get("/api/v1/example").json() == {"ok": True}
    assert client.get("/api/v1/missing").status_code == 404
    assert client.get("/assets/missing.js").status_code == 404
