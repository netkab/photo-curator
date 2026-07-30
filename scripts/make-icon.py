r"""Generate scripts/photo-curator.ico — a modern, transparent app icon.

Design: a gradient "squircle" (superellipse) tile with fully transparent corners,
a soft top highlight for depth, and a clean knockout camera glyph with a brand
green aperture. Rendered at 1024px for crisp anti-aliasing, then downscaled into a
multi-resolution .ico (16..256) so it stays sharp on the taskbar, Desktop, Alt-Tab.

Run with the backend venv:  .\backend\.venv\Scripts\python scripts\make-icon.py
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "photo-curator.ico"
S = 1024                       # supersample canvas
margin = 18                    # keep AA edges off the canvas border
half = (S - 2 * margin) / 2.0
cx = cy = S / 2.0

# ---- background: gradient squircle with transparent corners -----------------
ys, xs = np.mgrid[0:S, 0:S].astype(np.float32)
nx = (xs - cx) / half
ny = (ys - cy) / half

# Superellipse mask (|x|^n + |y|^n <= 1). n≈4.3 gives the modern squircle look.
n = 4.3
v = (np.abs(nx) ** n + np.abs(ny) ** n) ** (1.0 / n)
alpha = np.clip((1.0 - v) * half / 1.6 + 0.5, 0.0, 1.0)   # ~1.6px feathered edge

# Diagonal blue -> violet gradient.
c0 = np.array([0x5B, 0x9D, 0xFF], np.float32)   # bright blue (existing accent)
c1 = np.array([0x7B, 0x61, 0xFF], np.float32)   # violet
t = ((xs + ys) / (2.0 * S))[..., None]
rgb = c0 * (1 - t) + c1 * t

# Subtle top highlight for a soft, glassy depth.
hl = (np.clip((0.42 - ys / S) / 0.42, 0, 1)[..., None]) * 0.16
rgb = rgb * (1 - hl) + np.array([255, 255, 255], np.float32) * hl

bg = Image.fromarray(
    np.dstack([rgb, alpha * 255.0]).astype(np.uint8), "RGBA"
)

# ---- camera glyph (white, with a knockout lens revealing the gradient) ------
g = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(g)
WHITE = (255, 255, 255, 255)
CLEAR = (0, 0, 0, 0)
GREEN = (70, 192, 138, 255)     # brand green aperture


def rr(x0, y0, x1, y1, r, fill):
    d.rounded_rectangle([x0 * S, y0 * S, x1 * S, y1 * S], radius=r * S, fill=fill)


rr(0.40, 0.335, 0.605, 0.43, 0.03, WHITE)   # viewfinder bump
rr(0.205, 0.40, 0.795, 0.745, 0.07, WHITE)  # camera body

lx, ly, lr = 0.5 * S, 0.575 * S, 0.137 * S
d.ellipse([lx - lr, ly - lr, lx + lr, ly + lr], fill=CLEAR)       # lens knockout
fx, fy, fr = 0.705 * S, 0.452 * S, 0.020 * S
d.ellipse([fx - fr, fy - fr, fx + fr, fy + fr], fill=CLEAR)       # flash knockout
ar = 0.052 * S
d.ellipse([lx - ar, ly - ar, lx + ar, ly + ar], fill=GREEN)      # aperture dot

icon = Image.alpha_composite(bg, g)

sizes = [256, 128, 64, 48, 32, 16]
base = icon.resize((256, 256), Image.LANCZOS)
base.save(OUT, format="ICO", sizes=[(s, s) for s in sizes])

# Sanity check: corners transparent, center opaque.
chk = Image.open(OUT).convert("RGBA")
corner = chk.getpixel((1, 1))[3]
center = chk.getpixel((chk.width // 2, chk.height // 2))[3]
print(f"Wrote {OUT}  ({', '.join(map(str, sizes))} px)")
print(f"  corner alpha={corner} (want 0), center alpha={center} (want 255)")
