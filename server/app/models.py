from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Reading(Base):
    """One IV drip reading. Field names come from an earlier ESP32-S3
    prototype's IVDripReading struct and match schemas.ReadingIn - keep
    the two in sync."""

    __tablename__ = "readings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(String, index=True)
    timestamp_ms: Mapped[int] = mapped_column(Integer)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )

    fluid_level_percent: Mapped[float] = mapped_column(Float)
    volume_remaining_ml: Mapped[float] = mapped_column(Float)
    bag_capacity_ml: Mapped[float] = mapped_column(Float)
    flow_rate_ml_per_hr: Mapped[float] = mapped_column(Float)
    drop_rate_per_min: Mapped[float] = mapped_column(Float)
    estimated_time_remaining_min: Mapped[float] = mapped_column(Float)

    battery_percent: Mapped[int] = mapped_column(Integer)
    wifi_rssi: Mapped[int] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(String)
    alert: Mapped[str | None] = mapped_column(String, nullable=True)

    # Deployment metadata (which bed/patient/fluid this stand is assigned
    # to) - static per-device config from the firmware, not a sensor
    # reading, but the dashboard's bed cards need it. See bedfeed.py.
    bed_label: Mapped[str | None] = mapped_column(String, nullable=True)
    patient_name: Mapped[str | None] = mapped_column(String, nullable=True)
    fluid_label: Mapped[str | None] = mapped_column(String, nullable=True)

    aux_class: Mapped[str | None] = mapped_column(String, nullable=True)
