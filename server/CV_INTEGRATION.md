# The fluid-level model - how it's wired in

**Status: done.** `active_processor` in `app/cv/pipeline.py` runs the real
trained model (`app/cv/real_frame_processor.py`), not the mock, as of
2026-08-21. This doc now describes what's actually running and how to
update it later, rather than a handoff spec (it originally was one - see
git history if you want the "if you're integrating a model from scratch"
version).

## What's running

- **Model**: `app/cv/models/fill_regression_v2_mobilenet.pth` -
  MobileNetV3-Small, multitask (continuous 0-100% regression + auxiliary
  4-class head). Test MAE ~3.8%, RMSE ~8.9% on the training team's
  held-out set. Full training provenance, the training scripts, and the
  earlier classifier/classical-CV attempts it superseded live in
  `ml-model-training/` at the repo root (see that folder's own README).
- **Integration**: `app/cv/real_frame_processor.py`'s `RealFrameProcessor`
  loads the model once at import time (not per-request), decodes the
  uploaded image, replicates the exact preprocessing the model was
  trained with (224×224, aspect-preserving thumbnail, black-padded to
  square, ImageNet-normalized - see `_prepare_image()`), runs inference,
  and returns the regression head's fill % (clamped 0-100). The auxiliary
  classification head's prediction (empty/50%/80%/full) is logged
  alongside it as a sanity check but isn't otherwise used.
- **The interface** (`app/cv/base.py`) is unchanged from before -
  `FrameProcessor.process(device_id, frame_bytes) -> float`. Everything
  downstream (volume, flow-rate-from-history, status/alerts, defaults) is
  still derived centrally in `app/cv/reading_builder.py`; the model itself
  only ever answers one question, same as originally designed.

## Dependencies

`torch`, `torchvision`, `pillow` - added to `requirements.txt`, pinned to
CPU-only wheels via `--extra-index-url https://download.pytorch.org/whl/cpu`
(the default PyPI `torch` bundles CUDA and is enormous for no benefit on
a CPU-only host). Confirmed working: `torch 2.13.0+cpu`,
`torchvision 0.28.0+cpu`.

**Render deployment note**: this is a real dependency install (~1GB+ of
wheels) and a 4.2MB model file committed to the repo - expect a slower
first build than before. MobileNetV3-Small is lightweight enough that CPU
inference should be fine on Render's free tier (no GPU needed), but
confirm the build itself completes within Render's free-tier build time
limits once actually deployed there.

## Testing without any camera or ESP32 hardware

Unchanged - still the easiest way to try it:

1. `cd server`, activate the venv, `pip install -r requirements.txt`,
   `uvicorn app.main:app --reload --port 8000`.
2. Open `http://localhost:8000/docs`, find `POST /api/v1/frames`, "Try it
   out", set any `device_id`, upload a real photo, Execute.
3. `fluid_level_percent` in the response is the model's actual prediction
   on that image (not a mock random number anymore).

```sh
curl -X POST "http://localhost:8000/api/v1/frames?device_id=test-1" \
  -H "Authorization: Bearer <token>" \
  -F "file=@/path/to/some_bag_photo.jpg"
```

Verified end-to-end (mechanically - real image in, real inference,
correct reading derived, correct WebSocket broadcast) with a synthetic
test image. **Not yet validated against real IV bag/container photos or
intermediate fill levels** - the training data notes flag that
predictions between the four trained levels (0/50/80/100%) are
interpolation. Worth running a batch of real photos through `/docs` (or
`ml-model-training/test_v2.py` directly) before trusting this in a demo.

## If the model needs updating later

Retrain via `ml-model-training/train_continuous_v2.py`, drop the new
`.pth` file into `app/cv/models/` (same filename, or update the path in
`real_frame_processor.py`), restart the server. Nothing else changes.
