"""Perform one isolated rehearsal; the only caller input is a signed intent ID."""

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connections
from s06_guard import IsolationError
from s06_restore_http import release
from s06_restore_target import ALIAS, install_target

from observations.backup import parse_uuid
from observations.backup_authority import canonical
from observations.backup_restore import run
from observations.policy import Denied


class Command(BaseCommand):
    help = "Rehearse one custodian-authorized restore in the guarded S06 sandbox."

    def add_arguments(self, parser):
        parser.add_argument("operation")

    def handle(self, *args, **options):
        try:
            operation = parse_uuid(options["operation"])
            target, guard = install_target()
            result = run(operation, target, ALIAS, guard, release)
        except Denied as exc:
            raise CommandError(exc.code) from None
        except (DatabaseError, IsolationError, OSError, KeyError, ValueError):
            raise CommandError("restore_unavailable") from None
        finally:
            if ALIAS in connections.databases:
                connections[ALIAS].close()
        self.stdout.write(canonical(result).decode("ascii"))
