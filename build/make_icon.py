"""Build build/icon.ico (multi-size) for the exe and the window.

    python build/make_icon.py

Uses ui/assets/icon-1024.png when the UI worker provides it; otherwise draws a placeholder glass droplet with a down
arrow. No Instagram logo or colours are used on purpose.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SOURCE_PNG = ROOT / "ui" / "assets" / "icon-1024.png"
OUT_ICO = ROOT / "build" / "icon.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]
S = 1024  # drawing canvas, downscaled with LANCZOS for the smaller sizes


def _vertical_gradient(size: int, top: tuple, bottom: tuple) -> Image.Image:
    grad = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / (size - 1)
        grad.putpixel((0, y), tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(4)))
    return grad.resize((size, size))


def _droplet_mask(size: int) -> Image.Image:
    """Teardrop: a circle plus a triangle that narrows to a point at the top."""
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    cx, cy, r = size / 2, size * 0.58, size * 0.27
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    # the two tangent lines from the tip to the circle meet it at angle alpha from the vertical (cos alpha = r / dist)
    tip_y = size * 0.13
    alpha = math.acos(r / (cy - tip_y))
    left = (cx - r * math.sin(alpha), cy - r * math.cos(alpha))
    right = (cx + r * math.sin(alpha), cy - r * math.cos(alpha))
    d.polygon([(cx, tip_y), left, (cx, cy), right], fill=255)
    return mask.filter(ImageFilter.GaussianBlur(2))


def draw_placeholder() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # rounded-square background, deep blue to violet
    bg = _vertical_gradient(S, (28, 36, 84, 255), (92, 52, 160, 255))
    plate = Image.new("L", (S, S), 0)
    ImageDraw.Draw(plate).rounded_rectangle((24, 24, S - 24, S - 24), radius=230, fill=255)
    img.paste(bg, (0, 0), plate)

    # soft glow behind the droplet
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((S * 0.2, S * 0.28, S * 0.8, S * 0.88), fill=(120, 190, 255, 110))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(70)))

    # glass droplet: translucent body, brighter rim, specular highlight
    mask = _droplet_mask(S)
    body = _vertical_gradient(S, (210, 235, 255, 235), (120, 170, 255, 190))
    drop = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    drop.paste(body, (0, 0), mask)
    rim_inner = mask.filter(ImageFilter.MinFilter(21))
    rim = ImageChops.subtract(mask, rim_inner)
    rim_layer = Image.new("RGBA", (S, S), (255, 255, 255, 0))
    rim_layer.putalpha(rim.point(lambda v: int(v * 0.85)))
    drop = Image.alpha_composite(drop, rim_layer)
    spec = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(spec).ellipse((S * 0.33, S * 0.47, S * 0.40, S * 0.58), fill=(255, 255, 255, 190))
    drop = Image.alpha_composite(drop, spec.filter(ImageFilter.GaussianBlur(10)))
    img = Image.alpha_composite(img, drop)

    # down arrow inside the droplet
    arrow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    a = ImageDraw.Draw(arrow)
    cx = S / 2
    a.rounded_rectangle((cx - 34, S * 0.45, cx + 34, S * 0.66), radius=30, fill=(34, 48, 120, 245))
    a.polygon([(cx - 120, S * 0.62), (cx + 120, S * 0.62), (cx, S * 0.78)], fill=(34, 48, 120, 245))
    img = Image.alpha_composite(img, arrow.filter(ImageFilter.GaussianBlur(1.2)))
    return img


def main() -> int:
    if SOURCE_PNG.is_file():
        base = Image.open(SOURCE_PNG).convert("RGBA")
        origin = SOURCE_PNG.relative_to(ROOT)
    else:
        base = draw_placeholder()
        origin = "generated placeholder"
    if base.width != base.height:
        side = max(base.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(base, ((side - base.width) // 2, (side - base.height) // 2))
        base = square
    OUT_ICO.parent.mkdir(parents=True, exist_ok=True)
    base.save(OUT_ICO, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"icon.ico written from {origin} ({OUT_ICO.stat().st_size // 1024} KB, sizes {SIZES})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
