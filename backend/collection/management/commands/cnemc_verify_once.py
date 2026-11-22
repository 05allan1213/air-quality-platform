"""Operator-only field verification, called explicitly inside the local Web container."""

from decimal import Decimal
from getpass import getpass
from time import monotonic, sleep
from uuid import UUID

import httpx
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from observations.models import Receipt
from observations.policy import Denied, actor, clock

from collection.adapters.cnemc import ENDPOINTS
from collection.admission import admission, bounds, lock_job, parse_hour
from collection.cnemc_jobs import check_total
from collection.models import Run


class Command(BaseCommand):
    help = "Verify one registered limited-mode A03 job through the loopback Session API, or re-read an existing run."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--job", required=True, type=int)
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--request-id", type=UUID, help="Explicit manual request identity")
        group.add_argument("--read-run", type=UUID, help="Verify stored results only")
        parser.add_argument("--target", help="Approved timezone-aware source hour for this request")

    def handle(self, **o):
        if o["request_id"] and settings.CNEMC_MODE != "limited":
            raise CommandError("limited_mode_required")
        try:
            user = get_user_model().objects.get(username=o["username"], is_active=True)
            with transaction.atomic():
                job = lock_job(o["job"])
                if (
                    job.product.source.namespace != "cnemc"
                    or job.product.synthetic
                    or job.product.code != "city-live-hour"
                    or not job.validation_grant_id
                    or job.next_due_at
                ):
                    raise Denied("invalid_validation_job")
                actor(user.pk, "review_observation")
                if o["request_id"]:
                    target = parse_hour(o["target"])
                    admission(job, user.pk, *bounds(job, target), endpoint="A03")
                    if not Run.objects.filter(
                        job=job, actor=user, request_id=o["request_id"]
                    ).exists():
                        check_total(job)
                    if target != clock().replace(minute=0, second=0, microsecond=0):
                        raise Denied("outside_target")
            with httpx.Client(
                base_url="http://127.0.0.1:8000",
                trust_env=False,
                follow_redirects=False,
                timeout=10.0,
            ) as session:

                def call(method, path, **kwargs):
                    response = session.request(method, path, **kwargs)
                    if response.status_code not in (200, 202):
                        raise Denied("session_request_rejected")
                    return response.json()

                csrf = "/api/v1/auth/csrf/"
                token = call("GET", csrf)["csrf_token"]
                call(
                    "POST",
                    "/api/v1/auth/login/",
                    headers={"X-CSRFToken": token},
                    json={"username": user.username, "password": getpass("Session password: ")},
                )
                run_id = o["read_run"]
                if o["request_id"]:
                    response = call(
                        "POST",
                        "/api/v1/admin/collection-runs/",
                        headers={"X-CSRFToken": call("GET", csrf)["csrf_token"]},
                        json={
                            "job": job.pk,
                            "target": target.isoformat(),
                            "reason": "manual",
                            "request_id": str(o["request_id"]),
                        },
                    )
                    run_id = UUID(response["run_id"])
                deadline = monotonic() + 610
                while True:
                    run = Run.objects.select_related("window").get(
                        job=job,
                        pk=run_id,
                        endpoint="A03",
                        validation_grant_id=job.validation_grant_id,
                    )
                    if run.status == "finished":
                        break
                    if run.status not in {"pending", "running"} or monotonic() >= deadline:
                        raise Denied("sample_unavailable")
                    sleep(1)
                projection = call(
                    "GET",
                    f"/api/v1/admin/collection-windows/{run.window_id}/",
                    params={"run": str(run.pk)},
                )
                if len(projection["receipts"]) != 7 or not run.batch_id:
                    raise Denied("seven_field_evidence_required")
                evidence = {r["id"] for r in projection["receipts"]}
                stamp = run.window.target_start
                # Read each field through its authorized Session contract before comparing the DB receipt.
                for indicator in ENDPOINTS["A03"].indicators:
                    series = "not_applicable"
                    if indicator == "AQI":
                        from zoneinfo import ZoneInfo

                        day = (
                            stamp.astimezone(ZoneInfo(job.product.source_timezone))
                            .date()
                            .isoformat()
                        )
                        v = run.window.standard
                        series = v["series"] if v and v["from"] <= day < v["until"] else "unknown"
                    query = {
                        "product": job.product_id,
                        "city": job.city_id,
                        "indicator": indicator,
                        "report_kind": "hourly",
                        "window_definition": "hourly_label_unspecified",
                        "observed_at": stamp.isoformat(),
                        "standard_series": series,
                    }
                    result = call("GET", "/api/v1/observations/current/", params=query)["results"]
                    if len(result) != 1:
                        raise Denied("field_unavailable")
                    field = result[0]
                    if not Receipt.objects.filter(
                        pk__in=evidence,
                        batch_id=run.batch_id,
                        city_revision_id=field["revision_id"],
                        city_revision__value=Decimal(field["value"]),
                        city_revision__unit=field["unit"],
                        city_revision__key__city_id=job.city_id,
                        city_revision__key__observed_at_utc=stamp,
                    ).exists():
                        raise Denied("field_receipt_mismatch")
        except Denied as exc:
            raise CommandError(exc.code) from None
        except (
            get_user_model().DoesNotExist,
            Run.DoesNotExist,
            httpx.HTTPError,
            KeyError,
            ValueError,
        ):
            raise CommandError("validation_unavailable") from None
        self.stdout.write(f"PASS A03 run={run.pk} window={run.window_id} fields=7")
