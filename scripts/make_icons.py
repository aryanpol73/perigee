"""App icons from the artwork in web/brand/icon-source.webp (a rounded square on black).

Writes web/public/icons/: icon-{192,512}.png (the rounded square, transparent corners),
maskable-{192,512}.png (full bleed, the artwork inside Android's 80% safe circle),
apple-touch-icon.png (180, square: iOS rounds it), favicon-{32,48}.png and logo-96.png
(the in-app logo). Needs Pillow, which nothing else uses: pip install pillow.

    python scripts/make_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "web" / "brand" / "icon-source.webp"
OUT = ROOT / "web" / "public" / "icons"
BG_EDGE = (2, 10, 26)  # the artwork's space colour near its edges
BG_CENTRE = (8, 34, 80)


def bounds(im: Image.Image) -> tuple[int, int, int, int]:
    """The rounded square: the box of everything brighter than the black surround."""
    grey = im.convert("L").point(lambda v: 255 if v > 14 else 0)
    box = grey.getbbox()
    assert box, "the artwork is empty"
    return box


def rounded(im: Image.Image, radius: float) -> Image.Image:
    # Drawn at 4x and reduced, for a smooth edge.
    w, h = im.size
    mask = Image.new("L", (w * 4, h * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * 4 - 1, h * 4 - 1), radius=radius * 4, fill=255)
    out = im.convert("RGBA")
    out.putalpha(mask.resize((w, h), Image.LANCZOS))
    return out


def radial(size: int) -> Image.Image:
    g = Image.radial_gradient("L").resize((size, size), Image.LANCZOS)  # 0 centre -> 255 edge
    return Image.composite(Image.new("RGB", (size, size), BG_EDGE), Image.new("RGB", (size, size), BG_CENTRE), g)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    src = Image.open(SOURCE).convert("RGB")
    x0, y0, x1, y1 = bounds(src)
    side = min(x1 - x0, y1 - y0)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    square = src.crop((cx - side // 2, cy - side // 2, cx + side // 2, cy + side // 2))
    # Inside the rounded corners: the artwork without its frame, for full-bleed icons.
    inset = round(side * 0.075)
    inner = square.crop((inset, inset, side - inset, side - inset))

    any_icon = rounded(square.crop((3, 3, side - 3, side - 3)), side * 0.215)
    for size in (512, 192, 96, 48, 32):
        name = {96: "logo-96.png", 48: "favicon-48.png", 32: "favicon-32.png"}.get(size, f"icon-{size}.png")
        any_icon.resize((size, size), Image.LANCZOS).save(OUT / name, optimize=True)

    inner.resize((180, 180), Image.LANCZOS).save(OUT / "apple-touch-icon.png", optimize=True)

    # Maskable: launchers crop to any shape inside the central 80% circle. The artwork
    # is scaled into it over a blurred, darker copy of itself, so the Earth and the
    # space around it run on to the edges.
    big = 1024
    art = round(big * 0.80)
    canvas = Image.blend(inner.resize((big, big), Image.LANCZOS).filter(ImageFilter.GaussianBlur(big * 0.03)), radial(big), 0.45)
    feather = Image.new("L", (art, art), 0)
    edge = round(art * 0.06)
    ImageDraw.Draw(feather).rounded_rectangle((edge, edge, art - edge, art - edge), radius=art * 0.2, fill=255)
    feather = feather.filter(ImageFilter.GaussianBlur(edge * 0.6))
    canvas.paste(inner.resize((art, art), Image.LANCZOS), ((big - art) // 2, (big - art) // 2), feather)
    for size in (512, 192):
        canvas.resize((size, size), Image.LANCZOS).save(OUT / f"maskable-{size}.png", optimize=True)

    # Android's status bar shows a notification's badge as a white silhouette: the
    # artwork's white parts (the P, the orbit and the satellite).
    rgb = square.resize((288, 288), Image.LANCZOS)
    lo = rgb.convert("L").point(lambda v: 255 if v > 200 else 0)
    sat = rgb.convert("HSV").split()[1].point(lambda v: 255 if v < 70 else 0)
    alpha = Image.composite(lo, Image.new("L", lo.size, 0), sat).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(5))  # drops the stars
    badge = Image.new("RGBA", lo.size, (255, 255, 255, 0))
    badge.putalpha(alpha)
    badge.resize((96, 96), Image.LANCZOS).save(OUT / "badge-96.png", optimize=True)
    print(f"wrote {len(list(OUT.glob('*.png')))} icons to {OUT}")


if __name__ == "__main__":
    main()
