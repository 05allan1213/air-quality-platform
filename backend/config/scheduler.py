import logging

from django_celery_beat.schedulers import DatabaseScheduler
from kombu.messaging import Producer

from .beat_guard import BeatGuardError, BeatLease, BeatLeaseLost
from .pulses import record

logger = logging.getLogger(__name__)


class GuardedProducer(Producer):
    def __init__(self, connection, lease, enter_publish):
        super().__init__(connection, auto_declare=False)
        self.lease = lease
        self.enter_publish = enter_publish
        # Beat owns this connection and channel. Gate the actual Redis transport call,
        # after Kombu has prepared the message and declared its queue.
        channel = self.channel
        self.basic_publish = channel.basic_publish
        channel.basic_publish = self.publish_to_broker

    def publish(self, *args, headers=None, **kwargs):
        headers = {**(headers or {}), "aq_beat_publisher": self.lease.publisher}
        kwargs["retry"] = False
        return super().publish(*args, headers=headers, **kwargs)

    def publish_to_broker(self, message, **kwargs):
        self.lease.verify_broker(self.channel)
        self.lease.verify()
        task_id = message["headers"]["id"]
        self.enter_publish(message["headers"]["periodic_task_name"])
        logger.info("Beat publishing id=%s publisher=%s", task_id, self.lease.publisher)
        try:
            result = self.basic_publish(message, **kwargs)
        except BaseException:
            # Once in the transport, disconnects and termination can leave delivery unknown.
            logger.warning(
                "Beat delivery unknown id=%s publisher=%s", task_id, self.lease.publisher
            )
            raise
        return result


class ObservedScheduler(DatabaseScheduler):
    def __init__(self, *args, lazy=False, **kwargs):
        self.lease = None
        self.pending_entry = None
        if not lazy:
            self.lease = BeatLease()
        try:
            super().__init__(*args, lazy=lazy, **(kwargs | {"sync_every_tasks": 1}))
        except BaseException:
            if self.lease is not None:
                self.lease.close()
            if (connection := self.__dict__.pop("connection", None)) is not None:
                connection.release()
            raise

    def setup_schedule(self):
        # Admission happens before DatabaseScheduler updates any schedule rows.
        connection = self.connection
        try:
            # Use Celery's selected write transport, including CLI broker overrides. No fallback
            # broker or alternate virtual DB may publish on behalf of the registered instance.
            if connection.alt or connection.transport.driver_type != "redis":
                raise BeatGuardError("beat_broker_type")
            connection.ensure_connection(max_retries=0)
            self.producer = GuardedProducer(connection, self.lease, self.enter_publish)
            self.lease.verify_broker(self.producer.channel)
        except (OSError, *connection.connection_errors):
            raise BeatGuardError("beat_broker_unavailable") from None
        self.lease.verify()
        super().setup_schedule()

    def reserve(self, entry):
        # A Celery tick reserves before it sends. Advance the heap in memory and persist
        # the reservation only at the transport boundary. TERM or lease loss beforehand
        # leaves the slot due for the successor.
        if entry.name in self._dirty:
            self.sync()
            if entry.name in self._dirty:
                raise BeatLeaseLost("beat_schedule_sync_failed")
        self.pending_entry = entry.name
        return next(entry)

    def enter_publish(self, name):
        if name != self.pending_entry:
            raise BeatLeaseLost("beat_schedule_entry_mismatch")
        self._dirty.add(name)
        self.pending_entry = None

    def tick(self, *args, **kwargs):
        self.lease.verify()
        delay = super().tick(*args, **kwargs)
        record("beat", self.lease.publisher)
        return delay

    def sync(self):
        if self.lease is not None and self.lease.active and self._dirty:
            self.lease.verify()
            super().sync()

    def close(self):
        if self.lease is None:
            return
        try:
            super().close()
        finally:
            self._finalize.cancel()
            self.lease.close()
            self.lease = None
