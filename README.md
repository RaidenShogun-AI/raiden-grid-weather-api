# ⚡ Raiden Grid & Weather Telemetry REST API

A production-ready **FastAPI REST application** that automatically detects the host laptop's geographic location, retrieves real-time and 24-hour meteorological conditions, predicts **power cut possibilities** in the area using deterministic weather-grid stress modeling and public knowledge (Google News RSS utility advisories & regional CEB/LECO grid zones), and **discovers & notifies nearby devices** across the local network.

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
4. **Nearby Device Discovery & Multi-Channel Notification Broadcast (`/api/v1/nearby/*`)**
   - **Automatic Nearby Discovery (`GET /api/v1/nearby/devices`):** Discovers nearby devices on the same Wi-Fi/LAN subnet & Tailscale mesh via the kernel ARP neighbor table (`ip neigh`), Bluetooth radio (`bluetoothctl`), and registered webhook peers
   - **Channel 1 — UDP LAN Broadcast & Unicast:** Sends JSON alert packets over UDP (`SO_BROADCAST` on port `50505`) to the local subnet broadcast address (e.g. `192.168.1.255`) and directly to every discovered LAN neighbor IP (includes companion CLI receiver `scripts/nearby_listener.py`)
   - **Channel 2 — Real-Time Server-Sent Events (SSE) & Browser Push Notifications (`GET /api/v1/nearby/stream`):** Any phone, tablet, or laptop on the local Wi-Fi that opens the dashboard or connects to the SSE stream receives instant push notifications
   - **Channel 3 — Registered Peer Webhooks (`POST /api/v1/nearby/peers`):** Delivers HTTP POST JSON alerts to registered nearby devices, Home Assistant nodes, or `ntfy`/Gotify servers
   - **Channel 4 — Host Desktop Notification (`notify-send`):** Triggers native Linux desktop alerts on the host laptop

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

# 3. Start the REST API server (accessible to all nearby devices on Wi-Fi/LAN)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

- **Interactive Web Dashboard:** `http://localhost:8000/` (or `http://<laptop-lan-ip>:8000/` from nearby phones/laptops)
- **Swagger / OpenAPI Documentation:** `http://localhost:8000/docs`

---

## 📡 REST API Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/v1/health` | Service health and Linux `/sys/class/power_supply` AC/battery status |
| `GET` | `/api/v1/location` | Detect laptop location automatically (or via `?lat=&lon=` / `?city=`) |
| `GET` | `/api/v1/weather` | Current weather + 24-hour hourly forecast for the laptop's area |
| `GET` | `/api/v1/power-cuts` | Full power cut possibility score, factor breakdown, public news advisories & 24h timeline |
| `GET` | `/api/v1/status` | Unified all-in-one JSON bundle (location + weather + power cut prediction) |
| `GET` | `/api/v1/nearby/devices` | Discover nearby LAN/Wi-Fi (`ip neigh`), Bluetooth, and registered peer devices |
| `POST` | `/api/v1/nearby/notify` | Broadcast live weather & power cut alert to all nearby devices (UDP LAN, SSE, Webhooks, Desktop) |
| `GET` | `/api/v1/nearby/stream` | Real-time Server-Sent Events (SSE) push stream for nearby devices |
| `POST` | `/api/v1/nearby/peers` | Register a nearby device webhook URL to receive automatic HTTP POST alerts |
| `GET` | `/api/v1/nearby/alerts` | View recent alerts broadcast to nearby devices |

### Example `curl` Commands

```bash
# Discover nearby devices on the local Wi-Fi / LAN & Bluetooth
curl -s http://localhost:8000/api/v1/nearby/devices | python3 -m json.tool

# Broadcast a live weather & power-cut notification to all nearby devices
curl -s -X POST http://localhost:8000/api/v1/nearby/notify \
  -H "Content-Type: application/json" \
  -d '{"custom_message": "Thunderstorm approaching — charge devices now", "min_probability_threshold": 0.0}' | python3 -m json.tool

# Listen for UDP LAN broadcast alerts on any nearby laptop / Raspberry Pi / phone
python3 scripts/nearby_listener.py --port 50505
```

---

## 🧪 Running Tests

```bash
.venv/bin/pytest -v
```
