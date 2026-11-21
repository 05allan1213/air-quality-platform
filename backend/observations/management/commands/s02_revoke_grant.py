from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from observations.disposition import dispose_due
from observations.policy import revoke_grant


class Command(BaseCommand):
    help = "Withdraw a single grant and reconcile its product in the owning Worker."

    def add_arguments(self, parser):
        parser.add_argument("--actor", required=True)
        parser.add_argument("--grant", required=True, type=int)
        parser.add_argument("--reason", required=True)

    def handle(self, *args, actor, grant, reason, **options):
        pk = revoke_grant(
            get_user_model().objects.get(username=actor, is_active=True).pk, grant, reason
        )
        dispose_due(pk)
