"""V14 F data and one explicitly injected collection lease, in a guarded A or B."""

import json
import os
import sys
from datetime import timedelta
from uuid import uuid4

from s06_guard import require

USER = "s07-offline-operator"
VALUES = (1, 2, 6)
LEASE_PROFILE = "f-offline"


def seed():
    from collection.models import Job, Run, WorkWindow
    from django.contrib.auth import get_user_model
    from django.utils import timezone
    from s05_support import START, authorize, candidate, city, ingest, product

    op = get_user_model().objects.create_superuser(
        USER, password=os.environ["S07_OFFLINE_PASSWORD"]
    )
    fixture = product("s07-offline-city", shape="C")
    entity = city(fixture, name="F Offline City")
    authorize(op, fixture)
    hour = timedelta(hours=1)
    ingest(
        op,
        fixture,
        [candidate(fixture, value=v, at=START + i * hour) for i, v in enumerate(VALUES)],
    )

    # I: simulate an abandoned F lease. The job has no acquisition grant and stays disabled.
    job = Job.objects.create(product=fixture, city=entity, profile=LEASE_PROFILE, executor=op)
    owner = uuid4()
    window = WorkWindow.objects.create(
        product=fixture,
        scope_key=f"city:{job.city_id}",
        target_start=START,
        target_end=START + hour,
        profile=job.profile,
        generation=1,
        owner=owner,
        lease_until=timezone.now() + timedelta(hours=2),
    )
    run = Run.objects.create(
        job=job,
        window=window,
        actor=op,
        executor=op,
        reason="manual",
        status="running",
        started_at=timezone.now(),
        owner=owner,
        generation=1,
    )
    return {
        "city": entity.pk,
        "source": fixture.source.namespace,
        "product": fixture.pk,
        "start": START.isoformat(),
        "end": (START + 3 * hour).isoformat(),
        "lease": str(run.pk),
        "window": window.pk,
    }


def expire():
    from collection.models import WorkWindow
    from django.utils import timezone

    qty = WorkWindow.objects.filter(
        product__synthetic=True,
        profile=LEASE_PROFILE,
        generation=1,
        owner__isnull=False,
        lease_until__gt=timezone.now(),
    ).update(lease_until=timezone.now() - timedelta(seconds=1))
    require(qty == 1, "v14_injection_target")
    return {"expired_leases_injected": qty}


def main(action):
    import django
    from s07_performance_seed import guard

    require(action in {"seed", "expire"}, "v14_fixture_action")
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    django.setup()
    guard()
    print(json.dumps(seed() if action == "seed" else expire()))


if __name__ == "__main__":
    main(sys.argv[1])
