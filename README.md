# ⚡ Raiden Grid & Weather Telemetry REST API

A production-ready **FastAPI REST application** that automatically detects the host laptop's geographic location, retrieves real-time and 24-hour meteorological conditions, and predicts **power cut possibilities** in the area using deterministic weather-grid stress modeling, public knowledge (Google News RSS utility advisories), regional electricity grid zones (CEB / LECO), and local Linux hardware power telemetry.

> **No AI / LLM keys required:** All predictions are powered by deterministic & statistical meteorological modeling, public weather forecasts (Open-Meteo), public reverse geocoding (OpenStreetMap Nominatim), multi-provider IP/device geolocation, and live Google News RSS public utility intelligence.

---

## ✨ Features

1. **Automatic Device Geolocation (`/api/v1/location`)**
   - Multi-provider automatic laptop IP geolocation fallback (`ipwho.is` → `ip-api.com` → `get.geojs.io` → `ipinfo.io` + optional Google Maps Geolocation API)
   - Enriched with **OpenStreetMap Nominatim** reverse geocoding for suburb/district precision
   - Supports optional query overrides (`?lat=...&lon=...` from browser GPS or `?city=...`)
2. **Real-Time Weather & 24-Hour Forecast (`/api/v1/weather`)**
   - Powered by the **Open-Meteo Meteorological & Atmospheric API**
   - Tracks temperature, feels-like, humidity, precipitation, rain, showers, pressure, cloud cover, sustained wind speed, 10m wind gusts, WMO weather codes, and **CAPE (Convective Available Potential Energy in J/kg)** for thunderstorm/lightning severity
3. **Power Cut Possibility & 24-Hour Prediction Engine (`/api/v1/power-cuts`)**
   - **Factor 1 — Wind & Overhead Line Stress (0–30 pts):** Models tree branch contact and phase-to-phase conductor trips from wind speeds and peak gusts
   - **Factor 2 — Thunderstorm, Lightning & CAPE Instability (0–30 pts):** Evaluates lightning surge and substation breaker/auto-recloser trip risk
   - **Factor 3 — Precipitation & Insulator Moisture Risk (0–15 pts):** Evaluates heavy rain flashover and transformer flooding risk
   - **Factor 4 — Public Knowledge & Google News RSS Intelligence (0–15 pts):** Scans live Google News RSS feeds (`news.google.com/rss/search`) for power cut notices, scheduled maintenance, and grid breakdowns (including **CEB** & **LECO** in Sri Lanka)
   - **Factor 5 — Grid Peak Load & Local Hardware Telemetry (0–15 pts):** Maps cities to CEB/LECO utility zones, checks evening peak load hours (`18:30–22:30`), and inspects Linux `/sys/class/power_supply` to detect if the laptop is discharging on battery
   - Generates a **24-hour hourly power cut probability timeline** and identifies the highest-risk upcoming hour
4. **Interactive Live Telemetry Dashboard (`/`) & OpenAPI Swagger UI (`/docs`)**

---

## 🚀 Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/RaidenShogun-AI/raiden-grid-weather-api.git
cd raiden-grid-weather-api

# 2. Create a virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Start the REST API server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

- **Interactive Web Dashboard:** `http://localhost:8000/`
- **Swagger / OpenAPI Documentation:** `http://localhost:8000/docs`
- **ReDoc Documentation:** `http://localhost:8000/redoc`

---

## 📡 REST API Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/v1/health` | Service health and Linux `/sys/class/power_supply` AC/battery status |
| `GET` | `/api/v1/location` | Detect laptop location automatically (or via `?lat=&lon=` / `?city=`) |
| `GET` | `/api/v1/weather` | Current weather + 24-hour hourly forecast for the laptop's area |
| `GET` | `/api/v1/power-cuts` | Full power cut possibility score, factor breakdown, public news advisories & 24h timeline |
| `GET` | `/api/v1/status` | Unified all-in-one JSON bundle (location + weather + power cut prediction) |

### Example `curl` Commands

```bash
# Automatic laptop location detection + weather + power cut predictions
curl -s http://localhost:8000/api/v1/power-cuts | python3 -m json.tool

# Override by city name
curl -s "http://localhost:8000/api/v1/power-cuts?city=Colombo" | python3 -m json.tool

# Override by exact GPS coordinates
curl -s "http://localhost:8000/api/v1/status?lat=7.0897&lon=79.9925" | python3 -m json.tool
```

---

## 🧪 Running Tests

```bash
.venv/bin/pytest -v
```
