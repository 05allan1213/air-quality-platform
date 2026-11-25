"""Serve the built Vue shell without expanding Django's API or static routes."""

from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import path
from django.utils.cache import add_never_cache_headers, get_conditional_response, set_response_etag
from django.views.decorators.http import require_safe

ASSET_URL_PREFIX = "/static/app/assets/"


class _ShellAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = set()
        self.has_entry = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script":
            url = attributes.get("src")
        elif tag == "link" and {"stylesheet", "modulepreload"}.intersection(
            (attributes.get("rel") or "").lower().split()
        ):
            url = attributes.get("href")
        else:
            return

        if url and url.startswith(ASSET_URL_PREFIX):
            self.references.add(url)
            if tag == "script":
                self.has_entry = True


def _collected_assets_readable(content):
    assets = _ShellAssets()
    assets.feed(content.decode("utf-8"))
    assets.close()
    if not assets.has_entry:
        return False

    root = (settings.STATIC_ROOT / "app/assets").resolve(strict=True)
    for url in assets.references:
        name = unquote(urlsplit(url).path.removeprefix(ASSET_URL_PREFIX))
        if "\\" in name or any(part in {"", ".", ".."} for part in name.split("/")):
            return False
        path = (root / name).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            return False
        with path.open("rb") as asset:
            asset.read(1)
    return True


@require_safe
def home(request):
    return HttpResponseRedirect("/app/")


@require_safe
def shell(request, route=None):
    # Never translate the requested route into a filesystem path.
    try:
        content = (settings.SPA_DIST / "index.html").read_bytes()
        available = _collected_assets_readable(content)
    except (OSError, ValueError):
        available = False
    if not available:
        response = HttpResponse(
            "Frontend unavailable. Build frontend/ and run collectstatic.\n",
            status=503,
            content_type="text/plain; charset=utf-8",
        )
        add_never_cache_headers(response)
        return response

    response = set_response_etag(HttpResponse(content, content_type="text/html; charset=utf-8"))
    response.headers["Cache-Control"] = "no-cache"
    response = get_conditional_response(request, etag=response.headers["ETag"], response=response)
    if request.method == "HEAD" and response.status_code == 200:
        response.headers["Content-Length"] = str(len(content))
        response.content = b""
    return response


urlpatterns = [
    path("", home, name="spa-home"),
    path("app/", shell, name="spa-shell"),
    path("app/<path:route>", shell, name="spa-route"),
]
