from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from observations.uci_catalog import register_uci


class Command(BaseCommand):
    help = "Confirm the scoped UCI CC BY 4.0 local uses for the S02 selection."

    def add_arguments(self, parser):
        parser.add_argument("--actor", required=True)

    def handle(self, *args, actor, **options):
        return str(register_uci(get_user_model().objects.get(username=actor, is_active=True).pk).pk)
