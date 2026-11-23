from django_celery_beat.schedulers import DatabaseScheduler

from .pulses import record


class ObservedScheduler(DatabaseScheduler):
    def tick(self, *args, **kwargs):
        delay = super().tick(*args, **kwargs)
        record("beat", "scheduler")
        return delay
