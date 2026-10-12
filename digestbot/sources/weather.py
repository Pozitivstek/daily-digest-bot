"""Прогноз погоды на сегодня: Open-Meteo (без ключа)."""
import json
from dataclasses import dataclass

from ..errors import ParseError

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather code → (эмодзи, описание)
_WMO: dict[int, tuple[str, str]] = {
    0: ("☀️", "ясно"),
    1: ("🌤", "малооблачно"), 2: ("⛅", "переменная облачность"), 3: ("☁️", "пасмурно"),
    45: ("🌫", "туман"), 48: ("🌫", "изморозь"),
    51: ("🌦", "лёгкая морось"), 53: ("🌦", "морось"), 55: ("🌦", "сильная морось"),
    56: ("🌧", "ледяная морось"), 57: ("🌧", "ледяная морось"),
    61: ("🌧", "небольшой дождь"), 63: ("🌧", "дождь"), 65: ("🌧", "сильный дождь"),
    66: ("🌧", "ледяной дождь"), 67: ("🌧", "ледяной дождь"),
    71: ("🌨", "небольшой снег"), 73: ("❄️", "снег"), 75: ("❄️", "сильный снег"), 77: ("❄️", "снежная крупа"),
    80: ("🌦", "небольшие ливни"), 81: ("🌧", "ливни"), 82: ("⛈", "сильные ливни"),
    85: ("🌨", "снегопад"), 86: ("❄️", "сильный снегопад"),
    95: ("⛈", "гроза"), 96: ("⛈", "гроза с градом"), 99: ("⛈", "сильная гроза с градом"),
}


def describe(code: int) -> tuple[str, str]:
    return _WMO.get(code, ("🌡", "погода без описания"))


@dataclass(frozen=True)
class Weather:
    temp_now: float
    code: int            # состояние сейчас
    day_code: int        # состояние за день
    temp_max: float
    temp_min: float
    precip_prob: int | None
    wind_max: float      # м/с


def parse_forecast(raw: bytes) -> Weather:
    try:
        data = json.loads(raw)
        current, daily = data["current"], data["daily"]
        prob = daily["precipitation_probability_max"][0]
        return Weather(
            temp_now=float(current["temperature_2m"]),
            code=int(current["weather_code"]),
            day_code=int(daily["weather_code"][0]),
            temp_max=float(daily["temperature_2m_max"][0]),
            temp_min=float(daily["temperature_2m_min"][0]),
            precip_prob=None if prob is None else int(prob),
            wind_max=float(daily["wind_speed_10m_max"][0]),
        )
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ParseError(f"прогноз: неожиданный ответ ({type(exc).__name__})") from exc


async def fetch_forecast(fetcher, lat: float, lon: float) -> Weather:
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "current": "temperature_2m,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "precipitation_probability_max,wind_speed_10m_max",
        "wind_speed_unit": "ms",
        "timezone": "auto",
        "forecast_days": 1,
    }
    return parse_forecast(await fetcher.get(FORECAST_URL, params))
