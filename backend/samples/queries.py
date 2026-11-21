from django.db import transaction
from django.utils import timezone

from .models import FixtureObservation
from .normalization import ENTITY, KEY, SOURCE, Rejected, parse_time
from .policy import require_uses
from .serializers import ObservationSerializer


@transaction.atomic
def query_observation(params):
    if set(params) != {"product", "observed_at"} or any(
        len(params.getlist(k)) != 1 for k in params
    ):
        raise Rejected("invalid_request")
    product = params["product"]
    require_uses(product, "display")
    at = parse_time(params["observed_at"])
    rows = FixtureObservation.objects.filter(
        product=product,
        source=SOURCE,
        entity=ENTITY,
        fixture_key=KEY,
        indicator="CO",
        observed_at=at,
        quality="valid",
        synthetic=True,
    )
    items = ObservationSerializer(rows, many=True, context={"now": timezone.now()}).data
    return {
        "filters": {"product": product, "observed_at": at.isoformat().replace("+00:00", "Z")},
        "count": len(items),
        "results": items,
        "reason": None if items else "no_observation_at_time",
    }
