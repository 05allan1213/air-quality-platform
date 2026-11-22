import json
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError
from observations.policy import Denied

from collection.registration import install


def unique_keys(pairs):
    values = dict(pairs)
    if len(values) != len(pairs):
        raise ValueError
    return values


class Command(BaseCommand):
    help = (
        "Register a private, reviewed source manifest without network access. Jobs start disabled."
    )

    def add_arguments(self, parser):
        parser.add_argument("manifest", type=Path)
        parser.add_argument("--actor", type=int, required=True)

    def handle(self, **options):
        try:
            with options["manifest"].open("rb") as f:
                body = f.read(4 * 2**20 + 1)
            if len(body) > 4 * 2**20:
                raise ValueError
            result = install(options["actor"], json.loads(body, object_pairs_hook=unique_keys))
        except Denied as exc:
            raise CommandError(exc.code) from None
        except (
            ValueError,
            TypeError,
            KeyError,
            OSError,
            RecursionError,
            IntegrityError,
            ValidationError,
        ):
            raise CommandError("invalid_manifest") from None
        self.stdout.write(json.dumps(result))
