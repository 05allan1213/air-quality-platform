import sys

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from samples.normalization import Rejected

from observations.history import stage_uci
from observations.policy import Denied


class Command(BaseCommand):
    help = "Check rights, verify the frozen ZIP on stdin and stage it in Worker private tmpfs."

    def add_arguments(self, parser):
        parser.add_argument("--actor", required=True)

    def handle(self, *args, actor, **options):
        user = get_user_model().objects.get(username=actor, is_active=True)
        try:
            return str(stage_uci(user.pk, sys.stdin.buffer))
        except (Denied, Rejected) as exc:
            raise CommandError(exc.code) from None
