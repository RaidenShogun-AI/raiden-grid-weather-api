"""Linux host hardware power supply inspection service."""

from pathlib import Path
from typing import Optional
from app.schemas import DevicePowerStatus


def get_device_power_status(sys_power_dir: Path = Path("/sys/class/power_supply")) -> DevicePowerStatus:
    """Inspect Linux /sys/class/power_supply to determine AC mains & battery telemetry."""
    if not sys_power_dir.exists() or not sys_power_dir.is_dir():
        return DevicePowerStatus(
            on_ac_power=None,
            battery_present=False,
            battery_percent=None,
            battery_status="No hardware power_supply interface detected",
            local_outage_suspected=False,
        )

    on_ac_power: Optional[bool] = None
    battery_present = False
    battery_percent: Optional[int] = None
    battery_status: Optional[str] = None

    for entry in sorted(sys_power_dir.iterdir()):
        type_file = entry / "type"
        if not type_file.exists():
            continue
        try:
            supply_type = type_file.read_text(encoding="utf-8").strip().lower()
        except OSError:
            continue

        if supply_type in ("mains", "usb", "ups"):
            online_file = entry / "online"
            if online_file.exists():
                try:
                    online_val = online_file.read_text(encoding="utf-8").strip()
                    if online_val == "1":
                        on_ac_power = True
                    elif online_val == "0" and on_ac_power is None:
                        on_ac_power = False
                except OSError:
                    pass

        elif supply_type == "battery":
            battery_present = True
            cap_file = entry / "capacity"
            status_file = entry / "status"
            if cap_file.exists():
                try:
                    battery_percent = int(cap_file.read_text(encoding="utf-8").strip())
                except (OSError, ValueError):
                    pass
            if status_file.exists():
                try:
                    battery_status = status_file.read_text(encoding="utf-8").strip()
                except OSError:
                    pass

    if on_ac_power is None and battery_status:
        if battery_status.lower() in ("charging", "full", "not charging"):
            on_ac_power = True
        elif battery_status.lower() == "discharging":
            on_ac_power = False

    local_outage_suspected = bool(
        battery_present and (on_ac_power is False or (battery_status and battery_status.lower() == "discharging"))
    )

    return DevicePowerStatus(
        on_ac_power=on_ac_power,
        battery_present=battery_present,
        battery_percent=battery_percent,
        battery_status=battery_status,
        local_outage_suspected=local_outage_suspected,
    )
