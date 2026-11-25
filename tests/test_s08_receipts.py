"""Pure request-boundary and DTO checks for committed command receipt lookup."""

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from accounts import s08_receipts as receipts
from django.core import signing
from observations.policy import Denied
from rest_framework.test import APIRequestFactory, force_authenticate


def test_identifier_and_uuid_accept_only_bounded_canonical_values():
    assert receipts.identifier(12) == 12
    assert receipts.uuid(str(uuid4()))

    for value in (True, 0, 2**63, "12"):
        with pytest.raises(Denied):
            receipts.identifier(value)
    with pytest.raises(Denied):
        receipts.uuid("not-a-uuid")
    with pytest.raises(Denied):
        receipts.uuid("2C13800F-BC27-45E9-8D1C-F2B785B921F6")


def test_governance_result_is_a_minimal_committed_receipt():
    record = SimpleNamespace(
        outcome="applied",
        after_state="resolved",
        action="close",
        created_at=SimpleNamespace(isoformat=lambda: "2024-01-01T00:00:00+00:00"),
        pk=uuid4(),
        after_version=4,
    )

    result = receipts.governance_result(record)

    assert result == {
        "state": "resolved",
        "action": "close",
        "recorded_at": "2024-01-01T00:00:00+00:00",
        "action_id": str(record.pk),
        "version": 4,
    }
    assert not {"parameter", "origin", "reason_code"} & result.keys()


def test_run_status_does_not_expose_execution_result():
    run = SimpleNamespace(pk=uuid4(), window_id=42, dispatch="sent", status="succeeded")
    assert receipts.run_status(run) == {
        "run_id": str(run.pk),
        "window_id": 42,
        "dispatch": "sent",
        "status": "succeeded",
    }


def test_signed_proof_can_identify_old_receipt_but_requires_the_original_salt(monkeypatch):
    monkeypatch.setattr(signing.TimestampSigner, "timestamp", lambda self: "1")
    salt = "s08.receipt.test"
    value = [1, 2, 3, "correct_mapping", "edition", []]
    token = signing.dumps(value, salt=salt)
    with pytest.raises(signing.SignatureExpired):
        signing.loads(token, salt=salt, max_age=1800)

    assert receipts.submitted_proof(token, salt, length=6) == value
    with pytest.raises(Denied):
        receipts.submitted_proof(token, "another-purpose", length=6)


def test_lookup_rejects_malformed_envelopes_without_dispatch(monkeypatch):
    monkeypatch.setattr(receipts, "limited", lambda *args: False)
    request = APIRequestFactory().post(
        "/api/v1/admin/command-receipts/lookup/",
        {"family": "manual_run", "target": 1, "command": {}, "extra": True},
        format="json",
    )
    force_authenticate(request, user=SimpleNamespace(pk=91, is_authenticated=True))
    called = []
    monkeypatch.setitem(receipts.LOOKUPS, "manual_run", lambda *args: called.append(args))

    response = receipts.lookup(request)

    assert response.status_code == 400
    assert response.data == {"code": "invalid_request"}
    assert called == []


def test_lookup_dispatches_one_post_and_is_not_cacheable(monkeypatch):
    monkeypatch.setattr(receipts, "limited", lambda *args: False)
    user = SimpleNamespace(pk=91, is_authenticated=True)
    request = APIRequestFactory().post(
        "/api/v1/admin/command-receipts/lookup/",
        {"family": "manual_run", "target": 42, "command": {}},
        format="json",
    )
    force_authenticate(request, user=user)
    calls = []

    def resolve(request, target, command):
        calls.append((request.user.pk, target, command))
        return {"state": "submitted", "run_id": "run-id", "status": "pending"}

    @contextmanager
    def transaction():
        yield

    monkeypatch.setitem(receipts.LOOKUPS, "manual_run", resolve)
    monkeypatch.setattr(receipts, "request_transaction", transaction)

    response = receipts.lookup(request)

    assert response.status_code == 200
    assert response.data == {"state": "submitted", "run_id": "run-id", "status": "pending"}
    assert calls == [(91, 42, {})]
    assert "no-store" in response["Cache-Control"]


def test_lookup_rejects_get():
    request = APIRequestFactory().get("/api/v1/admin/command-receipts/lookup/")
    force_authenticate(request, user=SimpleNamespace(pk=91, is_authenticated=True))

    response = receipts.lookup(request)

    assert response.status_code == 405
