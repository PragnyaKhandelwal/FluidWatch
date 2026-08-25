from .schemas import ReadingOut

# frontend/src/lib/fluidwatch.ts BedStatus is "STABLE" | "WATCH" | "CRITICAL" -
# coarser than our device status, so OCCLUSION/ERROR both read as CRITICAL.
_STATUS_MAP = {
    "NORMAL": "STABLE",
    "LOW_LEVEL": "WATCH",
    "EMPTY": "CRITICAL",
    "OCCLUSION": "CRITICAL",
    "ERROR": "CRITICAL",
}


def to_bed_payload(reading: ReadingOut) -> dict:
    """Shapes a reading into exactly what frontend/src/lib/fluidwatch.ts's
    Bed type and dashboard.tsx's applyLiveUpdate() expect: {id, bed,
    patient, fluid, flow, level, status, auxClass, battery, wifi}. `id` is
    the join key the dashboard matches/creates bed cards on, so it's
    always device_id. auxClass is null for JSON-only (non-camera) readings.
    battery/wifi are the same hardware telemetry GET /api/v1/devices
    exposes, pushed live here so bed cards don't need to poll separately."""
    return {
        "id": reading.device_id,
        "bed": reading.bed_label or reading.device_id,
        "patient": reading.patient_name or "Unassigned",
        "fluid": reading.fluid_label or "Unknown",
        "flow": round(reading.flow_rate_ml_per_hr, 1),
        "level": round(reading.fluid_level_percent, 1),
        "status": _STATUS_MAP.get(reading.status, "STABLE"),
        "auxClass": reading.aux_class,
        "battery": reading.battery_percent,
        "wifi": reading.wifi_rssi,
    }
