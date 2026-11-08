"""FastAPI entry point for the Raiden Grid & Weather Telemetry REST API."""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from app.config import settings
from app.schemas import (
    LocationData,
    NearbyDiscoveryResponse,
    NotifyNearbyRequest,
    NotifyNearbyResponse,
    PeerWebhookCreate,
    PeerWebhookRecord,
    PowerCutPredictionResponse,
    UnifiedStatusResponse,
    WeatherResponse,
)
from app.services.device_power import get_device_power_status
from app.services.location import detect_device_location
from app.services.nearby import (
    broadcast_nearby_notification,
    discover_nearby_devices,
    get_recent_alerts,
    list_peer_webhooks,
    register_peer_webhook,
    remove_peer_webhook,
    subscribe_sse,
    unsubscribe_sse,
)
from app.services.predictor import generate_power_cut_prediction
from app.services.weather import fetch_weather_for_location

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "REST API that automatically detects the host laptop's geographic location, "
        "retrieves real-time & 24-hour meteorological conditions, calculates the "
        "probability of power cuts using deterministic weather-grid modeling and public "
        "knowledge (Google News RSS utility advisories & CEB/LECO grid zones), and "
        "discovers & notifies nearby devices via UDP LAN Broadcast, SSE Live Streams, "
        "Peer Webhooks, and Desktop Notifications."
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
                "/api/v1/nearby/devices",
                "/api/v1/nearby/notify",
                "/api/v1/nearby/stream",
                "/api/v1/nearby/peers",
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


@app.get("/api/v1/nearby/devices", response_model=NearbyDiscoveryResponse, tags=["Nearby Device Notifications"])
async def get_nearby_devices() -> NearbyDiscoveryResponse:
    """
    Discover nearby devices on the local Wi-Fi/LAN subnet (`ip neigh` ARP table),
    Bluetooth radio (`bluetoothctl`), and registered webhook peers.
    """
    return discover_nearby_devices()


@app.post("/api/v1/nearby/peers", response_model=PeerWebhookRecord, tags=["Nearby Device Notifications"])
async def create_nearby_peer(payload: PeerWebhookCreate) -> PeerWebhookRecord:
    """
    Register a nearby device webhook endpoint (e.g., another laptop, phone, Raspberry Pi,
    Home Assistant, or ntfy server on the LAN) to receive HTTP POST power-cut & weather alerts.
    """
    return register_peer_webhook(payload)


@app.get("/api/v1/nearby/peers", response_model=List[PeerWebhookRecord], tags=["Nearby Device Notifications"])
async def get_nearby_peers() -> List[PeerWebhookRecord]:
    """List all registered nearby device webhook endpoints."""
    return list_peer_webhooks()


@app.delete("/api/v1/nearby/peers/{peer_id}", tags=["Nearby Device Notifications"])
async def delete_nearby_peer(peer_id: str):
    """Unregister a nearby peer webhook endpoint by ID."""
    if not remove_peer_webhook(peer_id):
        raise HTTPException(status_code=404, detail=f"Peer '{peer_id}' not found")
    return {"status": "deleted", "peer_id": peer_id}


@app.post("/api/v1/nearby/notify", response_model=NotifyNearbyResponse, tags=["Nearby Device Notifications"])
async def notify_nearby_devices(
    payload: NotifyNearbyRequest = NotifyNearbyRequest(),
    lat: Optional[float] = Query(None, ge=-90.0, le=90.0, description="Optional latitude override"),
    lon: Optional[float] = Query(None, ge=-180.0, le=180.0, description="Optional longitude override"),
    city: Optional[str] = Query(None, description="Optional city/town name override"),
) -> NotifyNearbyResponse:
    """
    Fetch live weather & power cut predictions and broadcast an alert to all nearby devices across:
    1. **UDP LAN Broadcast & Direct Unicast** (`255.255.255.255` + subnet broadcast + discovered ARP neighbor IPs on port `50505`)
    2. **Real-Time SSE Stream** (`/api/v1/nearby/stream` for connected mobile/laptop web browsers and CLI listeners)
    3. **Registered Peer Webhooks** (`/api/v1/nearby/peers`)
    4. **Host Desktop Notification** (`notify-send`)
    """
    try:
        location = await detect_device_location(lat=lat, lon=lon, city=city)
        weather = await fetch_weather_for_location(location)
        prediction = await generate_power_cut_prediction(location, weather)
        return await broadcast_nearby_notification(prediction, payload)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to broadcast nearby alert: {exc}") from exc


@app.get("/api/v1/nearby/alerts", tags=["Nearby Device Notifications"])
async def list_recent_nearby_alerts():
    """Return the recent alert history broadcast to nearby devices."""
    return {"count": len(get_recent_alerts()), "alerts": get_recent_alerts()}


@app.get("/api/v1/nearby/stream", tags=["Nearby Device Notifications"])
async def stream_nearby_alerts(request: Request):
    """
    Server-Sent Events (SSE) live stream for nearby devices on the Wi-Fi/LAN.
    Nearby phones or laptops can connect via browser or `curl -N http://<laptop-lan-ip>:8000/api/v1/nearby/stream`
    to receive instant push notifications when a power cut or severe weather alert is triggered.
    """
    queue = subscribe_sse()

    async def event_generator():
        try:
            yield f"event: connected\ndata: {json.dumps({'status': 'subscribed', 'timestamp_utc': datetime.now(timezone.utc).isoformat()})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    alert = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: alert\ndata: {json.dumps(alert)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            unsubscribe_sse(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
