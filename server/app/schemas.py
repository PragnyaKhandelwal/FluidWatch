from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ReadingIn(BaseModel):
    """What the ESP32 firmware POSTs. Must match CloudClient::sendReading()
    in firmware/esp32-iv-monitor/src/cloud_client.cpp."""

    device_id: str
    timestamp_ms: int
    fluid_level_percent: float
    volume_remaining_ml: float
    bag_capacity_ml: float
    flow_rate_ml_per_hr: float
    drop_rate_per_min: float
    estimated_time_remaining_min: float
    battery_percent: int
    wifi_rssi: int
    status: str
    alert: str | None = None

    bed_label: str | None = None
    patient_name: str | None = None
    fluid_label: str | None = None

    # Only populated on the camera-frame path (app/routers/frames.py) -
    # the model's auxiliary 4-class prediction (empty/50%/80%/full) from
    # the same forward pass as the continuous fill_level_percent, shown
    # on the dashboard as a rough "does the model agree with itself"
    # sanity signal. Null for JSON-only readings (no image involved).
    aux_class: str | None = None


class ReadingOut(ReadingIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    received_at: datetime


class DeviceStatus(BaseModel):
    """One device's latest known state - see app/routers/devices.py."""

    device_id: str
    bed_label: str | None
    patient_name: str | None
    fluid_label: str | None
    battery_percent: int
    wifi_rssi: int
    status: str
    received_at: datetime
    online: bool
