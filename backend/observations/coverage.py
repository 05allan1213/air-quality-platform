"""Independent, disclosable directory evidence for an explicit station window."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from .models import SiteEpoch
from .normalization import WINDOWS
from .policy import Scope
from .selection import TICK


def outside_registered_epochs(ctx, product, ids, indicators, window, start, end):
    if product.level != "station":
        return False
    epochs = list(
        SiteEpoch.objects.filter(
            pk__in=ids,
            station__source_id=product.source_id,
            city__source_id=product.source_id,
            certainty__in=("confirmed", "documented"),
        ).select_related("city")
    )
    if len(epochs) != len(ids):
        return False
    zone = ZoneInfo(product.source_timezone)
    authorized = ctx.scope(product, indicators, ids, fields={"catalog"})
    disclosed_epochs = {s.entity_id for s in authorized.segments}
    for epoch in epochs:
        period = epoch.active_period
        # This only certifies whole windows outside a known site. Ambiguity remains unavailable.
        after_end = period.upper and start >= period.upper + (
            TICK if WINDOWS[window][1] == "right" else timedelta(0)
        )
        if epoch.pk not in disclosed_epochs or not (end <= period.lower or after_end):
            return False
        # A historical directory license alone cannot reveal anything about the requested day.
        for ind in indicators:
            scope = Scope(
                epoch.city.geography,
                "*",
                ind,
                start.astimezone(zone).date(),
                (end - TICK).astimezone(zone).date() + timedelta(days=1),
            )
            if not ctx.basis(product, scope) or not ctx.basis(product, scope, {"catalog"}):
                return False
    return True
