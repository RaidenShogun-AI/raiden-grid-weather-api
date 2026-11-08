#!/usr/bin/env python3
"""
Standalone UDP LAN Alert Listener for Nearby Devices.

Run this script on any nearby laptop, desktop, Raspberry Pi, or Termux phone
connected to the same Wi-Fi/LAN subnet to receive real-time weather & power cut
broadcasts from the Raiden Grid & Weather Telemetry API node.

Usage:
    python3 scripts/nearby_listener.py --port 50505
"""

import argparse
import json
import shutil
import socket
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser(description="Listen for Raiden Grid & Weather LAN UDP Broadcast Alerts")
    parser.add_argument("--port", type=int, default=50505, help="UDP port to listen on (default: 50505)")
    args = parser.parse_args()

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", args.port))
        print(f"⚡ Listening for Raiden Grid & Weather UDP LAN alerts on 0.0.0.0:{args.port} ...")

        while True:
            data, addr = sock.recvfrom(65535)
            try:
                payload = json.loads(data.decode("utf-8"))
                title = payload.get("title", "Grid & Weather Alert")
                message = payload.get("message", "")
                severity = payload.get("severity", "INFO")
                print(f"\n[{payload.get('timestamp_utc', '')}] From {addr[0]}:{addr[1]} [{severity}]")
                print(f"  {title}")
                print(f"  {message}")

                if shutil.which("notify-send"):
                    urgency = "critical" if severity in ("HIGH", "CRITICAL") else "normal"
                    subprocess.run(
                        ["notify-send", "-u", urgency, "-a", "Raiden Nearby Listener", title, message],
                        check=False,
                    )
            except (ValueError, UnicodeDecodeError):
                print(f"Received raw packet ({len(data)} bytes) from {addr}")


if __name__ == "__main__":
    main()
