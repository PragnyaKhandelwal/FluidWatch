
import os
import json
import random
import shutil
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, random_split
from torchvision import datasets, transforms, models
from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

# ============================================================
# Bottle Fill-Level CLASSIFICATION
# Priority: EMPTY vs FULL reliability
# Classes: 0%, 50%, 80%, 100%
# ============================================================

SEED = 42
random.seed(SEED)
torch.manual_seed(SEED)

ROOT = Path(".")
DATA_DIR = ROOT
CACHE_DIR = ROOT / "dataset_cache_cls_160"
MODEL_FILE = ROOT / "fill_classifier_mobilenet.pth"
INFO_FILE = ROOT / "fill_classifier_info.json"

IMAGE_SIZE = 160
BATCH_SIZE = 32
NUM_WORKERS = 0          # Windows-safe
EPOCHS_HEAD = 5
EPOCHS_FINE = 8
LR_HEAD = 1e-3
LR_FINE = 2e-5

CLASS_DIRS = [
    ("sal_data_empty", 0),
    ("sal_data_50", 50),
    ("sal_data_80", 80),
    ("sal_data_100", 100),
]

print("=" * 65)
print("Bottle Fill-Level Classification")
print("Priority: EMPTY / FULL recognition")
print("=" * 65)

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Device: {device}")
if device.type == "cpu":
    print("CPU training enabled. Images will be cached at 160x160.")

# ------------------------------------------------------------
# Make a clean temporary classification directory containing
# only the four intended classes.
# ------------------------------------------------------------
if not CACHE_DIR.exists():
    print("\nCreating 160x160 image cache...")
    for folder_name, fill in CLASS_DIRS:
        src = ROOT / folder_name
        dst = CACHE_DIR / folder_name
        dst.mkdir(parents=True, exist_ok=True)

        files = []
        if src.exists():
            for ext in ("*.jpg", "*.jpeg", "*.png", "*.JPG", "*.JPEG", "*.PNG"):
                files.extend(src.glob(ext))

        print(f"  {folder_name}: {len(files)} images")

        for i, src_file in enumerate(files):
            out = dst / f"{src_file.stem}.jpg"
            if out.exists():
                continue
            try:
                with Image.open(src_file) as im:
                    im = im.convert("RGB")
                    im.thumbnail((IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.LANCZOS)
                    canvas = Image.new("RGB", (IMAGE_SIZE, IMAGE_SIZE))
                    x = (IMAGE_SIZE - im.width) // 2
                    y = (IMAGE_SIZE - im.height) // 2
                    canvas.paste(im, (x, y))
                    canvas.save(out, "JPEG", quality=90)
            except Exception as e:
                print(f"  [SKIP] {src_file}: {e}")

    print("Cache created.")
else:
    print(f"\nUsing existing cache: {CACHE_DIR}")

# ------------------------------------------------------------
# Dataset
# ------------------------------------------------------------
train_tf = transforms.Compose([
    transforms.RandomHorizontalFlip(p=0.5),
    transforms.RandomRotation(4),
    transforms.ColorJitter(brightness=0.15, contrast=0.15, saturation=0.10),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406],
                         [0.229, 0.224, 0.225]),
])

eval_tf = transforms.Compose([
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406],
                         [0.229, 0.224, 0.225]),
])

# ImageFolder uses alphabetical order:
# sal_data_100, sal_data_50, sal_data_80, sal_data_empty
# We explicitly remap to:
# 0 = empty, 1 = 50, 2 = 80, 3 = full
raw = datasets.ImageFolder(CACHE_DIR)
alphabetical = raw.classes
print("\nFolders found:", alphabetical)

expected = {"sal_data_empty", "sal_data_50", "sal_data_80", "sal_data_100"}
if set(alphabetical) != expected:
    raise RuntimeError(
        f"Expected exactly {sorted(expected)}, found {alphabetical}"
    )

# Build samples ourselves so class indices are exactly what we want.
folder_to_label = {
    "sal_data_empty": 0,
    "sal_data_50": 1,
    "sal_data_80": 2,
    "sal_data_100": 3,
}

samples = []
for folder, label in folder_to_label.items():
    d = CACHE_DIR / folder
    for ext in ("*.jpg", "*.jpeg", "*.png"):
        for p in d.glob(ext):
            samples.append((str(p), label))

random.shuffle(samples)

# 80/10/10 split
n = len(samples)
n_train = int(n * 0.80)
n_val = int(n * 0.10)
n_test = n - n_train - n_val

train_samples = samples[:n_train]
val_samples = samples[n_train:n_train + n_val]
test_samples = samples[n_train + n_val:]

print(f"\nTotal images: {n}")
print(f"Training:     {len(train_samples)}")
print(f"Validation:   {len(val_samples)}")
print(f"Testing:      {len(test_samples)}")

class_names = ["empty", "50%", "80%", "full"]

class SimpleImageDataset(torch.utils.data.Dataset):
    def __init__(self, samples, transform):
        self.samples = samples
        self.transform = transform

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        path, label = self.samples[idx]
        try:
            with Image.open(path) as im:
                im = im.convert("RGB")
                im = self.transform(im)
            return im, label
        except Exception as e:
            raise RuntimeError(f"Could not read {path}: {e}")

train_ds = SimpleImageDataset(train_samples, train_tf)
val_ds = SimpleImageDataset(val_samples, eval_tf)
test_ds = SimpleImageDataset(test_samples, eval_tf)

train_loader = DataLoader(
    train_ds, batch_size=BATCH_SIZE, shuffle=True,
    num_workers=NUM_WORKERS, pin_memory=(device.type == "cuda")
)
val_loader = DataLoader(
    val_ds, batch_size=BATCH_SIZE, shuffle=False,
    num_workers=NUM_WORKERS, pin_memory=(device.type == "cuda")
)
test_loader = DataLoader(
    test_ds, batch_size=BATCH_SIZE, shuffle=False,
    num_workers=NUM_WORKERS, pin_memory=(device.type == "cuda")
)

# ------------------------------------------------------------
# Model
# ------------------------------------------------------------
print("\nLoading MobileNetV3-Small...")
weights = models.MobileNet_V3_Small_Weights.DEFAULT
model = models.mobilenet_v3_small(weights=weights)

# Freeze backbone initially
for p in model.features.parameters():
    p.requires_grad = False

in_features = model.classifier[-1].in_features
model.classifier[-1] = nn.Linear(in_features, 4)
model = model.to(device)

criterion = nn.CrossEntropyLoss()

def run_epoch(loader, optimizer=None):
    training = optimizer is not None
    model.train(training)

    total_loss = 0.0
    correct = 0
    total = 0

    for images, labels in loader:
        images = images.to(device)
        labels = labels.to(device)

        if training:
            optimizer.zero_grad(set_to_none=True)

        logits = model(images)
        loss = criterion(logits, labels)

        if training:
            loss.backward()
            optimizer.step()

        total_loss += loss.item() * labels.size(0)
        correct += (logits.argmax(1) == labels).sum().item()
        total += labels.size(0)

    return total_loss / total, correct / total

def evaluate(loader):
    model.eval()
    confusion = torch.zeros(4, 4, dtype=torch.int64)
    total_loss = 0.0
    total = 0

    with torch.no_grad():
        for images, labels in loader:
            images = images.to(device)
            labels = labels.to(device)
            logits = model(images)
            loss = criterion(logits, labels)

            preds = logits.argmax(1)
            total_loss += loss.item() * labels.size(0)
            total += labels.size(0)

            for t, p in zip(labels.cpu(), preds.cpu()):
                confusion[t, p] += 1

    return total_loss / total, confusion

best_val = float("inf")

# ------------------------------------------------------------
# Phase 1: classifier head only
# ------------------------------------------------------------
print("\nPhase 1: training classification head...")
optimizer = torch.optim.AdamW(
    filter(lambda p: p.requires_grad, model.parameters()),
    lr=LR_HEAD, weight_decay=1e-4
)

for epoch in range(1, EPOCHS_HEAD + 1):
    tr_loss, tr_acc = run_epoch(train_loader, optimizer)
    va_loss, cm = evaluate(val_loader)
    va_acc = cm.diag().sum().item() / cm.sum().item()

    print(
        f"Epoch {epoch:02d}/{EPOCHS_HEAD} | "
        f"train loss {tr_loss:.4f} acc {tr_acc*100:.2f}% | "
        f"val loss {va_loss:.4f} acc {va_acc*100:.2f}%"
    )

    if va_loss < best_val:
        best_val = va_loss
        torch.save(model.state_dict(), MODEL_FILE)

# ------------------------------------------------------------
# Phase 2: fine-tune last MobileNet blocks
# ------------------------------------------------------------
print("\nPhase 2: fine-tuning last MobileNet blocks...")

for p in model.features[-3:].parameters():
    p.requires_grad = True

optimizer = torch.optim.AdamW(
    filter(lambda p: p.requires_grad, model.parameters()),
    lr=LR_FINE, weight_decay=1e-4
)

for epoch in range(1, EPOCHS_FINE + 1):
    tr_loss, tr_acc = run_epoch(train_loader, optimizer)
    va_loss, cm = evaluate(val_loader)
    va_acc = cm.diag().sum().item() / cm.sum().item()

    print(
        f"Fine {epoch:02d}/{EPOCHS_FINE} | "
        f"train loss {tr_loss:.4f} acc {tr_acc*100:.2f}% | "
        f"val loss {va_loss:.4f} acc {va_acc*100:.2f}%"
    )

    if va_loss < best_val:
        best_val = va_loss
        torch.save(model.state_dict(), MODEL_FILE)

# ------------------------------------------------------------
# Final test
# ------------------------------------------------------------
print("\nLoading best model...")
model.load_state_dict(torch.load(MODEL_FILE, map_location=device))

test_loss, cm = evaluate(test_loader)
test_acc = cm.diag().sum().item() / cm.sum().item()

print("\n" + "=" * 65)
print("FINAL TEST RESULTS")
print("=" * 65)
print(f"Overall 4-class accuracy: {test_acc*100:.2f}%")
print(f"Test loss:                {test_loss:.4f}")

print("\nConfusion matrix")
print("Rows = actual, columns = predicted")
print("             empty   50%   80%   full")
for i, row in enumerate(cm.tolist()):
    print(f"{class_names[i]:>8}  " + "  ".join(f"{x:5d}" for x in row))

# Per-class accuracy
print("\nPer-class accuracy:")
for i, name in enumerate(class_names):
    total_i = cm[i].sum().item()
    acc_i = cm[i, i].item() / total_i if total_i else 0
    print(f"  {name:>6}: {acc_i*100:.2f}%")

# ------------------------------------------------------------
# Critical binary metrics: EMPTY vs FULL
# Any 50/80 prediction is considered "not empty" / "not full".
# ------------------------------------------------------------
binary_cm = torch.zeros(2, 2, dtype=torch.int64)
# binary 0 = empty, 1 = full
# We evaluate only actual empty/full images.
with torch.no_grad():
    model.eval()
    for images, labels in test_loader:
        images = images.to(device)
        preds = model(images).argmax(1).cpu()

        for true, pred in zip(labels, preds):
            if true.item() == 0:  # actual empty
                b_true = 0
            elif true.item() == 3:  # actual full
                b_true = 1
            else:
                continue

            # Exact empty/full recognition:
            # predicted empty => empty
            # predicted full => full
            # 50/80 are treated as "other", therefore incorrect.
            if pred.item() == 0:
                b_pred = 0
            elif pred.item() == 3:
                b_pred = 1
            else:
                continue

            binary_cm[b_true, b_pred] += 1

binary_total = binary_cm.sum().item()
binary_acc = (
    binary_cm.diag().sum().item() / binary_total
    if binary_total else 0
)

print("\n" + "=" * 65)
print("CRITICAL EMPTY / FULL RESULT")
print("=" * 65)
print("This measures exact recognition of the two extreme states.")
print(f"Empty/Full exact accuracy: {binary_acc*100:.2f}%")
print("\nBinary confusion matrix")
print("             predicted")
print("             empty   full")
print(f"actual empty {binary_cm[0,0]:6d} {binary_cm[0,1]:6d}")
print(f"actual full  {binary_cm[1,0]:6d} {binary_cm[1,1]:6d}")

# Save metadata for inference script
info = {
    "classes": class_names,
    "class_to_fill_percent": {
        "0": 0,
        "1": 50,
        "2": 80,
        "3": 100
    },
    "image_size": IMAGE_SIZE,
    "model": "MobileNetV3-Small",
    "model_file": str(MODEL_FILE),
    "test_accuracy_4_class": test_acc,
    "empty_full_exact_accuracy": binary_acc,
    "confusion_matrix": cm.tolist(),
}

with open(INFO_FILE, "w", encoding="utf-8") as f:
    json.dump(info, f, indent=2)

print("\nSaved:")
print(f"  Model: {MODEL_FILE}")
print(f"  Info:  {INFO_FILE}")
print("\nNext step: connect this classifier to iv_estimator_v8.py.")