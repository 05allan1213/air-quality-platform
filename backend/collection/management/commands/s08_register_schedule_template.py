from django.core.management.base import BaseCommand, CommandError
from observations.policy import Denied

from collection.s08_schedule import register_template


class Command(BaseCommand):
    help = "Approve a schedule template from an authorized, registered host Job."

    def add_arguments(self, parser):
        parser.add_argument("--actor", type=int, required=True)
        parser.add_argument("--job", type=int, required=True)
        parser.add_argument("--cadence", type=int, action="append", required=True)
        parser.add_argument("--minute", type=int, default=0)
        parser.add_argument(
            "--phase", help="Verified original due timestamp to enroll an enabled legacy Job"
        )

    def handle(self, **options):
        try:
            template = register_template(
                options["actor"],
                options["job"],
                options["cadence"],
                options["minute"],
                phase=options["phase"],
            )
        except Denied as exc:
            raise CommandError(exc.code) from None
        self.stdout.write(f"{template.pk} {template.version}")
