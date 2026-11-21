"""Fixed fixture request and field contracts for the four independent products."""

from dataclasses import dataclass
from types import MappingProxyType

from observations.normalization import CONCENTRATIONS


@dataclass(frozen=True)
class Contract:
    method: str
    path: str
    parameter: str | None
    max_bytes: int
    max_rows: int
    indicators: tuple[str, ...]


CONTRACTS = MappingProxyType(
    {
        "L": Contract(
            "GET",
            "/CityData/GetAllCityRealTimeAQIModels",
            None,
            2**20,
            512,
            ("AQI", *(pollutant + "Level" for pollutant in CONCENTRATIONS)),
        ),
        "C": Contract(
            "POST",
            "/CityData/GetAQIDataPublishLiveInfo",
            "cityCode",
            2**16,
            1,
            ("AQI", *CONCENTRATIONS),
        ),
        "S": Contract(
            "GET",
            "/CityData/GetAQIDataPublishLive",
            "cityName",
            2**19,
            256,
            ("AQI", *CONCENTRATIONS),
        ),
        "T": Contract(
            "POST",
            "/HourChangesPublish/GetCityRealTimeAqiHistoryByCondition",
            "citycode",
            2**17,
            48,
            ("AQI",),
        ),
    }
)

PROFILES = frozenset({"f-exact-v1", "f-latest-v1"})
