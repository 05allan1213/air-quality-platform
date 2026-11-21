import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class FixtureGrant(models.Model):
    product = models.CharField(max_length=8)
    source = models.CharField(max_length=64)
    audience = models.CharField(max_length=40)
    geography = models.CharField(max_length=40)
    operation = models.CharField(max_length=20)
    basis = models.CharField(max_length=160)
    enabled = models.BooleanField(default=True)
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "source", "audience", "geography", "operation"],
                name="fixture_grant_scope",
            ),
        ]


class FixtureObservation(models.Model):
    fixture_key = models.CharField(max_length=32, unique=True)
    fixture_version = models.CharField(max_length=16)
    product = models.CharField(max_length=8)
    source = models.CharField(max_length=64)
    entity = models.CharField(max_length=64)
    synthetic = models.BooleanField()
    purpose = models.CharField(max_length=32)
    indicator = models.CharField(max_length=16)
    raw_value = models.CharField(max_length=32)
    raw_unit = models.CharField(max_length=16)
    source_time = models.CharField(max_length=64)
    source_timezone = models.CharField(max_length=64)
    observed_at = models.DateTimeField()
    window_start = models.DateTimeField()
    window_end = models.DateTimeField()
    window_semantics = models.CharField(max_length=64)
    value = models.DecimalField(max_digits=12, decimal_places=3)
    unit = models.CharField(max_length=16)
    quality = models.CharField(max_length=16)
    aqi_standard = models.CharField(max_length=32)
    fetched_at = models.DateTimeField()
    published_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(product="F", synthetic=True), name="fixture_only_synthetic_f"
            ),
            models.CheckConstraint(condition=models.Q(value__gte=0), name="fixture_nonnegative"),
        ]


class FixtureRun(models.Model):
    class State(models.TextChoices):
        PENDING = "pending"
        SUCCEEDED = "succeeded"
        REJECTED = "rejected"
        FAILED = "failed"

    class Dispatch(models.TextChoices):
        PENDING = "pending"
        SENT = "sent"
        UNCONFIRMED = "unconfirmed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reason = models.CharField(max_length=32)
    fixture_key = models.CharField(max_length=32)
    requested_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    state = models.CharField(max_length=16, choices=State, default=State.PENDING)
    error_code = models.CharField(max_length=40, blank=True)
    dispatch = models.CharField(max_length=16, choices=Dispatch, default=Dispatch.PENDING)
    observation = models.ForeignKey(FixtureObservation, null=True, on_delete=models.PROTECT)

    class Meta:
        permissions = [("run_fixture", "May submit the fixed local synthetic fixture")]
