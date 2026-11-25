"""Prove historical first validity for the keys in a single authorized collection window."""

from collections import defaultdict
from datetime import timedelta

from collection.admission import LEDGER, cell_key
from collection.models import WorkWindow
from collection.queries import window_permission
from observations.models import CandidateClearance, Change, QualityDecision, Receipt
from observations.policy import Denied, Scope, applicable
from observations.revisions import key_scope, tables

# Bound history work without returning a time proved from only a prefix.
MAX_KEYS = 4_096
MAX_EVIDENCE = 32_768
ORIGINS = {
    "initial": "first",
    "source_revision": "content_changed",
    "same_version_conflict": "same_version_conflict",
    "unordered_conflict": "unordered_conflict",
}
QUALIFICATION = {"qualification_changed", "mapping_retired"}
SOURCE_PROOFS = {"first", "content_changed", "watermark_advanced"}


class Unproved(Exception):
    pass


def bounded(rows, limit=MAX_EVIDENCE):
    entries = list(rows[: limit + 1])
    if len(entries) > limit:
        raise Unproved
    return entries


class FirstWrite:
    def __init__(self, window_id, selectors, now, visible_keys):
        # read_window has already locked this product in the enclosing transaction.
        self.window = WorkWindow.objects.select_related("product").get(pk=window_id)
        self.product = self.window.product
        self.job = self.window.runs.order_by("created_at").select_related("job__city").first().job
        self.code = selectors.get("object_code")
        self.indicator = selectors.get("indicator")
        self.visible_keys = set(visible_keys)
        self.grants = list(
            self.product.usegrant_set.filter(
                operation__in=("review", "retain_normalized"), revoked_at__isnull=True
            )
        )
        self.now = now
        self.uses = set()
        self.projection = self.permit(now)
        self.obs, self.revision, self.entity, self.change_key, self.receipt_key = tables(
            self.product.level
        )

    def permit(self, now):
        return window_permission(self.window, self.job, self.grants, now, self.code, self.indicator)

    def require_key(self, key):
        if key.product_id != self.product.pk:
            raise Unproved
        scope = key_scope(key)
        if scope not in self.uses:
            try:
                self.require_scope(scope, self.now)
            except Denied:
                raise Unproved from None
            self.uses.add(scope)

    def require_scope(self, scope, now):
        for operation in ("review", "retain_normalized"):
            applicable(self.product, operation, scope, LEDGER, now, self.grants)

    def finish(self, now):
        if self.projection != self.permit(now):
            raise Denied()
        for scope in self.uses:
            self.require_scope(scope, now)

    def keys(self):
        if self.window.disposed:
            raise Unproved
        _, allowed, indicators = self.projection
        items = self.window.items.filter(accepted=True)
        if self.code:
            items = items.filter(object_code=self.code)
        if self.indicator:
            items = items.filter(indicator=self.indicator)
        if allowed is not None:
            items = items.filter(item_key__in=allowed)
        if indicators is not None:
            items = items.filter(indicator__in=indicators)
        if self.visible_keys:
            items = items.filter(item_key__in=self.visible_keys)
        else:
            return set()
        accepted = bounded(
            items.values(
                "id", "item_key", "object_code", "indicator", "observed_at", "run__batch_id"
            ),
            MAX_KEYS,
        )
        if not accepted:
            return set()
        by_id = {item["id"]: item for item in accepted}
        ids = set()
        lineage = set()
        fk = self.receipt_key
        key_fields = f"{fk}__key__"
        side = "city" if self.entity == "city" else "site_epoch__city"
        receipts = Receipt.objects.filter(collection_item_id__in=by_id)
        rows = receipts.values_list(
            "collection_item_id",
            "batch_id",
            "batch__product_id",
            "batch__redacted",
            f"{fk}__key_id",
            key_fields + "product_id",
            key_fields + self.entity + "_id",
            key_fields + "object_code",
            key_fields + "indicator",
            key_fields + "observed_at_utc",
            key_fields + side + "__geography",
            key_fields + "source_local_date",
        )
        for (
            item_id,
            batch,
            batch_product,
            redacted,
            key,
            product,
            entity,
            code,
            ind,
            at,
            geo,
            day,
        ) in bounded(rows):
            item = by_id[item_id]
            if (
                redacted
                or batch != item["run__batch_id"]
                or batch_product != self.product.pk
                or product != self.product.pk
                or key is None
                or code != item["object_code"]
                or ind != item["indicator"]
                or at is None
                or at != item["observed_at"]
                or cell_key(entity, at, ind) != item["item_key"]
            ):
                raise Unproved
            scope = Scope(geo, code, ind, day, day + timedelta(days=1))
            if scope not in self.uses:
                try:
                    self.require_scope(scope, self.now)
                except Denied:
                    raise Unproved from None
                self.uses.add(scope)
            lineage.add(item_id)
            ids.add(key)
        if len(lineage) != len(by_id) or len(ids) > MAX_KEYS:
            raise Unproved
        return ids

    def latest(self):
        keys = self.keys()
        if not keys:
            return None
        revisions = defaultdict(list)
        for row in bounded(
            self.revision.objects.filter(key_id__in=keys)
            .only(
                "id",
                "key_id",
                "revision_no",
                "kind",
                "candidate",
                "is_current",
                "value",
                "quality",
                "created_at",
                "rule_version",
                "source_token",
            )
            .order_by("key_id", "revision_no")
        ):
            revisions[row.key_id].append(row)
        fk = self.receipt_key
        origins = defaultdict(list)
        for revision_id, batch_id, outcome, redacted in bounded(
            Receipt.objects.filter(
                batch__product=self.product,
                outcome__in=ORIGINS.values(),
                **{f"{fk}__key_id__in": keys},
            )
            .order_by("pk")
            .values_list(f"{fk}_id", "batch_id", "outcome", "batch__redacted")
        ):
            origins[revision_id].append((batch_id, outcome, redacted))
        changes = defaultdict(list)
        for row in bounded(
            Change.objects.filter(
                product=self.product, kind__in=QUALIFICATION, **{f"{self.change_key}_id__in": keys}
            ).order_by("pk")
        ):
            changes[getattr(row, self.change_key + "_id")].append(row)
        level = self.product.level
        before, after = (f"{level}_{part}" for part in ("before", "after"))
        related = "city" if self.entity == "city" else "site_epoch__city"
        decisions = defaultdict(list)
        source_actions = set()
        for row in bounded(
            QualityDecision.objects.filter(
                **{f"{after}__key_id__in": keys},
                action__target_kind="quality",
                action__outcome="applied",
            ).select_related("action", f"{before}__key__{related}", after)
        ):
            decisions[getattr(row, after).key_id].append(row)
            if row.action.action == "correct_source":
                source_actions.add(row.action_id)
        source_clearances = defaultdict(list)
        if source_actions:
            for row in bounded(
                CandidateClearance.objects.filter(action_id__in=source_actions).select_related(
                    "correction__batch"
                )
            ):
                source_clearances[row.action_id].append(row)
        owners = self.obs.objects.filter(pk__in=keys).only(
            "pk", "qualification_version", "disputed", "retired"
        )
        latest = None
        for key in owners:
            at = self.first(
                key,
                revisions[key.pk],
                origins,
                changes[key.pk],
                decisions[key.pk],
                before,
                after,
                source_clearances,
            )
            if at is not None and (latest is None or latest < at):
                latest = at
        return latest

    def require_source_clearance(self, head, candidates, clearances):
        # The adjudication service clears every then-active candidate using the same
        # newer ordered receipt. A surviving subset cannot prove that transition.
        if not candidates or len(clearances) != len(candidates):
            raise Unproved
        correction = clearances[0].correction
        if (
            {getattr(c, self.receipt_key + "_id") for c in clearances} != candidates.keys()
            or any(c.correction_id != correction.pk for c in clearances)
            or getattr(correction, self.receipt_key + "_id") != head.pk
            or correction.batch.product_id != self.product.pk
            or correction.batch.redacted
            or correction.outcome not in SOURCE_PROOFS
            or correction.rule_version != head.rule_version
            or correction.source_token is None
            or any(
                c.kind not in {"same_version_conflict", "unordered_conflict"}
                or c.rule_version != head.rule_version
                or c.source_token is None
                or c.source_token >= correction.source_token
                for c in candidates.values()
            )
        ):
            raise Unproved

    def first(self, key, revisions, origins, changes, decisions, before, after, source_clearances):
        if (
            not revisions
            or [r.revision_no for r in revisions] != list(range(1, len(revisions) + 1))
            or any(
                a.created_at >= b.created_at for a, b in zip(revisions, revisions[1:], strict=False)
            )
            or [c.qualification_version for c in changes]
            != list(range(2, key.qualification_version + 1))
            or any(a.created_at >= b.created_at for a, b in zip(changes, changes[1:], strict=False))
        ):
            raise Unproved
        mappings = {}
        clearances = {}
        for decision in decisions:
            after_revision = getattr(decision, after)
            before_revision = getattr(decision, before)
            action = decision.action
            if action.action == "correct_mapping" and after_revision.kind == "local_mapping_fix":
                if after_revision.pk in mappings:
                    raise Unproved
                mappings[after_revision.pk] = decision
            if (
                before_revision.key_id == key.pk
                and action.after_state == "resolved"
                and decision.qualification_after == decision.qualification_before + 1
                and action.action in {"correct_source", "correct_mapping"}
            ):
                version = decision.qualification_after
                if version in clearances:
                    raise Unproved
                clearances[version] = decision

        # A batch may create multiple revisions of a key in one commit. An intermediate
        # head or a head immediately disputed by a candidate in that batch was not valid.
        final_heads = {}
        batch_by_revision = {}
        conflicting_batches = set()
        for row in revisions:
            if row.kind not in ORIGINS:
                if row.kind not in {"local_mapping_fix", "mapping_conflict"}:
                    raise Unproved
                continue
            receipts = origins[row.pk]
            if not receipts or receipts[0][1] != ORIGINS[row.kind] or receipts[0][2]:
                raise Unproved
            batch = receipts[0][0]
            batch_by_revision[row.pk] = batch
            if row.candidate:
                conflicting_batches.add(batch)
            else:
                if len(receipts) != 1:
                    raise Unproved
                final_heads[batch] = row.pk

        head = None
        active_candidates = {}
        disputed = retired = False
        first = None
        stream = sorted(
            [(r.created_at, False, r) for r in revisions]
            + [(c.created_at, True, c) for c in changes]
        )
        seen = None
        for at, qualification, row in stream:
            if seen == at or at > self.now:
                raise Unproved
            seen = at
            if qualification:
                if row.kind == "mapping_retired":
                    if retired:
                        raise Unproved
                    retired = True
                elif not disputed:
                    disputed = True
                else:
                    decision = clearances.get(row.qualification_version)
                    if (
                        decision is None
                        or head is None
                        or getattr(decision, after + "_id") != head.pk
                    ):
                        raise Unproved
                    action = decision.action
                    if action.action == "correct_source":
                        # The correction clears qualification without appending a head.
                        if (
                            getattr(decision, before + "_id") != head.pk
                            or not head.created_at < action.created_at <= at
                        ):
                            raise Unproved
                        self.require_source_clearance(
                            head, active_candidates, source_clearances[action.pk]
                        )
                        active_candidates.clear()
                    elif not (
                        head.pk in mappings
                        and head.created_at < at <= action.created_at <= self.now
                    ):
                        raise Unproved
                    disputed = False
                    if first is None and not retired and valid(head):
                        first = action.created_at
                continue
            if row.candidate:
                if row.kind not in {
                    "same_version_conflict",
                    "unordered_conflict",
                    "mapping_conflict",
                }:
                    raise Unproved
                active_candidates[row.pk] = row
                continue
            if retired:
                raise Unproved
            head = row
            transition_at = at
            if row.kind == "local_mapping_fix":
                decision = mappings.get(row.pk)
                if decision is None or not at <= decision.action.created_at <= self.now:
                    raise Unproved
                self.require_key(getattr(decision, before).key)
                if decision.action.after_state != "resolved":
                    # The legal mapping service may append an unqualified head, but
                    # that alone cannot prove a transition to validity.
                    if not disputed:
                        raise Unproved
                    continue
                transition_at = decision.action.created_at
            elif row.kind == "mapping_conflict":
                raise Unproved
            else:
                batch = batch_by_revision[row.pk]
                if final_heads[batch] != row.pk or batch in conflicting_batches:
                    continue
            if first is None and not disputed and valid(row):
                first = transition_at
        # Check final integrity only; current flags cannot decide past eligibility.
        if (
            head is None
            or not head.is_current
            or (disputed, retired) != (key.disputed, key.retired)
        ):
            raise Unproved
        return first


def valid(revision):
    return revision.quality == "valid" and revision.value is not None
