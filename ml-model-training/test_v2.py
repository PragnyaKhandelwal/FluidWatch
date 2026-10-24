from __future__ import annotations

import argparse
import csv
from pathlib import Path

import torch
import torch.nn as nn
from PIL import Image, ImageFile
from torchvision import transforms, models

ImageFile.LOAD_TRUNCATED_IMAGES = True

ROOT = Path(__file__).resolve().parent
MODEL_FILE = ROOT / "fill_regression_v2_mobilenet.pth"
IMAGE_SIZE = 224
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


class MultiTaskFillModel(nn.Module):
    def __init__(self):
        super().__init__()

        base = models.mobilenet_v3_small(weights=None)
        self.backbone = base.features
        self.pool = nn.AdaptiveAvgPool2d(1)

        self.shared = nn.Sequential(
            nn.Flatten(),
            nn.Linear(576, 160),
            nn.Hardswish(),
            nn.Dropout(0.15),
        )

        self.regression_head = nn.Sequential(
            nn.Linear(160, 64),
            nn.Hardswish(),
            nn.Dropout(0.10),
            nn.Linear(64, 1),
        )

        self.classification_head = nn.Linear(160, 4)

    def forward(self, x):
        x = self.backbone(x)
        x = self.pool(x)
        x = self.shared(x)

        regression_raw = self.regression_head(x)
        classification_logits = self.classification_head(x)

        fill_percent = torch.sigmoid(regression_raw).squeeze(1) * 100.0
        return fill_percent, classification_logits


TRANSFORM = transforms.Compose([
    transforms.ToTensor(),
    transforms.Normalize(
        [0.485, 0.456, 0.406],
        [0.229, 0.224, 0.225],
    ),
])

CLASS_NAMES = ["empty", "50%", "80%", "full"]


def prepare_image(path: Path):
    with Image.open(path) as im:
        im = im.convert("RGB")

        # Match the V2 training preprocessing:
        # square source -> 224x224. For non-square photos,
        # preserve aspect ratio and pad with black.
        im.thumbnail((IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.LANCZOS)

        canvas = Image.new("RGB", (IMAGE_SIZE, IMAGE_SIZE), (0, 0, 0))
        x = (IMAGE_SIZE - im.width) // 2
        y = (IMAGE_SIZE - im.height) // 2
        canvas.paste(im, (x, y))

        return TRANSFORM(canvas).unsqueeze(0)


def main():
    parser = argparse.ArgumentParser(
        description="Test V2 continuous bottle-fill model on real photos."
    )
    parser.add_argument(
        "--image-dir",
        default="./photos",
        help="Directory containing photos (default: ./photos)",
    )
    args = parser.parse_args()

    image_dir = Path(args.image_dir)
    if not image_dir.exists():
        raise FileNotFoundError(f"Image directory not found: {image_dir}")

    if not MODEL_FILE.exists():
        raise FileNotFoundError(
            f"V2 model not found: {MODEL_FILE}\n"
            "Make sure fill_regression_v2_mobilenet.pth is in the project folder."
        )

    print("=" * 72)
    print("V2 CONTINUOUS FILL MODEL — REAL PHOTO TEST")
    print("=" * 72)
    print(f"Device: {DEVICE}")
    print(f"Model:  {MODEL_FILE}")
    print(f"Photos: {image_dir.resolve()}")
    print()

    checkpoint = torch.load(
        MODEL_FILE,
        map_location=DEVICE,
        weights_only=False,
    )

    model = MultiTaskFillModel()

    state = checkpoint.get("model_state", checkpoint)
    model.load_state_dict(state)
    model.to(DEVICE)
    model.eval()

    image_paths = sorted(
        p for p in image_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
        and "_v2_result" not in p.stem
    )

    if not image_paths:
        print("No images found.")
        return

    results_dir = image_dir / "v2_results"
    results_dir.mkdir(exist_ok=True)

    csv_path = results_dir / "v2_predictions.csv"

    rows = []

    print(f"Found {len(image_paths)} images.")
    print("-" * 72)

    with torch.no_grad():
        for i, path in enumerate(image_paths, 1):
            try:
                x = prepare_image(path).to(DEVICE)

                pred_fill, class_logits = model(x)

                fill = float(pred_fill.item())
                fill = max(0.0, min(100.0, fill))

                class_idx = int(class_logits.argmax(dim=1).item())
                class_name = CLASS_NAMES[class_idx]

                rows.append([
                    path.name,
                    f"{fill:.2f}",
                    class_name,
                ])

                print(
                    f"[{i:3d}/{len(image_paths)}] "
                    f"{path.name:<40} "
                    f"V2 = {fill:6.2f}%   "
                    f"class = {class_name}"
                )

            except Exception as e:
                print(f"[ERROR] {path.name}: {e}")
                rows.append([path.name, "ERROR", str(e)])

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["image", "predicted_fill_percent", "predicted_class"])
        writer.writerows(rows)

    # Summary of the most important cases.
    valid = []
    for row in rows:
        try:
            valid.append((row[0], float(row[1]), row[2]))
        except ValueError:
            pass

    print()
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"Images tested: {len(image_paths)}")
    print(f"Successful:    {len(valid)}")
    print(f"Results CSV:   {csv_path}")

    if valid:
        print()
        print("Predictions:")
        for name, fill, cls in valid:
            print(f"  {name}: {fill:.2f}% ({cls})")

        print()
        print("NOTE:")
        print("The V2 model was trained only on 0%, 50%, 80% and 100% labels.")
        print("Its intermediate predictions (for example 63% or 79%) are")
        print("interpolations and need real intermediate-fill photos for validation.")
        print("This test is especially useful because these photos are outside")
        print("the original training classes.")

    print("=" * 72)


if __name__ == "__main__":
    main()
