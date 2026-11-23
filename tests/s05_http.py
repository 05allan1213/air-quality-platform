"""Local WSGI HTTP client with a real login, CSRF, and optional SQL measurements."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Thread
from time import perf_counter
from wsgiref.simple_server import WSGIRequestHandler, make_server

import httpx
from django.core.cache import cache
from django.core.wsgi import get_wsgi_application
from django.db import connection
from psycopg import Error

AUTH = "/api/v1/auth/"
TRACE_HEADER = "X-S07-CSV-Trace"


@dataclass
class RequestTrace:
    queries: list = field(default_factory=list)
    sqlstates: list = field(default_factory=list)
    response_started: bool = False

    def record_sqlstate(self, exception):
        for _ in range(5):
            if isinstance(exception, Error) and exception.sqlstate:
                self.sqlstates.append(exception.sqlstate)
                break
            exception = exception.__cause__ or exception.__context__
            if exception is None:
                break


class QuietRequestHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


@contextmanager
def loopback(user, *, before_sql=None):
    # Each task-owned HTTP server begins a new fixture and a fresh loopback login window.
    cache.delete("s01:login:127.0.0.1")
    application = get_wsgi_application()
    records = {}

    def measured_wsgi(environ, start_response):
        correlation = environ.get("HTTP_X_S07_CSV_TRACE")
        trace = RequestTrace()
        if correlation is not None:
            records[correlation] = trace

        def start(status, headers, exc_info=None):
            trace.response_started = True
            return start_response(status, headers, exc_info)

        def measure(execute, sql, params, many, context):
            if before_sql is not None:
                before_sql(sql)
            then = perf_counter()
            try:
                return execute(sql, params, many, context)
            except Exception as exc:
                trace.record_sqlstate(exc)
                raise
            finally:
                trace.queries.append((sql, params, (perf_counter() - then) * 1000))

        with connection.execute_wrapper(measure):
            result = application(environ, start)
            try:
                yield from result
            finally:
                result.close()

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
