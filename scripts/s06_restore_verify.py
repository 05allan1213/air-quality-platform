#!/usr/bin/env python3
"""Run backup/recovery checks with a second, explicitly owned rehearsal database."""

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import verify_s06
from s06_restore_custodian import custodian
from s06_restore_provision import target_bootstrap

_cluster = verify_s06.cluster
_sandbox_command = verify_s06.sandbox_command
authority_mount = None


def sandbox_command(env, *args, **kwargs):
    command = _sandbox_command(env, *args, **kwargs)
    end = command.index("--")
    command[end:end] = ["--ro-bind", str(authority_mount), "/s06-authority"]
    return command


@contextmanager
def cluster(env, receipt):
    global authority_mount
    with _cluster(env, receipt) as (container, image):
        run = env["S06_RUN"]
        verify_s06.isolation(verify_s06.inspect(container), run, container, image)
        bootstrap = target_bootstrap(env) + target_bootstrap(env, "2")
        verify_s06.docker(
            "exec",
            "-i",
            container,
            "sh",
            "-ec",
            'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -Xq -v ON_ERROR_STOP=1 '
            "-h /run/postgresql -U postgres -d template1",
            payload=bootstrap.as_string(),
        )
        receipt["restore_target"] = {
            "separate_role": True,
            "isolated_database": True,
            "target_count": 2,
        }
        root = Path(env["S06_WORK_ROOT"])
        # This sibling holder is outside the subtree mounted writable into bwrap.
        with tempfile.TemporaryDirectory(prefix=f"{run}-custody-", dir=root.parent) as directory:
            with custodian(root / "stage" / "custody.sock", Path(directory)) as authority_mount:
                env["S06_AUTHORITY_FIXTURE"] = "/s06-authority"
                env["S06_PRIVATE_KEY_PROBE"] = str(Path(directory) / "offline.pem")
                receipt["restore_target"]["authority_mount"] = "read_only"
                yield container, image


if __name__ == "__main__":
    verify_s06.cluster = cluster
    verify_s06.sandbox_command = sandbox_command
    sys.argv[1:] = [
        "pytest",
        *(sys.argv[1:] or ["tests/test_s06_backup.py", "tests/test_s06_backup_preparation.py"]),
    ]
    sys.exit(verify_s06.main())
