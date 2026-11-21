"""Rights preflight and cleanup without distributing real-source observation bytes."""

from datetime import timedelta

import pytest
from django.utils import timezone
from observations.disposition import dispose_due, schedule
from observations.history import check_import
from observations.models import Batch, ImportAttempt, QualityIssue, StagedInput, UseGrant
from observations.policy import REQUIRED, Denied, Scope, register_grant, revoke_grant
from observations.uci_catalog import register_uci
from s02_support import BOUNDARY, START, allow, candidate, create_product, create_site
from s02_support import ingest_fixture as ingest_batch

pytestmark = pytest.mark.django_db(transaction=True)


def test_uci_preflight_accepts_complete_independent_days_but_requires_fingerprint_right(
    django_user_model,
):
    user = django_user_model.objects.create_user("maintainer", is_superuser=True)
    product = register_uci(user.pk)
    grant = UseGrant.objects.get(
        product=product,
        operation="retain_normalized",
        object_code="Changping",
        data_from="2013-03-01",
    )
    revoke_grant(user.pk, grant.pk, "daily bases")
    with pytest.raises(Denied):
        check_import(product, user.pk)
    for day in (grant.data_from, grant.data_from + timedelta(days=1)):
        register_grant(
            user.pk,
            product.pk,
            operation=grant.operation,
            audience=grant.audience,
            geography=grant.geography,
            object_code=grant.object_code,
            fields=list(REQUIRED),
            data_from=day,
            data_until=day + timedelta(days=1),
            terms=grant.terms,
            terms_version=grant.terms_version,
        )
    check_import(product, user.pk)
    fingerprint = UseGrant.objects.get(product=product, fields=["batch_identity"])
    revoke_grant(user.pk, fingerprint.pk, "material identity withdrawal")
    with pytest.raises(Denied):
        check_import(product, user.pk)


def test_pending_attempt_is_terminal_after_its_unconsumed_file_expires(
    django_user_model, settings, tmp_path
):
    user = django_user_model.objects.create_user("owner", is_superuser=True)
    product = create_product()
    settings.S02_STAGE_DIR = str(tmp_path)
    tmp_path.chmod(0o700)
    stage = StagedInput.objects.create(
        owner=user,
        product=product,
        expires_at=timezone.now() - timedelta(seconds=1),
        state="claimed",
    )
    attempt = ImportAttempt.objects.create(
        product=product,
        stage=stage,
        actor=user,
        reason="historical_verification",
        dispatch="unconfirmed",
    )
    (tmp_path / str(stage.pk)).write_bytes(b"F")
    schedule(product, stage.expires_at, "stage_expiry")
    dispose_due(product.pk)
    attempt.refresh_from_db()
    stage.refresh_from_db()
    assert (attempt.state, attempt.error_code, stage.state) == (
        "rejected",
        "invalid_stage",
        "cleared",
    )
    assert not (tmp_path / str(stage.pk)).exists()


def test_unbound_issues_in_a_mixed_batch_are_reconciled_independently(django_user_model):
    user = django_user_model.objects.create_user("governor", is_superuser=True)
    product = create_product()
    create_site(product)
    allow(user, product, "process_local")
    authorized = allow(user, product)
    allow(user, product, object_code="001")
    batch, _ = ingest_batch(
        user.pk,
        product.pk,
        "F-mixed",
        "f-v1",
        [candidate(product, 1), candidate(product, 1, code="unknown")],
    )
    issue = QualityIssue.objects.get(batch=batch, code="identity_unresolved")
    assert Scope.from_issue(issue.scope) == Scope(
        "*", "unknown", "CO", START.date(), BOUNDARY.date()
    )
    revoke_grant(user.pk, authorized.pk, "narrow scope")
    dispose_due(product.pk)
    assert not QualityIssue.objects.filter(pk=issue.pk).exists()
    assert batch.receipts.count() == 1 and Batch.objects.get(pk=batch.pk).identity is None


def test_registered_fixture_identity_cannot_embed_source_material(django_user_model):
    from observations.revisions import fixture_ticket
    from observations.revisions import ingest_batch as actual_ingest

    user = django_user_model.objects.create_user("maintainer", is_superuser=True)
    product = create_product()
    create_site(product)
    allow(user, product, "process_local")
    allow(user, product)
    data = [candidate(product, 1)]
    profile = product.contract_version
    for identity in ("a" * 64, fixture_ticket(product.pk + 1)):
        with pytest.raises(Denied, match="invalid_batch"):
            actual_ingest(user.pk, product.pk, identity, profile, data)
    assert Batch.objects.filter(product=product).count() == 0
    ticket = fixture_ticket(product.pk)
    batch, created = actual_ingest(user.pk, product.pk, ticket, profile, data)
    assert created and actual_ingest(user.pk, product.pk, ticket, profile, data) == (batch, False)
    assert batch.identity not in ("a" * 64, ticket)


def test_current_read_cannot_detect_unauthorized_sibling_code(django_user_model):
    from observations.models import StationIdentifier
    from observations.queries import read_current
    from s02_support import selector

    user = django_user_model.objects.create_user("maintainer", is_superuser=True)
    product = create_product()
    epoch = create_site(product)
    first = StationIdentifier.objects.get(station=epoch.station)
    StationIdentifier.objects.create(
        station=epoch.station,
        source=product.source,
        kind="code",
        code="002",
        active_period=first.active_period,
        evidence="F second code",
    )
    allow(user, product, "process_local")
    allow(user, product)
    allow(user, product, "display", object_code="001")
    args = selector(product, epoch)
    before = read_current(user.pk, args)
    assert before["count"] == 0
    ingest_batch(user.pk, product.pk, "controlled", "f-v1", [candidate(product, 1, code="002")])
    assert read_current(user.pk, args) == before


def test_correction_requires_target_review_and_preserves_target_dispute(django_user_model):
    from decimal import Decimal

    from observations.models import MappingLink, StationObservation
    from observations.revisions import correct_mapping

    user = django_user_model.objects.create_user("maintainer", is_superuser=True)
    product = create_product()
    first = create_site(product)
    target = create_site(product, code="002", geography="B")
    allow(user, product, "process_local")
    allow(user, product)
    allow(user, product, "review", object_code="001")
    original = candidate(product, 1, "10000", unit="mg/m³")
    ingest_batch(user.pk, product.pk, "original", "f-v1", [original])
    for i, n in enumerate(("10", "20")):
        ingest_batch(
            user.pk, product.pk, "B" + str(i), "f-v1", [candidate(product, 1, n, code="002")]
        )
    key = StationObservation.objects.get(site_epoch=first)
    old = key.revisions.get(is_current=True)
    proposed = candidate(product, 1, "10000", code="002", unit="µg/m³", rule=2)
    assert proposed.data.value == Decimal("10")

    def correct():
        return correct_mapping(
            user.pk,
            product.pk,
            "station",
            key.pk,
            old.pk,
            key.watermark,
            proposed,
            "F correction evidence",
        )

    with pytest.raises(Denied, match="use_denied"):
        correct()
    allow(user, product, "review", object_code="002")
    assert correct() == "mapping_review_required"
    assert not MappingLink.objects.exists()
    b = StationObservation.objects.get(site_epoch=target)
    assert (
        b.disputed
        and b.revisions.count() == 2
        and b.revisions.get(is_current=True).value == Decimal("10")
    )
