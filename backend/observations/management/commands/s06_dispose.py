from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from observations.disposition_control import execute
from observations.policy import Denied


class Command(BaseCommand):
    help = "Consume one expiring product-scoped disposition authorization."

    def add_arguments(self, parser):
        parser.add_argument("--operation", type=UUID, required=True)

    def handle(self, *args, operation, **options):
        try:
            return execute(operation)
        except Denied as exc:
            raise CommandError(exc.code) from None
        except (DatabaseError, OSError):
            raise CommandError("cleanup_failed") from None
