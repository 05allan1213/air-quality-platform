import os
from functools import cached_property

from celery import Celery, platforms
from celery.apps.beat import Beat
from celery.apps.worker import Worker

from . import pulses  # noqa: F401 -- install process evidence signal handlers
from .beat_guard import SCHEDULER, BeatGuardError, BeatLeaseLost


class GuardedBeat(Beat):
    def run(self):
        # Validate the effective CLI/config choice before Celery resolves or initializes it.
        if self.scheduler_cls != SCHEDULER:
            raise SystemExit("beat_scheduler_not_supported")
        if self.remote_control or self.app.conf.beat_enable_remote_control:
            raise SystemExit("beat_remote_control_not_supported")
        try:
            return super().run()
        except (BeatGuardError, BeatLeaseLost) as error:
            raise SystemExit(str(error)) from None

    def install_sync_handler(self, service):
        def stop(signum, frame):
            # Interrupt the loop first. The service unwinds and syncs after the publishing
            # call, instead of closing a producer or opening a lazy scheduler in the handler.
            raise SystemExit()

        platforms.signals.update(SIGTERM=stop, SIGINT=stop)


class StandaloneWorker(Worker):
    def __init__(self, *args, beat=False, **kwargs):
        if beat:
            raise SystemExit("worker_embedded_beat_not_supported")
        super().__init__(*args, **kwargs)


class AirQualityCelery(Celery):
    @cached_property
    def Beat(self):
        return self.subclass_with_self(GuardedBeat)

    @cached_property
    def Worker(self):
        return self.subclass_with_self(StandaloneWorker)


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
app = AirQualityCelery("air_quality")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
