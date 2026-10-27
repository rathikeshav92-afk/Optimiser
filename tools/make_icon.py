"""Draw the app icon (white lightning bolt on a blue rounded square) as a multi-size .ico. Needs Pillow."""

import sys
from pathlib import Path

from PIL import Image, ImageDraw

BLUE, BLUE_DARK = (47, 111, 237), (30, 78, 190)
BOLT = [(0.58, 0.10), (0.24, 0.56), (0.47, 0.56), (0.40, 0.90), (0.76, 0.42), (0.53, 0.42), (0.62, 0.10)]


def draw(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(size * 0.22)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=r, fill=BLUE_DARK)
    d.rounded_rectangle((0, 0, size - 1, int(size * 0.94)), radius=r, fill=BLUE)
    d.polygon([(x * size, y * size) for x, y in BOLT], fill=(255, 255, 255))
    return img


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "pcoptimizer" / "ui" / "app.ico")
    draw().save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(out)
