from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import FixtureGrant
from .normalization import SOURCE, Rejected


def require_operator(actor_id):
    user = get_user_model().objects.filter(pk=actor_id, is_active=True).first()
    if user is None or not user.has_perm("samples.run_fixture"):
        raise Rejected("permission_denied")


def require_uses(product, *operations):
    """Called inside the caller's transaction, on the fixed S01 local F scope."""
    if product != "F":
        raise Rejected("use_denied")
    rows = list(
        FixtureGrant.objects.select_for_update()
        .filter(
            product=product,
            source=SOURCE,
            geography="synthetic",
            audience="local_authenticated",
            operation__in=operations,
            enabled=True,
        )
        .order_by("operation")
    )
    now = timezone.now()
    for op in operations:
        if not any(
            row.operation == op
            and row.valid_from <= now
            and (row.valid_until is None or now < row.valid_until)
            for row in rows
        ):
            raise Rejected(f"{op}_denied")
