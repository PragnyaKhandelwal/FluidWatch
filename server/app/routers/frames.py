from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..bedfeed import to_bed_payload
from ..cv.base import InvalidFrameError
from ..cv.pipeline import active_processor
from ..cv.reading_builder import build_reading, prune_old_readings
from ..dataset_archive import archive_enabled, archive_frame
from ..db import get_db
from ..frame_storage import load_latest_frame, save_latest_frame
from ..models import Reading
from ..schemas import ReadingOut
from ..ws_manager import bed_manager, reading_manager
from .readings import require_auth

router = APIRouter(prefix="/api/v1/frames", tags=["frames"])


@router.post("", response_model=ReadingOut, status_code=201, dependencies=[Depends(require_auth)])
async def ingest_frame(
    device_id: str,
    file: UploadFile,
    background_tasks: BackgroundTasks,
    bed_label: str | None = None,
    patient_name: str | None = None,
    fluid_label: str | None = None,
    db: Session = Depends(get_db),
):
    """The camera-frame path: an image comes in, active_processor (see
    app/cv/pipeline.py) turns it into a fill percentage, and
    build_reading() (app/cv/reading_builder.py) turns that into a full
    reading - same DB row and broadcast as the ESP32 JSON path. Test this
    manually with any image via the file-upload widget at /docs, no
    device required. See the README's "Fill-level model" section.

    The uploaded image itself is saved (overwriting any previous frame
    for this device_id - see frame_storage.py) so GET /latest can serve
    it back for display on the dashboard. When Supabase is configured,
    every frame is also archived for retraining (dataset_archive.py),
    after the response is sent."""
    frame_bytes = await file.read()

    # Process before saving, so an unreadable upload never replaces the
    # device's last good frame on disk.
    try:
        result = active_processor.process(device_id, frame_bytes)
    except InvalidFrameError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    save_latest_frame(device_id, frame_bytes)
    if archive_enabled():
        background_tasks.add_task(archive_frame, device_id, frame_bytes, result.fill_percent, result.aux_class)

    reading_in = build_reading(
        db,
        device_id,
        result.fill_percent,
        bed_label=bed_label,
        patient_name=patient_name,
        fluid_label=fluid_label,
        aux_class=result.aux_class,
    )

    row = Reading(**reading_in.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    prune_old_readings(db, device_id)

    out = ReadingOut.model_validate(row)
    await reading_manager.broadcast(out.model_dump(mode="json"))
    await bed_manager.broadcast(to_bed_payload(out))
    return out


@router.get("/latest")
def latest_frame(device_id: str):
    """The most recent photo received from this device - what the model's
    last reading for it was actually computed from. 404 if this device
    hasn't uploaded a frame yet."""
    frame_bytes = load_latest_frame(device_id)
    if frame_bytes is None:
        raise HTTPException(status_code=404, detail="no frame received yet for this device")
    return Response(content=frame_bytes, media_type="image/jpeg")
