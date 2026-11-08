"""Public knowledge & Google News RSS scraper + regional utility grid intelligence."""

from datetime import datetime
from email.utils import parsedate_to_datetime
import time
from typing import Dict, List, Tuple
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET
import httpx
from app.config import settings
from app.schemas import LocationData, OutageNewsItem, UtilityGridInfo

_INTEL_CACHE: Dict[str, Tuple[float, List[OutageNewsItem]]] = {}

# LECO (Lanka Electricity Company) coastal Western/Southern urban service areas
LECO_AREAS = {
    "negombo",
    "kelaniya",
    "moratuwa",
    "maharagama",
    "kotte",
    "sri jayawardenepura kotte",
    "nugegoda",
    "dehiwala",
    "mount lavinia",
    "kalutara",
    "galle",
    "ja-ela",
    "wattala",
    "kandana",
}

OUTAGE_KEYWORDS = [
    "power cut",
    "power outage",
    "outage",
    "blackout",
    "grid failure",
    "power failure",
    "electricity disruption",
    "electricity cut",
    "load shedding",
    "scheduled maintenance",
    "power interruption",
    "substation",
    "transmission line",
    "breakdown",
    "feeder trip",
]


def resolve_utility_grid_info(location: LocationData, local_hour: int, local_minute: int = 0) -> UtilityGridInfo:
    """Resolve the regional electricity utility provider, hotline, and peak load status."""
    city_lower = (location.city or "").lower()
    district_lower = (location.district_or_suburb or "").lower()
    country_code = (location.country_code or "").upper()

    # Peak demand window in South Asia / general grids is 18:30 to 22:30 local time
    time_decimal = local_hour + (local_minute / 60.0)
    is_peak = 18.5 <= time_decimal <= 22.5

    if country_code == "LK":
        is_leco = any(area in city_lower or area in district_lower for area in LECO_AREAS)
        if is_leco:
            return UtilityGridInfo(
                primary_utility="LECO (Lanka Electricity Company) / CEB Grid",
                grid_zone=f"{location.city} ({location.region})",
                emergency_hotline="1910 (LECO) / 1987 (CEB)",
                outage_portal_url="https://www.leco.lk / https://cebcare.ceb.lk",
                is_peak_demand_now=is_peak,
                peak_demand_window="18:30 - 22:30 Asia/Colombo",
            )
        return UtilityGridInfo(
            primary_utility="CEB (Ceylon Electricity Board)",
            grid_zone=f"{location.region} - {location.district_or_suburb or location.city} Zone",
            emergency_hotline="1987 (CEB Call Centre)",
            outage_portal_url="https://cebcare.ceb.lk",
            is_peak_demand_now=is_peak,
            peak_demand_window="18:30 - 22:30 Asia/Colombo",
        )

    return UtilityGridInfo(
        primary_utility=f"{location.region or location.country} Regional Power Grid",
        grid_zone=f"{location.city}, {location.region}",
        emergency_hotline="Local Utility Emergency Dispatch",
        outage_portal_url=f"https://news.google.com/search?q={quote_plus(location.city + ' power outage')}",
        is_peak_demand_now=is_peak,
        peak_demand_window="18:30 - 22:30 Local Time",
    )


def _parse_google_news_rss(xml_text: str, location: LocationData) -> List[OutageNewsItem]:
    """Parse Google News RSS XML into structured OutageNewsItem records."""
    items: List[OutageNewsItem] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    city_l = (location.city or "").lower()
    region_l = (location.region or "").lower()
    district_l = (location.district_or_suburb or "").lower()

    for item_el in root.findall("./channel/item"):
        title = (item_el.findtext("title") or "").strip()
        link = (item_el.findtext("link") or "").strip()
        pub_date = (item_el.findtext("pubDate") or "").strip()
        source_el = item_el.find("source")
        source_name = source_el.text.strip() if source_el is not None and source_el.text else "Google News"

        if not title or not link:
            continue

        # Filter to recent news where possible (within last 14 days if parseable)
        if pub_date:
            try:
                dt = parsedate_to_datetime(pub_date)
                age_days = (datetime.now(dt.tzinfo) - dt).days
                if age_days > 14:
                    continue
            except (ValueError, TypeError):
                pass

        title_lower = title.lower()
        matched_kw = [kw for kw in OUTAGE_KEYWORDS if kw in title_lower]
        if not matched_kw:
            continue

        is_local = any(
            loc_term and len(loc_term) >= 3 and loc_term in title_lower
            for loc_term in (city_l, district_l, region_l.replace(" province", ""))
        )

        items.append(
            OutageNewsItem(
                title=title,
                link=link,
                published_at=pub_date or None,
                source=source_name,
                matched_keywords=matched_kw,
                is_local_match=is_local,
            )
        )
        if len(items) >= 8:
            break

    return items


async def fetch_public_outage_intelligence(location: LocationData) -> List[OutageNewsItem]:
    """
    Query Google News public RSS feeds for recent power cuts, grid failures,
    or utility maintenance notices in the laptop's detected region/country.
    """
    cache_key = f"{location.country_code}:{location.region}:{location.city}"
    now = time.time()
    if cache_key in _INTEL_CACHE:
        cached_at, cached_items = _INTEL_CACHE[cache_key]
        if now - cached_at < settings.cache_ttl_seconds:
            return cached_items

    if location.country_code == "LK":
        query = (
            f'("{location.city}" OR "{location.country}" OR "CEB" OR "LECO") '
            f'("power cut" OR "power outage" OR "blackout" OR "scheduled maintenance" OR "breakdown") when:14d'
        )
        gl = "LK"
        ceid = "LK:en"
    else:
        query = (
            f'("{location.city}" OR "{location.region}") '
            f'("power outage" OR "power cut" OR "blackout" OR "grid failure") when:14d'
        )
        gl = location.country_code if len(location.country_code) == 2 else "US"
        ceid = f"{gl}:en"

    rss_url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en&gl={gl}&ceid={ceid}"

    async with httpx.AsyncClient(
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": settings.user_agent},
        follow_redirects=True,
    ) as client:
        try:
            resp = await client.get(rss_url)
            if resp.status_code == 200:
                parsed = _parse_google_news_rss(resp.text, location)
                _INTEL_CACHE[cache_key] = (now, parsed)
                return parsed
        except httpx.HTTPError:
            pass

    return []
