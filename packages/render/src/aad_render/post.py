"""Depth-based shading, done in NumPy so that it repeats exactly on one machine.

Hardware ambient occlusion passes vary with drivers; these only read the depth buffer. They add what
flat Phong lacks in orthographic views: pockets read as pockets, a part's far steps read as farther,
and curved silhouettes get an outline that no B-rep edge provides.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

CAVITY_STRENGTH = 1.1
CAVITY_LIMIT = 0.34
DEPTH_FADE = 0.12
SILHOUETTE_COLOR = np.asarray((36.0, 42.0, 50.0))


def box_blur(values: np.ndarray, radius: int) -> np.ndarray:
    """Mean over a (2r+1) square, with the window clipped at the image border."""
    if radius < 1:
        return values
    result = values
    for axis in (0, 1):
        padded = np.concatenate([np.zeros_like(np.take(result, [0], axis=axis)), result], axis=axis)
        running = np.cumsum(padded, axis=axis, dtype=np.float64)
        size = result.shape[axis]
        low = np.clip(np.arange(size) - radius, 0, size)
        high = np.clip(np.arange(size) + radius + 1, 0, size)
        count = (high - low).reshape([-1 if index == axis else 1 for index in range(2)])
        result = (np.take(running, high, axis=axis) - np.take(running, low, axis=axis)) / count
    return result


def silhouette_mask(height: np.ndarray, part: np.ndarray, pixel_world: float) -> np.ndarray:
    """Pixels on the part's outline and on steps in depth.

    A second difference ignores any flat surface however steeply it is tilted, and fires on the
    jump where a curved surface turns away. The threshold is relative to pixel size.
    """
    outline = part & ~_all_neighbors(part)
    limit = pixel_world * 6.0
    step = np.zeros_like(part)
    with np.errstate(invalid="ignore"):  # Background is nan, and a comparison with nan is false.
        step[1:-1, :] = np.abs(height[2:, :] - 2.0 * height[1:-1, :] + height[:-2, :]) > limit
        step[:, 1:-1] |= np.abs(height[:, 2:] - 2.0 * height[:, 1:-1] + height[:, :-2]) > limit
    return outline | step


def _all_neighbors(mask: np.ndarray) -> np.ndarray:
    result = mask.copy()
    result[1:, :] &= mask[:-1, :]
    result[:-1, :] &= mask[1:, :]
    result[:, 1:] &= mask[:, :-1]
    result[:, :-1] &= mask[:, 1:]
    return result


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    result = mask
    for _ in range(max(0, radius)):
        grown = result.copy()
        grown[1:, :] |= result[:-1, :]
        grown[:-1, :] |= result[1:, :]
        grown[:, 1:] |= result[:, :-1]
        grown[:, :-1] |= result[:, 1:]
        result = grown
    return result


def draw_outlines(rgb: np.ndarray, height: np.ndarray, part: np.ndarray, pixel_world: float, factor: int) -> np.ndarray:
    """Paint silhouettes into a supersampled frame, at about one final pixel wide."""
    if not part.any():
        return rgb
    result = rgb.copy()
    result[dilate(silhouette_mask(height, part, pixel_world), factor // 2)] = SILHOUETTE_COLOR.astype(np.uint8)
    return result


def cavity_gain(height: np.ndarray, part: np.ndarray, pixel_world: float) -> np.ndarray | None:
    """Per-pixel brightness from local depth: crevices and far surfaces are darker.

    Low-frequency, so it runs on the final image rather than the supersampled one.
    """
    if not part.any():
        return None
    span = height.shape[0]
    low = float(height[part].min())
    high = float(height[part].max())
    relief = max(high - low, pixel_world)
    filled = np.where(part, height, low).astype(np.float64)
    darkness = np.zeros(height.shape, dtype=np.float64)
    for fraction, weight in ((0.012, 0.7), (0.04, 0.3)):
        radius = max(2, int(span * fraction))
        blurred = box_blur(box_blur(filled, radius), radius)
        # A pixel below its surroundings sits in a crevice. Depth is scaled to the blur width.
        darkness += weight * np.maximum((blurred - filled) / (radius * pixel_world), 0.0)
    # Saturates smoothly, so a deep pocket's shadow has no visible boundary.
    shadow = 1.0 - CAVITY_LIMIT * (1.0 - np.exp(-darkness * CAVITY_STRENGTH))
    fade = 1.0 - DEPTH_FADE * (high - filled) / relief
    return np.where(part, shadow * fade, 1.0).astype(np.float32)


def apply_gain(rgb: np.ndarray, gain: np.ndarray | None) -> np.ndarray:
    if gain is None:
        return rgb
    return np.clip(rgb.astype(np.float32) * gain[..., None] + 0.5, 0.0, 255.0).astype(np.uint8)


def downsample(rgb: np.ndarray, factor: int) -> np.ndarray:
    """Exact box filter over whole supersample blocks."""
    if factor <= 1:
        return rgb
    return np.asarray(Image.fromarray(rgb, mode="RGB").reduce(factor))
