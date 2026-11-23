"""An isolated, fresh Django database session and one verified loopback read."""

from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from secrets import token_urlsafe

from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY, get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import DatabaseError
from django.utils import timezone
from observations.backup_authority import canonical
from observations.policy import Denied


def release(alias, read):
    User = get_user_model()
    name, password = token_urlsafe(24), token_urlsafe(48)
    reader = User(username=name)
    reader.set_password(password)
    reader.save(using=alias)
    result = {}
    database_failure = None
    session_id = None
    consumed = False

    class Handler(BaseHTTPRequestHandler):
        timeout = 3

        def log_message(self, *args):
            pass

        def reply(self, status, data=None, *, cookie=None):
            data = b"" if data is None else canonical(data)
            self.send_response(status)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            if cookie:
                self.send_header("Set-Cookie", f"probe={cookie}; HttpOnly; SameSite=Strict; Path=/")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            nonlocal session_id
            if (
                self.path != "/login"
                or session_id is not None
                or self.headers.get("Content-Length") != str(len(password))
            ):
                return self.reply(403)
            user = User.objects.using(alias).get(username=name)
            if not user.is_active or not user.check_password(
                self.rfile.read(len(password)).decode("ascii")
            ):
                return self.reply(403)
            session_id = token_urlsafe(24)
            store = SessionStore()
            Session.objects.using(alias).create(
                session_key=session_id,
                session_data=store.encode(
                    {
                        SESSION_KEY: str(user.pk),
                        BACKEND_SESSION_KEY: "django.contrib.auth.backends.ModelBackend",
                        HASH_SESSION_KEY: user.get_session_auth_hash(),
                    }
                ),
                expire_date=store.get_expiry_date(),
            )
            self.reply(204, cookie=session_id)

        def do_GET(self):
            nonlocal consumed, database_failure
            cookie = SimpleCookie(self.headers.get("Cookie"))
            if (
                self.path != "/probe"
                or consumed
                or session_id is None
                or "probe" not in cookie
                or cookie["probe"].value != session_id
            ):
                return self.reply(403)
            row = (
                Session.objects.using(alias)
                .filter(session_key=session_id, expire_date__gt=timezone.now())
                .first()
            )
            if (
                row is None
                or row.get_decoded().get(SESSION_KEY) != str(reader.pk)
                or row.get_decoded().get(HASH_SESSION_KEY) != reader.get_session_auth_hash()
            ):
                return self.reply(403)
            consumed = True

            def deliver(verified):
                result.update(verified)
                self.reply(200, result)

            try:
                read(deliver)
            except Denied:
                return self.reply(403)
            except DatabaseError as exc:
                database_failure = exc
                return self.reply(503)

    def probe(port):
        http = HTTPConnection("127.0.0.1", port, timeout=12)

        def request(method, url, *, body=None, headers=None):
            http.request(method, url, body=body, headers=headers or {})
            response = http.getresponse()
            return response.status, response.getheader("Set-Cookie"), response.read()

        try:
            missing, _, _ = request("GET", "/probe")
            login, cookie, _ = request("POST", "/login", body=password)
            headers = {"Cookie": cookie.split(";", 1)[0]} if cookie else {}
            first, _, content = request("GET", "/probe", headers=headers)
            repeated, _, _ = request("GET", "/probe", headers=headers)
        finally:
            http.close()
        return (missing, login, first, repeated), content

    with HTTPServer(("127.0.0.1", 0), Handler) as server, ThreadPoolExecutor(max_workers=1) as pool:
        server.timeout = 3
        promise = pool.submit(probe, server.server_port)
        for _ in range(4):
            server.handle_request()
        statuses, content = promise.result(timeout=13)
    if database_failure is not None:
        if statuses != (403, 204, 503, 403) or content:
            raise Denied("restore_read_denied")
        raise database_failure
    if statuses != (403, 204, 200, 403) or content != canonical(result):
        raise Denied("restore_read_denied")
    return result | {"http": list(statuses)}
