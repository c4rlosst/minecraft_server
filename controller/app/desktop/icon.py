"""Turn any picture into the 64x64 PNG Minecraft wants as a server icon (needs Pillow, which the app bundles)."""
import io

from .. import identity


def make_icon(source) -> bytes:
    """source: a file path, a file-like object or a PIL image. Centre-cropped to a square and scaled to 64x64; photos are
    smoothed, small pixel art is kept sharp. Raises ValueError with a plain message when it isn't a usable picture."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        img = source if isinstance(source, Image.Image) else Image.open(source)
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError):
        raise ValueError("That doesn't look like a picture (use a PNG, JPG, GIF, BMP or WEBP file).") from None
    img = ImageOps.exif_transpose(img).convert("RGBA")           # phone photos are often rotated by a flag
    side = min(img.size)
    left, top = (img.width - side) // 2, (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img = img.resize((64, 64), Image.NEAREST if side <= 128 else Image.LANCZOS)
    data = _png(img)
    if len(data) > identity.MAX_ICON_BYTES:                      # a very detailed photo: a 128-colour palette, still 64x64
        data = _png(img.convert("RGB").quantize(colors=128))
    if not identity.icon_ok(data):
        raise ValueError("That picture can't be made small enough. Try a simpler one.")
    return data


def _png(img) -> bytes:
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
