import random

from .base import FrameProcessor, FrameResult
from .real_frame_processor import RealFrameProcessor

__all__ = ["FrameProcessor", "FrameResult", "MockFrameProcessor", "active_processor"]


class MockFrameProcessor(FrameProcessor):
    """Ignores the frame entirely and returns a plausible random level, so
    the /api/v1/frames upload path can be exercised end-to-end before any
    real CV exists."""

    def process(self, device_id: str, frame_bytes: bytes) -> FrameResult:
        level = round(random.uniform(10, 100), 1)
        if level <= 5:
            aux_class = "empty"
        elif level <= 60:
            aux_class = "50%"
        elif level <= 90:
            aux_class = "80%"
        else:
            aux_class = "full"
        return FrameResult(fill_percent=level, aux_class=aux_class)


# The real model - see real_frame_processor.py and the README's
# "Fill-level model" section. Fall back to `MockFrameProcessor()` here if
# you need the random-numbers path again (e.g. the model file isn't
# present locally).
active_processor: FrameProcessor = RealFrameProcessor()
