"""Tiny perceptual hash (dHash, 64-bit) — no extra dependency beyond Pillow.

Two ad images with Hamming distance <= NEAR_THRESHOLD are treated as 'the same
creative' (A/B re-uploads, placement variants, minor crops).
"""
from __future__ import annotations

from pathlib import Path

NEAR_THRESHOLD = 8  # bits out of 64


def phash_file(path: str | Path, size: int = 8) -> str | None:
    try:
        from PIL import Image

        img = Image.open(path).convert("L").resize((size + 1, size), Image.LANCZOS)
    except Exception:  # noqa: BLE001
        return None
    px = list(img.getdata())
    w = size + 1
    bits = 0
    for row in range(size):
        for col in range(size):
            bits = (bits << 1) | (1 if px[row * w + col] > px[row * w + col + 1] else 0)
    return f"{bits:016x}"


def hamming(a: str | None, b: str | None) -> int:
    if not a or not b:
        return 64
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def same_creative(a: str | None, b: str | None) -> bool:
    return hamming(a, b) <= NEAR_THRESHOLD
