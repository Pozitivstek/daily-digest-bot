"""Поиск города: геокодер Open-Meteo (без ключа)."""
import json
from dataclasses import dataclass

from ..errors import ParseError

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
MAX_RESULTS = 5


@dataclass(frozen=True)
class Place:
    name: str
    region: str
    country: str
    lat: float
    lon: float
    tz: str

    @property
    def label(self) -> str:
        return ", ".join(part for part in (self.name, self.region, self.country) if part)


def parse_places(raw: bytes) -> list[Place]:
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ParseError("геокодер: ответ не JSON") from exc
    if not isinstance(data, dict):
        raise ParseError("геокодер: неожиданный формат ответа")

    places = []
    for item in data.get("results") or []:
        try:
            tz = item["timezone"]
            places.append(Place(
                name=str(item["name"]),
                region=str(item.get("admin1") or ""),
                country=str(item.get("country") or ""),
                lat=float(item["latitude"]),
                lon=float(item["longitude"]),
                tz=str(tz),
            ))
        except (KeyError, TypeError, ValueError):
            continue   # без пояса прогноз и время рассылки считать нельзя
    return places


async def search_places(fetcher, query: str) -> list[Place]:
    params = {"name": query, "count": MAX_RESULTS, "language": "ru", "format": "json"}
    return parse_places(await fetcher.get(GEO_URL, params))
