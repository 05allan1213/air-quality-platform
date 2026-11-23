"""Migrate a freshly seeded S05 database inside the guarded S06 network namespace."""

import json
import os
import subprocess
import sys
from pathlib import Path

from s06_guard import check_database, check_environment, require

SEED = """
import django, json, os
from pathlib import Path
from django.core.management import call_command
django.setup()
import config.settings
assert Path(config.settings.__file__).resolve().is_relative_to(Path(os.environ['S06_BASELINE_CODE']))
call_command('migrate', verbosity=0, interactive=False)
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from observations.models import Product, Source, QualityIssue
from observations.policy import register_grant
from collection.models import Job
from accounts.models import SavedQuery
from datetime import date, timedelta
from django.utils import timezone
user=get_user_model().objects.create_user('upgrade-F', password=os.environ['S06_TEST_LOGIN'])
user.user_permissions.add(Permission.objects.get(codename='manage_grants', content_type__app_label='observations'))
p=Product.objects.create(source=Source.objects.create(namespace='s06.upgrade',publisher='F'),code='upgrade-C',family='F',shape='C',level='city',synthetic=True)
g=register_grant(user.pk,p.pk,operation='retain_normalized',audience='platform',geography='*',data_from=date(2024,1,1),data_until=date(2024,1,3),fields=['normalized','provenance'],terms='F grant',terms_version='F1')
fp=Product.objects.create(source=Source.objects.create(namespace='cnemc.fixture',publisher='F'),code='city-live-hour',family='F',shape='C',level='city',synthetic=True)
quota=register_grant(user.pk,fp.pk,operation='acquire_online',audience='platform',geography='*',data_from=date(2024,1,1),data_until=date(2024,1,3),fields=['*'],terms='F offline upgrade fixture',terms_version='F-validation',max_total_requests=1,valid_until=timezone.now()+timedelta(days=1),endpoints=['A03'],collection_limits={'min_interval_seconds':1,'max_concurrent':1,'period_seconds':60,'max_requests':1})
issue=QualityIssue.objects.create(product=p,code='identity_unresolved',scope={'geography':'*','object_code':'F','indicator':'AQI','start':'2024-01-01','end':'2024-01-02'})
job=Job.objects.create(product=p, executor=user, profile='f-exact-v1',enabled=False)
saved=SavedQuery.objects.create(owner=user,product=p,synthetic_scope=True,title='Upgrade F',intent={'scope':'synthetic'})
client=Client(); assert client.login(username=user.username,password=os.environ['S06_TEST_LOGIN'])
print(json.dumps({'cookie':client.cookies['sessionid'].value,'product':p.pk,'grant':g.pk,'quota':quota.pk,'issue':issue.pk,'job':job.pk,'saved':str(saved.pk),'user':user.pk}))
"""


def run(root):
    import secrets

    import django
    import httpx
    from django.core.management import call_command

    check_environment(os.environ)
    check_database(os.environ, expected_marker=os.environ["S06_RUN"], empty=True)
    old = Path(os.environ["S06_BASELINE_CODE"])
    require(old.is_dir() and old.resolve(strict=True) == old, "missing_s05_backend")
    env = dict(
        os.environ,
        DJANGO_SETTINGS_MODULE="config.settings",
        PYTHONPATH=str(old),
        S06_TEST_LOGIN=secrets.token_urlsafe(24),
    )
    seeded = subprocess.run(
        [sys.executable, "-c", SEED], cwd=old, env=env, capture_output=True, text=True, timeout=90
    )
    require(seeded.returncode == 0, "s05_fixture_failed")
    ids = json.loads(seeded.stdout)
    django.setup()
    from threading import Thread
    from wsgiref.simple_server import make_server

    from accounts.models import AccountState, SavedQuery
    from collection.models import Job
    from django.contrib.auth import get_user_model
    from django.core.wsgi import get_wsgi_application
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from observations.models import QualityIssue, UseGrant
    from s05_http import QuietRequestHandler

    def pending():
        executor = MigrationExecutor(connection)
        return executor.migration_plan(executor.loader.graph.leaf_nodes())

    before = len(pending())
    require(before > 0, "missing_upgrade_steps")
    call_command("migrate", interactive=False, verbosity=0)
    call_command("check", verbosity=0)
    call_command("makemigrations", check=True, dry_run=True, interactive=False, verbosity=1)
    require(not pending(), "pending_after_upgrade")
    call_command("migrate", interactive=False, verbosity=0)
    row = QualityIssue.objects.get(pk=ids["issue"])
    user = get_user_model().objects.get(pk=ids["user"])
    require(
        row.rule_version is None
        and row.handler_id is None
        and row.severity == "unknown"
        and row.event_version == 0
        and row.status == "open",
        "invented_historical_quality",
    )
    require(
        not user.has_perm("accounts.manage_accounts")
        and not AccountState.objects.filter(user=user).exists(),
        "legacy_permissions",
    )
    require(
        Job.objects.get(pk=ids["job"]).control_version == 0
        and UseGrant.objects.filter(pk=ids["grant"], product_id=ids["product"]).exists()
        and SavedQuery.objects.filter(pk=ids["saved"], owner=user).exists(),
        "legacy_ledger_changed",
    )
    require(
        UseGrant.objects.filter(
            pk=ids["quota"],
            registered_at__isnull=False,
            max_total_requests=1,
            validation_disabled=False,
        ).exists(),
        "legacy_validation_allocation_changed",
    )
    with make_server(
        "127.0.0.1", 0, get_wsgi_application(), handler_class=QuietRequestHandler
    ) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False, timeout=5
            ) as client:
                client.cookies.set("sessionid", ids["cookie"])
                status = client.get("/api/v1/me/").status_code
                forbidden = client.get(f"/api/v1/admin/accounts/{user.pk}/actions/").status_code
        finally:
            server.shutdown()
            thread.join()
    require((status, forbidden) == (200, 403), "old_session_incompatible")
    (root / "upgrade.json").write_text(
        json.dumps(
            {
                "run": os.environ["S06_RUN"],
                "type": "F",
                "from": "be6681298a28220ac97136087a4bdf316799c93d",
                "applied": before,
                "second_migration_pending": 0,
                "legacy_session_http": status,
                "management_http": forbidden,
                "quality_severity": row.severity,
                "existing_grant_job_private_intent_preserved": True,
                "legacy_validation_allocation_preserved": True,
                "result": "PASS",
            },
            indent=2,
        )
        + "\n"
    )
    return 0
