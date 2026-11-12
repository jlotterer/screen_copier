"""Simulated camera for tests and demos (architecture §5.1, REQ-CAM-10).

``FakeCameraSource`` exposes the same read interface as ``cv2.VideoCapture`` (``isOpened``,
``read``, ``get``, ``set``, ``release``) so ``CameraWorker`` can use it through its
``capture_factory``. Pure logic: must not import PySide6.

- A **folder** cycles through its images in sorted file-name order, each held for ``hold_ms``.
  Images of a different size are resized to the first image's size.
- A **file** plays as a video and loops at the end.

``read`` paces itself to ``fps`` with the injected clock and sleep, like a real camera that blocks
until the next frame is ready. Which image is shown depends on the frame count, not on wall time,
so the sequence is deterministic.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"})


class FakeCameraSource:
    """A ``cv2.VideoCapture`` look-alike backed by a folder of images or a video file."""

    def __init__(
        self,
        path: Path,
        *,
        fps: int = 30,
        hold_ms: int = 1000,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if fps <= 0:
            raise ValueError("fps must be positive")
        if hold_ms <= 0:
            raise ValueError("hold_ms must be positive")
        self.path = Path(path)
        self.fps = fps
        self.hold_ms = hold_ms
        self._clock = clock
        self._sleep = sleep
        self._images: list[NDArray[np.uint8]] = []
        self._video: cv2.VideoCapture | None = None
        self._size = (0, 0)
        self._props: dict[int, float] = {}
        self._frame_count = 0
        self._next_time: float | None = None
        self._opened = False

        if self.path.is_dir():
            self._load_images()
        elif self.path.is_file():
            self._open_video()
        else:
            logger.warning("fake camera path does not exist: %s", self.path)

    # -- setup ---------------------------------------------------------------------------------

    def _load_images(self) -> None:
        files = sorted(
            p for p in self.path.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
        )
        for file in files:
            image = cv2.imread(str(file), cv2.IMREAD_COLOR)
            if image is None:
                logger.warning("fake camera skipped unreadable image: %s", file.name)
                continue
            if self._images:
                h, w = self._images[0].shape[:2]
                if image.shape[:2] != (h, w):
                    image = cv2.resize(image, (w, h), interpolation=cv2.INTER_AREA)
            self._images.append(image)
        if self._images:
            h, w = self._images[0].shape[:2]
            self._size = (w, h)
            self._opened = True
        else:
            logger.warning("fake camera folder has no readable images: %s", self.path)

    def _open_video(self) -> None:
        video = cv2.VideoCapture(str(self.path))
        if not video.isOpened():
            logger.warning("fake camera could not open video: %s", self.path)
            video.release()
            return
        self._video = video
        self._size = (
            int(video.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(video.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        )
        self._opened = True

    # -- cv2.VideoCapture interface ------------------------------------------------------------

    def isOpened(self) -> bool:
        return self._opened

    def read(self) -> tuple[bool, NDArray[np.uint8] | None]:
        if not self._opened:
            return False, None
        self._pace()
        frame = self._next_frame()
        if frame is None:
            return False, None
        self._frame_count += 1
        return True, frame

    def get(self, prop_id: int) -> float:
        if prop_id == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._size[0])
        if prop_id == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._size[1])
        if prop_id == cv2.CAP_PROP_FPS:
            return float(self.fps)
        return self._props.get(prop_id, 0.0)

    def set(self, prop_id: int, value: float) -> bool:
        """Record the property. The frame size never changes: the source keeps its native size."""
        self._props[prop_id] = float(value)
        return True

    def release(self) -> None:
        self._opened = False
        if self._video is not None:
            self._video.release()
            self._video = None

    # -- internals -----------------------------------------------------------------------------

    def _pace(self) -> None:
        """Block until the next frame is due, like a camera delivering ``fps`` frames a second."""
        interval = 1.0 / self.fps
        now = self._clock()
        if self._next_time is None:
            self._next_time = now
        wait = self._next_time - now
        if wait > 0:
            self._sleep(wait)
            self._next_time += interval
        else:
            # Running late (or first frame): don't try to catch up with a burst of frames.
            self._next_time = now + interval

    def _next_frame(self) -> NDArray[np.uint8] | None:
        if self._images:
            frames_per_image = max(1, round(self.hold_ms * self.fps / 1000))
            index = (self._frame_count // frames_per_image) % len(self._images)
            return self._images[index].copy()
        if self._video is None:
            return None
        ok, frame = self._video.read()
        if not ok or frame is None:
            self._video.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._video.read()
            if not ok or frame is None:
                return None
        return frame
