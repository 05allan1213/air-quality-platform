"""Catalog of controlled logical segments and privileged, single-use restore intents."""

import uuid

from django.conf import settings
from django.db import models


class BackupSegment(models.Model):
    class State(models.TextChoices):
        PREPARING = "preparing"
        AVAILABLE = "available"
        BLOCKED = "blocked"
        REMOVED = "removed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey("observations.Product", on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    scope = models.JSONField()
    fields = models.JSONField()
    snapshot_at = models.DateTimeField()
    snapshot_version = models.PositiveBigIntegerField()
    expires_at = models.DateTimeField(db_index=True)
    volume_generation = models.UUIDField(null=True, editable=False)
    state = models.CharField(max_length=12, choices=State, default=State.PREPARING)
    manifest = models.JSONField(null=True)
    catalog_dependencies = models.JSONField(default=list)

    class Meta:
        default_permissions = ()
        indexes = [models.Index(fields=["product", "state"], name="backup_product_state")]


class RestoreIntent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    segment = models.ForeignKey(BackupSegment, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    session = models.ForeignKey("sessions.Session", null=True, on_delete=models.SET_NULL)
    target = models.UUIDField()
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True)

    class Meta:
        default_permissions = ()


class BackupAuthorityEvent(models.Model):
    """A rights mutation captured in the same authority transaction as its version."""

    product = models.ForeignKey("observations.Product", on_delete=models.PROTECT)
    version = models.PositiveBigIntegerField()
    previous = models.CharField(max_length=64)
    digest = models.CharField(max_length=64)
    kind = models.CharField(max_length=32)
    effective_at = models.DateTimeField()
    rights = models.JSONField()

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=("product", "version"), name="backup_rights_sequence")
        ]


class BackupSubject(models.Model):
    """A durable principal UUID tied to the lifetime of the user row."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, primary_key=True
    )
    identity = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    class Meta:
        default_permissions = ()
