from django.core.management.base import BaseCommand

from collection.services import reconcile_due


class Command(BaseCommand):
    help = "Recover due PostgreSQL collection work."

    def handle(self, *args, **options):
        reconcile_due()
