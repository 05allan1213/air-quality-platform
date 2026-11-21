from rest_framework import serializers

from .models import FixtureObservation


class ObservationSerializer(serializers.ModelSerializer):
    observation_age_seconds = serializers.SerializerMethodField()
    freshness_threshold_seconds = serializers.SerializerMethodField()

    def get_observation_age_seconds(self, row):
        return int((self.context["now"] - row.observed_at).total_seconds())

    def get_freshness_threshold_seconds(self, row):
        return None

    class Meta:
        model = FixtureObservation
        fields = (
            "id",
            "product",
            "source",
            "entity",
            "synthetic",
            "purpose",
            "indicator",
            "fixture_key",
            "fixture_version",
            "source_time",
            "source_timezone",
            "observed_at",
            "fetched_at",
            "published_at",
            "window_start",
            "window_end",
            "window_semantics",
            "raw_value",
            "raw_unit",
            "value",
            "unit",
            "quality",
            "aqi_standard",
            "observation_age_seconds",
            "freshness_threshold_seconds",
        )
