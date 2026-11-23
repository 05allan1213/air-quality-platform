"""Product-scoped use rights and the typed observation ledger. No model is in Admin."""

import uuid

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from .backup_models import BackupAuthorityEvent, BackupSegment, BackupSubject, RestoreIntent  # noqa: F401 -- register logical backup models


def text(size=100, **kwargs):
    return models.CharField(max_length=size, **kwargs)


class Source(models.Model):
    namespace = text(unique=True)
    publisher = text(200)
    collection_limits = models.JSONField(null=True, blank=True)
    collection_pause = text(blank=True)
    collection_resume_after = models.DateTimeField(null=True, blank=True)


class Product(models.Model):
    source = models.ForeignKey(Source, on_delete=models.PROTECT)
    code = text()
    family = text(1)
    shape = text(1)
    level = text(8)
    synthetic = models.BooleanField(default=False)
    contract_version = text(default="s02.v1")
    source_timezone = text(default="UTC")
    evidence = models.TextField(blank=True)
    attribution = models.TextField(blank=True)
    rights_version = models.PositiveBigIntegerField(default=0)
    next_loss_at = models.DateTimeField(null=True)
    last_disposed_at = models.DateTimeField(null=True)
    collection_pause = text(blank=True)

    class Meta:
        permissions = [
            ("manage_collection", "Manage collection jobs and pauses"),
            ("run_collection", "Execute authorized collection jobs"),
            ("import_history", "Import a controlled historical batch"),
            ("review_observation", "Review authorized observation evidence"),
            ("manage_grants", "Register and withdraw source uses"),
        ]
        constraints = [
            models.UniqueConstraint(fields=("source", "code"), name="product_in_source"),
            models.CheckConstraint(
                condition=(
                    Q(family="F", synthetic=True)
                    | (~Q(family="F") & Q(synthetic=False, family=F("shape")))
                ),
                name="product_synthetic_family",
            ),
            models.CheckConstraint(
                condition=(
                    Q(shape__in=["S", "H"], level="station")
                    | Q(shape__in=["L", "C", "T", "D"], level="city")
                ),
                name="product_shape_level",
            ),
        ]


class UseGrant(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    operation = text(32)
    audience = text(32)
    geography = text()
    object_code = text(default="*")
    indicator = text(default="*")
    data_from = models.DateField()
    data_until = models.DateField()
    fields = models.JSONField()
    terms = models.TextField()
    terms_version = text()
    attribution = models.TextField(blank=True)
    restrictions = models.JSONField(default=dict)
    collection_limits = models.JSONField(null=True, blank=True)
    # Empty for legacy grants. CNEMC online allows must name the permitted endpoints.
    endpoints = models.JSONField(default=list, blank=True)
    validation_allocation = models.JSONField(default=dict, blank=True)
    validation_disabled = models.BooleanField(default=False)
    max_total_requests = models.PositiveIntegerField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    # First PG witness after registration commits. NULL cannot prove historical overlap.
    registered_at = models.DateTimeField(null=True, editable=False)
    valid_from = models.DateTimeField(default=timezone.now)
    valid_until = models.DateTimeField(null=True)
    retain_until = models.DateTimeField(null=True)
    expiry_mode = text(16, default="hard")
    effect = text(8, default="allow")
    revoked_at = models.DateTimeField(null=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+"
    )
    revoke_reason = text(blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(max_total_requests=None)
                | Q(operation="acquire_online", max_total_requests__gte=1),
                name="cnemc_positive_total",
            ),
            models.CheckConstraint(
                condition=Q(data_until__gt=F("data_from")), name="grant_data_span"
            ),
            models.CheckConstraint(
                condition=Q(valid_until__isnull=True) | Q(valid_until__gt=F("valid_from")),
                name="grant_usage_span",
            ),
            models.CheckConstraint(
                condition=Q(effect__in=["allow", "deny"], expiry_mode__in=["hard", "recoverable"]),
                name="grant_modes",
            ),
        ]


class City(models.Model):
    source = models.ForeignKey(Source, on_delete=models.PROTECT)
    name = text()
    geography = text()


class Station(models.Model):
    source = models.ForeignKey(Source, on_delete=models.PROTECT)
    name = text()


class Period(models.Model):
    active_period = DateTimeRangeField()

    class Meta:
        abstract = True
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    active_period__isempty=False,
                    active_period__lower_inf=False,
                    active_period__lower_inc=True,
                    active_period__upper_inc=False,
                ),
                name="%(class)s_half_open",
            )
        ]


class Identifier(Period):
    source = models.ForeignKey(Source, on_delete=models.PROTECT)
    code = text()
    kind = text(8, default="code")
    evidence = text(240)

    class Meta(Period.Meta):
        abstract = True
        constraints = [
            *Period.Meta.constraints,
            models.CheckConstraint(
                condition=Q(kind__in=["code", "alias"]) & ~Q(code=""), name="%(class)s_kind_code"
            ),
            ExclusionConstraint(
                name="%(class)s_code_period",
                expressions=[("source", "="), ("code", "="), ("active_period", "&&")],
                condition=Q(kind="code"),
            ),
        ]


class CityIdentifier(Identifier):
    city = models.ForeignKey(City, on_delete=models.PROTECT)

    class Meta(Identifier.Meta):
        constraints = [
            *Identifier.Meta.constraints,
            models.CheckConstraint(
                condition=~Q(kind="code", code__regex="^0+$"), name="city_code_nonzero"
            ),
        ]


class StationIdentifier(Identifier):
    station = models.ForeignKey(Station, on_delete=models.PROTECT)


class SiteEpoch(Period):
    station = models.ForeignKey(Station, on_delete=models.PROTECT)
    city = models.ForeignKey(City, on_delete=models.PROTECT)
    name = text()
    location_evidence = text(240)
    certainty = text()
    longitude = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    latitude = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    crs = text(blank=True)

    class Meta(Period.Meta):
        constraints = [
            *Period.Meta.constraints,
            ExclusionConstraint(
                name="station_epoch_no_overlap",
                expressions=[("station", "="), ("active_period", "&&")],
            ),
        ]


class Observation(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    indicator = text(24)
    object_code = text()
    report_kind = text(16)
    window_definition = text()
    standard_series = text()
    observed_at_utc = models.DateTimeField(null=True)
    local_report_date = models.DateField(null=True)
    source_local_date = models.DateField()
    watermark = models.PositiveBigIntegerField(null=True)
    trusted_scheme = text(blank=True)
    disputed = models.BooleanField(default=False)
    retired = models.BooleanField(default=False)
    qualification_version = models.PositiveBigIntegerField(default=1)
    rule_floor = models.PositiveIntegerField(default=1)

    class Meta:
        abstract = True
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    report_kind="daily",
                    local_report_date__isnull=False,
                    observed_at_utc__isnull=True,
                )
                | Q(
                    report_kind__in=["hourly", "rolling_8h", "rolling_24h"],
                    observed_at_utc__isnull=False,
                    local_report_date__isnull=True,
                ),
                name="%(class)s_report_anchor",
            )
        ]


def identity_constraints(entity):
    fields = ["product", entity, "indicator", "report_kind", "window_definition", "standard_series"]
    return [
        *Observation.Meta.constraints,
        *[
            models.UniqueConstraint(
                fields=[*fields, anchor],
                condition=Q(**{anchor + "__isnull": False}),
                name="%(class)s_" + suffix,
            )
            for anchor, suffix in [("observed_at_utc", "instant"), ("local_report_date", "day")]
        ],
    ]


class CityObservation(Observation):
    city = models.ForeignKey(City, on_delete=models.PROTECT)

    class Meta(Observation.Meta):
        constraints = identity_constraints("city")


class StationObservation(Observation):
    site_epoch = models.ForeignKey(SiteEpoch, on_delete=models.PROTECT)

    class Meta(Observation.Meta):
        constraints = identity_constraints("site_epoch")


class Revision(models.Model):
    revision_no = models.PositiveIntegerField()
    is_current = models.BooleanField(default=False)
    candidate = models.BooleanField(default=False)
    value = models.DecimalField(max_digits=18, decimal_places=6, null=True)
    unit = text(16)
    quality = text(24)
    limit_value = models.DecimalField(max_digits=18, decimal_places=6, null=True)
    limit_operator = text(2, blank=True)
    # Optional source field evidence may be independently withdrawn.
    raw_value = text(200, null=True)
    raw_unit = text(20, null=True)
    source_description = text(240, blank=True)
    provenance = models.JSONField(default=dict)
    rule_version = models.PositiveIntegerField(default=1)
    kind = text(32)
    source_token = models.PositiveBigIntegerField(null=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reason = text(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        constraints = [
            models.UniqueConstraint(fields=["key", "revision_no"], name="%(class)s_number"),
            models.UniqueConstraint(
                fields=["key"], condition=Q(is_current=True), name="%(class)s_current"
            ),
            models.CheckConstraint(
                condition=~Q(candidate=True, is_current=True), name="%(class)s_candidate"
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        quality__in=["valid", "suspect"],
                        value__isnull=False,
                        limit_value__isnull=True,
                    )
                    | Q(
                        quality="censored",
                        value__isnull=True,
                        limit_operator="<",
                        limit_value__isnull=False,
                    )
                    | Q(
                        quality__in=["missing", "invalid"],
                        value__isnull=True,
                        limit_value__isnull=True,
                    )
                )
                & Q(unit__in=["µg/m³", "mg/m³", "1", "grade"]),
                name="%(class)s_value",
            ),
        ]


class CityRevision(Revision):
    key = models.ForeignKey(CityObservation, on_delete=models.CASCADE, related_name="revisions")


class StationRevision(Revision):
    key = models.ForeignKey(StationObservation, on_delete=models.CASCADE, related_name="revisions")


class Batch(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    identity = text(null=True)
    profile = text()
    acquired_at = models.DateTimeField(null=True)
    fetched_at = models.DateTimeField(default=timezone.now)
    redacted = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "identity", "profile"], name="source_batch_selection"
            )
        ]


class StagedInput(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    state = text(default="preparing")
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    volume_generation = models.UUIDField(null=True, editable=False)


class ImportAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    stage = models.OneToOneField(StagedInput, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reason = text()
    batch = models.ForeignKey(Batch, null=True, on_delete=models.SET_NULL)
    state = text(default="pending")
    dispatch = text(default="pending")
    error_code = text(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    deliveries = models.PositiveIntegerField(default=0)


class Receipt(models.Model):
    collection_metadata = models.JSONField(null=True)
    collection_item = models.ForeignKey(
        "collection.RunItem", null=True, on_delete=models.SET_NULL, related_name="receipts"
    )
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="receipts")
    city_revision = models.ForeignKey(CityRevision, null=True, on_delete=models.CASCADE)
    station_revision = models.ForeignKey(StationRevision, null=True, on_delete=models.CASCADE)
    locator = text(null=True)
    ordinal = models.PositiveIntegerField()
    rule_version = models.PositiveIntegerField()
    source_token = models.PositiveBigIntegerField(null=True)
    outcome = text(32)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(city_revision__isnull=True, station_revision__isnull=False)
                | Q(city_revision__isnull=False, station_revision__isnull=True),
                name="receipt_one_side",
            ),
            models.UniqueConstraint(
                fields=["batch", "ordinal", "rule_version"], name="receipt_once"
            ),
        ]


class Event(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    city_key = models.ForeignKey(CityObservation, null=True, on_delete=models.CASCADE)
    station_key = models.ForeignKey(StationObservation, null=True, on_delete=models.CASCADE)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        abstract = True
        constraints = [
            models.CheckConstraint(
                condition=Q(city_key__isnull=True) | Q(station_key__isnull=True),
                name="%(class)s_one_side",
            )
        ]


class QualityIssue(Event):
    # Only unbound issues need an explicit rights scope. Keyed issues use their FK.
    scope = models.JSONField(null=True)
    code = text()
    batch = models.ForeignKey(Batch, null=True, on_delete=models.CASCADE)
    resolved_at = models.DateTimeField(null=True)
    status = text(
        12,
        default="open",
        choices=[("open", "Open"), ("in_review", "In review"), ("resolved", "Resolved")],
    )
    severity = text(
        8,
        default="unknown",
        choices=[
            ("unknown", "Unknown"),
            ("info", "Info"),
            ("warning", "Warning"),
            ("blocking", "Blocking"),
        ],
    )
    event_version = models.PositiveBigIntegerField(default=0)
    rule_version = models.PositiveIntegerField(null=True)
    run = models.ForeignKey(
        "collection.Run", null=True, on_delete=models.CASCADE, related_name="quality_issues"
    )
    run_item = models.ForeignKey(
        "collection.RunItem", null=True, on_delete=models.CASCADE, related_name="quality_issues"
    )
    receipt = models.ForeignKey(
        Receipt, null=True, on_delete=models.CASCADE, related_name="quality_issues"
    )
    handler = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    last_action = models.ForeignKey(
        "accounts.GovernanceAction", null=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta(Event.Meta):
        indexes = [models.Index(fields=["product", "status", "-id"], name="quality_product_status")]
        constraints = [
            *Event.Meta.constraints,
            models.CheckConstraint(
                condition=Q(run_item__isnull=True) | Q(run__isnull=False),
                name="quality_item_has_run",
            ),
            models.UniqueConstraint(
                fields=["run", "code"],
                condition=Q(
                    run__isnull=False,
                    run_item__isnull=True,
                    city_key__isnull=True,
                    station_key__isnull=True,
                ),
                name="quality_run_code",
            ),
            models.UniqueConstraint(
                fields=["run_item", "code"],
                condition=Q(run_item__isnull=False),
                name="quality_item_code",
            ),
        ]


class CandidateClearance(models.Model):
    """Evidence that a retained candidate was superseded by a governed source correction."""

    city_revision = models.OneToOneField(CityRevision, null=True, on_delete=models.CASCADE)
    station_revision = models.OneToOneField(StationRevision, null=True, on_delete=models.CASCADE)
    correction = models.ForeignKey(Receipt, on_delete=models.CASCADE)
    action = models.ForeignKey("accounts.GovernanceAction", on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(city_revision__isnull=True, station_revision__isnull=False)
                | Q(city_revision__isnull=False, station_revision__isnull=True),
                name="quality_clearance_one_side",
            )
        ]


class Change(Event):
    kind = text(32)
    rights_version = models.PositiveBigIntegerField(default=0)
    qualification_version = models.PositiveBigIntegerField(default=0)


class CatalogChange(Period):
    change = models.OneToOneField(Change, on_delete=models.CASCADE)
    action = models.ForeignKey("accounts.GovernanceAction", on_delete=models.PROTECT)
    epoch = models.ForeignKey(SiteEpoch, on_delete=models.CASCADE)
    identifier = models.ForeignKey(StationIdentifier, on_delete=models.CASCADE)
    label = models.ForeignKey(
        StationIdentifier, null=True, on_delete=models.CASCADE, related_name="label_changes"
    )


class QualityDecision(models.Model):
    action = models.OneToOneField("accounts.GovernanceAction", on_delete=models.CASCADE)
    city_before = models.ForeignKey(
        CityRevision, null=True, on_delete=models.CASCADE, related_name="decisions_before"
    )
    city_after = models.ForeignKey(
        CityRevision, null=True, on_delete=models.CASCADE, related_name="decisions_after"
    )
    station_before = models.ForeignKey(
        StationRevision, null=True, on_delete=models.CASCADE, related_name="decisions_before"
    )
    station_after = models.ForeignKey(
        StationRevision, null=True, on_delete=models.CASCADE, related_name="decisions_after"
    )
    qualification_before = models.PositiveBigIntegerField()
    qualification_after = models.PositiveBigIntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        city_before__isnull=False,
                        city_after__isnull=False,
                        station_before__isnull=True,
                        station_after__isnull=True,
                    )
                    | Q(
                        city_before__isnull=True,
                        city_after__isnull=True,
                        station_before__isnull=False,
                        station_after__isnull=False,
                    )
                ),
                name="quality_decision_one_level",
            )
        ]


class DispositionJob(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    due_at = models.DateTimeField(db_index=True)
    completed_at = models.DateTimeField(null=True)
    boundary_grant = models.ForeignKey(UseGrant, null=True, on_delete=models.PROTECT)
    boundary_version = models.PositiveBigIntegerField(null=True)
    # Grant IDs visible at the loss, captured before any subsequent rights mutation.
    boundary_basis = models.JSONField(null=True)
    reason = text(32)
    error_code = text(blank=True)


class MappingLink(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    city_old = models.ForeignKey(
        CityObservation, null=True, on_delete=models.CASCADE, related_name="outgoing_mapping"
    )
    city_new = models.ForeignKey(
        CityObservation, null=True, on_delete=models.CASCADE, related_name="incoming_mapping"
    )
    station_old = models.ForeignKey(
        StationObservation, null=True, on_delete=models.CASCADE, related_name="outgoing_mapping"
    )
    station_new = models.ForeignKey(
        StationObservation, null=True, on_delete=models.CASCADE, related_name="incoming_mapping"
    )
    rule = models.PositiveIntegerField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reason = text()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    city_old__isnull=False,
                    city_new__isnull=False,
                    station_old__isnull=True,
                    station_new__isnull=True,
                )
                | Q(
                    city_old__isnull=True,
                    city_new__isnull=True,
                    station_old__isnull=False,
                    station_new__isnull=False,
                ),
                name="mapping_one_pair",
            )
        ]
