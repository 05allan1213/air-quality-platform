from django.core.management.base import BaseCommand, CommandError
from observations.policy import Denied

from collection.services import register_job


class Command(BaseCommand):
    help = (
        "Register a disabled synthetic collection job from existing, authorized catalogue entries."
    )

    def add_arguments(self, parser):
        parser.add_argument("--actor", type=int, required=True)
        parser.add_argument("--executor", type=int, required=True)
        parser.add_argument("--product", type=int, required=True)
        parser.add_argument("--city", type=int)
        parser.add_argument("--profile", choices=("f-exact-v1", "f-latest-v1"), required=True)
        parser.add_argument("--cadence", type=int, default=3600)
        parser.add_argument(
            "--first-due", help="First fixed UTC hour for scheduling; omit for manual use"
        )
        parser.add_argument("--expected-code", action="append")
        parser.add_argument("--roster-basis", default="")

    def handle(self, **options):
        try:
            job = register_job(
                options["actor"],
                options["executor"],
                options["product"],
                options["city"],
                options["profile"],
                options["cadence"],
                options["first_due"],
                options["expected_code"],
                options["roster_basis"],
            )
        except Denied as exc:
            raise CommandError(exc.code) from None
        self.stdout.write(str(job.pk))
