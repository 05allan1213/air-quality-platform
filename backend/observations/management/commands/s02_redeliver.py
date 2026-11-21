from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from observations.history import redeliver


class Command(BaseCommand):
    help = "Recheck a pending attempt and deliver its existing UUID again."

    def add_arguments(self, parser):
        parser.add_argument("--actor", required=True)
        parser.add_argument("--attempt", required=True, type=UUID)

    def handle(self, *args, actor, attempt, **options):
        user = get_user_model().objects.get(username=actor, is_active=True)
        identifier, result = redeliver(user.pk, attempt)
        if result == "unconfirmed":
            raise CommandError(f"{identifier} dispatch_unconfirmed")
        return f"{identifier} {result}"
