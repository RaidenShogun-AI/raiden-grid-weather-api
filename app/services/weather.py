"""Meteorological real-time observation & 24-hour forecast service via Open-Meteo."""

import time
from typing import Dict, List, Tuple
import httpx
from app.config import settings
from app.schemas import CurrentWeather, HourlyForecastPoint, LocationData, WeatherResponse

_WEATHER_CACHE: Dict[str, Tuple[float, WeatherResponse]] = {}

WMO_CODE_DESCRIPTIONS: Dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm (slight or moderate)",
    96: "Thunderstorm with slight hail",
    99: "Severe thunderstorm with heavy hail",
}

STORM_WMO_CODES = {82, 95, 96, 99}


def decode_wmo_code(code: int) -> Tuple[str, bool]:
    """Return human-readable condition text and whether it represents a storm/thunderstorm."""
    desc = WMO_CODE_DESCRIPTIONS.get(code, f"Weather code {code}")
    is_storm = code in STORM_WMO_CODES
    return desc, is_storm


async def fetch_weather_for_location(location: LocationData) -> WeatherResponse:
    """Fetch current weather and 24-hour hourly forecast from Open-Meteo for the given LocationData."""
    cache_key = f"{round(location.latitude, 3)}:{round(location.longitude, 3)}"
    now = time.time()
    if cache_key in _WEATHER_CACHE:
        cached_at, cached_resp = _WEATHER_CACHE[cache_key]
        if now - cached_at < settings.cache_ttl_seconds:
            # Update location metadata in case detection details changed
            return cached_resp.model_copy(update={"location": location})

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": location.latitude,
        "longitude": location.longitude,
        "current": (
            "temperature_2m,relative_humidity_2m,apparent_temperature,"
            "precipitation,rain,showers,weather_code,cloud_cover,"
            "pressure_msl,wind_speed_10m,wind_direction_10m,wind_gusts_10m"
        ),
        "hourly": (
            "temperature_2m,precipitation_probability,precipitation,"
            "weather_code,wind_speed_10m,wind_gusts_10m,cape"
        ),
        "timezone": "auto",
        "forecast_days": 2,
    }

    async with httpx.AsyncClient(
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": settings.user_agent},
    ) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        payload = resp.json()

    curr = payload.get("current", {})
    hourly = payload.get("hourly", {})

    w_code = int(curr.get("weather_code", 0))
    cond_text, is_storm = decode_wmo_code(w_code)
    curr_time_str = str(curr.get("time", ""))

    times: List[str] = hourly.get("time", [])
    temps: List[float] = hourly.get("temperature_2m", [])
    precip_probs: List[int] = hourly.get("precipitation_probability", [])
    precips: List[float] = hourly.get("precipitation", [])
    w_codes: List[int] = hourly.get("weather_code", [])
    wind_speeds: List[float] = hourly.get("wind_speed_10m", [])
    wind_gusts: List[float] = hourly.get("wind_gusts_10m", [])
    capes: List[float] = hourly.get("cape", [])

    # Determine starting index in hourly array matching current hour
    start_idx = 0
    curr_hour_prefix = curr_time_str[:13]
    for idx, t_val in enumerate(times):
        if t_val.startswith(curr_hour_prefix):
            start_idx = idx
            break

    current_cape = float(capes[start_idx]) if start_idx < len(capes) and capes[start_idx] is not None else 0.0

    current_weather = CurrentWeather(
        observation_time=curr_time_str,
        temperature_c=float(curr.get("temperature_2m", 0.0)),
        feels_like_c=float(curr.get("apparent_temperature", 0.0)),
        humidity_percent=int(curr.get("relative_humidity_2m", 0)),
        precipitation_mm=float(curr.get("precipitation", 0.0)),
        rain_mm=float(curr.get("rain", 0.0)),
        showers_mm=float(curr.get("showers", 0.0)),
        weather_code=w_code,
        condition_text=cond_text,
        is_storm_or_thunder=is_storm or current_cape >= 1200.0,
        cloud_cover_percent=int(curr.get("cloud_cover", 0)),
        pressure_hpa=float(curr.get("pressure_msl", 1013.25)),
        wind_speed_kmh=float(curr.get("wind_speed_10m", 0.0)),
        wind_gusts_kmh=float(curr.get("wind_gusts_10m", 0.0)),
        wind_direction_deg=int(curr.get("wind_direction_10m", 0)),
        cape_j_kg=current_cape,
    )

    forecast_24h: List[HourlyForecastPoint] = []
    for idx in range(start_idx, min(start_idx + 24, len(times))):
        h_code = int(w_codes[idx]) if idx < len(w_codes) and w_codes[idx] is not None else 0
        h_text, _ = decode_wmo_code(h_code)
        forecast_24h.append(
            HourlyForecastPoint(
                time=times[idx],
                temperature_c=float(temps[idx]) if idx < len(temps) and temps[idx] is not None else 0.0,
                precipitation_probability_percent=(
                    int(precip_probs[idx]) if idx < len(precip_probs) and precip_probs[idx] is not None else 0
                ),
                precipitation_mm=float(precips[idx]) if idx < len(precips) and precips[idx] is not None else 0.0,
                weather_code=h_code,
                condition_text=h_text,
                wind_speed_kmh=float(wind_speeds[idx]) if idx < len(wind_speeds) and wind_speeds[idx] is not None else 0.0,
                wind_gusts_kmh=float(wind_gusts[idx]) if idx < len(wind_gusts) and wind_gusts[idx] is not None else 0.0,
                cape_j_kg=float(capes[idx]) if idx < len(capes) and capes[idx] is not None else 0.0,
            )
        )

    weather_response = WeatherResponse(
        location=location,
        current=current_weather,
        forecast_next_24h=forecast_24h,
        data_source="Open-Meteo Public Meteorological & Atmospheric Forecast API",
    )
    _WEATHER_CACHE[cache_key] = (now, weather_response)
    return weather_response
