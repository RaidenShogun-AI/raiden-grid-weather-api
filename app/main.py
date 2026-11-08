"""FastAPI entry point for the Raiden Grid & Weather Telemetry REST API."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from app.config import settings
from app.schemas import (
    LocationData,
    PowerCutPredictionResponse,
    UnifiedStatusResponse,
    WeatherResponse,
)
from app.services.device_power import get_device_power_status
from app.services.location import detect_device_location
from app.services.predictor import generate_power_cut_prediction
from app.services.weather import fetch_weather_for_location

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "REST API that automatically detects the host laptop's geographic location, "
        "retrieves real-time & 24-hour meteorological conditions, and calculates the "
        "probability of power cuts using deterministic weather-grid modeling, public "
        "knowledge (Google News RSS utility advisories), regional grid zones (CEB/LECO), "
        "and local hardware power telemetry."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
async def root_dashboard():
    """Serve the interactive Telemetry & REST API Explorer dashboard."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse(
        {
            "service": settings.app_name,
            "version": settings.app_version,
            "docs_url": "/docs",
            "endpoints": [
                "/api/v1/health",
                "/api/v1/location",
                "/api/v1/weather",
                "/api/v1/power-cuts",
                "/api/v1/status",
            ],
        }
    )


@app.get("/api/v1/health", tags=["System & Hardware"])
async def health_check():
    """Return API service health and local laptop hardware AC/battery power telemetry."""
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "device_power": get_device_power_status().model_dump(),
    }


@app.get("/api/v1/location", response_model=LocationData, tags=["Geolocation"])
async def get_location(
    lat: Optional[float] = Query(None, ge=-90.0, le=90.0, description="Optional latitude override"),
    lon: Optional[float] = Query(None, ge=-180.0, le=180.0, description="Optional longitude override"),
    city: Optional[str] = Query(None, description="Optional city/town name override (e.g., Gampaha, Colombo)"),
) -> LocationData:
    """
    Detect the current location of this laptop automatically (via multi-provider IP geolocation
    and OpenStreetMap Nominatim reverse geocoding), or resolve optional `lat`/`lon` or `city` overrides.
    """
    return await detect_device_location(lat=lat, lon=lon, city=city)


@app.get("/api/v1/weather", response_model=WeatherResponse, tags=["Weather"])
async def get_weather(
    lat: Optional[float] = Query(None, ge=-90.0, le=90.0, description="Optional latitude override"),
    lon: Optional[float] = Query(None, ge=-180.0, le=180.0, description="Optional longitude override"),
    city: Optional[str] = Query(None, description="Optional city/town name override"),
) -> WeatherResponse:
    """
    Get real-time meteorological observations and a 24-hour hourly forecast
    for the laptop's detected location (or specified coordinates/city).
    """
    try:
        location = await detect_device_location(lat=lat, lon=lon, city=city)
        return await fetch_weather_for_location(location)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to retrieve weather telemetry: {exc}") from exc


@app.get("/api/v1/power-cuts", response_model=PowerCutPredictionResponse, tags=["Power Cut Predictions"])
async def get_power_cut_possibilities(
    lat: Optional[float] = Query(None, ge=-90.0, le=90.0, description="Optional latitude override"),
    lon: Optional[float] = Query(None, ge=-180.0, le=180.0, description="Optional longitude override"),
    city: Optional[str] = Query(None, description="Optional city/town name override"),
) -> PowerCutPredictionResponse:
    """
    Analyze and predict the possibility of power cuts in the laptop's area.

    Combines:
    - Real-time & 24h forecasted wind gusts, thunderstorm/CAPE instability, and rainfall
    - Public knowledge & Google News RSS utility advisories (CEB, LECO, scheduled maintenance, breakdowns)
    - Regional utility zone & evening peak load window analysis
    - Local Linux hardware power supply (`/sys/class/power_supply`) telemetry
    """
    try:
        location = await detect_device_location(lat=lat, lon=lon, city=city)
        weather = await fetch_weather_for_location(location)
        return await generate_power_cut_prediction(location, weather)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to generate power cut prediction: {exc}") from exc


@app.get("/api/v1/status", response_model=UnifiedStatusResponse, tags=["Unified Telemetry"])
async def get_unified_status(
    lat: Optional[float] = Query(None, ge=-90.0, le=90.0, description="Optional latitude override"),
    lon: Optional[float] = Query(None, ge=-180.0, le=180.0, description="Optional longitude override"),
    city: Optional[str] = Query(None, description="Optional city/town name override"),
) -> UnifiedStatusResponse:
    """
    Unified single-call endpoint returning the laptop's detected location, current & 24h weather,
    and complete power cut possibility prediction report.
    """
    try:
        location = await detect_device_location(lat=lat, lon=lon, city=city)
        weather = await fetch_weather_for_location(location)
        prediction = await generate_power_cut_prediction(location, weather)
        return UnifiedStatusResponse(
            generated_at=datetime.now(timezone.utc).isoformat(),
            location=location,
            weather=weather,
            power_cut_prediction=prediction,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to compute unified status: {exc}") from exc
