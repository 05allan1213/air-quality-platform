"""Shared local WSGI settings. Keep rate limiting in one application process."""

workers = 1
worker_class = "gthread"
threads = 8
accesslog = None
graceful_timeout = 10
