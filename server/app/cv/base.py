from abc import ABC, abstractmethod
from typing import NamedTuple


class InvalidFrameError(ValueError):
    """The uploaded bytes aren't a decodable image. Implementations raise
    this (not their imaging library's own error) so the router can turn
    it into a 422 instead of a 500."""


class FrameResult(NamedTuple):
    fill_percent: float
    # Optional auxiliary classification (e.g. "empty"/"50%"/"80%"/"full")
    # if the implementation has one - shown on the dashboard as a rough
    # sanity signal alongside the continuous percentage. None if the
    # implementation doesn't produce one.
    aux_class: str | None = None


class FrameProcessor(ABC):
    """Turns a raw camera frame into a fluid-level reading. This is the
    *entire* contract a real CV/ML implementation needs to satisfy - see
    CV_INTEGRATION.md for the full spec. Volume, flow rate, status and
    defaults are derived centrally from fill_percent in
    app/cv/reading_builder.py, so an implementation here only ever needs
    to answer one real question: given this image, what's the fill
    percentage? (aux_class is optional flavor, not required.)
    """

    @abstractmethod
    def process(self, device_id: str, frame_bytes: bytes) -> FrameResult: ...
