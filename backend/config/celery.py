import os

from celery import Celery

from . import pulses  # noqa: F401 -- install process evidence signal handlers

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
app = Celery("air_quality")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
