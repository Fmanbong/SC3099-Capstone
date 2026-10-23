"""Passive presentation-attack detection on a single frame.

FaceMesh depth cannot tell a real face from a photo of one: MediaPipe infers
3D landmarks from any 2D picture, so a printed photo reports "good" depth.
These detectors look instead for physical evidence of a presentation medium:

- monochrome: a black-and-white print has no colour at all on the face
- screen_pattern: a display's pixel grid / moire leaves sharp periodic peaks
  in the face's spectrum, which natural skin never does
- pixelation: an upscaled low-resolution re-capture only changes colour on a
  fixed k-pixel grid in both directions (a denoised webcam frame is smooth
  but has no such grid, so counting identical neighbours is not enough)
- presentation_frame: the face sits on a card or screen whose edges are fully
  visible against a uniform surround (a live person's body runs off the frame)

Each threshold keeps a wide margin from every genuine image we measured,
including simulated 640x480 webcam frames in low light and real webcam frames
(which are denoised and less saturated than simulations), so a detector only
fires on clear evidence. Subtle attacks (a colour print filling the camera's
view) are not caught here; those need an active challenge across frames.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import cv2
import numpy as np

MONOCHROME_MAX_SATURATION = 0.05  # genuine faces measured 0.15 (real webcam) to 0.49
SCREEN_PATTERN_MIN_PEAK = 2.6     # genuine faces measured 1.96-2.19
PIXELATION_MIN_GRID = 0.25        # genuine (incl. denoised webcam) 0.00-0.05; pixelated 0.42-0.51

Box = Tuple[int, int, int, int]  # x, y, w, h


def _spectral_peak(gray: np.ndarray) -> float:
    """Sharpness of the strongest isolated mid/high-frequency spectral peak.

    Returned as the 99.95th-percentile residual over the residual's spread;
    bins on the JPEG 8x8 block grid are excluded because every JPEG has them.
    """
    n = min(gray.shape) // 8 * 8
    if n < 64:
        return 0.0
    g = gray[:n, :n].astype(np.float32)
    g = (g - g.mean()) * np.outer(np.hanning(n), np.hanning(n))
    mag = np.log1p(np.abs(np.fft.fftshift(np.fft.fft2(g))))
    freqs = np.fft.fftshift(np.fft.fftfreq(n))
    fy, fx = np.meshgrid(freqs, freqs, indexing="ij")
    radius = np.hypot(fx, fy)
    band = (radius > 0.08) & (radius < 0.48)
    for k in range(1, 5):
        band &= ~((np.abs(np.abs(fx) - k / 8) < 1.5 / n) | (np.abs(np.abs(fy) - k / 8) < 1.5 / n))
    resid = (mag - cv2.blur(mag, (9, 9)))[band]
    return float(np.percentile(resid, 99.95) / (resid.std() + 1e-6))


def _pixel_grid(gray: np.ndarray) -> float:
    """How strongly colour changes lock onto a fixed k-pixel grid (0 = none, 1 = only on the grid).

    For each axis and block size k, measure the share of changes falling on
    the busiest residue mod k, rescaled so a uniform spread scores 0. Both
    axes must show the grid, as nearest-neighbour upscaling produces.
    """
    g = gray.astype(np.int16)
    per_axis = []
    for axis in (1, 0):
        changes = (np.abs(np.diff(g, axis=axis)) > 2).sum(axis=1 - axis).astype(np.float64)
        best = 0.0
        for k in range(2, 9):
            if len(changes) < 4 * k or changes.sum() == 0:
                continue
            shares = np.array([changes[r::k].sum() for r in range(k)]) / changes.sum()
            best = max(best, (shares.max() - 1 / k) / (1 - 1 / k))
        per_axis.append(best)
    return float(min(per_axis))


def _presentation_frame(image_rgb: np.ndarray, box: Box) -> float:
    """Fraction of the frame covered by a card/screen held inside a uniform surround, else 0."""
    h0, w0 = image_rgb.shape[:2]
    scale = 320 / max(h0, w0)
    small = cv2.resize(image_rgb, (max(1, int(w0 * scale)), max(1, int(h0 * scale))), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(small, cv2.COLOR_RGB2LAB).astype(np.float32)
    h, w = lab.shape[:2]
    band = max(2, int(0.03 * min(h, w)))
    border = np.concatenate([lab[:band].reshape(-1, 3), lab[-band:].reshape(-1, 3),
                             lab[:, :band].reshape(-1, 3), lab[:, -band:].reshape(-1, 3)])
    background = np.median(border, axis=0)
    if np.mean(np.linalg.norm(border - background, axis=1) < 18) < 0.9:
        return 0.0  # the surround is a real scene, not a plain desk or wall
    foreground = (np.linalg.norm(lab - background, axis=2) >= 18).astype(np.uint8)
    foreground = cv2.morphologyEx(foreground, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(foreground, connectivity=8)
    x, y, bw, bh = (int(v * scale) for v in box)
    cx, cy = min(w - 1, x + bw // 2), min(h - 1, y + bh // 2)
    label = labels[cy, cx]
    if label == 0:
        return 0.0
    left, top, cw, ch, area = stats[label]
    margin = max(2, int(0.02 * min(h, w)))
    if left < margin or top < margin or left + cw > w - margin or top + ch > h - margin:
        return 0.0  # touches the edge: a person's body or a print filling the view
    if not (left <= x and top <= y and x + bw <= left + cw and y + bh <= top + ch):
        return 0.0
    contour = max(cv2.findContours((labels == label).astype(np.uint8), cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)[0], key=cv2.contourArea)
    (_, _), (rw, rh), _ = cv2.minAreaRect(contour)
    if rw * rh == 0 or area / (rw * rh) < 0.85:
        return 0.0  # not rectangular, so not a card or screen
    return float(area / (h * w))


def detect_presentation_attack(image_rgb: np.ndarray, box: Box) -> Tuple[List[str], Dict[str, float]]:
    """Return (attack signals found, raw measurements) for the face at `box`."""
    x, y, w, h = box
    face = image_rgb[y:y + h, x:x + w]
    if face.size == 0:
        return [], {}
    saturation = float(cv2.cvtColor(face, cv2.COLOR_RGB2HSV)[..., 1].mean() / 255)
    gray = cv2.cvtColor(face, cv2.COLOR_RGB2GRAY)
    r, g = face[..., 0].astype(np.float32), face[..., 1].astype(np.float32)
    peak = max(_spectral_peak(gray), _spectral_peak(r - g + 128))
    grid = _pixel_grid(gray)
    frame = _presentation_frame(image_rgb, box)

    signals = []
    if saturation < MONOCHROME_MAX_SATURATION:
        signals.append("monochrome")
    if peak >= SCREEN_PATTERN_MIN_PEAK:
        signals.append("screen_pattern")
    if grid >= PIXELATION_MIN_GRID:
        signals.append("pixelation")
    if frame > 0:
        signals.append("presentation_frame")
    measurements = {"saturation": round(saturation, 3), "spectral_peak": round(peak, 2),
                    "pixel_grid": round(grid, 3), "frame_coverage": round(frame, 3)}
    return signals, measurements
