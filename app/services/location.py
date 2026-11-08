"""Device location resolution service with multi-provider fallback and reverse geocoding."""

import time
from typing import Dict, Optional, Tuple
import httpx
from app.config import settings
from app.schemas import LocationData

_LOCATION_CACHE: Dict[str, Tuple[float, LocationData]] = {}


async def _reverse_geocode_nominatim(client: httpx.AsyncClient, lat: float, lon: float) -> dict:
    """Enrich coordinates with suburb, district, city, and state using OpenStreetMap Nominatim."""
    url = "https://nominatim.openstreetmap.org/reverse"
    params = {
        "format": "jsonv2",
        "lat": str(lat),
        "lon": str(lon),
        "zoom": "12",
        "addressdetails": "1",
    }
    headers = {"User-Agent": settings.user_agent, "Accept-Language": "en"}
    try:
        resp = await client.get(url, params=params, headers=headers)
        if resp.status_code == 200:
            return resp.json().get("address", {})
    except httpx.HTTPError:
        pass
    return {}


async def geocode_city_name(client: httpx.AsyncClient, city_query: str) -> Optional[LocationData]:
    """Resolve a city/place name query to coordinates and region metadata via Open-Meteo Geocoding API."""
    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {"name": city_query, "count": 1, "language": "en", "format": "json"}
    try:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        if not results:
            return None
        top = results[0]
        lat = float(top["latitude"])
        lon = float(top["longitude"])
        addr = await _reverse_geocode_nominatim(client, lat, lon)
        district = (
            addr.get("county")
            or addr.get("state_district")
            or addr.get("suburb")
            or top.get("admin2")
        )
        return LocationData(
            ip_address=None,
            latitude=lat,
            longitude=lon,
            city=top.get("name", city_query),
            district_or_suburb=district,
            region=top.get("admin1") or addr.get("state") or "Unknown Region",
            country=top.get("country") or addr.get("country") or "Unknown Country",
            country_code=(top.get("country_code") or addr.get("country_code") or "XX").upper(),
            postal_code=(top.get("postcodes") or [None])[0] or addr.get("postcode"),
            timezone=top.get("timezone") or "UTC",
            isp=None,
            detection_method="city_query_geocoding",
        )
    except httpx.HTTPError:
        return None


async def resolve_coordinates_location(
    client: httpx.AsyncClient, lat: float, lon: float, source_label: str = "coordinate_override"
) -> LocationData:
    """Resolve explicit latitude/longitude coordinates (e.g. from browser GPS or query params) into LocationData."""
    addr = await _reverse_geocode_nominatim(client, lat, lon)
    city = (
        addr.get("city")
        or addr.get("town")
        or addr.get("municipality")
        or addr.get("village")
        or addr.get("suburb")
        or addr.get("county")
        or "Detected Coordinates"
    )
    district = addr.get("state_district") or addr.get("county") or addr.get("suburb")
    region = addr.get("state") or addr.get("region") or "Unknown Region"
    country = addr.get("country") or "Unknown Country"
    country_code = (addr.get("country_code") or "XX").upper()

    return LocationData(
        ip_address=None,
        latitude=round(lat, 5),
        longitude=round(lon, 5),
        city=city,
        district_or_suburb=district,
        region=region,
        country=country,
        country_code=country_code,
        postal_code=addr.get("postcode"),
        timezone="Asia/Colombo" if country_code == "LK" else "UTC",
        isp=None,
        detection_method=source_label,
    )


async def detect_device_location(
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    city: Optional[str] = None,
) -> LocationData:
    """
    Detect the laptop's geographic location automatically or via optional overrides.

    Fallback hierarchy for automatic detection:
    1. Google Maps Geolocation API (if GOOGLE_API_KEY is configured)
    2. ipwho.is (high accuracy + ISP + timezone metadata)
    3. ip-api.com (fast JSON geolocation)
    4. get.geojs.io (lightweight global CDN geolocation)
    5. ipinfo.io (ASN & coordinate fallback)
    Enriched with OpenStreetMap Nominatim reverse geocoding for suburb/district accuracy.
    """
    cache_key = f"{lat}:{lon}:{city}"
    now = time.time()
    if cache_key in _LOCATION_CACHE:
        cached_time, cached_loc = _LOCATION_CACHE[cache_key]
        if now - cached_time < settings.cache_ttl_seconds:
            return cached_loc

    async with httpx.AsyncClient(
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": settings.user_agent},
        follow_redirects=True,
    ) as client:
        # 1. Explicit city search override
        if city and city.strip():
            resolved_city = await geocode_city_name(client, city.strip())
            if resolved_city:
                _LOCATION_CACHE[cache_key] = (now, resolved_city)
                return resolved_city

        # 2. Explicit coordinate override (e.g., HTML5 Geolocation API / GPS)
        if lat is not None and lon is not None:
            resolved_coords = await resolve_coordinates_location(client, lat, lon)
            _LOCATION_CACHE[cache_key] = (now, resolved_coords)
            return resolved_coords

        # 3. Optional Google Geolocation API if GOOGLE_API_KEY is configured
        if settings.google_api_key:
            try:
                g_url = f"https://www.googleapis.com/geolocation/v1/geolocate?key={settings.google_api_key}"
                g_resp = await client.post(g_url, json={"considerIp": True})
                if g_resp.status_code == 200:
                    g_data = g_resp.json().get("location", {})
                    if "lat" in g_data and "lng" in g_data:
                        loc = await resolve_coordinates_location(
                            client,
                            float(g_data["lat"]),
                            float(g_data["lng"]),
                            source_label="google_geolocation_api",
                        )
                        _LOCATION_CACHE[cache_key] = (now, loc)
                        return loc
            except httpx.HTTPError:
                pass

        # 4. Provider 1: ipwho.is
        try:
            resp = await client.get("https://ipwho.is/")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success", False):
                    lat_val = float(data["latitude"])
                    lon_val = float(data["longitude"])
                    addr = await _reverse_geocode_nominatim(client, lat_val, lon_val)
                    district = addr.get("state_district") or addr.get("county") or addr.get("suburb")
                    loc = LocationData(
                        ip_address=data.get("ip"),
                        latitude=lat_val,
                        longitude=lon_val,
                        city=data.get("city") or addr.get("city") or addr.get("town") or "Unknown City",
                        district_or_suburb=district,
                        region=data.get("region") or addr.get("state") or "Unknown Region",
                        country=data.get("country") or "Unknown Country",
                        country_code=(data.get("country_code") or "XX").upper(),
                        postal_code=data.get("postal") or addr.get("postcode"),
                        timezone=(data.get("timezone") or {}).get("id") or "UTC",
                        isp=(data.get("connection") or {}).get("isp"),
                        detection_method="auto_device_ip (ipwho.is + OSM Nominatim)",
                    )
                    _LOCATION_CACHE[cache_key] = (now, loc)
                    return loc
        except (httpx.HTTPError, KeyError, ValueError):
            pass

        # 5. Provider 2: ip-api.com
        try:
            resp = await client.get("http://ip-api.com/json/")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success":
                    lat_val = float(data["lat"])
                    lon_val = float(data["lon"])
                    addr = await _reverse_geocode_nominatim(client, lat_val, lon_val)
                    district = addr.get("state_district") or addr.get("county") or addr.get("suburb")
                    loc = LocationData(
                        ip_address=data.get("query"),
                        latitude=lat_val,
                        longitude=lon_val,
                        city=data.get("city") or "Unknown City",
                        district_or_suburb=district,
                        region=data.get("regionName") or "Unknown Region",
                        country=data.get("country") or "Unknown Country",
                        country_code=(data.get("countryCode") or "XX").upper(),
                        postal_code=data.get("zip"),
                        timezone=data.get("timezone") or "UTC",
                        isp=data.get("isp"),
                        detection_method="auto_device_ip (ip-api.com + OSM Nominatim)",
                    )
                    _LOCATION_CACHE[cache_key] = (now, loc)
                    return loc
        except (httpx.HTTPError, KeyError, ValueError):
            pass

        # 6. Provider 3: get.geojs.io
        try:
            resp = await client.get("https://get.geojs.io/v1/ip/geo.json")
            if resp.status_code == 200:
                data = resp.json()
                lat_val = float(data["latitude"])
                lon_val = float(data["longitude"])
                loc = LocationData(
                    ip_address=data.get("ip"),
                    latitude=lat_val,
                    longitude=lon_val,
                    city=data.get("city") or "Unknown City",
                    district_or_suburb=None,
                    region=data.get("region") or "Unknown Region",
                    country=data.get("country") or "Unknown Country",
                    country_code=(data.get("country_code") or "XX").upper(),
                    postal_code=None,
                    timezone=data.get("timezone") or "UTC",
                    isp=data.get("organization_name"),
                    detection_method="auto_device_ip (geojs.io)",
                )
                _LOCATION_CACHE[cache_key] = (now, loc)
                return loc
        except (httpx.HTTPError, KeyError, ValueError):
            pass

        # 7. Provider 4: ipinfo.io
        try:
            resp = await client.get("https://ipinfo.io/json")
            if resp.status_code == 200:
                data = resp.json()
                loc_str = data.get("loc", "6.9271,79.8612")
                lat_s, lon_s = loc_str.split(",")
                loc = LocationData(
                    ip_address=data.get("ip"),
                    latitude=float(lat_s),
                    longitude=float(lon_s),
                    city=data.get("city") or "Unknown City",
                    district_or_suburb=None,
                    region=data.get("region") or "Unknown Region",
                    country=data.get("country") or "LK",
                    country_code=(data.get("country") or "LK").upper(),
                    postal_code=data.get("postal"),
                    timezone=data.get("timezone") or "Asia/Colombo",
                    isp=data.get("org"),
                    detection_method="auto_device_ip (ipinfo.io)",
                )
                _LOCATION_CACHE[cache_key] = (now, loc)
                return loc
        except (httpx.HTTPError, KeyError, ValueError):
            pass

    # Final fallback if device is offline
    fallback = LocationData(
        ip_address="127.0.0.1",
        latitude=7.0897,
        longitude=79.9925,
        city="Gampaha",
        district_or_suburb="Gampaha District",
        region="Western Province",
        country="Sri Lanka",
        country_code="LK",
        postal_code="11000",
        timezone="Asia/Colombo",
        isp="Local Fallback",
        detection_method="offline_system_timezone_fallback",
    )
    return fallback
