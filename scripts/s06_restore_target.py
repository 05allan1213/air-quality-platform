"""Guard the disposable restore database; there is no operator-supplied DSN."""

import os
from copy import deepcopy

from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from s06_guard import check_environment, identity, require
from s06_restore_provision import target_id
from s06_sandbox import check_network

ALIAS = "s06_restore"


def install_target(slot=""):
    require(slot in ("", "2"), "restore_target_id")
    run = os.environ.get("S06_RUN")
    root = check_environment(os.environ)
    check_network()
    default = connections.databases["default"]
    database, role = identity(run)
    require(default["HOST"] == str(root / "pg") and default["USER"] == role, "restore_authority")
    require(default["NAME"] in (database, f"test_{database}"), "restore_authority")
    config = deepcopy(default)
    config.update(
        {
            "HOST": str(root / "pg"),
            "NAME": f"{database}_restore{slot}",
            "USER": f"{role}_restore{slot}",
            "PASSWORD": os.environ[f"S06_RESTORE_PASSWORD{slot}"],
            "CONN_MAX_AGE": 0,
            "OPTIONS": {"connect_timeout": 3},
        }
    )
    connections.databases[ALIAS] = config

    def check_target(cursor, identifier, *, migrated=True):
        cursor.execute("""SELECT current_database(), current_user, inet_server_addr() IS NULL,
            current_setting('listen_addresses'), current_setting('airquality.s06_run', true),
            current_setting('airquality.s06_restore', true), rolsuper, rolcreatedb, rolcreaterole
            FROM pg_roles WHERE rolname=current_user""")
        require(
            cursor.fetchone()
            == (
                config["NAME"],
                config["USER"],
                True,
                "",
                run,
                str(identifier),
                False,
                False,
                False,
            ),
            "restore_identity",
        )
        require(identifier == target_id(run, slot), "restore_target_id")
        cursor.execute(
            "SELECT has_database_privilege(current_user, %s, 'CONNECT')", [default["NAME"]]
        )
        require(cursor.fetchone() == (False,), "restore_authority_connection")
        if migrated:
            cursor.execute("LOCK TABLE observations_source, auth_user IN EXCLUSIVE MODE")
            for table in (
                "observations_source",
                "observations_product",
                "observations_usegrant",
                "observations_cityobservation",
                "observations_stationobservation",
                "auth_user",
                "django_session",
                "observations_backupsegment",
                "observations_restoreintent",
                "accounts_exportticket",
                "accounts_savedquery",
                "collection_job",
                "collection_run",
            ):
                cursor.execute(f"SELECT NOT EXISTS (SELECT 1 FROM {table})")
                require(cursor.fetchone() == (True,), "restore_target_not_empty")

    return target_id(run, slot), check_target


def bootstrap_target(slot=""):
    """Only the authenticated rehearsal runner bootstraps its already owned database."""
    identifier, guard = install_target(slot)
    db = connections[ALIAS]
    with db.cursor() as cursor:
        guard(cursor, identifier, migrated=False)
    executor = MigrationExecutor(db)
    executor.migrate(executor.loader.graph.leaf_nodes())
    db.close()
    return identifier
