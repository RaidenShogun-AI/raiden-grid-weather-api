"""Unit and integration tests for the Weather, Power-Cut Prediction, and Nearby Device Notification REST API."""

from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.schemas import LocationData
from app.services.device_power import get_device_power_status
from app.services.predictor import (
    _classify_risk_level,
    _score_precipitation,
    _score_thunderstorm_and_cape,
    _score_wind_stress,
)
from app.services.public_intel import _parse_google_news_rss, resolve_utility_grid_info
from app.services.weather import decode_wmo_code

client = TestClient(app)


def test_health_endpoint():
    """Verify /api/v1/health returns 200 and hardware power status."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "device_power" in data
    assert "battery_present" in data["device_power"]


def test_dashboard_root():
    """Verify root endpoint serves the HTML telemetry dashboard with Nearby Broadcast controls."""
    response = client.get("/")
    assert response.status_code == 200
    assert "Raiden Grid" in response.text
    assert "Nearby Devices Discovery" in response.text


def test_wmo_weather_decoder():
    """Verify WMO weather codes decode accurately and flag storms."""
    text_clear, is_storm_clear = decode_wmo_code(0)
    assert text_clear == "Clear sky"
    assert is_storm_clear is False

    text_storm, is_storm = decode_wmo_code(95)
    assert "Thunderstorm" in text_storm
    assert is_storm is True


def test_prediction_scoring_models():
    """Verify deterministic wind, storm/CAPE, and precipitation risk scoring."""
    low_wind_score, low_wind_sev, _ = _score_wind_stress(8.0, 12.0)
    assert low_wind_sev == "LOW"
    assert low_wind_score < 5.0

    crit_wind_score, crit_wind_sev, _ = _score_wind_stress(55.0, 72.0)
    assert crit_wind_sev == "CRITICAL"
    assert crit_wind_score >= 25.0

    storm_score, storm_sev, _ = _score_thunderstorm_and_cape(96, 1800.0)
    assert storm_sev == "CRITICAL"
    assert storm_score >= 25.0

    rain_score, rain_sev, _ = _score_precipitation(18.0, 95)
    assert rain_sev == "CRITICAL"
    assert rain_score == 15.0

    assert _classify_risk_level(10.0) == "LOW"
    assert _classify_risk_level(30.0) == "MODERATE"
    assert _classify_risk_level(55.0) == "HIGH"
    assert _classify_risk_level(80.0) == "CRITICAL"


def test_utility_grid_resolution():
    """Verify CEB and LECO utility zone mapping for Sri Lankan cities."""
    gampaha_loc = LocationData(
        latitude=7.0897,
        longitude=79.9925,
        city="Gampaha",
        region="Western Province",
        country="Sri Lanka",
        country_code="LK",
        detection_method="test",
    )
    ceb_info = resolve_utility_grid_info(gampaha_loc, local_hour=20, local_minute=0)
    assert "CEB" in ceb_info.primary_utility
    assert ceb_info.is_peak_demand_now is True

    negombo_loc = gampaha_loc.model_copy(update={"city": "Negombo"})
    leco_info = resolve_utility_grid_info(negombo_loc, local_hour=14, local_minute=0)
    assert "LECO" in leco_info.primary_utility
    assert leco_info.is_peak_demand_now is False


def test_google_news_rss_parser():
    """Verify Google News RSS XML parsing and local city keyword matching."""
    sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <item>
          <title>CEB announces scheduled power cut in Gampaha district tomorrow</title>
          <link>https://news.google.com/articles/test1</link>
          <source>Daily Mirror</source>
        </item>
      </channel>
    </rss>
    """
    loc = LocationData(
        latitude=7.0897,
        longitude=79.9925,
        city="Gampaha",
        region="Western Province",
        country="Sri Lanka",
        country_code="LK",
        detection_method="test",
    )
    items = _parse_google_news_rss(sample_xml, loc)
    assert len(items) == 1
    assert items[0].is_local_match is True
    assert "power cut" in items[0].matched_keywords


def test_device_power_mock_sysfs(tmp_path: Path):
    """Verify Linux /sys/class/power_supply parser with mocked AC and BAT0 directories."""
    ac_dir = tmp_path / "AC"
    ac_dir.mkdir()
    (ac_dir / "type").write_text("Mains\n", encoding="utf-8")
    (ac_dir / "online").write_text("0\n", encoding="utf-8")

    bat_dir = tmp_path / "BAT0"
    bat_dir.mkdir()
    (bat_dir / "type").write_text("Battery\n", encoding="utf-8")
    (bat_dir / "capacity").write_text("78\n", encoding="utf-8")
    (bat_dir / "status").write_text("Discharging\n", encoding="utf-8")

    status = get_device_power_status(sys_power_dir=tmp_path)
    assert status.on_ac_power is False
    assert status.battery_present is True
    assert status.battery_percent == 78
    assert status.battery_status == "Discharging"
    assert status.local_outage_suspected is True


def test_nearby_devices_and_peer_registration():
    """Verify nearby LAN/Bluetooth discovery and peer webhook CRUD endpoints."""
    dev_resp = client.get("/api/v1/nearby/devices")
    assert dev_resp.status_code == 200
    dev_data = dev_resp.json()
    assert "host_lan_ips" in dev_data
    assert "discovered_devices" in dev_data

    # Register a nearby peer webhook
    peer_resp = client.post(
        "/api/v1/nearby/peers",
        json={
            "name": "Nearby Arch Laptop",
            "callback_url": "http://192.168.1.41:9000/webhook",
            "min_risk_probability": 10.0,
        },
    )
    assert peer_resp.status_code == 200
    peer_id = peer_resp.json()["peer_id"]

    list_resp = client.get("/api/v1/nearby/peers")
    assert list_resp.status_code == 200
    assert any(p["peer_id"] == peer_id for p in list_resp.json())

    del_resp = client.delete(f"/api/v1/nearby/peers/{peer_id}")
    assert del_resp.status_code == 200


def test_nearby_notification_broadcast():
    """Verify POST /api/v1/nearby/notify broadcasts across UDP LAN, SSE, and records alert history."""
    notify_resp = client.post(
        "/api/v1/nearby/notify",
        json={
            "custom_message": "Automated test broadcast to nearby LAN devices",
            "min_probability_threshold": 0.0,
            "udp_port": 50505,
            "include_desktop_notify": False,
        },
    )
    assert notify_resp.status_code == 200
    data = notify_resp.json()
    assert data["triggered"] is True
    assert "alert_title" in data
    channels = {r["channel"]: r for r in data["delivery_results"]}
    assert "udp_lan_broadcast" in channels
    assert channels["udp_lan_broadcast"]["status"] == "delivered"
    assert "sse_live_stream" in channels

    alerts_resp = client.get("/api/v1/nearby/alerts")
    assert alerts_resp.status_code == 200
    assert alerts_resp.json()["count"] >= 1


def test_live_api_endpoints():
    """Verify live /api/v1/location, /api/v1/weather, /api/v1/power-cuts, and /api/v1/status."""
    loc_resp = client.get("/api/v1/location")
    assert loc_resp.status_code == 200
    loc_data = loc_resp.json()
    assert "latitude" in loc_data
    assert "longitude" in loc_data
    assert "city" in loc_data

    status_resp = client.get("/api/v1/status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert "location" in status_data
    assert "weather" in status_data
    assert "power_cut_prediction" in status_data
    pred = status_data["power_cut_prediction"]
    assert 0.0 <= pred["outage_probability_percent"] <= 100.0
    assert pred["risk_level"] in ("LOW", "MODERATE", "HIGH", "CRITICAL")
    assert len(pred["risk_factors"]) == 5
    assert len(pred["hourly_predictions_24h"]) > 0
