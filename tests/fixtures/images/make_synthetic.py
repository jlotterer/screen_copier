"""Deterministic synthetic "webcam photo of a code screen" generator (TASK-06).

A white screen with rendered code text and one vertical reference line is placed in 3D in front
of a pinhole camera, rotated by ``yaw``/``pitch`` (the perspective skew), projected onto a dark
background, and given Gaussian sensor noise. The true screen corners and aspect ratio are
returned with the image, so tests can measure detection and rectification accuracy.

Same arguments + same seed -> byte-identical image.

Run as a script to write sample PNGs::

    uv run python tests/fixtures/images/make_synthetic.py OUT_DIR
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

CODE_LINES = [
    "def stitch(frames: list[Frame]) -> Document:",
    "    document = Document()",
    "    for frame in frames:",
    "        if frame.result is None:",
    "            continue",
    "        lines = normalize(frame.result.lines)",
    "        offset = align(document, lines, min_overlap=2)",
    "        if offset is None:",
    "            document.add_gap(kind='overlap')",
    "        document.merge(lines, offset)",
    "    return document",
    "",
    "class QuadSmoother:",
    "    def __init__(self, window: int = 5) -> None:",
    "        self._history = deque(maxlen=window)",
]

REFERENCE_LINE_X_FRAC = 0.92
"""Right of the longest code line, so the reference line never touches text."""
REFERENCE_LINE_Y_FRAC = (0.08, 0.92)

BACKGROUND_BGR = (32, 30, 28)
TEXT_BGR = (20, 20, 20)
LINE_BGR = (0, 0, 0)


@dataclass(frozen=True)
class SyntheticScreen:
    frame: npt.NDArray[np.uint8]
    """BGR camera frame, shape (frame_h, frame_w, 3)."""
    quad: npt.NDArray[np.float32]
    """True screen corners in the frame, (4, 2), ordered TL, TR, BR, BL."""
    aspect: float
    """True screen width / height."""
    line_x_frac: float
    """Horizontal position of the vertical reference line, as a fraction of the screen width."""
    line_y_frac: tuple[float, float]
    """Vertical extent (top, bottom) of the reference line, as fractions of the screen height."""


def render_screen(width: int, height: int) -> npt.NDArray[np.uint8]:
    """Render the flat screen content: code text on white, plus a vertical reference line."""
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    scale = height / 900
    line_h = max(1, round(52 * scale))
    for i, text in enumerate(CODE_LINES):
        y = round(60 * scale) + i * line_h
        if y > height - line_h:
            break
        cv2.putText(
            img,
            text,
            (round(40 * scale), y),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0 * scale,
            TEXT_BGR,
            max(1, round(2 * scale)),
            cv2.LINE_AA,
        )
    x = round(width * REFERENCE_LINE_X_FRAC)
    y0, y1 = (round(height * f) for f in REFERENCE_LINE_Y_FRAC)
    cv2.line(img, (x, y0), (x, y1), LINE_BGR, max(2, round(4 * scale)), cv2.LINE_AA)
    return img


def _rotation(yaw_deg: float, pitch_deg: float, roll_deg: float) -> npt.NDArray[np.float64]:
    y, p, r = (math.radians(a) for a in (yaw_deg, pitch_deg, roll_deg))
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    return rz @ ry @ rx


def make_synthetic(
    *,
    frame_size: tuple[int, int] = (1920, 1080),
    screen_size: tuple[int, int] = (1600, 1000),
    yaw_deg: float = 15.0,
    pitch_deg: float = 0.0,
    roll_deg: float = 0.0,
    hfov_deg: float = 65.0,
    fill: float = 0.7,
    center_offset: tuple[float, float] = (0.0, 0.0),
    noise_sigma: float = 6.0,
    seed: int = 0,
) -> SyntheticScreen:
    """Render one synthetic camera frame.

    Args:
        frame_size: Camera frame (width, height).
        screen_size: Resolution of the flat screen content (width, height); sets the aspect.
        yaw_deg / pitch_deg / roll_deg: Rotation of the screen plane (the skew), in degrees.
        hfov_deg: Horizontal field of view of the simulated camera.
        fill: Unrotated screen width as a fraction of the frame width.
        center_offset: Screen center offset from the optical axis, as fractions of the frame
            (width, height).
        noise_sigma: Standard deviation of the additive Gaussian noise, 0-255 scale.
        seed: Seed for the noise.
    """
    fw, fh = frame_size
    sw, sh = screen_size
    screen = render_screen(sw, sh)

    focal = (fw / 2) / math.tan(math.radians(hfov_deg) / 2)
    plane_w = 1.0
    plane_h = plane_w * sh / sw
    depth = focal * plane_w / (fill * fw)
    offset = np.array(
        [center_offset[0] * fw * depth / focal, center_offset[1] * fh * depth / focal, depth]
    )

    corners_3d = np.array(
        [
            [-plane_w / 2, -plane_h / 2, 0],
            [plane_w / 2, -plane_h / 2, 0],
            [plane_w / 2, plane_h / 2, 0],
            [-plane_w / 2, plane_h / 2, 0],
        ]
    )
    cam = corners_3d @ _rotation(yaw_deg, pitch_deg, roll_deg).T + offset
    projected = np.stack(
        [focal * cam[:, 0] / cam[:, 2] + fw / 2, focal * cam[:, 1] / cam[:, 2] + fh / 2], axis=1
    ).astype(np.float32)

    src = np.array([[0, 0], [sw, 0], [sw, sh], [0, sh]], dtype=np.float32)
    homography = cv2.getPerspectiveTransform(src, projected)
    warped = cv2.warpPerspective(screen, homography, (fw, fh), flags=cv2.INTER_LINEAR)
    mask = cv2.warpPerspective(
        np.ones((sh, sw), dtype=np.float32), homography, (fw, fh), flags=cv2.INTER_LINEAR
    )[..., None]

    background = np.empty((fh, fw, 3), dtype=np.float32)
    background[:] = BACKGROUND_BGR
    composite = warped.astype(np.float32) * mask + background * (1 - mask)

    rng = np.random.default_rng(seed)
    composite += rng.normal(0.0, noise_sigma, composite.shape).astype(np.float32)
    frame = np.clip(np.rint(composite), 0, 255).astype(np.uint8)

    return SyntheticScreen(
        frame=frame,
        quad=projected,
        aspect=sw / sh,
        line_x_frac=REFERENCE_LINE_X_FRAC,
        line_y_frac=REFERENCE_LINE_Y_FRAC,
    )


def main(argv: list[str]) -> int:
    out_dir = Path(argv[0]) if argv else Path("synthetic_out")
    out_dir.mkdir(parents=True, exist_ok=True)
    for angle in (0, 5, 10, 15):
        sample = make_synthetic(yaw_deg=angle, pitch_deg=angle / 3, seed=angle)
        cv2.imwrite(str(out_dir / f"screen_yaw{angle:02d}.png"), sample.frame)
    print(f"wrote samples to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
