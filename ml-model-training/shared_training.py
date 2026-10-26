"""Shared-model training: the whole team trains ONE fill-level model, in turns,
one capture session at a time.

The model lives in the private Supabase Storage bucket `models`:

    registry.json       version history, the fixed test sessions, which version is best
    lock.json           present while someone is training - everyone else waits
    v0001/model.pth     baseline: the model currently deployed on the backend
    v0002/model.pth     ...each training round adds the next version

One round (`train`):
  1. reserve the model (lock.json) - if a teammate is mid-round, you're told who/until when
  2. start from the BEST version
  3. train on your session(s) plus a replay sample of every session trained before,
     so the model doesn't forget earlier sessions
  4. score the new version and the best one on the same fixed test sessions
  5. upload it as the next version; it becomes best only if it scored better
     (a worse round stays in the history, but the next person continues from best)
  6. release the lock - even if training crashed

    python ml-model-training/shared_training.py status
    python ml-model-training/shared_training.py init --test-sessions run-07,run-08
    python ml-model-training/shared_training.py train --sessions run-04
    python ml-model-training/shared_training.py download            # best -> shared_model_vNNNN.pth
    python ml-model-training/shared_training.py download --deploy   # best -> the backend's model file
    python ml-model-training/shared_training.py unlock              # only after a crashed run

Frames and labels come from Supabase (`training_frames`, filled by the backend;
labels via `capture_settings` - see the README's "Collecting training data").
Credentials: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY from the environment or
server/.env - Supabase project members can copy the secret key from Project
Settings -> API Keys. Requirements: the backend's (`pip install -r
server/requirements.txt`).
"""

import argparse
import csv
import getpass
import io
import json
import math
import os
import random
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import torch
import torch.nn as nn
from PIL import Image
from torch.utils.data import DataLoader, Dataset
from torchvision import transforms

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.cv.real_frame_processor import (  # noqa: E402 - same architecture/preprocessing as production
    _IMAGE_SIZE,
    _MODEL_PATH,
    _TRANSFORM,
    MultiTaskFillModel,
)
from fetch_dataset import fetch_rows, sync_images  # noqa: E402

# Matches the model's 4 auxiliary classes (empty / 50% / 80% / full).
CLASS_LEVELS = [0.0, 50.0, 80.0, 100.0]
# Same recipe as train_continuous_v2.py.
REGRESSION_WEIGHT, CLASSIFICATION_WEIGHT = 0.75, 0.25
LOCK_HOURS = 6


# --- Supabase Storage: model versions, registry, lock ------------------------


class LockHeld(Exception):
    def __init__(self, lock: dict | None):
        self.lock = lock or {}
        super().__init__(f"{self.lock.get('holder', 'someone')} is training (until {self.lock.get('expires_at', '?')})")


def _is_conflict(res: httpx.Response) -> bool:
    text = res.text.lower()
    return res.status_code == 409 or (res.status_code == 400 and ("duplicate" in text or "already exists" in text))


def _is_missing(res: httpx.Response) -> bool:
    return res.status_code == 404 or (res.status_code == 400 and "not found" in res.text.lower())


class ModelStore:
    def __init__(self, client: httpx.Client, key: str, bucket: str = "models", prefix: str = ""):
        self.client, self.bucket, self.prefix = client, bucket, prefix
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    def _path(self, path: str) -> str:
        return f"{self.bucket}/{self.prefix}{path}"

    def ensure_bucket(self) -> None:
        res = self.client.post(
            "/storage/v1/bucket",
            headers=self.headers,
            json={"id": self.bucket, "name": self.bucket, "public": False, "file_size_limit": 52_428_800},
        )
        if res.is_error and not _is_conflict(res):
            res.raise_for_status()

    def put(self, path: str, data: bytes, content_type: str, upsert: bool = False) -> bool:
        """Uploads; with upsert=False an existing object is left alone and
        False is returned - that create-if-absent is what makes the lock work."""
        res = self.client.post(
            f"/storage/v1/object/{self._path(path)}",
            content=data,
            headers={**self.headers, "Content-Type": content_type, "x-upsert": "true" if upsert else "false"},
        )
        if not upsert and _is_conflict(res):
            return False
        res.raise_for_status()
        return True

    def get(self, path: str) -> bytes | None:
        res = self.client.get(f"/storage/v1/object/authenticated/{self._path(path)}", headers=self.headers)
        if _is_missing(res):
            return None
        res.raise_for_status()
        return res.content

    def delete(self, path: str) -> None:
        res = self.client.delete(f"/storage/v1/object/{self._path(path)}", headers=self.headers)
        if res.is_error and not _is_missing(res):
            res.raise_for_status()

    def get_json(self, path: str) -> dict | None:
        data = self.get(path)
        return json.loads(data) if data is not None else None

    def acquire_lock(self, holder: str, hours: float = LOCK_HOURS, take_stale: bool = False) -> dict:
        now = datetime.now(UTC)
        lock = {
            "holder": holder,
            "host": socket.gethostname(),
            "acquired_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=hours)).isoformat(),
        }
        body = json.dumps(lock).encode()
        if self.put("lock.json", body, "application/json"):
            return lock
        current = self.get_json("lock.json")
        if current is None and self.put("lock.json", body, "application/json"):
            return lock  # released between our two calls
        stale = current is not None and datetime.fromisoformat(current["expires_at"]) < now
        if stale and take_stale:
            self.delete("lock.json")
            if self.put("lock.json", body, "application/json"):
                return lock
        raise LockHeld(current)

    def release_lock(self) -> None:
        self.delete("lock.json")

    def load_registry(self) -> dict | None:
        return self.get_json("registry.json")

    def save_registry(self, registry: dict) -> None:
        self.put("registry.json", json.dumps(registry, indent=2).encode(), "application/json", upsert=True)


def version_path(version: int) -> str:
    return f"v{version:04d}/model.pth"


# --- Data ---------------------------------------------------------------------


def nearest_class(fill_percent: float) -> int:
    return min(range(len(CLASS_LEVELS)), key=lambda i: abs(CLASS_LEVELS[i] - fill_percent))


def letterbox(im: Image.Image) -> torch.Tensor:
    """Identical to production's _prepare_image() (minus the batch dim):
    aspect-preserving thumbnail, black-padded square, ImageNet-normalized."""
    im = im.convert("RGB")
    im.thumbnail((_IMAGE_SIZE, _IMAGE_SIZE), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (_IMAGE_SIZE, _IMAGE_SIZE), (0, 0, 0))
    canvas.paste(im, ((_IMAGE_SIZE - im.width) // 2, (_IMAGE_SIZE - im.height) // 2))
    return _TRANSFORM(canvas)


class FrameDataset(Dataset):
    # Gentle, level-preserving augmentation - same as train_continuous_v2.py.
    AUGMENT = transforms.Compose(
        [
            transforms.RandomApply(
                [transforms.ColorJitter(brightness=0.12, contrast=0.12, saturation=0.08, hue=0.015)], p=0.7
            ),
            transforms.RandomAffine(degrees=3, translate=(0.015, 0.015), scale=(0.98, 1.02)),
        ]
    )

    def __init__(self, rows: list[dict], images_dir: Path, train: bool):
        self.rows, self.images_dir, self.train = rows, images_dir, train

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, i: int):
        row = self.rows[i]
        with Image.open(self.images_dir / row["storage_path"]) as im:
            im = im.convert("RGB")
        if self.train:
            im = self.AUGMENT(im)
        fill = float(row["label_fill_percent"])
        return letterbox(im), torch.tensor(fill, dtype=torch.float32), torch.tensor(nearest_class(fill))


def split_rows(
    rows: list[dict],
    train_sessions: list[str],
    test_sessions: list[str],
    replay_sessions: set[str],
    replay_ratio: float,
    seed: int,
) -> tuple[list[dict], list[dict], int]:
    """(train rows, test rows, how many of the train rows are replay)."""
    overlap = set(train_sessions) & set(test_sessions)
    if overlap:
        raise SystemExit(f"Can't train on test session(s) {sorted(overlap)} - they're held out for scoring.")
    labelled = [r for r in rows if r.get("label_fill_percent") is not None]
    test = [r for r in labelled if r.get("session") in test_sessions]
    new = [r for r in labelled if r.get("session") in train_sessions]
    old = [
        r
        for r in labelled
        if r.get("session") in replay_sessions and r.get("session") not in train_sessions + test_sessions
    ]
    if not new:
        raise SystemExit(f"No labelled frames for session(s) {train_sessions}.")
    if not test:
        raise SystemExit(f"No labelled frames in the test session(s) {test_sessions} - collect those first.")
    replay = random.Random(seed).sample(old, min(len(old), round(len(new) * replay_ratio)))
    return new + replay, test, len(replay)


# --- Model --------------------------------------------------------------------


def model_from_bytes(data: bytes, device: torch.device, trusted_legacy: bool = False) -> MultiTaskFillModel:
    # Versions this script saves hold only tensors + plain metadata, so they
    # load with weights_only=True. Only the repo's own baseline file needs the
    # legacy unpickler.
    checkpoint = torch.load(io.BytesIO(data), map_location=device, weights_only=not trusted_legacy)
    state = checkpoint.get("model_state", checkpoint) if isinstance(checkpoint, dict) else checkpoint
    model = MultiTaskFillModel()
    model.load_state_dict(state)
    return model.to(device)


def model_to_bytes(model: nn.Module, meta: dict) -> bytes:
    buf = io.BytesIO()
    torch.save({"model_state": model.state_dict(), "meta": meta}, buf)
    return buf.getvalue()


def train_model(model, rows, images_dir, epochs: int, lr: float, batch_size: int, device, seed: int) -> None:
    torch.manual_seed(seed)
    loader = DataLoader(
        FrameDataset(rows, images_dir, train=True),
        batch_size=batch_size,
        shuffle=True,
        drop_last=len(rows) > batch_size,
    )
    regression_loss = nn.SmoothL1Loss(beta=4.0)
    classification_loss = nn.CrossEntropyLoss(label_smoothing=0.03)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    model.train()
    for epoch in range(1, epochs + 1):
        total = seen = 0
        for x, fill, cls in loader:
            x, fill, cls = x.to(device), fill.to(device), cls.to(device)
            pred_fill, logits = model(x)
            loss = REGRESSION_WEIGHT * regression_loss(pred_fill, fill) + CLASSIFICATION_WEIGHT * classification_loss(
                logits, cls
            )
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total += loss.item() * len(x)
            seen += len(x)
        print(f"  epoch {epoch}/{epochs}  loss {total / max(seen, 1):.3f}")


@torch.no_grad()
def evaluate(model, rows, images_dir, device) -> dict:
    model.eval()
    errors: list[float] = []
    for x, fill, _ in DataLoader(FrameDataset(rows, images_dir, train=False), batch_size=64):
        pred_fill, _ = model(x.to(device))
        errors.extend((pred_fill.cpu() - fill).tolist())
    return {
        "mae": sum(abs(e) for e in errors) / len(errors),
        "rmse": math.sqrt(sum(e * e for e in errors) / len(errors)),
        "n": len(errors),
    }


# --- Commands -----------------------------------------------------------------


def who_am_i() -> str:
    if os.environ.get("FLUIDWATCH_TRAINER"):
        return os.environ["FLUIDWATCH_TRAINER"]
    try:
        name = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    except OSError:
        name = ""
    return name or getpass.getuser()


def credentials() -> tuple[str, str]:
    env: dict[str, str] = {}
    server_env = ROOT / "server" / ".env"
    if server_env.exists():
        for line in server_env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip("'\"")
    url = os.environ.get("SUPABASE_URL") or env.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or env.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (environment or server/.env).")
    return url.rstrip("/"), key


def load_rows(args, client: httpx.Client, store: ModelStore) -> list[dict]:
    images = Path(args.dataset) / "images"
    if args.labels_csv:
        with open(args.labels_csv, newline="", encoding="utf-8") as f:
            rows = [
                {**r, "label_fill_percent": float(r["label_fill_percent"]) if r.get("label_fill_percent") else None}
                for r in csv.DictReader(f)
            ]
        return rows
    rows = fetch_rows(client, store.headers, session=None, labelled_only=True)
    print(f"Syncing {len(rows)} labelled frames to {images} ...")
    sync_images(client, store.headers, rows, images)
    return rows


def print_status(registry: dict | None, lock: dict | None) -> None:
    if lock:
        print(f"LOCKED: {lock['holder']} ({lock['host']}) since {lock['acquired_at']}, expires {lock['expires_at']}")
    else:
        print("Not locked - free to train.")
    if not registry:
        print("No shared model yet - run `init --test-sessions ...` first.")
        return
    print(f"Test sessions: {', '.join(registry['test_sessions'])}   Best: v{registry['best']:04d}\n")
    header = f"{'ver':>5} {'from':>5}  {'trained by':<22} {'sessions':<22} {'train':>6} {'test MAE':>9} {'base MAE':>9}"
    print(f"{header}  date")
    for v in registry["versions"]:
        mae = f"{v['test_mae']:.2f}" if v.get("test_mae") is not None else "-"
        base = f"{v['base_test_mae']:.2f}" if v.get("base_test_mae") is not None else "-"
        mark = " *best" if v["version"] == registry["best"] else ("" if v.get("promoted", True) else " (worse)")
        parent = f"v{v['parent']:04d}" if v.get("parent") else "-"
        print(
            f"v{v['version']:04d} {parent:>5}  {v['trained_by'][:22]:<22} {','.join(v['sessions'])[:22]:<22} "
            f"{v.get('n_train') or '-':>6} {mae:>9} {base:>9}  {v['created_at'][:16]}{mark}"
        )


def cmd_status(args, client, store) -> None:
    print_status(store.load_registry(), store.get_json("lock.json"))


def cmd_unlock(args, client, store) -> None:
    lock = store.get_json("lock.json")
    if not lock:
        print("Not locked.")
        return
    store.release_lock()
    print(f"Released the lock held by {lock['holder']} since {lock['acquired_at']}.")


def cmd_init(args, client, store, device) -> None:
    store.ensure_bucket()
    store.acquire_lock(who_am_i())
    try:
        if store.load_registry():
            raise SystemExit("Already initialised - see `status`. Test sessions are fixed so scores stay comparable.")
        test_sessions = [s.strip() for s in args.test_sessions.split(",") if s.strip()]
        source = Path(args.baseline) if args.baseline else _MODEL_PATH
        label = f"baseline ({source.name})" if args.baseline else "baseline (deployed model)"
        model = model_from_bytes(source.read_bytes(), device, trusted_legacy=True)
        meta = {"version": 1, "trained_by": label, "sessions": []}
        store.put(version_path(1), model_to_bytes(model, meta), "application/octet-stream")

        test_mae = None
        rows = [r for r in load_rows(args, client, store) if r.get("session") in test_sessions]
        if rows:
            test_mae = evaluate(model, rows, Path(args.dataset) / "images", device)["mae"]
        store.save_registry(
            {
                "test_sessions": test_sessions,
                "best": 1,
                "versions": [
                    {
                        "version": 1,
                        "parent": None,
                        "trained_by": label,
                        "sessions": [],
                        "n_train": None,
                        "test_mae": test_mae,
                        "base_test_mae": None,
                        "promoted": True,
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                ],
            }
        )
        print(f"Shared model initialised: v0001 = {label}. Test sessions: {test_sessions}")
    finally:
        store.release_lock()


def cmd_train(args, client, store, device) -> None:
    holder = who_am_i()
    try:
        store.acquire_lock(holder, take_stale=args.take_stale_lock)
    except LockHeld as held:
        raise SystemExit(f"Not now: {held}. Try again after their round (see `status`).") from None
    try:
        registry = store.load_registry()  # re-read under the lock
        if not registry:
            raise SystemExit("No shared model yet - run `init --test-sessions ...` first.")
        sessions = [s.strip() for s in args.sessions.split(",") if s.strip()]
        trained_before = {s for v in registry["versions"] for s in v["sessions"]}
        rows = load_rows(args, client, store)
        images = Path(args.dataset) / "images"
        train_rows, test_rows, n_replay = split_rows(
            rows, sessions, registry["test_sessions"], trained_before, args.replay, args.seed
        )

        best = registry["best"]
        base = model_from_bytes(store.get(version_path(best)), device)
        base_score = evaluate(base, test_rows, images, device)
        print(f"Base v{best:04d}: test MAE {base_score['mae']:.2f} on {base_score['n']} frames")
        print(f"Training on {len(train_rows) - n_replay} new + {n_replay} replay frames, {args.epochs} epochs ...")
        train_model(base, train_rows, images, args.epochs, args.lr, args.batch_size, device, args.seed)
        score = evaluate(base, test_rows, images, device)

        version = max(v["version"] for v in registry["versions"]) + 1
        promoted = score["mae"] < base_score["mae"]
        entry = {
            "version": version,
            "parent": best,
            "trained_by": holder,
            "sessions": sessions,
            "replay_sessions": sorted(trained_before - set(sessions)),
            "n_train": len(train_rows),
            "n_replay": n_replay,
            "epochs": args.epochs,
            "lr": args.lr,
            "test_mae": score["mae"],
            "test_rmse": score["rmse"],
            "base_test_mae": base_score["mae"],
            "promoted": promoted,
            "created_at": datetime.now(UTC).isoformat(),
        }
        store.put(version_path(version), model_to_bytes(base, entry), "application/octet-stream")
        registry["versions"].append(entry)
        if promoted:
            registry["best"] = version
        store.save_registry(registry)
        verdict = "NEW BEST" if promoted else f"not better - next round continues from v{best:04d}"
        print(f"v{version:04d}: test MAE {score['mae']:.2f} (base {base_score['mae']:.2f}) -> {verdict}")
    finally:
        store.release_lock()


def cmd_download(args, client, store) -> None:
    registry = store.load_registry()
    if not registry:
        raise SystemExit("No shared model yet.")
    version = args.version or registry["best"]
    data = store.get(version_path(version))
    if data is None:
        raise SystemExit(f"v{version:04d} not found.")
    default_out = ROOT / "ml-model-training" / f"shared_model_v{version:04d}.pth"
    out = _MODEL_PATH if args.deploy else Path(args.out or default_out)
    out.write_bytes(data)
    print(f"v{version:04d} -> {out}")
    if args.deploy:
        print("Deployed file replaced - commit it on the model-training branch and open a PR to main to ship it.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bucket", default="models")
    parser.add_argument("--prefix", default="", help="Object prefix inside the bucket (testing only)")
    parser.add_argument("--dataset", default=str(ROOT / "ml-model-training" / "dataset"))
    parser.add_argument("--labels-csv", help="Use a local labels.csv instead of syncing from Supabase")
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Versions, best, and who (if anyone) is training")
    p = sub.add_parser("init", help="Create the shared model from the deployed one")
    p.add_argument("--test-sessions", required=True, help="Comma-separated sessions held out for scoring")
    p.add_argument("--baseline", help="Start from this .pth instead of the deployed model")
    p = sub.add_parser("train", help="Train the best version on your session(s)")
    p.add_argument("--sessions", required=True, help="Comma-separated capture sessions to train on")
    p.add_argument("--epochs", type=int, default=5)
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--batch-size", type=int, default=24)
    p.add_argument("--replay", type=float, default=1.0, help="Replay frames per new frame from earlier sessions")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--take-stale-lock", action="store_true", help="Take over a lock past its expiry")
    p = sub.add_parser("download", help="Download a version (default: best)")
    p.add_argument("--version", type=int)
    p.add_argument("--out")
    p.add_argument("--deploy", action="store_true", help="Overwrite the backend's deployed model file")
    sub.add_parser("unlock", help="Release a lock left by a crashed run")
    args = parser.parse_args()

    url, key = credentials()
    device = torch.device(args.device)
    with httpx.Client(base_url=url, timeout=120.0) as client:
        store = ModelStore(client, key, args.bucket, args.prefix)
        if args.command == "status":
            cmd_status(args, client, store)
        elif args.command == "init":
            cmd_init(args, client, store, device)
        elif args.command == "train":
            cmd_train(args, client, store, device)
        elif args.command == "download":
            cmd_download(args, client, store)
        elif args.command == "unlock":
            cmd_unlock(args, client, store)


if __name__ == "__main__":
    main()
