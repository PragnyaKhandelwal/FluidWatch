from app.cv import reading_builder

from .conftest import AUTH, make_jpeg


def _upload(client, device_id="CAM-01", image=None, **params):
    return client.post(
        "/api/v1/frames",
        params={"device_id": device_id, **params},
        files={"file": ("frame.jpg", image or make_jpeg(), "image/jpeg")},
        headers=AUTH,
    )


def test_frame_upload_requires_auth(client, fake_processor):
    res = client.post(
        "/api/v1/frames",
        params={"device_id": "CAM-01"},
        files={"file": ("frame.jpg", make_jpeg(), "image/jpeg")},
    )
    assert res.status_code == 401
    assert fake_processor.calls == []


def test_frame_upload_builds_reading_from_model_output(client, fake_processor):
    res = _upload(client, bed_label="Bed 09", patient_name="P. Test", fluid_label="Dextrose 5%")
    assert res.status_code == 201
    out = res.json()

    assert fake_processor.calls == ["CAM-01"]
    assert out["device_id"] == "CAM-01"
    assert out["fluid_level_percent"] == 72.5
    assert out["volume_remaining_ml"] == 362.5  # 72.5% of the default 500 ml bag
    assert out["status"] == "NORMAL"
    assert out["aux_class"] == "80%"
    assert out["bed_label"] == "Bed 09"
    assert out["patient_name"] == "P. Test"
    assert out["fluid_label"] == "Dextrose 5%"


def test_latest_frame_round_trip(client, fake_processor):
    assert client.get("/api/v1/frames/latest", params={"device_id": "CAM-01"}).status_code == 404

    image = make_jpeg(color=(10, 20, 30))
    _upload(client, image=image)

    res = client.get("/api/v1/frames/latest", params={"device_id": "CAM-01"})
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"
    assert res.content == image


def test_latest_frame_is_overwritten_not_accumulated(client, fake_processor):
    first, second = make_jpeg(color=(1, 1, 1)), make_jpeg(color=(250, 250, 250))
    _upload(client, image=first)
    _upload(client, image=second)

    assert client.get("/api/v1/frames/latest", params={"device_id": "CAM-01"}).content == second


def test_non_image_upload_is_rejected(client, monkeypatch):
    # Uses the real preprocessing path - a garbage upload must be a clean
    # 4xx, and must not replace the last good frame on disk.
    good = make_jpeg()
    from app.cv.pipeline import active_processor
    from app.routers import frames

    monkeypatch.setattr(frames, "active_processor", active_processor)
    assert _upload(client, image=good).status_code == 201

    res = _upload(client, image=b"definitely not a jpeg")
    assert res.status_code == 422
    assert client.get("/api/v1/frames/latest", params={"device_id": "CAM-01"}).content == good


def test_frame_readings_are_pruned_per_device(client, fake_processor, monkeypatch):
    monkeypatch.setattr(reading_builder, "MAX_READINGS_PER_DEVICE", 3)
    # prune_old_readings' default arg was bound at definition time, so
    # patch the function the router calls instead of just the constant.
    from app.routers import frames

    real_prune = reading_builder.prune_old_readings
    monkeypatch.setattr(frames, "prune_old_readings", lambda db, device_id: real_prune(db, device_id, keep=3))

    for _ in range(5):
        _upload(client, device_id="CAM-01")
    _upload(client, device_id="CAM-02")

    assert len(client.get("/api/v1/readings", params={"device_id": "CAM-01"}).json()) == 3
    assert len(client.get("/api/v1/readings", params={"device_id": "CAM-02"}).json()) == 1
