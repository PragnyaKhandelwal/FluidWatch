"""shared_training.py against a fake Supabase Storage: the lock, the version
registry, preprocessing parity with production, session rules, and a full
init -> train -> download cycle on a tiny synthetic dataset."""

import csv
import io
import json
import sys
from argparse import Namespace
from pathlib import Path

import httpx
import pytest
import torch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shared_training as st  # noqa: E402
from app.cv.real_frame_processor import _prepare_image  # noqa: E402

SUPABASE = "https://example.supabase.co"


class FakeStorage:
    def __init__(self):
        self.buckets: set[str] = set()
        self.objects: dict[str, bytes] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/storage/v1/bucket":
            bucket = json.loads(request.content)["id"]
            if bucket in self.buckets:
                return httpx.Response(400, json={"statusCode": "409", "error": "Duplicate", "message": "exists"})
            self.buckets.add(bucket)
            return httpx.Response(200, json={"name": bucket})
        key = path.removeprefix("/storage/v1/object/authenticated/").removeprefix("/storage/v1/object/")
        if request.method == "GET":
            if key not in self.objects:
                # Supabase's older shape for a missing object: HTTP 400 wrapping a 404.
                return httpx.Response(400, json={"statusCode": "404", "error": "not_found", "message": "not found"})
            return httpx.Response(200, content=self.objects[key])
        if request.method == "DELETE":
            if key not in self.objects:
                return httpx.Response(404, json={"message": "Object not found"})
            del self.objects[key]
            return httpx.Response(200, json={})
        if request.method == "POST":
            if key in self.objects and request.headers.get("x-upsert") != "true":
                return httpx.Response(409, json={"error": "Duplicate", "message": "The resource already exists"})
            self.objects[key] = request.content
            return httpx.Response(200, json={"Key": key})
        return httpx.Response(405)


@pytest.fixture
def fake():
    return FakeStorage()


@pytest.fixture
def store(fake):
    client = httpx.Client(base_url=SUPABASE, transport=httpx.MockTransport(fake.handler))
    s = st.ModelStore(client, "service-key")
    s.ensure_bucket()
    return s


def make_dataset(root: Path, sessions: dict[str, list[float]]) -> Path:
    """Synthetic frames whose brightness tracks the label - enough signal to
    train on. Returns the labels.csv path."""
    rows = []
    for session, levels in sessions.items():
        for i, level in enumerate(levels):
            rel = f"CAM/{session}/{i}.jpg"
            path = root / "images" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            shade = int(40 + level * 2)
            Image.new("RGB", (64, 48), (shade, shade, shade)).save(path, format="JPEG")
            rows.append({"storage_path": rel, "session": session, "label_fill_percent": level})
    labels = root / "labels.csv"
    with labels.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["storage_path", "session", "label_fill_percent"])
        writer.writeheader()
        writer.writerows(rows)
    return labels


# --- lock & registry ----------------------------------------------------------


def test_lock_is_exclusive_and_releasable(store):
    store.acquire_lock("alice")
    with pytest.raises(st.LockHeld, match="alice"):
        store.acquire_lock("bob")
    store.release_lock()
    assert store.acquire_lock("bob")["holder"] == "bob"


def test_stale_lock_needs_explicit_takeover(store):
    store.acquire_lock("alice", hours=-1)  # already expired
    with pytest.raises(st.LockHeld):
        store.acquire_lock("bob")
    assert store.acquire_lock("bob", take_stale=True)["holder"] == "bob"


def test_fresh_lock_cannot_be_taken_over(store):
    store.acquire_lock("alice")
    with pytest.raises(st.LockHeld):
        store.acquire_lock("bob", take_stale=True)


def test_registry_round_trip_and_bucket_idempotent(store):
    assert store.load_registry() is None
    store.save_registry({"best": 1, "versions": []})
    store.save_registry({"best": 2, "versions": []})  # upsert
    assert store.load_registry()["best"] == 2
    store.ensure_bucket()  # second call is a no-op


# --- preprocessing & rules ------------------------------------------------------


def test_letterbox_matches_production_preprocessing():
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), (90, 140, 200)).save(buf, format="JPEG")
    with Image.open(io.BytesIO(buf.getvalue())) as im:
        ours = st.letterbox(im)
    assert torch.equal(ours, _prepare_image(buf.getvalue())[0])


@pytest.mark.parametrize("fill, cls", [(0, 0), (20, 0), (30, 1), (64, 1), (66, 2), (89, 2), (91, 3), (100, 3)])
def test_nearest_class(fill, cls):
    assert st.nearest_class(fill) == cls


def _rows(session, n, label=50.0):
    return [{"storage_path": f"{session}/{i}", "session": session, "label_fill_percent": label} for i in range(n)]


def test_split_refuses_training_on_test_sessions():
    with pytest.raises(SystemExit, match="held out"):
        st.split_rows(_rows("t", 2), ["t"], ["t"], set(), 1.0, 0)


def test_split_requires_labelled_test_frames():
    with pytest.raises(SystemExit, match="test session"):
        st.split_rows(_rows("a", 2), ["a"], ["t"], set(), 1.0, 0)


def test_split_replays_earlier_sessions_in_proportion():
    rows = _rows("new", 10) + _rows("old", 40) + _rows("test", 5) + _rows("unseen", 7)
    train, test, n_replay = st.split_rows(rows, ["new"], ["test"], {"old"}, 0.5, 0)
    assert n_replay == 5
    assert len(train) == 15 and len(test) == 5
    assert {r["session"] for r in train} == {"new", "old"}  # never "unseen" or "test"


def test_split_ignores_unlabelled_frames():
    rows = _rows("a", 3) + [{"storage_path": "x", "session": "a", "label_fill_percent": None}] + _rows("t", 1)
    train, _, _ = st.split_rows(rows, ["a"], ["t"], set(), 0, 0)
    assert len(train) == 3


# --- full cycle -----------------------------------------------------------------


def _args(tmp_path, labels, **kw):
    base = {
        "dataset": str(tmp_path),
        "labels_csv": str(labels),
        "baseline": None,
        "epochs": 1,
        "lr": 1e-4,
        "batch_size": 4,
        "replay": 1.0,
        "seed": 0,
        "take_stale_lock": False,
        "version": None,
        "out": None,
        "deploy": False,
    }
    return Namespace(**{**base, **kw})


def test_init_train_download_cycle(store, fake, tmp_path, monkeypatch):
    monkeypatch.setenv("FLUIDWATCH_TRAINER", "alice")
    labels = make_dataset(
        tmp_path, {"run-01": [0, 50, 80, 100] * 2, "run-02": [20, 60, 90, 40] * 2, "run-test": [10, 55, 85, 95]}
    )
    device = torch.device("cpu")

    st.cmd_init(_args(tmp_path, labels, test_sessions="run-test"), None, store, device)
    registry = store.load_registry()
    assert registry["best"] == 1 and registry["test_sessions"] == ["run-test"]
    assert registry["versions"][0]["test_mae"] is not None
    assert "lock.json" not in str(list(fake.objects))

    with pytest.raises(SystemExit, match="Already initialised"):
        st.cmd_init(_args(tmp_path, labels, test_sessions="other"), None, store, device)

    st.cmd_train(_args(tmp_path, labels, sessions="run-01"), None, store, device)
    monkeypatch.setenv("FLUIDWATCH_TRAINER", "bob")
    st.cmd_train(_args(tmp_path, labels, sessions="run-02"), None, store, device)

    registry = store.load_registry()
    v2, v3 = registry["versions"][1], registry["versions"][2]
    assert (v2["trained_by"], v2["sessions"], v2["parent"]) == ("alice", ["run-01"], 1)
    assert (v3["trained_by"], v3["sessions"]) == ("bob", ["run-02"])
    assert v3["replay_sessions"] == ["run-01"] and v3["n_replay"] == 8
    # Each round starts from whatever was best when it began.
    assert v3["parent"] == (2 if v2["promoted"] else 1)
    best = max((v for v in registry["versions"] if v["promoted"]), key=lambda v: v["version"])
    assert registry["best"] == best["version"]
    assert f"models/v{3:04d}/model.pth" in fake.objects
    assert not any(k.endswith("lock.json") for k in fake.objects)

    out = tmp_path / "best.pth"
    st.cmd_download(_args(tmp_path, labels, out=str(out)), None, store)
    model = st.model_from_bytes(out.read_bytes(), device)
    assert isinstance(model, st.MultiTaskFillModel)


def test_lock_released_when_training_fails(store, fake, tmp_path):
    labels = make_dataset(tmp_path, {"run-01": [0, 100], "run-test": [50]})
    device = torch.device("cpu")
    st.cmd_init(_args(tmp_path, labels, test_sessions="run-test"), None, store, device)
    with pytest.raises(SystemExit, match="No labelled frames"):
        st.cmd_train(_args(tmp_path, labels, sessions="does-not-exist"), None, store, device)
    assert store.get_json("lock.json") is None


def test_train_refused_while_someone_else_holds_the_lock(store, tmp_path):
    labels = make_dataset(tmp_path, {"run-01": [0, 100], "run-test": [50]})
    st.cmd_init(_args(tmp_path, labels, test_sessions="run-test"), None, store, torch.device("cpu"))
    store.acquire_lock("carol")
    with pytest.raises(SystemExit, match="carol is training"):
        st.cmd_train(_args(tmp_path, labels, sessions="run-01"), None, store, torch.device("cpu"))
    assert store.get_json("lock.json")["holder"] == "carol"  # their lock is untouched
