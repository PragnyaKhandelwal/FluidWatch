
"""
Continuous fill-level V2
------------------------
Goal:
  Improve continuous 0-100% estimation while leaving the already-trained
  97.4% four-class classifier completely untouched.

Dataset:
  sal_data_empty -> 0
  sal_data_50    -> 50
  sal_data_80    -> 80
  sal_data_100   -> 100

Key improvements over the old regression model:
  1. 224x224 input instead of 160x160.
  2. Initializes the MobileNet backbone from the GOOD 4-class classifier
     (fill_classifier_mobilenet.pth) when available.
  3. Multi-task learning:
       - regression head learns the continuous percentage
       - classification head anchors the four known levels
  4. SmoothL1 regression + cross-entropy classification loss.
  5. Gentle image augmentation that does not change fill level.
  6. Stratified train/validation/test split.
  7. Saves a NEW model file; never overwrites the existing regression or
     classifier models.

Important:
  The dataset still contains only 0/50/80/100% labels. Intermediate values
  such as 63% are interpolation and must be validated on real intermediate
  fill images.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from torchvision import models, transforms

# ============================================================
# CONFIG
# ============================================================

ROOT = Path(__file__).resolve().parent

DATASET_DIRS = {
    "sal_data_empty": 0.0,
    "sal_data_50": 50.0,
    "sal_data_80": 80.0,
    "sal_data_100": 100.0,
}

CLASS_TO_INDEX = {
    0.0: 0,
    50.0: 1,
    80.0: 2,
    100.0: 3,
}

IMAGE_SIZE = 224
BATCH_SIZE = 24
NUM_WORKERS = 0

HEAD_EPOCHS = 5
FINE_TUNE_EPOCHS = 12

LR_HEAD = 1e-3
LR_FINE = 1e-5

VAL_RATIO = 0.10
TEST_RATIO = 0.10
SEED = 42
EARLY_STOP = 4

# Regression loss gets more weight because continuous estimation is the goal.
REGRESSION_WEIGHT = 0.75
CLASSIFICATION_WEIGHT = 0.25

CACHE_DIR = ROOT / "dataset_cache_cont_224"

# NEW files: existing good models are never overwritten.
MODEL_FILE = ROOT / "fill_regression_v2_mobilenet.pth"
INFO_FILE = ROOT / "fill_regression_v2_info.json"

# Existing 97.4% classifier used ONLY as backbone initialization.
CLASSIFIER_FILE = ROOT / "fill_classifier_mobilenet.pth"

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


# ============================================================
# UTILITIES
# ============================================================

def seed_everything(seed=SEED):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def list_images(folder: Path):
    exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    return sorted(
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in exts
    )


def build_manifest():
    by_label = {}

    for folder_name, fill in DATASET_DIRS.items():
        folder = ROOT / folder_name

        if not folder.exists():
            raise FileNotFoundError(f"Dataset folder not found: {folder}")

        files = list_images(folder)
        print(f"{folder_name:18s}: {len(files)} images")

        by_label[fill] = [(p, fill) for p in files]

    train, val, test = [], [], []
    rng = random.Random(SEED)

    for fill, items in by_label.items():
        rng.shuffle(items)

        n = len(items)
        n_test = max(1, int(round(n * TEST_RATIO)))
        n_val = max(1, int(round(n * VAL_RATIO)))

        test.extend(items[:n_test])
        val.extend(items[n_test:n_test + n_val])
        train.extend(items[n_test + n_val:])

    rng.shuffle(train)
    rng.shuffle(val)
    rng.shuffle(test)

    print()
    print(f"Total: {len(train) + len(val) + len(test)}")
    print(f"Train: {len(train)}")
    print(f"Val:   {len(val)}")
    print(f"Test:  {len(test)}")

    return train, val, test


def cache_image(src: Path):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    key = hashlib.md5(
        str(src.resolve()).encode("utf-8")
    ).hexdigest()

    dst = CACHE_DIR / f"{key}.jpg"

    if not dst.exists():
        try:
            with Image.open(src) as im:
                im = im.convert("RGB")

                # All source images are square in this dataset, but use
                # thumbnail to avoid stretching if that changes later.
                im.thumbnail(
                    (IMAGE_SIZE, IMAGE_SIZE),
                    Image.Resampling.LANCZOS
                )

                canvas = Image.new(
                    "RGB",
                    (IMAGE_SIZE, IMAGE_SIZE),
                    (0, 0, 0)
                )

                x = (IMAGE_SIZE - im.width) // 2
                y = (IMAGE_SIZE - im.height) // 2

                canvas.paste(im, (x, y))
                canvas.save(
                    dst,
                    "JPEG",
                    quality=90,
                    optimize=True
                )

        except Exception as e:
            print(f"\n[WARN] Could not cache {src}: {e}")
            return None

    return dst


def make_cached_manifest(items):
    out = []

    for i, (src, fill) in enumerate(items, 1):
        dst = cache_image(src)

        if dst is not None:
            out.append((dst, fill))

        if i % 200 == 0 or i == len(items):
            print(
                f"\r  cached {i}/{len(items)}",
                end="",
                flush=True
            )

    print()
    return out


# ============================================================
# DATASET
# ============================================================

class FillDataset(Dataset):

    def __init__(self, items, train=False):

        self.items = items

        if train:
            self.transform = transforms.Compose([
                transforms.RandomApply(
                    [
                        transforms.ColorJitter(
                            brightness=0.12,
                            contrast=0.12,
                            saturation=0.08,
                            hue=0.015,
                        )
                    ],
                    p=0.7,
                ),

                transforms.RandomAffine(
                    degrees=3,
                    translate=(0.015, 0.015),
                    scale=(0.98, 1.02),
                ),

                transforms.ToTensor(),

                transforms.Normalize(
                    [0.485, 0.456, 0.406],
                    [0.229, 0.224, 0.225],
                ),
            ])

        else:
            self.transform = transforms.Compose([
                transforms.ToTensor(),

                transforms.Normalize(
                    [0.485, 0.456, 0.406],
                    [0.229, 0.224, 0.225],
                ),
            ])

    def __len__(self):
        return len(self.items)

    def __getitem__(self, idx):

        path, fill = self.items[idx]

        with Image.open(path) as im:
            im = im.convert("RGB")

        image = self.transform(im)

        class_index = CLASS_TO_INDEX[fill]

        return (
            image,
            torch.tensor(fill, dtype=torch.float32),
            torch.tensor(class_index, dtype=torch.long),
        )


# ============================================================
# MODEL
# ============================================================

class MultiTaskFillModel(nn.Module):

    def __init__(self):

        super().__init__()

        weights = models.MobileNet_V3_Small_Weights.DEFAULT

        base = models.mobilenet_v3_small(weights=weights)

        self.backbone = base.features
        self.pool = nn.AdaptiveAvgPool2d(1)

        # MobileNetV3-Small features output 576 channels.
        self.shared = nn.Sequential(
            nn.Flatten(),
            nn.Linear(576, 160),
            nn.Hardswish(),
            nn.Dropout(0.15),
        )

        # Continuous 0-100 prediction.
        self.regression_head = nn.Sequential(
            nn.Linear(160, 64),
            nn.Hardswish(),
            nn.Dropout(0.10),
            nn.Linear(64, 1),
        )

        # Four known anchors: empty / 50 / 80 / full.
        self.classification_head = nn.Linear(160, 4)

    def forward(self, x):

        x = self.backbone(x)
        x = self.pool(x)
        x = self.shared(x)

        regression_raw = self.regression_head(x)
        classification_logits = self.classification_head(x)

        # Physical range.
        fill_percent = torch.sigmoid(regression_raw).squeeze(1) * 100.0

        return fill_percent, classification_logits


def load_good_classifier_backbone(model):

    if not CLASSIFIER_FILE.exists():

        print(
            "\n[INFO] Existing classifier model not found."
        )
        print(
            "Using ImageNet pretrained MobileNet instead."
        )

        return

    print(
        "\nLoading backbone from existing "
        "97.4% classifier..."
    )

    try:

        checkpoint = torch.load(
            CLASSIFIER_FILE,
            map_location="cpu",
            weights_only=False,
        )

        # train_classifier.py saves a plain state_dict.
        if isinstance(checkpoint, dict) and "state_dict" in checkpoint:
            state = checkpoint["state_dict"]
        else:
            state = checkpoint

        # The classifier architecture has:
        # features.* -> backbone
        backbone_state = {}

        for key, value in state.items():

            if key.startswith("features."):
                new_key = key[len("features."):]
                backbone_state[new_key] = value

        if backbone_state:

            missing, unexpected = model.backbone.load_state_dict(
                backbone_state,
                strict=False
            )

            print(
                f"  Loaded classifier backbone. "
                f"missing={len(missing)}, "
                f"unexpected={len(unexpected)}"
            )

        else:
            print(
                "[WARN] Could not find classifier backbone weights."
            )

    except Exception as e:

        print(
            f"[WARN] Could not load classifier backbone: {e}"
        )

        print(
            "Continuing with ImageNet pretrained weights."
        )


def freeze_backbone(model):

    for p in model.backbone.parameters():
        p.requires_grad = False


def unfreeze_last_blocks(model, n_blocks=4):

    freeze_backbone(model)

    blocks = list(model.backbone.children())

    for block in blocks[-n_blocks:]:

        for p in block.parameters():
            p.requires_grad = True


# ============================================================
# TRAINING
# ============================================================

def make_loaders(train, val, test):

    train_ds = FillDataset(train, train=True)
    val_ds = FillDataset(val, train=False)
    test_ds = FillDataset(test, train=False)

    common = {
        "num_workers": NUM_WORKERS,
        "pin_memory": DEVICE.type == "cuda",
    }

    return (
        DataLoader(
            train_ds,
            batch_size=BATCH_SIZE,
            shuffle=True,
            **common,
        ),

        DataLoader(
            val_ds,
            batch_size=BATCH_SIZE,
            shuffle=False,
            **common,
        ),

        DataLoader(
            test_ds,
            batch_size=BATCH_SIZE,
            shuffle=False,
            **common,
        ),
    )


def compute_loss(
    pred_fill,
    pred_class,
    true_fill,
    true_class,
    regression_loss,
    classification_loss,
):

    reg = regression_loss(
        pred_fill,
        true_fill
    )

    cls = classification_loss(
        pred_class,
        true_class
    )

    total = (
        REGRESSION_WEIGHT * reg
        + CLASSIFICATION_WEIGHT * cls
    )

    return total, reg, cls


def train_epoch(
    model,
    loader,
    optimizer,
    regression_loss,
    classification_loss,
):

    model.train()

    total_loss = 0.0
    total_reg = 0.0
    total_cls = 0.0
    total = 0

    for batch_idx, (
        images,
        true_fill,
        true_class
    ) in enumerate(loader, 1):

        images = images.to(DEVICE)
        true_fill = true_fill.to(DEVICE)
        true_class = true_class.to(DEVICE)

        optimizer.zero_grad(set_to_none=True)

        pred_fill, pred_class = model(images)

        loss, reg, cls = compute_loss(
            pred_fill,
            pred_class,
            true_fill,
            true_class,
            regression_loss,
            classification_loss,
        )

        loss.backward()

        # Helps stabilize CPU fine-tuning.
        torch.nn.utils.clip_grad_norm_(
            model.parameters(),
            max_norm=3.0
        )

        optimizer.step()

        count = len(images)

        total_loss += loss.item() * count
        total_reg += reg.item() * count
        total_cls += cls.item() * count
        total += count

        if batch_idx % 10 == 0 or batch_idx == len(loader):

            print(
                f"\r  batch {batch_idx:3d}/{len(loader)} "
                f"loss={loss.item():.3f}",
                end="",
                flush=True
            )

    print()

    return (
        total_loss / total,
        total_reg / total,
        total_cls / total,
    )


@torch.no_grad()
def evaluate(
    model,
    loader,
    regression_loss,
    classification_loss,
):

    model.eval()

    total = 0

    total_loss = 0.0
    total_reg = 0.0
    total_cls = 0.0

    total_abs = 0.0
    total_sq = 0.0

    correct_class = 0

    per_class_abs = [0.0] * 4
    per_class_count = [0] * 4

    for images, true_fill, true_class in loader:

        images = images.to(DEVICE)
        true_fill = true_fill.to(DEVICE)
        true_class = true_class.to(DEVICE)

        pred_fill, pred_class = model(images)

        loss, reg, cls = compute_loss(
            pred_fill,
            pred_class,
            true_fill,
            true_class,
            regression_loss,
            classification_loss,
        )

        count = len(images)

        total_loss += loss.item() * count
        total_reg += reg.item() * count
        total_cls += cls.item() * count

        diff = pred_fill - true_fill

        total_abs += diff.abs().sum().item()
        total_sq += (diff ** 2).sum().item()

        predicted_class = pred_class.argmax(dim=1)

        correct_class += (
            predicted_class == true_class
        ).sum().item()

        for c in range(4):

            mask = true_class == c

            if mask.any():

                per_class_abs[c] += (
                    diff[mask].abs().sum().item()
                )

                per_class_count[c] += mask.sum().item()

        total += count

    mae = total_abs / total
    rmse = (total_sq / total) ** 0.5

    class_accuracy = (
        100.0 * correct_class / total
    )

    per_class_mae = []

    for c in range(4):

        if per_class_count[c]:

            per_class_mae.append(
                per_class_abs[c]
                / per_class_count[c]
            )

        else:

            per_class_mae.append(None)

    return {
        "loss": total_loss / total,
        "reg_loss": total_reg / total,
        "cls_loss": total_cls / total,
        "mae": mae,
        "rmse": rmse,
        "class_accuracy": class_accuracy,
        "per_class_mae": per_class_mae,
    }


def save_checkpoint(model, metrics):

    torch.save(
        {
            "model_state": model.state_dict(),
            "image_size": IMAGE_SIZE,
            "metrics": metrics,
            "classes": [
                0.0,
                50.0,
                80.0,
                100.0,
            ],
        },
        MODEL_FILE,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    seed_everything()

    print("=" * 68)
    print("IV BOTTLE CONTINUOUS FILL MODEL V2")
    print("=" * 68)

    print(f"Device: {DEVICE}")

    if DEVICE.type == "cpu":

        print(
            "CPU mode: 224x224 cached images."
        )

        print(
            "This is slower than the old 160x160 model, "
            "but should provide better visual detail."
        )

    print()

    train_raw, val_raw, test_raw = build_manifest()

    print("\nPreparing 224x224 cache...")

    train = make_cached_manifest(train_raw)
    val = make_cached_manifest(val_raw)
    test = make_cached_manifest(test_raw)

    train_loader, val_loader, test_loader = make_loaders(
        train,
        val,
        test
    )

    print("\nBuilding model...")

    model = MultiTaskFillModel()

    # Important: use the already-trained 97.4% classifier
    # as feature initialization.
    load_good_classifier_backbone(model)

    model = model.to(DEVICE)

    regression_loss = nn.SmoothL1Loss(
        beta=4.0
    )

    classification_loss = nn.CrossEntropyLoss(
        label_smoothing=0.03
    )

    # --------------------------------------------------------
    # PHASE 1
    # --------------------------------------------------------

    print("\n" + "=" * 68)
    print("PHASE 1: TRAINING NEW HEADS")
    print("=" * 68)

    freeze_backbone(model)

    optimizer = torch.optim.AdamW(
        filter(
            lambda p: p.requires_grad,
            model.parameters()
        ),
        lr=LR_HEAD,
        weight_decay=1e-4,
    )

    best_mae = float("inf")
    patience = 0

    for epoch in range(1, HEAD_EPOCHS + 1):

        start = time.time()

        tr_loss, tr_reg, tr_cls = train_epoch(
            model,
            train_loader,
            optimizer,
            regression_loss,
            classification_loss,
        )

        val = evaluate(
            model,
            val_loader,
            regression_loss,
            classification_loss,
        )

        print(
            f"Epoch {epoch:02d}/{HEAD_EPOCHS} | "
            f"train={tr_loss:.3f} "
            f"(reg={tr_reg:.3f}, cls={tr_cls:.3f}) | "
            f"val_MAE={val['mae']:.2f}% | "
            f"val_RMSE={val['rmse']:.2f}% | "
            f"val_cls={val['class_accuracy']:.2f}% | "
            f"time={time.time()-start:.1f}s"
        )

        if val["mae"] < best_mae:

            best_mae = val["mae"]
            patience = 0

            save_checkpoint(
                model,
                val,
            )

            print(
                "  -> saved best V2 model"
            )

        else:

            patience += 1

            if patience >= EARLY_STOP:

                print(
                    "  Early stopping Phase 1."
                )

                break

    # --------------------------------------------------------
    # PHASE 2
    # --------------------------------------------------------

    print("\n" + "=" * 68)
    print("PHASE 2: FINE-TUNING LAST 4 BACKBONE BLOCKS")
    print("=" * 68)

    unfreeze_last_blocks(
        model,
        n_blocks=4
    )

    optimizer = torch.optim.AdamW(
        filter(
            lambda p: p.requires_grad,
            model.parameters()
        ),
        lr=LR_FINE,
        weight_decay=1e-4,
    )

    patience = 0

    for epoch in range(1, FINE_TUNE_EPOCHS + 1):

        start = time.time()

        tr_loss, tr_reg, tr_cls = train_epoch(
            model,
            train_loader,
            optimizer,
            regression_loss,
            classification_loss,
        )

        val = evaluate(
            model,
            val_loader,
            regression_loss,
            classification_loss,
        )

        print(
            f"Fine {epoch:02d}/{FINE_TUNE_EPOCHS} | "
            f"train={tr_loss:.3f} "
            f"(reg={tr_reg:.3f}, cls={tr_cls:.3f}) | "
            f"val_MAE={val['mae']:.2f}% | "
            f"val_RMSE={val['rmse']:.2f}% | "
            f"val_cls={val['class_accuracy']:.2f}% | "
            f"time={time.time()-start:.1f}s"
        )

        if val["mae"] < best_mae:

            best_mae = val["mae"]
            patience = 0

            save_checkpoint(
                model,
                val,
            )

            print(
                "  -> saved best V2 model"
            )

        else:

            patience += 1

            if patience >= EARLY_STOP:

                print(
                    "  Early stopping Phase 2."
                )

                break

    # --------------------------------------------------------
    # FINAL TEST
    # --------------------------------------------------------

    checkpoint = torch.load(
        MODEL_FILE,
        map_location=DEVICE,
        weights_only=False,
    )

    model.load_state_dict(
        checkpoint["model_state"]
    )

    test = evaluate(
        model,
        test_loader,
        regression_loss,
        classification_loss,
    )

    print("\n" + "=" * 68)
    print("FINAL TEST RESULTS — V2")
    print("=" * 68)

    print(
        f"Test MAE:                 {test['mae']:.2f}%"
    )

    print(
        f"Test RMSE:                {test['rmse']:.2f}%"
    )

    print(
        f"4-class auxiliary acc:    "
        f"{test['class_accuracy']:.2f}%"
    )

    print(
        f"Best validation MAE:      "
        f"{best_mae:.2f}%"
    )

    print("\nMAE by known fill level:")

    for fill, mae in zip(
        [0, 50, 80, 100],
        test["per_class_mae"]
    ):

        print(
            f"  {fill:3d}%: "
            f"{mae:.2f}%"
        )

    print("\nComparison with your old model:")

    print(
        "  OLD test MAE:  7.14%"
    )

    print(
        f"  NEW test MAE:  {test['mae']:.2f}%"
    )

    improvement = (
        (7.14 - test["mae"])
        / 7.14
        * 100
    )

    print(
        f"  MAE change:    {improvement:+.1f}%"
    )

    print(
        "\nSaved NEW model:"
    )

    print(
        f"  {MODEL_FILE}"
    )

    print(
        "\nThe existing files were NOT overwritten:"
    )

    print(
        "  fill_classifier_mobilenet.pth"
    )

    print(
        "  fill_regression_mobilenet.pth"
    )

    info = {
        "type": "continuous_fill_regression_v2_multitask",
        "dataset": {
            "sal_data_empty": 0,
            "sal_data_50": 50,
            "sal_data_80": 80,
            "sal_data_100": 100,
        },
        "image_size": IMAGE_SIZE,
        "model": "MobileNetV3-Small multitask",
        "initialized_from_classifier": CLASSIFIER_FILE.exists(),
        "test_mae_percent": test["mae"],
        "test_rmse_percent": test["rmse"],
        "auxiliary_class_accuracy_percent": test["class_accuracy"],
        "best_validation_mae_percent": best_mae,
        "per_class_mae_percent": {
            "0": test["per_class_mae"][0],
            "50": test["per_class_mae"][1],
            "80": test["per_class_mae"][2],
            "100": test["per_class_mae"][3],
        },
        "old_test_mae_percent": 7.14,
        "note": (
            "This model is separate from the existing 97.4% classifier. "
            "Training labels remain only 0, 50, 80 and 100 percent, so "
            "intermediate predictions require real intermediate-fill "
            "validation."
        ),
    }

    INFO_FILE.write_text(
        json.dumps(info, indent=2),
        encoding="utf-8"
    )

    print(
        f"  {INFO_FILE}"
    )

    print("\nDone.")


if __name__ == "__main__":
    main()

