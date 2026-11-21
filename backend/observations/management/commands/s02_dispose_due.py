from django.core.management.base import BaseCommand

from observations.disposition import dispose_due


class Command(BaseCommand):
    help = "Reconcile durable due obligations against the Worker filesystem."

    def add_arguments(self, parser):
        parser.add_argument("--product", type=int)

    def handle(self, *args, product, **options):
        dispose_due(product)
