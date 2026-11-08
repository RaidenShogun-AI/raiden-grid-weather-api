"""Pydantic v2 data schemas for Location, Weather, Public Intel, and Power Cut Predictions."""

from typing import List, Optional
from pydantic import BaseModel, Field


class DevicePowerStatus(BaseModel):
    """Local laptop/device power supply telemetry from Linux /sys/class/power_supply."""

    on_ac_power: Optional[bool] = Field(
        None, description="True if connected to AC mains, False if running on battery, None if unknown/desktop"
    )
    battery_present: bool = Field(False, description="Whether a battery was detected on the host")
    battery_percent: Optional[int] = Field(None, description="Current battery charge percentage (0-100)")
    battery_status: Optional[str] = Field(
        None, description="Kernel battery status (Charging, Discharging, Full, Not charging)"
    )
    local_outage_suspected: bool = Field(
        False, description="True if the device is actively discharging on battery, suggesting a possible local power cut"
    )


class LocationData(BaseModel):
    """Resolved geographic location of the laptop/device."""

    ip_address: Optional[str] = Field(None, description="Detected public IPv4/IPv6 address of the device")
    latitude: float = Field(..., description="Latitude in decimal degrees")
    longitude: float = Field(..., description="Longitude in decimal degrees")
    city: str = Field(..., description="Detected city or town name")
    district_or_suburb: Optional[str] = Field(None, description="District, suburb, or municipality from reverse geocoding")
    region: str = Field(..., description="State, province, or region")
    country: str = Field(..., description="Full country name")
    country_code: str = Field(..., description="ISO 3166-1 alpha-2 country code (e.g., LK)")
    postal_code: Optional[str] = Field(None, description="Postal or ZIP code")
    timezone: str = Field("UTC", description="IANA timezone identifier (e.g., Asia/Colombo)")
    isp: Optional[str] = Field(None, description="Internet Service Provider or ASN organization")
    detection_method: str = Field(
        ..., description="How the location was resolved (e.g., ipwho.is, ip-api.com, query_override, google_geolocation)"
    )


class CurrentWeather(BaseModel):
    """Real-time meteorological metrics at the device location."""

    observation_time: str = Field(..., description="ISO 8601 timestamp of current weather observation")
    temperature_c: float = Field(..., description="Air temperature at 2 meters (°C)")
    feels_like_c: float = Field(..., description="Apparent feels-like temperature (°C)")
    humidity_percent: int = Field(..., description="Relative humidity (%)")
    precipitation_mm: float = Field(..., description="Total current precipitation (mm)")
    rain_mm: float = Field(..., description="Current rain amount (mm)")
    showers_mm: float = Field(..., description="Current convective showers (mm)")
    weather_code: int = Field(..., description="WMO standardized weather interpretation code")
    condition_text: str = Field(..., description="Human-readable weather condition")
    is_storm_or_thunder: bool = Field(..., description="True if WMO code indicates thunderstorm or severe squalls")
    cloud_cover_percent: int = Field(..., description="Total cloud cover (%)")
    pressure_hpa: float = Field(..., description="Mean sea-level atmospheric pressure (hPa)")
    wind_speed_kmh: float = Field(..., description="Sustained wind speed at 10m (km/h)")
    wind_gusts_kmh: float = Field(..., description="Peak wind gusts at 10m (km/h)")
    wind_direction_deg: int = Field(..., description="Wind direction in degrees (0-360)")
    cape_j_kg: float = Field(
        0.0, description="Convective Available Potential Energy (J/kg) indicating atmospheric thunderstorm instability"
    )


class HourlyForecastPoint(BaseModel):
    """Single hour meteorological forecast."""

    time: str = Field(..., description="ISO 8601 local timestamp for the forecasted hour")
    temperature_c: float
    precipitation_probability_percent: int
    precipitation_mm: float
    weather_code: int
    condition_text: str
    wind_speed_kmh: float
    wind_gusts_kmh: float
    cape_j_kg: float


class WeatherResponse(BaseModel):
    """Complete weather report for the laptop's location."""

    location: LocationData
    current: CurrentWeather
    forecast_next_24h: List[HourlyForecastPoint]
    data_source: str = Field("Open-Meteo Public Meteorological Forecast API", description="Primary weather data provider")


class OutageNewsItem(BaseModel):
    """Public knowledge / news advisory item regarding power cuts or grid maintenance."""

    title: str
    link: str
    published_at: Optional[str] = None
    source: Optional[str] = None
    matched_keywords: List[str] = Field(default_factory=list)
    is_local_match: bool = Field(
        False, description="True if the advisory explicitly mentions the user's city, district, or province"
    )


class RiskFactorBreakdown(BaseModel):
    """Individual contributing factor to the power cut probability model."""

    factor_name: str
    score_contribution: float = Field(..., description="Points contributed toward the 0-100 outage risk score")
    max_possible_score: float
    severity: str = Field(..., description="LOW, MODERATE, HIGH, or CRITICAL")
    explanation: str


class HourlyOutagePrediction(BaseModel):
    """Predicted power cut probability for a specific upcoming hour."""

    time: str
    outage_probability_percent: float = Field(..., ge=0.0, le=100.0)
    risk_level: str
    weather_condition: str
    wind_gusts_kmh: float
    precipitation_mm: float
    is_peak_grid_demand_hour: bool
    primary_driver: str


class UtilityGridInfo(BaseModel):
    """Local electricity utility provider details and zone classification."""

    primary_utility: str = Field(..., description="Electricity distribution provider (e.g., CEB / LECO in Sri Lanka)")
    grid_zone: str = Field(..., description="Regional distribution zone or province")
    emergency_hotline: str = Field(..., description="Official utility outage reporting hotline")
    outage_portal_url: str = Field(..., description="Official public utility web portal")
    is_peak_demand_now: bool = Field(..., description="True if current local time falls within peak grid demand hours")
    peak_demand_window: str = Field("18:30 - 22:30 Local Time", description="Standard evening peak load window")


class PowerCutPredictionResponse(BaseModel):
    """Comprehensive power cut possibility analysis & 24-hour prediction."""

    location: LocationData
    current_weather_summary: CurrentWeather
    device_power: DevicePowerStatus
    utility_grid: UtilityGridInfo
    outage_probability_percent: float = Field(
        ..., ge=0.0, le=100.0, description="Estimated current/imminent probability of a power cut (0-100%)"
    )
    peak_24h_probability_percent: float = Field(
        ..., ge=0.0, le=100.0, description="Highest predicted hourly power cut probability in the next 24 hours"
    )
    highest_risk_hour: Optional[str] = Field(
        None, description="Timestamp of the highest risk hour in the next 24 hours"
    )
    risk_level: str = Field(..., description="Overall risk classification: LOW, MODERATE, HIGH, or CRITICAL")
    prediction_methodology: str = Field(
        "Non-AI Deterministic & Statistical Multi-Factor Model (Meteorological Grid Stress + Google News/Public Utility Intelligence + Peak Load Curve + Host Hardware Telemetry)",
        description="Explanation of the prediction model",
    )
    summary: str = Field(..., description="Actionable summary of the power cut possibility in the area")
    recommendations: List[str] = Field(..., description="Practical preparedness recommendations based on risk")
    risk_factors: List[RiskFactorBreakdown]
    public_outage_advisories: List[OutageNewsItem] = Field(
        default_factory=list,
        description="Recent public knowledge & news items from Google News RSS regarding power cuts or utility maintenance in the region",
    )
    hourly_predictions_24h: List[HourlyOutagePrediction]


class UnifiedStatusResponse(BaseModel):
    """All-in-one response combining location, weather, device power, and power cut predictions."""

    generated_at: str
    location: LocationData
    weather: WeatherResponse
    power_cut_prediction: PowerCutPredictionResponse
