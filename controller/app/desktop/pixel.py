"""Minecraft-style pixel art for the SMP Host window, drawn with Pillow (no game assets): inventory panels, slots, stone
buttons, a crafting-table wood panel, the chunky orange title, a blurred blocky landscape, player-head avatars and a grass
block icon. The text font is Monocraft (SIL Open Font License, see assets/Monocraft-OFL.txt)."""
import functools
import hashlib
import os
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ASSETS = Path(__file__).resolve().parent / "assets"
FONT_FILE = ASSETS / "Monocraft.ttf"
TITLE_FONT_FILE = ASSETS / "Monocraft-Black.ttf"
FAMILY = "Monocraft"

BLACK = (0, 0, 0, 255)
PANEL = (198, 198, 198, 255)
PANEL_LIGHT = (255, 255, 255, 255)
PANEL_DARK = (85, 85, 85, 255)
LABEL = "#404040"


def register_fonts() -> str:
    """Make Monocraft usable by Tk on this PC (private to this app). Returns the family name to use."""
    if sys.platform != "win32" or not FONT_FILE.exists():
        return "Consolas"
    import ctypes
    added = 0
    for f in (FONT_FILE, TITLE_FONT_FILE):
        added += ctypes.windll.gdi32.AddFontResourceExW(str(f), 0x10, 0)        # FR_PRIVATE
    return FAMILY if added else "Consolas"


def _rect(d, x, y, w, h, color):
    if w > 0 and h > 0:
        d.rectangle([x, y, x + w - 1, y + h - 1], fill=color)


def _noise(img, amount, seed):
    """Speckle a flat area like a Minecraft texture."""
    rnd = random.Random(seed)
    px = img.load()
    w, h = img.size
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            n = rnd.randint(-amount, amount)
            c = (max(0, min(255, r + n)), max(0, min(255, g + n)), max(0, min(255, b + n)), a)
            for dx in (0, 1):
                for dy in (0, 1):
                    if x + dx < w and y + dy < h:
                        px[x + dx, y + dy] = c
    return img


def _frame(d, w, h, u):
    """Black outline with the corners cut, Minecraft style."""
    _rect(d, 2 * u, 0, w - 4 * u, u, BLACK)
    _rect(d, 2 * u, h - u, w - 4 * u, u, BLACK)
    _rect(d, 0, 2 * u, u, h - 4 * u, BLACK)
    _rect(d, w - u, 2 * u, u, h - 4 * u, BLACK)
    for x, y in ((u, u), (w - 2 * u, u), (u, h - 2 * u), (w - 2 * u, h - 2 * u)):
        _rect(d, x, y, u, u, BLACK)


def _bevel(d, w, h, u, fill, light, dark, depth=2):
    _rect(d, u, 2 * u, w - 2 * u, h - 4 * u, fill)
    _rect(d, 2 * u, u, w - 4 * u, h - 2 * u, fill)
    _rect(d, 2 * u, u, w - (3 + depth) * u, depth * u, light)                 # top
    _rect(d, u, 2 * u, depth * u, h - (3 + depth) * u, light)                 # left
    _rect(d, (2 + depth) * u, h - (1 + depth) * u, w - (3 + depth) * u, depth * u, dark)   # bottom
    _rect(d, w - (1 + depth) * u, (2 + depth) * u, depth * u, h - (3 + depth) * u, dark)   # right


# Every piece below is drawn in Python pixel by pixel, which is slow; the window asks for the same ones again and again (a
# rename rebuilds it), so each is drawn once. Callers only ever read the returned images, never change them.
cached = functools.lru_cache(maxsize=128)


@cached
def panel(w, h, u=2, fill=PANEL, light=PANEL_LIGHT, dark=PANEL_DARK):
    """A grey inventory panel."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    _bevel(d, w, h, u, fill, light, dark)
    _frame(d, w, h, u)
    return img


@cached
def wood_panel(w, h, u=2, seed=3):
    """A crafting-table style panel: oak planks with a light/dark bevel."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rnd = random.Random(seed)
    plank_h = 8 * u
    shades = [(186, 143, 92, 255), (174, 131, 83, 255), (165, 123, 78, 255)]
    for i, y in enumerate(range(0, h, plank_h)):
        _rect(d, 0, y, w, plank_h, shades[i % 3])
        _rect(d, 0, y + plank_h - u, w, u, (120, 86, 52, 255))                 # seam between planks
        x = rnd.randrange(8 * u, max(9 * u, w - 8 * u))
        _rect(d, x, y, u, plank_h - u, (120, 86, 52, 255))                      # plank end
    _noise(img, 7, seed)
    d = ImageDraw.Draw(img)
    _rect(d, u, u, w - 2 * u, 2 * u, (220, 180, 125, 255))
    _rect(d, u, u, 2 * u, h - 2 * u, (220, 180, 125, 255))
    _rect(d, u, h - 3 * u, w - 2 * u, 2 * u, (98, 70, 40, 255))
    _rect(d, w - 3 * u, u, 2 * u, h - 2 * u, (98, 70, 40, 255))
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    _rect(md, u, 2 * u, w - 2 * u, h - 4 * u, 255)
    _rect(md, 2 * u, u, w - 4 * u, h - 2 * u, 255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    _frame(ImageDraw.Draw(out), w, h, u)
    return out


@cached
def plaque(w, h, u=2):
    """The darker wooden name plate on top of a panel ('CRAFTING TABLE')."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    _bevel(d, w, h, u, (139, 96, 55, 255), (176, 128, 78, 255), (84, 57, 31, 255), depth=1)
    _frame(d, w, h, u)
    return img


@cached
def slot(s, u=2, fill=(139, 139, 139, 255)):
    img = Image.new("RGBA", (s, s), fill)
    d = ImageDraw.Draw(img)
    _rect(d, 0, 0, s, u, (55, 55, 55, 255))
    _rect(d, 0, 0, u, s, (55, 55, 55, 255))
    _rect(d, u, s - u, s - u, u, (255, 255, 255, 255))
    _rect(d, s - u, u, u, s - u, (255, 255, 255, 255))
    return img


@cached
def text_field(w, h, u=2):
    """A black text box with a grey border, like Minecraft's."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    for i in range(u):
        d.rectangle([i, i, w - 1 - i, h - 1 - i], outline=(160, 160, 160, 255))
    return img


BUTTON_COLORS = {          # fill, top light, bottom dark, outline
    "normal": ((111, 111, 111, 255), (168, 168, 168, 255), (72, 72, 72, 255), BLACK),
    "hover": ((122, 130, 186, 255), (176, 184, 238, 255), (79, 86, 138, 255), (255, 255, 255, 255)),
    "disabled": ((52, 52, 52, 255), (74, 74, 74, 255), (36, 36, 36, 255), BLACK),
    "green": ((76, 140, 60, 255), (125, 196, 96, 255), (46, 92, 34, 255), BLACK),
    "green_hover": ((92, 166, 74, 255), (150, 222, 120, 255), (56, 110, 42, 255), (255, 255, 255, 255)),
}


@cached
def button(w, h, state="normal", u=2):
    fill, light, dark, outline = BUTTON_COLORS[state]
    img = Image.new("RGBA", (w, h), fill)
    _noise(img, 6, hash((w, h, state)) & 0xffff)
    d = ImageDraw.Draw(img)
    _rect(d, u, u, w - 2 * u, u, light)
    _rect(d, u, u, u, h - 2 * u, light)
    _rect(d, u, h - 3 * u, w - 2 * u, 2 * u, dark)
    d.rectangle([0, 0, w - 1, h - 1], outline=outline, width=u)
    return img


def title(text, k=3, size=18, top=(255, 221, 87), bottom=(228, 98, 18), edge=(58, 30, 11), font_file=None):
    """Chunky orange pixel lettering with a dark outline and drop shadow."""
    try:
        font = ImageFont.truetype(str(font_file or FONT_FILE), size)
    except OSError:
        font = ImageFont.load_default()
    x0, y0, x1, y1 = font.getbbox(text)
    mask = Image.new("L", (x1 - x0 + 4, y1 - y0 + 4), 0)
    ImageDraw.Draw(mask).text((2 - x0, 2 - y0), text, font=font, fill=255)
    mask = mask.point(lambda v: 255 if v >= 100 else 0)
    big = mask.resize((mask.width * k, mask.height * k), Image.NEAREST)
    pad = 2 * k
    W, H = big.width + 2 * pad + k, big.height + 2 * pad + k
    full = Image.new("L", (W, H), 0)
    full.paste(big, (pad, pad))
    outline = full.filter(ImageFilter.MaxFilter(2 * k + 1))
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (W, H), (20, 10, 4, 150))
    out.paste(shadow, (k, k), outline)
    out.paste(Image.new("RGBA", (W, H), edge + (255,)), (0, 0), outline)
    grad = Image.new("RGBA", (W, H))
    gd = ImageDraw.Draw(grad)
    for y in range(H):
        t = max(0.0, min(1.0, (y - pad) / max(1, big.height)))
        t = round(t * 5) / 5                                    # banded, like pixel shading
        gd.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    out.paste(grad, (0, 0), full)
    shine = Image.new("RGBA", (W, H), (255, 246, 200, 255))
    band = Image.new("L", (W, H), 0)
    ImageDraw.Draw(band).rectangle([0, pad, W, pad + k - 1], fill=255)
    out.paste(shine, (0, 0), Image.composite(full, Image.new("L", (W, H), 0), band))
    return out


@cached
def fit_title(text, max_width=560):
    """The chunky title, as big as still fits in max_width (long names shrink)."""
    img = None
    for k, size in ((3, 18), (3, 14), (2, 18), (2, 14), (2, 11), (1, 14)):
        img = title(text, k=k, size=size)
        if img.width <= max_width:
            break
    return img


def background(w, h, seed=7, blur=5):
    """A blocky landscape (sky, clouds, mountains, forest), blurred like a menu backdrop."""
    rnd = random.Random(seed)
    img = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=(int(104 + 80 * t), int(156 + 60 * t), int(232 + 18 * t), 255))
    b = 8
    for _ in range(7):                                          # clouds
        cx, cy = rnd.randrange(0, w), rnd.randrange(10, h // 3)
        for _ in range(rnd.randint(4, 9)):
            _rect(d, cx + rnd.randint(-6, 6) * b, cy + rnd.randint(-1, 1) * b, rnd.randint(4, 9) * b, b * 2,
                  (246, 248, 252, 255))

    def ridge(base, rough, color, step):
        height = base
        for x in range(0, w, step):
            height = max(h // 4, min(h - 20, height + rnd.randint(-rough, rough) * b))
            _rect(d, x, height, step, h - height, color)
    ridge(int(h * 0.45), 2, (122, 146, 176, 255), 16)           # far mountains
    ridge(int(h * 0.55), 2, (88, 128, 82, 255), 16)             # hills
    for _ in range(140):                                        # forest
        x, y = rnd.randrange(0, w), rnd.randrange(int(h * 0.55), h)
        size = rnd.randint(2, 5) * b
        shade = rnd.choice([(46, 94, 42, 255), (56, 110, 48, 255), (38, 80, 36, 255), (70, 124, 58, 255)])
        _rect(d, x, y, size, size, shade)
    _rect(d, 0, h - 3 * b, w, 3 * b, (60, 104, 52, 255))
    img = img.filter(ImageFilter.GaussianBlur(blur))
    return Image.alpha_composite(img, Image.new("RGBA", (w, h), (10, 18, 30, 40)))


@cached
def avatar(name, size=32):
    """A little player head made from the name (always the same for the same name)."""
    h = hashlib.md5(name.lower().encode()).digest()
    skins = [(241, 194, 158), (224, 172, 125), (198, 134, 90), (150, 98, 64), (110, 72, 46)]
    hairs = [(60, 40, 22), (24, 20, 18), (140, 90, 40), (214, 176, 90), (170, 60, 40), (80, 80, 90)]
    eyes = [(60, 90, 200), (60, 140, 70), (90, 60, 40), (40, 40, 40)]
    skin, hair, eye = skins[h[0] % 5], hairs[h[1] % 6], eyes[h[2] % 4]
    img = Image.new("RGBA", (8, 8), skin + (255,))
    d = ImageDraw.Draw(img)
    _rect(d, 0, 0, 8, 2 + (h[3] % 2), hair + (255,))
    _rect(d, 0, 0, 1, 4, hair + (255,))
    _rect(d, 7, 0, 1, 4, hair + (255,))
    for x in (1, 5):
        _rect(d, x, 4, 1, 1, (255, 255, 255, 255))
        _rect(d, x + 1, 4, 1, 1, eye + (255,))
    _rect(d, 3, 6, 2, 1, (120, 60, 50, 255))
    return img.resize((size, size), Image.NEAREST)


@cached
def check_mark(s, u=2):
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(2, 6), (3, 7), (4, 8), (5, 7), (6, 6), (7, 5), (8, 4), (9, 3)]
    k = max(1, s // 12)
    for x, y in pts:
        _rect(d, x * k, y * k, 2 * k, 2 * k, (90, 200, 70, 255))
    return img


@cached
def grass_block(size=16):
    """The app icon: a grass block."""
    rnd = random.Random(5)
    img = Image.new("RGBA", (16, 16))
    px = img.load()
    for y in range(16):
        for x in range(16):
            if y < 4 or (y < 6 and rnd.random() < 0.45 - (y - 4) * 0.2):
                base = (95, 159, 53) if rnd.random() < 0.7 else (78, 138, 44)
            else:
                base = (134, 96, 67) if rnd.random() < 0.65 else (115, 80, 54)
                if rnd.random() < 0.08:
                    base = (150, 150, 150)
            px[x, y] = base + (255,)
    return img.resize((size, size), Image.NEAREST)


def save_icon(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    grass_block(256).save(path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])
