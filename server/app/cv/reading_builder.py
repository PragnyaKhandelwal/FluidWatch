from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import Reading
from ..schemas import ReadingIn

DEFAULT_BAG_CAPACITY_ML = 500.0
LOW_LEVEL_THRESHOLD_PCT = 15.0

# At a 10s capture interval that's ~8600 readings/device/day - SQLite
# itself handles far more than this without issue, but a long-running
# demo/rehearsal period shouldn't accumulate rows forever either. This
# cap keeps exports and "latest" queries fast and storage predictable.
MAX_READINGS_PER_DEVICE = 1000


def prune_old_readings(db: Session, device_id: str, keep: int = MAX_READINGS_PER_DEVICE) -> None:
    stale_ids = db.scalars(
        select(Reading.id)
        .where(Reading.device_id == device_id)
        .order_by(Reading.received_at.desc())
        .offset(keep)
    ).all()
    if stale_ids:
        db.execute(delete(Reading).where(Reading.id.in_(stale_ids)))
        db.commit()


def build_reading(
    db: Session,
    device_id: str,
    fluid_level_percent: float,
    bag_capacity_ml: float = DEFAULT_BAG_CAPACITY_ML,
    bed_label: str | None = None,
    patient_name: str | None = None,
    fluid_label: str | None = None,
    aux_class: str | None = None,
) -> ReadingIn:
    """Turns a bare fill percentage (from any FrameProcessor - mock or
    real) into a full ReadingIn. Flow rate isn't derivable from a single
    still image, so it's estimated from how much volume drained since
    this device's last reading, same as a real drip would report it."""
    level = max(0.0, min(100.0, fluid_level_percent))
    volume_remaining_ml = bag_capacity_ml * level / 100

    prev = db.scalars(
        select(Reading).where(Reading.device_id == device_id).order_by(Reading.received_at.desc())
    ).first()

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    flow_rate_ml_per_hr = 0.0
    if prev is not None and now_ms > prev.timestamp_ms:
        elapsed_hr = (now_ms - prev.timestamp_ms) / 3_600_000
        drained_ml = prev.volume_remaining_ml - volume_remaining_ml
        flow_rate_ml_per_hr = max(0.0, drained_ml / elapsed_hr)

    drop_rate_per_min = flow_rate_ml_per_hr / 3  # ~20 drops/mL, same rough ratio used elsewhere
    estimated_time_remaining_min = (
        (volume_remaining_ml / flow_rate_ml_per_hr) * 60 if flow_rate_ml_per_hr > 0 else 0.0
    )

    if level <= 0.5:
        status, alert = "EMPTY", "Bag empty - replace immediately"
    elif level <= LOW_LEVEL_THRESHOLD_PCT:
        status, alert = "LOW_LEVEL", "Fluid level low"
    else:
        status, alert = "NORMAL", None

    return ReadingIn(
        device_id=device_id,
        timestamp_ms=now_ms,
        fluid_level_percent=round(level, 1),
        volume_remaining_ml=round(volume_remaining_ml, 1),
        bag_capacity_ml=bag_capacity_ml,
        flow_rate_ml_per_hr=round(flow_rate_ml_per_hr, 1),
        drop_rate_per_min=round(drop_rate_per_min, 1),
        estimated_time_remaining_min=round(estimated_time_remaining_min, 1),
        battery_percent=100,
        wifi_rssi=0,
        status=status,
        alert=alert,
        bed_label=bed_label,
        patient_name=patient_name,
        fluid_label=fluid_label,
        aux_class=aux_class,
    )
