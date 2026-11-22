from django.conf import settings
from django.db import models
from django.db.models import Q


class Favorite(models.Model):
    class Kind(models.TextChoices):
        CITY = "city"
        STATION = "station"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    kind = models.CharField(max_length=7, choices=Kind)
    city = models.ForeignKey("observations.City", null=True, on_delete=models.SET_NULL)
    station = models.ForeignKey("observations.Station", null=True, on_delete=models.SET_NULL)
    preferred_product = models.ForeignKey(
        "observations.Product", null=True, on_delete=models.SET_NULL
    )
    synthetic_scope = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(kind="city", station__isnull=True)
                | Q(kind="station", city__isnull=True),
                name="favorite_kind_target",
            ),
            models.UniqueConstraint(
                fields=("owner", "city"),
                condition=Q(city__isnull=False),
                name="favorite_owner_city",
            ),
            models.UniqueConstraint(
                fields=("owner", "station"),
                condition=Q(station__isnull=False),
                name="favorite_owner_station",
            ),
        ]
        indexes = [
            models.Index(
                fields=("owner", "synthetic_scope", "-created_at", "-id"),
                name="favorite_owner_page",
            )
        ]
