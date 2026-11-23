"""Test settings, loaded only with a validated private S06 work root."""

import os

from s06_guard import check_environment, require

_root = check_environment(os.environ)

from config.settings import *  # noqa: E402,F403

OPERATIONS_WORKER = "worker@" + os.environ["S06_RUN"]
OPERATIONS_SAMPLE_SECONDS = 1
OPERATIONS_TTL_SECONDS = 3

if (_root / "stage" / "collection-fixture").is_file():
    require(os.readlink("/proc/self/ns/net") != os.environ["S06_HOST_NET_NS"], "fixture_network")
    from collection.adapters import transport

    S03_FIXTURE_HTTP = True
    transport.ORIGIN = "http://127.0.0.1:18080"

S02_STAGE_DIR = str(_root / "stage")
CELERY_BROKER_URL = f"redis+socket://{_root}/broker/redis.sock"
CELERY_TASK_DEFAULT_QUEUE = f"{os.environ['S06_RUN']}.fixture"
CELERY_TASK_ROUTES = {
    task: {"queue": f"{os.environ['S06_RUN']}.{group}"}
    for task, group in (
        ("collection.execute_run", "collection"),
        ("collection.reconcile_due", "collection"),
        ("observations.import_history", "history"),
        ("observations.dispose", "history"),
        ("observations.expire_research", "history"),
    )
}
CELERY_BEAT_SCHEDULE = {
    "s06.expired-private-metadata": {
        "task": "observations.expire_research",
        "schedule": 60.0,
        "options": {"queue": f"{os.environ['S06_RUN']}.history"},
    },
}
