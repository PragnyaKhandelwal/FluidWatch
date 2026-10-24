"""Real fill-level model, wired into the FrameProcessor seam (see
pipeline.py). Architecture, preprocessing and weights come from
ml-model-training/fill_regression_v2_mobilenet.pth (a MobileNetV3-Small
multitask model - continuous regression head + auxiliary 4-class head,
initialized from a separately-trained 97.4%-accurate classifier and
fine-tuned for continuous 0-100% output). See
ml-model-training/train_continuous_v2.py and test_v2.py for the original
training/inference code this was adapted from; the class predictions
(empty/50%/80%/full) from the auxiliary head are logged for sanity-
checking but the regression output is what's returned - the fill%
consumers actually need."""

import io
import logging
from pathlib import Path

import torch
import torch.nn as nn
from PIL import Image, UnidentifiedImageError
from torchvision import models, transforms

from .base import FrameProcessor, FrameResult, InvalidFrameError

logger = logging.getLogger(__name__)

_MODEL_PATH = Path(__file__).parent / "models" / "fill_regression_v2_mobilenet.pth"
_IMAGE_SIZE = 224
_CLASS_NAMES = ["empty", "50%", "80%", "full"]

_TRANSFORM = transforms.Compose(
    [
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ]
)


class MultiTaskFillModel(nn.Module):
    """Must match ml-model-training/test_v2.py's architecture exactly -
    this is what fill_regression_v2_mobilenet.pth's weights were trained
    against."""

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


def _prepare_image(frame_bytes: bytes) -> torch.Tensor:
    """Same preprocessing as training/test_v2.py's prepare_image(): square
    canvas, aspect-preserving thumbnail, black-padded, ImageNet-normalized.
    Must stay in sync with that - the model was trained on exactly this."""
    try:
        im = Image.open(io.BytesIO(frame_bytes))
        im.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidFrameError("uploaded file is not a readable image") from exc

    with im:
        im = im.convert("RGB")
        im.thumbnail((_IMAGE_SIZE, _IMAGE_SIZE), Image.Resampling.LANCZOS)

        canvas = Image.new("RGB", (_IMAGE_SIZE, _IMAGE_SIZE), (0, 0, 0))
        x = (_IMAGE_SIZE - im.width) // 2
        y = (_IMAGE_SIZE - im.height) // 2
        canvas.paste(im, (x, y))

        return _TRANSFORM(canvas).unsqueeze(0)


class RealFrameProcessor(FrameProcessor):
    def __init__(self, model_path: Path = _MODEL_PATH):
        if not model_path.exists():
            raise FileNotFoundError(
                f"Fill-level model not found at {model_path}. "
                "Copy fill_regression_v2_mobilenet.pth into app/cv/models/."
            )

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        checkpoint = torch.load(model_path, map_location=self.device, weights_only=False)
        state = checkpoint.get("model_state", checkpoint) if isinstance(checkpoint, dict) else checkpoint

        self.model = MultiTaskFillModel()
        self.model.load_state_dict(state)
        self.model.to(self.device)
        self.model.eval()

        logger.info("RealFrameProcessor loaded %s on %s", model_path.name, self.device)

    def process(self, device_id: str, frame_bytes: bytes) -> FrameResult:
        x = _prepare_image(frame_bytes).to(self.device)

        with torch.no_grad():
            pred_fill, class_logits = self.model(x)

        fill = float(pred_fill.item())
        fill = max(0.0, min(100.0, fill))

        class_name = _CLASS_NAMES[int(class_logits.argmax(dim=1).item())]
        logger.info("device=%s fill=%.1f%% aux_class=%s", device_id, fill, class_name)

        return FrameResult(fill_percent=fill, aux_class=class_name)
