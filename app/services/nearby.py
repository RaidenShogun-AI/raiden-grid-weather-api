"""Nearby device discovery (LAN ARP, mDNS, Bluetooth, Peer Webhooks) and multi-channel alert broadcasting."""

import asyncio
from datetime import datetime, timezone
import ipaddress
import json
import shutil
import socket
import subprocess
from typing import Any, Dict, List, Set, Tuple
import uuid
import httpx
from app.config import settings
from app.schemas import (
    ChannelDeliveryResult,
    NearbyDevice,
    NearbyDiscoveryResponse,
    NotifyNearbyRequest,
    NotifyNearbyResponse,
    PeerWebhookCreate,
    PeerWebhookRecord,
    PowerCutPredictionResponse,
)

# In-memory registry of nearby peer webhooks and active SSE subscriber queues
_REGISTERED_PEERS: Dict[str, PeerWebhookRecord] = {}
_SSE_SUBSCRIBERS: Set[asyncio.Queue] = set()
_LAST_ALERT_HISTORY: List[Dict[str, Any]] = []


def register_peer_webhook(payload: PeerWebhookCreate) -> PeerWebhookRecord:
    """Register a nearby device webhook URL to receive power cut & weather alerts."""
    peer_id = str(uuid.uuid4())[:8]
    record = PeerWebhookRecord(
        peer_id=peer_id,
        name=payload.name,
        callback_url=payload.callback_url,
        min_risk_probability=payload.min_risk_probability,
        registered_at=datetime.now(timezone.utc).isoformat(),
    )
    _REGISTERED_PEERS[peer_id] = record
    return record


def list_peer_webhooks() -> List[PeerWebhookRecord]:
    """Return all registered nearby peer webhooks."""
    return list(_REGISTERED_PEERS.values())


def remove_peer_webhook(peer_id: str) -> bool:
    """Delete a registered peer webhook by ID."""
    return _REGISTERED_PEERS.pop(peer_id, None) is not None


def subscribe_sse() -> asyncio.Queue:
    """Create and register a new SSE queue for a nearby device listening to /api/v1/nearby/stream."""
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    _SSE_SUBSCRIBERS.add(q)
    return q


def unsubscribe_sse(q: asyncio.Queue) -> None:
    """Remove a disconnected SSE client queue."""
    _SSE_SUBSCRIBERS.discard(q)


def get_host_network_interfaces() -> Tuple[List[str], List[str]]:
    """
    Inspect active IPv4 network interfaces on this laptop using `ip -j -4 addr show`.
    Returns (host_lan_ips, subnet_broadcast_ips).
    """
    host_ips: List[str] = []
    broadcast_ips: List[str] = []
    try:
        res = subprocess.run(
            ["ip", "-j", "-4", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            ifaces = json.loads(res.stdout)
            for iface in ifaces:
                ifname = iface.get("ifname", "")
                if ifname == "lo" or ifname.startswith(("docker", "veth", "br-")):
                    continue
                for addr_info in iface.get("addr_info", []):
                    local_ip = addr_info.get("local")
                    prefixlen = addr_info.get("prefixlen")
                    bcast = addr_info.get("broadcast")
                    if local_ip:
                        host_ips.append(local_ip)
                        if bcast:
                            broadcast_ips.append(bcast)
                        elif prefixlen and int(prefixlen) < 31:
                            try:
                                net = ipaddress.IPv4Network(f"{local_ip}/{prefixlen}", strict=False)
                                broadcast_ips.append(str(net.broadcast_address))
                            except ValueError:
                                pass
    except (OSError, ValueError, subprocess.SubprocessError):
        pass

    if not host_ips:
        host_ips.append("127.0.0.1")
    if "255.255.255.255" not in broadcast_ips:
        broadcast_ips.append("255.255.255.255")

    return host_ips, broadcast_ips


def _discover_arp_neighbors() -> List[NearbyDevice]:
    """Inspect the Linux kernel ARP / neighbor table (`ip neigh show`) for nearby LAN devices."""
    devices: List[NearbyDevice] = []
    try:
        res = subprocess.run(
            ["ip", "neigh", "show"],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
        if res.returncode != 0:
            return devices

        for line in res.stdout.splitlines():
            parts = line.strip().split()
            if not parts:
                continue
            ip_str = parts[0]
            state = parts[-1].upper()
            if state in ("FAILED", "INCOMPLETE", "NOARP"):
                continue

            iface = None
            mac = None
            if "dev" in parts:
                dev_idx = parts.index("dev")
                if dev_idx + 1 < len(parts):
                    iface = parts[dev_idx + 1]
            if "lladdr" in parts:
                mac_idx = parts.index("lladdr")
                if mac_idx + 1 < len(parts):
                    mac = parts[mac_idx + 1]

            # Skip internal container bridges
            if iface and iface.startswith(("docker", "br-", "veth")):
                continue

            devices.append(
                NearbyDevice(
                    device_id=ip_str,
                    ip_address=ip_str,
                    mac_address=mac,
                    hostname=None,
                    interface=iface,
                    discovery_method="lan_arp_neighbor",
                    state=state,
                )
            )
    except (OSError, subprocess.SubprocessError):
        pass

    return devices


def _discover_bluetooth_devices() -> List[NearbyDevice]:
    """Query `bluetoothctl devices` for paired or cached nearby Bluetooth devices."""
    devices: List[NearbyDevice] = []
    if not shutil.which("bluetoothctl"):
        return devices
    try:
        res = subprocess.run(
            ["bluetoothctl", "devices"],
            capture_output=True,
            text=True,
            timeout=2.5,
            check=False,
        )
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                line = line.strip()
                if line.startswith("Device "):
                    parts = line.split(" ", 2)
                    if len(parts) >= 3:
                        mac_addr = parts[1]
                        dev_name = parts[2]
                        devices.append(
                            NearbyDevice(
                                device_id=mac_addr,
                                ip_address=None,
                                mac_address=mac_addr,
                                hostname=dev_name,
                                interface="bluetooth",
                                discovery_method="bluetooth_radio",
                                state="PAIRED_OR_VISIBLE",
                            )
                        )
    except (OSError, subprocess.SubprocessError):
        pass
    return devices


def discover_nearby_devices(udp_port: int = 50505) -> NearbyDiscoveryResponse:
    """Discover all nearby devices across LAN ARP, Bluetooth, and registered Peer Webhooks."""
    host_ips, bcast_ips = get_host_network_interfaces()
    primary_ip = host_ips[0] if host_ips else "localhost"

    devices: List[NearbyDevice] = []
    devices.extend(_discover_arp_neighbors())
    devices.extend(_discover_bluetooth_devices())

    for peer in _REGISTERED_PEERS.values():
        devices.append(
            NearbyDevice(
                device_id=peer.peer_id,
                ip_address=None,
                mac_address=None,
                hostname=f"{peer.name} ({peer.callback_url})",
                interface="http_webhook",
                discovery_method="registered_peer",
                state="REGISTERED",
            )
        )

    return NearbyDiscoveryResponse(
        host_lan_ips=host_ips,
        subnet_broadcast_ips=bcast_ips,
        sse_stream_url=f"http://{primary_ip}:{settings.port}/api/v1/nearby/stream",
        udp_broadcast_port=udp_port,
        discovered_devices=devices,
        active_sse_subscribers=len(_SSE_SUBSCRIBERS),
    )


def _broadcast_udp_packets(
    alert_payload: Dict[str, Any],
    broadcast_ips: List[str],
    devices: List[NearbyDevice],
    udp_port: int,
) -> ChannelDeliveryResult:
    """Send UDP LAN broadcast and unicast alert packets to all discovered IPv4 neighbors."""
    encoded = json.dumps(alert_payload).encode("utf-8")
    targets: Set[str] = set()

    for b_ip in broadcast_ips:
        targets.add(b_ip)

    for dev in devices:
        if dev.ip_address and ":" not in dev.ip_address:
            targets.add(dev.ip_address)

    sent_count = 0
    errors: List[str] = []

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(1.0)
            for target_ip in sorted(targets):
                try:
                    sock.sendto(encoded, (target_ip, udp_port))
                    sent_count += 1
                except OSError as exc:
                    errors.append(f"{target_ip}: {exc}")
    except OSError as exc:
        return ChannelDeliveryResult(
            channel="udp_lan_broadcast",
            status="failed",
            targets_reached=0,
            details=f"UDP socket error: {exc}",
        )

    return ChannelDeliveryResult(
        channel="udp_lan_broadcast",
        status="delivered" if sent_count > 0 else "skipped",
        targets_reached=sent_count,
        details=f"Broadcast UDP alert packet ({len(encoded)} bytes) to {sent_count} LAN target(s) on port {udp_port}: {', '.join(sorted(targets))}",
    )


def _push_sse_alert(alert_payload: Dict[str, Any]) -> ChannelDeliveryResult:
    """Push real-time alert event to all active nearby browser/SSE stream clients."""
    _LAST_ALERT_HISTORY.append(alert_payload)
    if len(_LAST_ALERT_HISTORY) > 20:
        _LAST_ALERT_HISTORY.pop(0)

    reached = 0
    for q in list(_SSE_SUBSCRIBERS):
        try:
            q.put_nowait(alert_payload)
            reached += 1
        except asyncio.QueueFull:
            pass

    return ChannelDeliveryResult(
        channel="sse_live_stream",
        status="delivered" if reached > 0 else "queued_in_history",
        targets_reached=reached,
        details=(
            f"Pushed instant SSE alert to {reached} connected nearby browser/stream subscriber(s) "
            f"(also cached in recent alert buffer for newly connecting LAN devices)."
        ),
    )


async def _dispatch_peer_webhooks(
    alert_payload: Dict[str, Any], effective_prob: float
) -> ChannelDeliveryResult:
    """Deliver HTTP POST JSON alerts to all registered nearby peer webhook URLs."""
    if not _REGISTERED_PEERS:
        return ChannelDeliveryResult(
            channel="peer_webhooks",
            status="skipped",
            targets_reached=0,
            details="No custom peer webhook URLs currently registered via POST /api/v1/nearby/peers.",
        )

    eligible_peers = [
        p for p in _REGISTERED_PEERS.values() if effective_prob >= p.min_risk_probability
    ]
    if not eligible_peers:
        return ChannelDeliveryResult(
            channel="peer_webhooks",
            status="skipped",
            targets_reached=0,
            details=f"No registered peers matched minimum probability threshold ({effective_prob}%).",
        )

    reached = 0
    failures = 0
    async with httpx.AsyncClient(timeout=5.0, headers={"User-Agent": settings.user_agent}) as client:
        for peer in eligible_peers:
            try:
                resp = await client.post(peer.callback_url, json=alert_payload)
                if resp.status_code < 400:
                    reached += 1
                else:
                    failures += 1
            except httpx.HTTPError:
                failures += 1

    return ChannelDeliveryResult(
        channel="peer_webhooks",
        status="delivered" if reached > 0 else "failed",
        targets_reached=reached,
        details=f"Delivered webhook POST payload to {reached}/{len(eligible_peers)} registered nearby peer(s) ({failures} failed).",
    )


def _trigger_host_desktop_notify(title: str, message: str, risk_level: str) -> ChannelDeliveryResult:
    """Trigger a local Linux desktop notification via `notify-send` if available."""
    import os

    if not shutil.which("notify-send"):
        return ChannelDeliveryResult(
            channel="host_desktop",
            status="skipped",
            targets_reached=0,
            details="notify-send binary not found on host.",
        )

    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return ChannelDeliveryResult(
            channel="host_desktop",
            status="skipped",
            targets_reached=0,
            details="No active X11/Wayland DISPLAY session attached to API process.",
        )

    urgency = "critical" if risk_level in ("HIGH", "CRITICAL") else "normal"
    try:
        res = subprocess.run(
            ["notify-send", "-u", urgency, "-a", "Raiden Grid Watch", title, message],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        if res.returncode == 0:
            return ChannelDeliveryResult(
                channel="host_desktop",
                status="delivered",
                targets_reached=1,
                details=f"Displayed desktop notification via notify-send (urgency={urgency}).",
            )
        return ChannelDeliveryResult(
            channel="host_desktop",
            status="skipped",
            targets_reached=0,
            details="Host D-Bus notification session inactive or headless.",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ChannelDeliveryResult(
            channel="host_desktop",
            status="skipped",
            targets_reached=0,
            details=f"Desktop notification skipped: {exc}",
        )


async def broadcast_nearby_notification(
    prediction: PowerCutPredictionResponse,
    req: NotifyNearbyRequest,
) -> NotifyNearbyResponse:
    """
    Evaluate power cut & weather risk and broadcast multi-channel notifications
    to all nearby devices on the LAN/Wi-Fi, SSE subscribers, registered webhooks, and host desktop.
    """
    now_utc = datetime.now(timezone.utc).isoformat()
    discovery = discover_nearby_devices(udp_port=req.udp_port)

    curr_prob = prediction.outage_probability_percent
    peak_prob = prediction.peak_24h_probability_percent
    effective_prob = max(curr_prob, peak_prob)

    loc = prediction.location
    w = prediction.current_weather_summary

    title = (
        f"⚡ Grid & Weather Alert [{prediction.risk_level}] — {loc.city} ({curr_prob}% Now / {peak_prob}% Peak 24h)"
    )
    base_msg = (
        f"Location: {loc.city}, {loc.region} | Weather: {w.condition_text}, {w.temperature_c}°C "
        f"(Gusts {w.wind_gusts_kmh} km/h, CAPE {w.cape_j_kg} J/kg) | "
        f"Immediate Outage Risk: {curr_prob}% ({prediction.risk_level}) | "
        f"24h Peak Risk: {peak_prob}% at {prediction.highest_risk_hour} | "
        f"Utility: {prediction.utility_grid.primary_utility} ({prediction.utility_grid.emergency_hotline})"
    )
    if req.custom_message:
        full_message = f"{req.custom_message} — {base_msg}"
    else:
        full_message = base_msg

    if effective_prob < req.min_probability_threshold:
        return NotifyNearbyResponse(
            triggered=False,
            timestamp_utc=now_utc,
            alert_severity=prediction.risk_level,
            alert_title=title,
            alert_message=(
                f"Alert suppressed: max outage probability ({effective_prob}%) is below "
                f"requested threshold ({req.min_probability_threshold}%)."
            ),
            location_city=loc.city,
            current_outage_probability_percent=curr_prob,
            peak_24h_probability_percent=peak_prob,
            highest_risk_hour=prediction.highest_risk_hour,
            discovered_nearby_devices=discovery.discovered_devices,
            delivery_results=[],
        )

    alert_packet: Dict[str, Any] = {
        "event": "raiden_grid_weather_alert",
        "timestamp_utc": now_utc,
        "severity": prediction.risk_level,
        "title": title,
        "message": full_message,
        "location": {
            "city": loc.city,
            "region": loc.region,
            "country": loc.country,
            "latitude": loc.latitude,
            "longitude": loc.longitude,
        },
        "weather": {
            "condition": w.condition_text,
            "temperature_c": w.temperature_c,
            "wind_gusts_kmh": w.wind_gusts_kmh,
            "precipitation_mm": w.precipitation_mm,
            "cape_j_kg": w.cape_j_kg,
        },
        "power_cut_prediction": {
            "current_probability_percent": curr_prob,
            "peak_24h_probability_percent": peak_prob,
            "highest_risk_hour": prediction.highest_risk_hour,
            "risk_level": prediction.risk_level,
            "utility": prediction.utility_grid.primary_utility,
            "hotline": prediction.utility_grid.emergency_hotline,
            "recommendations": prediction.recommendations,
        },
    }

    delivery_results: List[ChannelDeliveryResult] = []

    # 1. UDP LAN Broadcast & Unicast to discovered LAN neighbors
    udp_res = _broadcast_udp_packets(
        alert_packet,
        discovery.subnet_broadcast_ips,
        discovery.discovered_devices,
        req.udp_port,
    )
    delivery_results.append(udp_res)

    # 2. SSE Real-Time Stream to connected nearby browsers/devices
    sse_res = _push_sse_alert(alert_packet)
    delivery_results.append(sse_res)

    # 3. Registered Peer Webhooks
    webhook_res = await _dispatch_peer_webhooks(alert_packet, effective_prob)
    delivery_results.append(webhook_res)

    # 4. Local Host Desktop Notification
    if req.include_desktop_notify:
        desktop_res = _trigger_host_desktop_notify(title, full_message, prediction.risk_level)
        delivery_results.append(desktop_res)

    return NotifyNearbyResponse(
        triggered=True,
        timestamp_utc=now_utc,
        alert_severity=prediction.risk_level,
        alert_title=title,
        alert_message=full_message,
        location_city=loc.city,
        current_outage_probability_percent=curr_prob,
        peak_24h_probability_percent=peak_prob,
        highest_risk_hour=prediction.highest_risk_hour,
        discovered_nearby_devices=discovery.discovered_devices,
        delivery_results=delivery_results,
    )


def get_recent_alerts() -> List[Dict[str, Any]]:
    """Return recent alert payloads broadcast by this node."""
    return list(_LAST_ALERT_HISTORY)
