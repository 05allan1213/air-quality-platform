import pytest
from django.http import HttpResponse
from django.test import Client, RequestFactory, override_settings
from whitenoise.middleware import WhiteNoiseMiddleware

pytestmark = pytest.mark.django_db(transaction=True)

ENTRY = "index-AbC123_X-8.js"
STYLESHEET = "index-AbC123_X-8.css"
PRELOAD = "shared-X8_C123A.js"


def build_fixture(root):
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    static_root = root / "staticfiles"
    (static_root / "app" / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        f'<script type="module" crossorigin src="/static/app/assets/{ENTRY}"></script>\n'
        f'<link rel="modulepreload" crossorigin href="/static/app/assets/{PRELOAD}">\n'
        f'<link rel="stylesheet" crossorigin href="/static/app/assets/{STYLESHEET}">\n'
        '<!-- <script src="/static/app/assets/unused-AbC123_X-8.js"></script> -->\n',
        encoding="utf-8",
    )
    for name, content in {
        ENTRY: "window.appReady = true;",
        STYLESHEET: ":root { color: black; }",
        PRELOAD: "export const ready = true;",
    }.items():
        (dist / "assets" / name).write_text(content, encoding="utf-8")
        (static_root / "app" / "assets" / name).write_text(content, encoding="utf-8")
    return dist, static_root


def test_shell_serves_build_and_revalidates_for_deep_links(tmp_path):
    dist, static_root = build_fixture(tmp_path)
    with override_settings(SPA_DIST=dist, STATIC_ROOT=static_root):
        client = Client()
        root = client.get("/")
        assert root.status_code == 302
        assert root["Location"] == "/app/"

        response = client.get("/app/analysis?tab=compare")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/html")
        assert response["Cache-Control"] == "no-cache"
        assert ENTRY.encode() in response.content

        cached = client.get("/app/analysis", HTTP_IF_NONE_MATCH=response["ETag"])
        assert cached.status_code == 304
        assert cached.content == b""

        head = client.head("/app/cities/42")
        assert head.status_code == 200
        assert head.content == b""
        assert int(head["Content-Length"]) == len(response.content)
        assert head["ETag"] == response["ETag"]

        cached_head = client.head("/app/cities/42", HTTP_IF_NONE_MATCH=response["ETag"])
        assert cached_head.status_code == 304
        assert cached_head.content == b""


@pytest.mark.parametrize(
    "stale_names",
    [(ENTRY, STYLESHEET, PRELOAD), (ENTRY,), (STYLESHEET,), (PRELOAD,)],
    ids=["old-build-only", "missing-entry", "missing-stylesheet", "missing-preload"],
)
def test_shell_rejects_missing_collected_references(tmp_path, stale_names):
    dist, static_root = build_fixture(tmp_path)
    with override_settings(SPA_DIST=dist, STATIC_ROOT=static_root):
        client = Client()
        ready = client.get("/app/")
        assert ready.status_code == 200

        collected = static_root / "app/assets"
        for name in stale_names:
            asset = collected / name
            old_hash_name = f"{asset.stem.split('-', 1)[0]}-Old987654{asset.suffix}"
            asset.rename(collected / old_hash_name)
            assert (dist / "assets" / name).is_file()

        unavailable = client.get("/app/")
        assert unavailable.status_code == 503
        assert "no-store" in unavailable["Cache-Control"]

        for method in (client.get, client.head):
            conditional = method("/app/cities/42", HTTP_IF_NONE_MATCH=ready["ETag"])
            assert conditional.status_code == 503
            assert "no-store" in conditional["Cache-Control"]
            assert "ETag" not in conditional


def test_shell_rejects_unreadable_collected_reference(tmp_path, monkeypatch):
    dist, static_root = build_fixture(tmp_path)
    asset_path = static_root / "app/assets" / STYLESHEET
    path_open = type(asset_path).open

    def open_with_unreadable_asset(path, *args, **kwargs):
        if path == asset_path:
            raise PermissionError("unreadable collected stylesheet")
        return path_open(path, *args, **kwargs)

    monkeypatch.setattr(type(asset_path), "open", open_with_unreadable_asset)
    with override_settings(SPA_DIST=dist, STATIC_ROOT=static_root):
        unavailable = Client().get("/app/")
        assert unavailable.status_code == 503
        assert "no-store" in unavailable["Cache-Control"]


def test_shell_missing_build_fails_closed_and_does_not_capture_other_namespaces(tmp_path):
    with override_settings(
        SPA_DIST=tmp_path / "missing-dist", STATIC_ROOT=tmp_path / "missing-static"
    ):
        client = Client()
        unavailable = client.get("/app/")
        assert unavailable.status_code == 503
        assert unavailable["Content-Type"].startswith("text/plain")
        assert "no-store" in unavailable["Cache-Control"]

        for path in ("/api/v1/s08-never-match/", "/health/s08-never-match/"):
            response = client.get(path)
            assert response.status_code == 404
            assert b"Frontend unavailable" not in response.content

        readiness = client.get("/health/ready/")
        assert readiness.status_code == 200
        assert readiness["Content-Type"].startswith("application/json")

        admin = client.get("/admin/s08-never-match/")
        assert admin.status_code in {302, 404}
        assert b"Frontend unavailable" not in admin.content

        wrong_method = client.post("/app/analysis")
        assert wrong_method.status_code == 405
        assert "GET" in wrong_method["Allow"]


def test_collected_hash_asset_is_immutable_and_missing_static_is_not_a_shell(tmp_path):
    _, static_root = build_fixture(tmp_path)
    with override_settings(STATIC_ROOT=static_root):
        middleware = WhiteNoiseMiddleware(lambda request: HttpResponse("application fallback"))
        request = RequestFactory().get(f"/static/app/assets/{ENTRY}")
        response = middleware(request)
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == b"window.appReady = true;"
        assert "immutable" in response["Cache-Control"]

        missing = middleware(RequestFactory().get("/static/app/assets/missing.js"))
        assert missing.status_code == 200
        assert missing.content == b"application fallback"

        shell = Client().get("/static/app/assets/missing.js")
        assert shell.status_code == 404
        assert b"window.appReady" not in shell.content
