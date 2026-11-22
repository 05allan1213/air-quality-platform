"""Local WSGI HTTP client with a real login, CSRF, and optional SQL measurements."""

from contextlib import contextmanager
from threading import Thread
from time import perf_counter
from wsgiref.simple_server import WSGIRequestHandler, make_server

import httpx
from django.core.cache import cache
from django.core.wsgi import get_wsgi_application
from django.db import connection

AUTH = "/api/v1/auth/"


class QuietRequestHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


@contextmanager
def loopback(user, *, before_sql=None):
    # Each task-owned HTTP server begins a new fixture and a fresh loopback login window.
    cache.delete("s01:login:127.0.0.1")
    application = get_wsgi_application()
    records = []

    def measured_wsgi(environ, start_response):
        queries = []

        def measure(execute, sql, params, many, context):
            if before_sql is not None:
                before_sql(sql)
            start = perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                queries.append((sql, params, (perf_counter() - start) * 1000))

        with connection.execute_wrapper(measure):
            result = application(environ, start_response)
            try:
                yield from result
            finally:
                result.close()
                records.append(queries)

    with make_server("127.0.0.1", 0, measured_wsgi, handler_class=QuietRequestHandler) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{server.server_port}", timeout=12, trust_env=False
            ) as web:
                csrf = web.get(AUTH + "csrf/").json()["csrf_token"]
                assert (
                    web.post(
                        AUTH + "login/",
                        json={"username": user.username, "password": "s05-test-only"},
                        headers={"X-CSRFToken": csrf},
                    ).status_code
                    == 200
                )
                web.headers["X-CSRFToken"] = web.get(AUTH + "csrf/").json()["csrf_token"]
                yield web, records
        finally:
            server.shutdown()
            thread.join()
