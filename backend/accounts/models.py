import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class Favorite(models.Model):
    class Kind(models.TextChoices):
        CITY = "city"
        STATION = "station"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    kind = models.CharField(max_length=7, choices=Kind)
    city = models.ForeignKey("observations.City", null=True, on_delete=models.SET_NULL)
    station = models.ForeignKey("observations.Station", null=True, on_delete=models.SET_NULL)
    preferred_product = models.ForeignKey(
        "observations.Product", null=True, on_delete=models.SET_NULL
    )
    synthetic_scope = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(kind="city", station__isnull=True)
                | Q(kind="station", city__isnull=True),
                name="favorite_kind_target",
            ),
            models.UniqueConstraint(
                fields=("owner", "city"),
                condition=Q(city__isnull=False),
                name="favorite_owner_city",
            ),
            models.UniqueConstraint(
                fields=("owner", "station"),
                condition=Q(station__isnull=False),
                name="favorite_owner_station",
            ),
        ]
        indexes = [
            models.Index(
                fields=("owner", "synthetic_scope", "-created_at", "-id"),
                name="favorite_owner_page",
            )
        ]


class SavedQuery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    product = models.ForeignKey("observations.Product", null=True, on_delete=models.SET_NULL)
    synthetic_scope = models.BooleanField()
    title = models.CharField(max_length=100)
    intent = models.JSONField()
    intent_revision = models.PositiveBigIntegerField(default=1)
    baseline = models.CharField(max_length=64, blank=True, default="")
    baseline_basis = models.JSONField(default=list)
    baseline_until = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("owner", "synthetic_scope", "-created_at", "-id"),
                name="saved_query_owner_page",
            )
        ]


class SavedQueryTarget(models.Model):
    query = models.ForeignKey(SavedQuery, related_name="targets", on_delete=models.CASCADE)
    position = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=7, choices=Favorite.Kind)
    city = models.ForeignKey("observations.City", null=True, on_delete=models.SET_NULL)
    site_epoch = models.ForeignKey("observations.SiteEpoch", null=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(position__lt=5), name="saved_target_position_bound"),
            models.CheckConstraint(
                condition=Q(kind="city", site_epoch__isnull=True)
                | Q(kind="station", city__isnull=True),
                name="saved_target_kind",
            ),
            models.UniqueConstraint(fields=("query", "position"), name="saved_target_position"),
            models.UniqueConstraint(fields=("query", "city"), name="saved_target_city"),
            models.UniqueConstraint(fields=("query", "site_epoch"), name="saved_target_site"),
        ]

    @property
    def entity_id(self):
        return self.city_id if self.kind == "city" else self.site_epoch_id


class ExportTicket(models.Model):
    class Kind(models.TextChoices):
        DETAIL = "detail"
        AGGREGATE = "aggregate"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    products = models.ManyToManyField("observations.Product", related_name="export_tickets")
    synthetic_scope = models.BooleanField()
    kind = models.CharField(max_length=9, choices=Kind)
    format_version = models.CharField(max_length=32)
    groups = models.JSONField()
    fields = models.JSONField()
    basis = models.JSONField()
    input_versions = models.JSONField()
    projection_signature = models.CharField(max_length=64)
    data_sha256 = models.CharField(max_length=64)
    row_count = models.PositiveIntegerField()
    byte_count = models.PositiveIntegerField()
    created_at = models.DateTimeField()
    expires_at = models.DateTimeField(db_index=True)
    invalidated_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(expires_at__gt=models.F("created_at")), name="export_ticket_lifetime"
            ),
            models.CheckConstraint(condition=Q(row_count__lte=10000), name="export_ticket_rows"),
            models.CheckConstraint(
                condition=Q(byte_count__lte=4 * 1024 * 1024), name="export_ticket_bytes"
            ),
        ]
        indexes = [
            models.Index(
                fields=("owner", "synthetic_scope", "-created_at", "-id"),
                name="export_ticket_owner_page",
            ),
        ]


class AccountState(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, primary_key=True
    )
    session_generation = models.PositiveBigIntegerField(default=0)
    version = models.PositiveBigIntegerField(default=0)

    class Meta:
        default_permissions = ()
        permissions = [
            ("manage_accounts", "Manage subordinate accounts"),
            ("adjudicate_quality", "Adjudicate authorized quality evidence"),
            ("manage_catalog", "Govern authorized station identities"),
            ("maintain_city_search_alias", "Maintain local city search aliases"),
            ("diagnose_operations", "Read authorized operational diagnostics"),
            ("manage_backups", "Prepare authorized backup segments"),
            ("restore_backup", "Restore authorized backup segments"),
        ]


class GovernanceAction(models.Model):
    """Committed receipts. Only server-selected codes and identifiers belong here."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    request_id = models.UUIDField(null=True)
    action = models.CharField(max_length=32)
    target_kind = models.CharField(max_length=24)
    target = models.CharField(max_length=64)
    origin = models.CharField(max_length=64, blank=True)
    reason_code = models.CharField(max_length=48, blank=True)
    parameter = models.CharField(max_length=80, blank=True)
    before_state = models.CharField(max_length=48)
    after_state = models.CharField(max_length=48)
    before_version = models.PositiveBigIntegerField()
    after_version = models.PositiveBigIntegerField()
    outcome = models.CharField(max_length=16, default="applied")
    created_at = models.DateTimeField()

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=("actor", "request_id"), name="gov_actor_request")
        ]


class ServicePulse(models.Model):
    """Latest process evidence only. No observations, user input or task payload."""

    component = models.CharField(max_length=12, primary_key=True)
    instance = models.CharField(max_length=120)
    observed_at = models.DateTimeField()
    subscribed_queues = models.JSONField(null=True)
    consumed_at = models.DateTimeField(null=True)
    queue = models.CharField(max_length=120, blank=True)

    class Meta:
        default_permissions = ()
