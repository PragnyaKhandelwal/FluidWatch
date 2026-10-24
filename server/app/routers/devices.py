from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Reading
from ..schemas import DeviceStatus

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])

# A device that hasn't reported in this long reads as offline in the UI -
# comfortably above any reasonable capture interval (ESP32 firmware
# defaults to a 10s timer) without flapping the badge on a single missed beat.
_ONLINE_WINDOW = timedelta(seconds=90)


@router.get("", response_model=list[DeviceStatus])
def list_devices(db: Session = Depends(get_db)):
    """One row per distinct device_id, from its single most recent reading.
    Powers the dashboard's fleet-status pill and the analytics hardware
    table - the battery/wifi/last-seen data the backend has always
    collected but the frontend never surfaced until now."""
    stmt = select(Reading).order_by(Reading.received_at.desc()).limit(1000)
    latest_by_device: dict[str, Reading] = {}
    for row in db.scalars(stmt).all():
        latest_by_device.setdefault(row.device_id, row)

    now = datetime.now(UTC)
    devices = []
    for row in latest_by_device.values():
        received_at = row.received_at if row.received_at.tzinfo else row.received_at.replace(tzinfo=UTC)
        devices.append(
            DeviceStatus(
                device_id=row.device_id,
                bed_label=row.bed_label,
                patient_name=row.patient_name,
                fluid_label=row.fluid_label,
                battery_percent=row.battery_percent,
                wifi_rssi=row.wifi_rssi,
                status=row.status,
                received_at=row.received_at,
                online=(now - received_at) <= _ONLINE_WINDOW,
            )
        )
    devices.sort(key=lambda d: d.device_id)
    return devices
