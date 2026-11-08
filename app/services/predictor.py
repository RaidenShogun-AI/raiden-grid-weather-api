"""Deterministic & statistical power cut possibility prediction engine."""

from typing import List, Tuple
from app.schemas import (
    CurrentWeather,
    DevicePowerStatus,
    HourlyForecastPoint,
    HourlyOutagePrediction,
    LocationData,
    OutageNewsItem,
    PowerCutPredictionResponse,
    RiskFactorBreakdown,
    WeatherResponse,
)
from app.services.device_power import get_device_power_status
from app.services.public_intel import fetch_public_outage_intelligence, resolve_utility_grid_info
from app.services.weather import STORM_WMO_CODES


def _classify_risk_level(probability: float) -> str:
    """Classify a 0-100 probability percentage into a standard risk tier."""
    if probability >= 70.0:
        return "CRITICAL"
    if probability >= 45.0:
        return "HIGH"
    if probability >= 22.0:
        return "MODERATE"
    return "LOW"


def _parse_hour_minute(iso_time: str) -> Tuple[int, int]:
    """Extract (hour, minute) from an ISO 8601 timestamp string like '2026-11-08T22:30'."""
    try:
        if "T" in iso_time:
            time_part = iso_time.split("T")[1]
            parts = time_part.split(":")
            return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        pass
    return 12, 0


def _score_wind_stress(wind_speed_kmh: float, wind_gusts_kmh: float) -> Tuple[float, str, str]:
    """
    Evaluate overhead distribution line fault risk from wind speed & gusts (max 30 points).
    In tropical/suburban grids with overhead conductors & vegetation, gusts > 40 km/h
    cause tree branch contact and phase-to-phase faults.
    """
    effective_gust = max(wind_gusts_kmh, wind_speed_kmh * 1.25)
    if effective_gust >= 65.0:
        return (
            28.0,
            "CRITICAL",
            f"Severe wind gusts ({wind_gusts_kmh:.1f} km/h) pose a high threat of fallen branches and overhead feeder trips.",
        )
    if effective_gust >= 45.0:
        score = 15.0 + ((effective_gust - 45.0) / 20.0) * 12.0
        return (
            round(score, 1),
            "HIGH",
            f"Strong wind gusts ({wind_gusts_kmh:.1f} km/h) increase overhead line sway and vegetation contact risk.",
        )
    if effective_gust >= 25.0:
        score = 4.0 + ((effective_gust - 25.0) / 20.0) * 10.0
        return (
            round(score, 1),
            "MODERATE",
            f"Moderate winds (gusts up to {wind_gusts_kmh:.1f} km/h) cause minor mechanical stress on distribution lines.",
        )
    score = max(1.0, (effective_gust / 25.0) * 4.0)
    return (
        round(score, 1),
        "LOW",
        f"Calm to light wind conditions (gusts {wind_gusts_kmh:.1f} km/h, sustained {wind_speed_kmh:.1f} km/h).",
    )


def _score_thunderstorm_and_cape(weather_code: int, cape_j_kg: float) -> Tuple[float, str, str]:
    """
    Evaluate lightning surge & substation breaker trip risk from WMO storm codes and CAPE (max 30 points).
    """
    score = 0.0
    if weather_code in (96, 99):
        score += 24.0
    elif weather_code == 95:
        score += 18.0
    elif weather_code == 82:
        score += 12.0
    elif weather_code in (65, 81):
        score += 6.0

    # Convective Available Potential Energy (J/kg) indicates thunderstorm updraft intensity
    if cape_j_kg >= 2000.0:
        score += 10.0
    elif cape_j_kg >= 1000.0:
        score += 6.0
    elif cape_j_kg >= 400.0:
        score += 3.0

    score = min(30.0, round(score, 1))
    if score >= 20.0:
        return (
            score,
            "CRITICAL",
            f"Active thunderstorm / high convective instability (CAPE {cape_j_kg:.0f} J/kg) creates severe lightning surge and auto-recloser trip risk.",
        )
    if score >= 10.0:
        return (
            score,
            "HIGH",
            f"Elevated convective storm activity (WMO {weather_code}, CAPE {cape_j_kg:.0f} J/kg) increases lightning-induced transformer fuse trips.",
        )
    if score >= 4.0:
        return (
            score,
            "MODERATE",
            f"Mild atmospheric instability (CAPE {cape_j_kg:.0f} J/kg); isolated lightning trips possible if storm cells develop.",
        )
    return (
        score,
        "LOW",
        f"Low convective lightning risk (WMO code {weather_code}, CAPE {cape_j_kg:.0f} J/kg).",
    )


def _score_precipitation(precip_mm: float, humidity_pct: int) -> Tuple[float, str, str]:
    """Evaluate heavy rain insulator flashover & flooding risk (max 15 points)."""
    if precip_mm >= 15.0:
        return (
            15.0,
            "CRITICAL",
            f"Torrential precipitation ({precip_mm:.1f} mm) risks substation flooding and insulator flashover.",
        )
    if precip_mm >= 5.0:
        score = 8.0 + ((precip_mm - 5.0) / 10.0) * 6.0
        return (
            round(score, 1),
            "HIGH",
            f"Heavy rainfall ({precip_mm:.1f} mm) can trigger moisture ingress in older pole-mounted switchgear.",
        )
    if precip_mm >= 1.0:
        score = 3.0 + ((precip_mm - 1.0) / 4.0) * 4.5
        return (
            round(score, 1),
            "MODERATE",
            f"Light-to-moderate rain ({precip_mm:.1f} mm) observed at location.",
        )
    # High humidity (>90%) slightly increases insulator leakage current
    hum_bonus = 1.5 if humidity_pct >= 90 else 0.5
    return (
        hum_bonus,
        "LOW",
        f"No significant precipitation ({precip_mm:.1f} mm, relative humidity {humidity_pct}%).",
    )


def _score_public_intel(advisories: List[OutageNewsItem], city: str) -> Tuple[float, str, str]:
    """Evaluate Google News & public utility outage/maintenance reports (max 15 points)."""
    if not advisories:
        return (
            1.0,
            "LOW",
            "No active power cut emergencies or scheduled maintenance advisories found in recent Google News public feeds.",
        )

    local_matches = [item for item in advisories if item.is_local_match]
    if local_matches:
        score = min(15.0, 9.0 + len(local_matches) * 3.0)
        return (
            round(score, 1),
            "HIGH",
            f"Found {len(local_matches)} public news/utility report(s) directly mentioning {city} or surrounding province.",
        )

    score = min(8.0, 2.5 + len(advisories) * 1.0)
    return (
        round(score, 1),
        "MODERATE",
        f"Found {len(advisories)} regional/national utility news advisory item(s) in public feeds (none exclusively targeting {city}).",
    )


def _score_grid_and_hardware(
    is_peak_now: bool, temp_c: float, device_power: DevicePowerStatus
) -> Tuple[float, str, str]:
    """Evaluate grid peak load window, thermal stress, and local laptop AC power state (max 15 points)."""
    score = 2.0  # Baseline grid operational risk
    reasons: List[str] = []

    if is_peak_now:
        score += 4.5
        reasons.append("Currently within evening peak electricity demand window (18:30–22:30)")
    if temp_c >= 33.0:
        score += 3.0
        reasons.append(f"High ambient temperature ({temp_c:.1f}°C) increases cooling load and transformer oil temperature")

    if device_power.local_outage_suspected:
        score += 8.0
        reasons.append("Laptop is currently discharging on battery power (possible local AC mains interruption)")
    elif device_power.on_ac_power is True:
        reasons.append("Host AC mains power detected online")

    score = min(15.0, round(score, 1))
    if score >= 10.0:
        sev = "HIGH"
    elif score >= 5.0:
        sev = "MODERATE"
    else:
        sev = "LOW"

    explanation = "; ".join(reasons) if reasons else "Normal baseline grid load outside peak hours."
    return score, sev, explanation


def _predict_single_hour(
    point: HourlyForecastPoint, public_intel_score: float
) -> HourlyOutagePrediction:
    """Compute hourly outage probability for a single forecasted hour in the next 24h."""
    hour, minute = _parse_hour_minute(point.time)
    is_peak = 18.5 <= (hour + minute / 60.0) <= 22.5

    wind_score, _, _ = _score_wind_stress(point.wind_speed_kmh, point.wind_gusts_kmh)
    storm_score, _, _ = _score_thunderstorm_and_cape(point.weather_code, point.cape_j_kg)
    rain_score, _, _ = _score_precipitation(point.precipitation_mm, point.precipitation_probability_percent)

    # Precipitation probability amplifier
    prob_factor = (point.precipitation_probability_percent / 100.0) * 4.0
    peak_score = 5.0 if is_peak else 1.5
    thermal_score = 2.5 if point.temperature_c >= 33.0 else 0.0

    # Weighted total (carry 60% of public intel score into future hours)
    raw_prob = wind_score + storm_score + rain_score + prob_factor + peak_score + thermal_score + (public_intel_score * 0.6)
    prob = round(min(98.0, max(2.0, raw_prob)), 1)
    level = _classify_risk_level(prob)

    if point.weather_code in STORM_WMO_CODES or point.cape_j_kg >= 1200.0:
        driver = "Thunderstorm / Lightning Instability"
    elif point.wind_gusts_kmh >= 40.0:
        driver = f"High Wind Gusts ({point.wind_gusts_kmh:.1f} km/h)"
    elif point.precipitation_mm >= 3.0 or point.precipitation_probability_percent >= 70:
        driver = f"Heavy Rain Forecast ({point.precipitation_probability_percent}% chance)"
    elif is_peak:
        driver = "Evening Peak Grid Demand Window"
    else:
        driver = "Baseline Grid & Weather Conditions"

    return HourlyOutagePrediction(
        time=point.time,
        outage_probability_percent=prob,
        risk_level=level,
        weather_condition=point.condition_text,
        wind_gusts_kmh=point.wind_gusts_kmh,
        precipitation_mm=point.precipitation_mm,
        is_peak_grid_demand_hour=is_peak,
        primary_driver=driver,
    )


async def generate_power_cut_prediction(
    location: LocationData, weather: WeatherResponse
) -> PowerCutPredictionResponse:
    """
    Build the complete PowerCutPredictionResponse combining meteorological stress,
    public knowledge/Google News RSS advisories, regional utility grid zones, and laptop power sensors.
    """
    curr: CurrentWeather = weather.current
    local_hour, local_min = _parse_hour_minute(curr.observation_time)

    device_power = get_device_power_status()
    utility_grid = resolve_utility_grid_info(location, local_hour, local_min)
    advisories = await fetch_public_outage_intelligence(location)

    wind_score, wind_sev, wind_exp = _score_wind_stress(curr.wind_speed_kmh, curr.wind_gusts_kmh)
    storm_score, storm_sev, storm_exp = _score_thunderstorm_and_cape(curr.weather_code, curr.cape_j_kg)
    rain_score, rain_sev, rain_exp = _score_precipitation(curr.precipitation_mm, curr.humidity_percent)
    intel_score, intel_sev, intel_exp = _score_public_intel(advisories, location.city)
    grid_score, grid_sev, grid_exp = _score_grid_and_hardware(
        utility_grid.is_peak_demand_now, curr.temperature_c, device_power
    )

    risk_factors = [
        RiskFactorBreakdown(
            factor_name="Wind & Overhead Line Mechanical Stress",
            score_contribution=wind_score,
            max_possible_score=30.0,
            severity=wind_sev,
            explanation=wind_exp,
        ),
        RiskFactorBreakdown(
            factor_name="Thunderstorm, Lightning & CAPE Instability",
            score_contribution=storm_score,
            max_possible_score=30.0,
            severity=storm_sev,
            explanation=storm_exp,
        ),
        RiskFactorBreakdown(
            factor_name="Precipitation & Insulator Moisture Risk",
            score_contribution=rain_score,
            max_possible_score=15.0,
            severity=rain_sev,
            explanation=rain_exp,
        ),
        RiskFactorBreakdown(
            factor_name="Public Knowledge & Google News Utility Advisories",
            score_contribution=intel_score,
            max_possible_score=15.0,
            severity=intel_sev,
            explanation=intel_exp,
        ),
        RiskFactorBreakdown(
            factor_name="Grid Peak Load & Local Hardware Power Telemetry",
            score_contribution=grid_score,
            max_possible_score=15.0,
            severity=grid_sev,
            explanation=grid_exp,
        ),
    ]

    total_score = sum(f.score_contribution for f in risk_factors)
    current_probability = round(min(98.0, max(2.0, total_score)), 1)
    overall_risk_level = _classify_risk_level(current_probability)

    hourly_predictions = [
        _predict_single_hour(pt, intel_score) for pt in weather.forecast_next_24h
    ]

    if hourly_predictions:
        peak_hour_obj = max(hourly_predictions, key=lambda h: h.outage_probability_percent)
        peak_24h_prob = peak_hour_obj.outage_probability_percent
        highest_risk_hour = peak_hour_obj.time
    else:
        peak_24h_prob = current_probability
        highest_risk_hour = curr.observation_time

    # Build actionable summary and recommendations
    summary = (
        f"Power cut probability in {location.city} ({location.region}) is currently {current_probability}% "
        f"({overall_risk_level} risk) under '{curr.condition_text}' conditions ({curr.temperature_c}°C, "
        f"gusts {curr.wind_gusts_kmh} km/h). Over the next 24 hours, the peak predicted outage risk is "
        f"{peak_24h_prob}% around {highest_risk_hour}."
    )

    recommendations: List[str] = []
    if overall_risk_level in ("HIGH", "CRITICAL") or peak_24h_prob >= 45.0:
        recommendations.append(
            f"Charge laptop, mobile devices, and UPS/power banks prior to {highest_risk_hour} (predicted {peak_24h_prob}% outage risk)."
        )
        recommendations.append(
            "Enable surge protection on sensitive electronics due to elevated storm or feeder trip probability."
        )
    else:
        recommendations.append(
            f"Grid conditions in {location.city} are currently stable ({current_probability}% immediate risk); standard power management applies."
        )

    if utility_grid.is_peak_demand_now:
        recommendations.append(
            f"Local grid is currently in the evening peak load window ({utility_grid.peak_demand_window}); avoid running heavy inductive appliances simultaneously."
        )
    if advisories:
        recommendations.append(
            f"Review the {len(advisories)} recent public utility/news advisory item(s) listed in `public_outage_advisories` for scheduled maintenance areas."
        )
    recommendations.append(
        f"In case of an unscheduled breakdown in {location.city}, contact {utility_grid.primary_utility} via {utility_grid.emergency_hotline}."
    )

    return PowerCutPredictionResponse(
        location=location,
        current_weather_summary=curr,
        device_power=device_power,
        utility_grid=utility_grid,
        outage_probability_percent=current_probability,
        peak_24h_probability_percent=peak_24h_prob,
        highest_risk_hour=highest_risk_hour,
        risk_level=overall_risk_level,
        summary=summary,
        recommendations=recommendations,
        risk_factors=risk_factors,
        public_outage_advisories=advisories,
        hourly_predictions_24h=hourly_predictions,
    )
