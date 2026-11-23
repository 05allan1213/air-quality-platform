"""Provision a distinct role/database in the already guarded disposable cluster."""

from secrets import token_hex
from uuid import NAMESPACE_URL, uuid5

from s06_guard import identity


def target_id(run, slot=""):
    return uuid5(NAMESPACE_URL, f"airquality.restore:{run}{slot}")


def target_bootstrap(env, slot=""):
    from psycopg import sql

    run = env["S06_RUN"]
    authority_db, authority_user = identity(run)
    database = sql.Identifier(f"{authority_db}_restore{slot}")
    user = sql.Identifier(f"{authority_user}_restore{slot}")
    password = env[f"S06_RESTORE_PASSWORD{slot}"] = token_hex(32)
    return sql.SQL(
        "REVOKE CONNECT ON DATABASE {authority_db} FROM PUBLIC;\n"
        "CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {password};\n"
        "CREATE DATABASE {db} OWNER {role};\n"
        "REVOKE CONNECT ON DATABASE {db} FROM PUBLIC;\n"
        "ALTER DATABASE {db} SET airquality.s06_run = {run};\n"
        "ALTER DATABASE {db} SET airquality.s06_restore = {target};\n"
    ).format(
        authority_db=sql.Identifier(authority_db),
        db=database,
        role=user,
        password=sql.Literal(password),
        run=sql.Literal(run),
        target=sql.Literal(str(target_id(run, slot))),
    )
