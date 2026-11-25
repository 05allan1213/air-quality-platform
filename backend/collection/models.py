"""PostgreSQL collection ledger. Only domain services change its state."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from observations.models import Batch, City, Product, UseGrant, text

USER = settings.AUTH_USER_MODEL


class Job(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    city = models.ForeignKey(City, null=True, on_delete=models.PROTECT)
    executor = models.ForeignKey(USER, on_delete=models.PROTECT)
    profile = text(32)
    expected_codes = models.JSONField(null=True)
    roster_basis = text(blank=True)
    selection = models.JSONField(default=dict, blank=True)
    standard = models.JSONField(default=dict, blank=True)
    backfill_policy = models.JSONField(default=dict, blank=True)
    backfill_cursor = models.DateTimeField(null=True)
    validation_grant = models.ForeignKey(
        UseGrant, null=True, on_delete=models.PROTECT, related_name="+"
    )
    disposed = models.BooleanField(default=False)
    cadence_seconds = models.PositiveIntegerField(default=3600)
    next_due_at = models.DateTimeField(null=True, db_index=True)
    enabled = models.BooleanField(default=False)
    control_version = models.PositiveBigIntegerField(default=0)
    pause_reason = text(blank=True)
    plan_state = text(12, default="legacy")
    schedule_template = models.ForeignKey(
        "ScheduleTemplate", null=True, on_delete=models.PROTECT, related_name="plans"
    )
    schedule_template_version = models.PositiveBigIntegerField(null=True)
    schedule_anchor_at = models.DateTimeField(null=True)
    effective_from = models.DateTimeField(null=True)
    effective_until = models.DateTimeField(null=True)
    predecessor = models.ForeignKey("self", null=True, on_delete=models.PROTECT, related_name="+")
    pending_successor = models.OneToOneField(
        "self", null=True, on_delete=models.PROTECT, related_name="+"
    )
    handoff_checked = models.BooleanField(default=False)
    changed_at = models.DateTimeField(default=timezone.now)
    changed_by = models.ForeignKey(USER, null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(cadence_seconds__gte=1), name="s03_positive_cadence"
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        plan_state="legacy",
                        schedule_anchor_at=None,
                        effective_from=None,
                        effective_until=None,
                        predecessor=None,
                        pending_successor=None,
                        schedule_template=None,
                        schedule_template_version=None,
                    )
                    | Q(
                        plan_state__in=("draft", "discarded"),
                        enabled=False,
                        next_due_at=None,
                        effective_from=None,
                        effective_until=None,
                        pending_successor=None,
                        schedule_anchor_at__isnull=False,
                        schedule_template__isnull=False,
                        schedule_template_version__isnull=False,
                    )
                    | (
                        Q(
                            plan_state="active",
                            effective_from__isnull=False,
                            schedule_anchor_at__isnull=False,
                            schedule_template__isnull=False,
                            schedule_template_version__isnull=False,
                        )
                        & (Q(effective_until=None) | Q(effective_until__gt=F("effective_from")))
                    )
                ),
                name="s08_schedule_segment",
            ),
            models.UniqueConstraint(
                fields=("predecessor",),
                condition=Q(plan_state__in=("draft", "active")),
                name="s08_schedule_successor",
            ),
            models.CheckConstraint(
                condition=Q(disposed=False)
                | Q(
                    enabled=False,
                    next_due_at=None,
                    expected_codes=None,
                    selection={},
                    roster_basis="",
                    standard={},
                    backfill_policy={},
                ),
                name="cnemc_disposed_job_empty",
            ),
        ]


class ScheduleTemplate(models.Model):
    """Host-approved frequencies for an already registered collection contract."""

    basis_job = models.OneToOneField(Job, on_delete=models.PROTECT, related_name="host_template")
    version = models.PositiveBigIntegerField(default=1)
    allowed_cadences = models.JSONField()
    delivery_minute = models.PositiveSmallIntegerField(default=0)

    class Meta:
        default_permissions = ()


class ScheduleTemplateRevision(models.Model):
    template = models.ForeignKey(
        ScheduleTemplate, on_delete=models.PROTECT, related_name="revisions"
    )
    version = models.PositiveBigIntegerField()
    allowed_cadences = models.JSONField()
    delivery_minute = models.PositiveSmallIntegerField(default=0)
    actor = models.ForeignKey(USER, null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=("template", "version"), name="s08_template_revision")
        ]


class WorkWindow(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    scope_key = text()
    target_start = models.DateTimeField()
    target_end = models.DateTimeField()
    profile = text(32)
    # An independently registered catalogue snapshot, never inferred from a response.
    expected = models.JSONField(null=True)
    roster_basis = text(blank=True)
    selection = models.JSONField(default=dict, blank=True)
    standard = models.JSONField(default=dict, blank=True)
    generation = models.PositiveBigIntegerField(default=0)
    owner = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True, db_index=True)
    closed_at = models.DateTimeField(null=True)
    result = text(24, default="pending")
    disposed = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "scope_key", "target_start", "target_end", "profile"],
                name="s03_business_window",
            ),
            models.CheckConstraint(
                condition=Q(target_end__gt=F("target_start")), name="s03_positive_window"
            ),
            models.CheckConstraint(
                condition=Q(owner=None, lease_until=None)
                | Q(owner__isnull=False, lease_until__isnull=False, generation__gt=0),
                name="s03_window_fence",
            ),
        ]


class Run(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    window = models.ForeignKey(WorkWindow, on_delete=models.PROTECT, related_name="runs")
    job = models.ForeignKey(Job, on_delete=models.PROTECT)
    actor = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="+")
    executor = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="+")
    reason = text(16)
    request_id = models.UUIDField(null=True)
    scheduled_for = models.DateTimeField(null=True)
    predecessor = models.ForeignKey(
        "self", null=True, on_delete=models.PROTECT, related_name="successors"
    )
    attempt = models.PositiveSmallIntegerField(default=1)
    status = text(16, default="pending", db_index=True)
    error_code = text(64, blank=True)
    result = text(24, blank=True)
    dispatch = text(16, default="pending")
    deliveries = models.PositiveSmallIntegerField(default=0)
    control_version = models.PositiveBigIntegerField(default=0)
    dispatch_due = models.DateTimeField(null=True, db_index=True)
    next_retry_at = models.DateTimeField(null=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    owner = models.UUIDField(null=True)
    generation = models.PositiveBigIntegerField(default=0)
    endpoint = text(4, blank=True)
    validation_grant = models.ForeignKey(
        UseGrant, null=True, on_delete=models.PROTECT, related_name="+"
    )
    request_state = text(16, default="unsent")
    reserved_at = models.DateTimeField(null=True, db_index=True)
    occupied_until = models.DateTimeField(null=True)
    fetched_at = models.DateTimeField(null=True)
    manifest_count = models.PositiveIntegerField(null=True)
    batch = models.OneToOneField(
        Batch, null=True, on_delete=models.SET_NULL, related_name="collection_run"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["id", "window"], name="s03_run_window_pair"),
            models.UniqueConstraint(fields=["actor", "request_id"], name="s03_manual_identity"),
            models.UniqueConstraint(fields=["job", "scheduled_for"], name="s03_beat_slot"),
            models.UniqueConstraint(
                fields=["predecessor"], condition=Q(reason="retry"), name="s03_retry_successor"
            ),
            models.CheckConstraint(
                condition=Q(
                    status__in=[
                        "pending",
                        "running",
                        "finished",
                        "failed",
                        "paused",
                        "expired",
                        "coalesced",
                    ]
                ),
                name="s03_run_states",
            ),
            models.CheckConstraint(
                condition=~Q(status="running")
                | Q(owner__isnull=False, generation__gt=0, started_at__isnull=False),
                name="s03_running_owner",
            ),
            models.CheckConstraint(
                condition=Q(request_state="unsent", reserved_at=None)
                | Q(request_state__in=["possible", "responded"], reserved_at__isnull=False)
                | (
                    Q(
                        request_state="disposed",
                        validation_grant=None,
                        reserved_at=None,
                        occupied_until=None,
                    )
                    & ~Q(endpoint="")
                    & ~Q(status__in=["pending", "running"])
                ),
                name="s03_request_budget",
            ),
        ]


class RunItem(models.Model):
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="items")
    window = models.ForeignKey(WorkWindow, on_delete=models.PROTECT, related_name="items")
    item_key = text(160)
    object_code = text()
    indicator = text(32)
    observed_at = models.DateTimeField()
    generation = models.PositiveBigIntegerField()
    owner = models.UUIDField()
    accepted = models.BooleanField(default=False)
    status = text(16)
    reason = text(64, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "item_key"], name="s03_run_item"),
            models.UniqueConstraint(
                fields=["window", "item_key"],
                condition=Q(accepted=True),
                name="s03_window_accept_once",
            ),
            models.CheckConstraint(
                condition=Q(generation__gt=0)
                & (
                    Q(status="accepted", accepted=True)
                    | Q(status__in=["reread", "absent", "rejected"], accepted=False)
                ),
                name="s03_item_states",
            ),
        ]


class ControlEvent(models.Model):
    job = models.ForeignKey(Job, on_delete=models.PROTECT)
    actor = models.ForeignKey(USER, null=True, on_delete=models.PROTECT)
    action = text(24)
    occurred_at = models.DateTimeField(default=timezone.now)
    # Half-open cadence range skipped after a long outage; no response or object identities.
    slot_start = models.DateTimeField(null=True)
    slot_end = models.DateTimeField(null=True)
    run = models.ForeignKey(Run, null=True, on_delete=models.PROTECT)
    receipt = models.OneToOneField(
        "accounts.GovernanceAction", null=True, on_delete=models.SET_NULL
    )
    legacy_protocol = models.BooleanField(default=False)
    response_enabled = models.BooleanField(null=True)
    response_reason = text(64, blank=True)
    schedule_response = models.JSONField(null=True)


class CollectionControl(models.Model):
    """Source and Product control versions, shared across all referencing Jobs."""

    source = models.OneToOneField("observations.Source", null=True, on_delete=models.CASCADE)
    product = models.OneToOneField(Product, null=True, on_delete=models.CASCADE)
    version = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(source__isnull=True, product__isnull=False)
                | Q(source__isnull=False, product__isnull=True),
                name="s06_control_target",
            ),
        ]
