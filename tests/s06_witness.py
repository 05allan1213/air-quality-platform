"""Attach fixed S06 observations to the guarded runner's temporary JUnit XML."""

import json
from datetime import UTC


def witness(record_property, *, pg_at, **observed):
    record = observed | {"pg_utc": pg_at.astimezone(UTC).isoformat()}
    record_property("s06_observed", json.dumps(record))
