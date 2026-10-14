"""Draw the NutMeter icon (level-meter bars on a rounded square) and write assets/nutmeter.ico. No Pillow needed."""
import os, struct, zlib
import numpy as np


def png(rgba):
    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def draw(n):
    s = 4                                                     # supersampling for smooth edges
    N = n * s
    yy, xx = np.mgrid[0:N, 0:N] + 0.5
    img = np.zeros((N, N, 4))
    r, m = 0.22 * N, 0.04 * N                                 # corner radius, margin
    dx = np.maximum(np.maximum(m + r - xx, xx - (N - m - r)), 0)
    dy = np.maximum(np.maximum(m + r - yy, yy - (N - m - r)), 0)
    inside = (dx ** 2 + dy ** 2 <= r ** 2) & (xx > m) & (xx < N - m) & (yy > m) & (yy < N - m)
    img[inside] = (0.36, 0.22, 0.12, 1.0)                     # walnut brown
    heights = (0.38, 0.62, 0.50, 0.78, 0.30)
    colors = [(0.45, 0.85, 0.40), (0.45, 0.85, 0.40), (0.95, 0.80, 0.25), (0.95, 0.35, 0.25), (0.45, 0.85, 0.40)]
    x0, x1, base, top = 0.17 * N, 0.83 * N, 0.80 * N, 0.20 * N
    bw = (x1 - x0) / len(heights)
    for i, (hgt, c) in enumerate(zip(heights, colors)):
        a, b = x0 + i * bw + 0.12 * bw, x0 + (i + 1) * bw - 0.12 * bw
        bar = (xx >= a) & (xx < b) & (yy <= base) & (yy >= base - hgt * (base - top))
        img[bar & inside] = (*c, 1.0)
    line = (yy >= base + 0.02 * N) & (yy < base + 0.045 * N) & (xx >= x0) & (xx < x1)
    img[line & inside] = (0.96, 0.90, 0.80, 1.0)
    img = img.reshape(n, s, n, s, 4).mean(axis=(1, 3))
    rgb = np.where(img[..., 3:] > 0, img[..., :3] / np.maximum(img[..., 3:], 1e-9), 0)
    return (np.concatenate([rgb, img[..., 3:]], axis=2) * 255 + 0.5).clip(0, 255).astype(np.uint8)


sizes = (16, 24, 32, 48, 64, 128, 256)
blobs = [png(draw(n)) for n in sizes]
head = struct.pack("<HHH", 0, 1, len(sizes))
off = 6 + 16 * len(sizes)
dirs = b""
for n, b in zip(sizes, blobs):
    dirs += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(b), off)
    off += len(b)
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nutmeter.ico")
open(out, "wb").write(head + dirs + b"".join(blobs))
print("wrote", out)
