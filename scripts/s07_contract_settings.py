"""Settings scoped to the guarded V12 loopback F origin."""

import os

from s06_guard import require
from s07_host_guard import check_broker, check_database, check_process_network

_env = os.environ.copy()
require(_env.pop("DJANGO_SETTINGS_MODULE", None) == __name__, "contract_test_settings")
check_process_network(_env["S06_HOST_NET_NS"])
check_database(_env, empty=False)
check_broker(_env, empty=False)
_port = int(_env["S07_CONTRACT_ORIGIN_PORT"])
require(1024 <= _port <= 65535, "contract_fixture_port")

from collection.adapters import transport  # noqa: E402
from config.settings import *  # noqa: E402,F403

transport.ORIGIN = f"http://127.0.0.1:{_port}"
S03_FIXTURE_HTTP = True
OPERATIONS_WORKER = "worker@" + _env["AQ_INSTANCE_ID"]
OPERATIONS_SAMPLE_SECONDS = 1
OPERATIONS_TTL_SECONDS = 5
