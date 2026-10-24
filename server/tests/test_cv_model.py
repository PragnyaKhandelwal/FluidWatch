"""Smoke tests for the real MobileNetV3 fill-level model. These check that
the checkpoint loads against the architecture in real_frame_processor.py
and produces sane output - not accuracy, which needs real labelled IV-bag
photos (see CV_INTEGRATION.md)."""

import io

import pytest
from PIL import Image

from app.cv.pipeline import active_processor
from app.cv.real_frame_processor import _CLASS_NAMES, _IMAGE_SIZE, RealFrameProcessor, _prepare_image

from .conftest import make_jpeg


def test_active_processor_is_the_real_model():
    assert isinstance(active_processor, RealFrameProcessor)


@pytest.mark.parametrize("size", [(640, 480), (240, 320), (50, 50), (1600, 1200)])
def test_prepare_image_letterboxes_to_model_input(size):
    tensor = _prepare_image(make_jpeg(size=size))
    assert tuple(tensor.shape) == (1, 3, _IMAGE_SIZE, _IMAGE_SIZE)


def test_prepare_image_accepts_png_and_grayscale():
    buf = io.BytesIO()
    Image.new("L", (100, 200), 128).save(buf, format="PNG")
    assert tuple(_prepare_image(buf.getvalue()).shape) == (1, 3, _IMAGE_SIZE, _IMAGE_SIZE)


@pytest.mark.parametrize("color", [(0, 0, 0), (255, 255, 255), (30, 140, 220)])
def test_model_output_is_in_range(color):
    result = active_processor.process("test", make_jpeg(color=color))
    assert 0.0 <= result.fill_percent <= 100.0
    assert result.aux_class in _CLASS_NAMES


def test_model_is_deterministic_in_eval_mode():
    frame = make_jpeg(color=(90, 90, 160))
    assert active_processor.process("a", frame) == active_processor.process("b", frame)


def test_missing_checkpoint_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError, match="model not found"):
        RealFrameProcessor(model_path=tmp_path / "missing.pth")
