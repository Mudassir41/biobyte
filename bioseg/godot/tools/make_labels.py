"""Generate printed packaging textures for the twin's products (fictional brands, Indian FMCG style).

Run: .venv/bin/python twin/tools/make_labels.py   -> twin/assets/labels/<SKU>.png
Each label has what real Indian packs carry: brand, variant, net weight, veg mark, MRP line, barcode.
"""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parents[1] / "assets" / "labels"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_R = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# sku: (size, brand, variant, weight, mrp, bg, accent, text, layout)
PRODUCTS = {
    "RICE-5KG": ((512, 640), "KAVERI GOLD", "Sona Masoori Rice", "5 kg", 345, "#f4f1e6", "#1f7a3a", "#14532d", "sack"),
    "ATTA-5KG": ((512, 640), "DHANYA", "Chakki Fresh Atta", "5 kg", 270, "#f2c14e", "#9a3412", "#7c2d12", "sack"),
    "OIL-1L": ((1024, 300), "SURYAKANTI", "Refined Sunflower Oil", "1 L", 155, "#facc15", "#1d4ed8", "#1e3a8a", "wrap"),
    "OIL-5L": ((420, 460), "SURYAKANTI", "Sunflower Oil  Jar", "5 L", 720, "#fde047", "#1d4ed8", "#1e3a8a", "front"),
    "BISC-GLUCO": ((512, 300), "GLUCO BITE", "Glucose Biscuits", "250 g", 30, "#fbbf24", "#dc2626", "#7f1d1d", "pack"),
    "BISC-BUTTER": ((512, 300), "CRUNCHY", "Butter Cookies", "200 g", 40, "#1e40af", "#f59e0b", "#ffffff", "pack"),
    "TEA-500G": ((400, 520), "NILACHAI", "Premium Assam Tea", "500 g", 260, "#b91c1c", "#facc15", "#ffffff", "front"),
    "COFFEE-200G": ((1024, 300), "COORG BREW", "Filter Coffee 80:20", "200 g", 340, "#3f2a1d", "#d4a373", "#fef3c7", "wrap"),
    # decorative aisles (not load-cell tracked)
    "SOAP": ((400, 260), "CHANDAN", "Sandal Bathing Bar", "4x100 g", 160, "#fef3c7", "#b45309", "#78350f", "pack"),
    "DETERGENT": ((420, 520), "SAFEWASH", "Matic Detergent Powder", "1 kg", 115, "#2563eb", "#facc15", "#ffffff", "front"),
    "SHAMPOO": ((1024, 300), "KESH SHAKTI", "Herbal Shampoo", "340 ml", 245, "#065f46", "#a7f3d0", "#ecfdf5", "wrap"),
    "NOODLES": ((512, 300), "MAGIC MASALA", "Instant Noodles", "4 pack", 56, "#facc15", "#dc2626", "#7f1d1d", "pack"),
    "CHIPS": ((400, 520), "KURKURI", "Masala Potato Chips", "90 g", 20, "#16a34a", "#fde047", "#ffffff", "front"),
    "MASALA": ((400, 520), "RASOI RANI", "Garam Masala", "100 g", 78, "#7c2d12", "#f97316", "#fff7ed", "front"),
    "TOOTHPASTE": ((512, 300), "DANT KAVACH", "Clove Toothpaste", "150 g", 99, "#dc2626", "#ffffff", "#ffffff", "pack"),
    "JUICE": ((400, 520), "PHAL RAS", "Mango Drink", "1 L", 110, "#f59e0b", "#16a34a", "#ffffff", "front"),
}


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def fit(draw: ImageDraw.ImageDraw, text: str, path: str, max_w: int, start: int) -> ImageFont.FreeTypeFont:
    s = start
    while s > 10:
        f = font(path, s)
        if draw.textlength(text, font=f) <= max_w:
            return f
        s -= 2
    return font(path, s)


def veg_mark(d: ImageDraw.ImageDraw, x: int, y: int, s: int) -> None:
    d.rectangle([x, y, x + s, y + s], outline="#15803d", width=max(2, s // 10))
    r = s // 4
    d.ellipse([x + s // 2 - r, y + s // 2 - r, x + s // 2 + r, y + s // 2 + r], fill="#15803d")


def barcode(d: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, rng: random.Random) -> None:
    d.rectangle([x - 6, y - 6, x + w + 6, y + h + 20], fill="white")
    cx = x
    while cx < x + w:
        bw = rng.choice([1, 2, 2, 3, 4])
        if rng.random() < 0.55:
            d.rectangle([cx, y, cx + bw - 1, y + h], fill="black")
        cx += bw
    d.text((x, y + h + 2), "8 901234 " + "".join(rng.choice("0123456789") for _ in range(6)), fill="black",
           font=font(FONT_R, 12))


def make(sku: str, spec) -> Image.Image:
    (w, h), brand, variant, weight, mrp, bg, accent, fg, layout = spec
    rng = random.Random(sku)
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    # background artwork: diagonal band + soft circles, like printed packs
    d.polygon([(0, int(h * 0.62)), (w, int(h * 0.40)), (w, int(h * 0.58)), (0, int(h * 0.80))], fill=accent)
    for _ in range(6):
        r = rng.randint(w // 10, w // 4)
        cx, cy = rng.randint(0, w), rng.randint(0, h)
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(overlay).ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 28))
        img.paste(overlay, (0, 0), overlay)
    d = ImageDraw.Draw(img)
    pad = w // 16
    if layout == "wrap":  # bottle/jar label: content on the front third, details on the back
        fx0, fx1 = int(w * 0.30), int(w * 0.70)
        fb = fit(d, brand, FONT_B, fx1 - fx0, h // 4)
        d.text(((fx0 + fx1) // 2, int(h * 0.30)), brand, font=fb, fill=fg, anchor="mm", stroke_width=2, stroke_fill=bg)
        fv = fit(d, variant, FONT_B, fx1 - fx0, h // 9)
        d.text(((fx0 + fx1) // 2, int(h * 0.55)), variant, font=fv, fill="white", anchor="mm")
        d.text(((fx0 + fx1) // 2, int(h * 0.80)), weight, font=font(FONT_B, h // 7), fill=fg, anchor="mm")
        veg_mark(d, fx0 - h // 5, h // 10, h // 7)
        d.text((int(w * 0.75), h // 6), f"MRP ₹{mrp}.00\n(incl. of all taxes)\nPkd: 09/2026\nBest before 6 months", font=font(FONT_R, h // 14), fill=fg)
        barcode(d, int(w * 0.04), int(h * 0.30), int(w * 0.18), h // 3, rng)
    else:
        fb = fit(d, brand, FONT_B, w - 2 * pad, h // 6 if layout != "pack" else h // 4)
        d.text((w // 2, int(h * 0.20)), brand, font=fb, fill=fg, anchor="mm", stroke_width=3, stroke_fill=bg)
        fv = fit(d, variant, FONT_B, w - 2 * pad, h // 12 if layout != "pack" else h // 8)
        d.text((w // 2, int(h * 0.38 if layout != "pack" else h * 0.45)), variant, font=fv, fill=fg, anchor="mm")
        # net weight badge
        bw, bh = w // 3, h // 9
        bx, by = w - pad - bw, int(h * 0.84) - bh // 2
        d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=bh // 3, fill="white", outline=accent, width=3)
        d.text((bx + bw // 2, by + bh // 2), weight, font=fit(d, weight, FONT_B, bw - 10, int(bh * 0.7)), fill="black", anchor="mm")
        veg_mark(d, pad, int(h * 0.80), h // 12)
        d.text((pad + h // 10, int(h * 0.80)), f"MRP ₹{mrp}", font=font(FONT_B, max(12, h // 22)), fill=fg)
        if layout in ("sack", "front"):
            # product illustration: a bowl / grains / cup made of simple shapes
            cy = int(h * 0.62)
            d.ellipse([w // 2 - w // 5, cy - h // 14, w // 2 + w // 5, cy + h // 9], fill="white", outline=accent, width=4)
            for _ in range(60):
                gx, gy = rng.randint(w // 2 - w // 6, w // 2 + w // 6), rng.randint(cy - h // 18, cy + h // 30)
                d.ellipse([gx - 4, gy - 2, gx + 4, gy + 2], fill="#fffbeb" if sku != "TEA-500G" else "#7c2d12")
    if layout == "sack":  # woven polypropylene texture
        tex = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        td = ImageDraw.Draw(tex)
        for y in range(0, h, 6):
            td.line([(0, y), (w, y)], fill=(0, 0, 0, 14))
        for x in range(0, w, 6):
            td.line([(x, 0), (x, h)], fill=(255, 255, 255, 10))
        img.paste(tex, (0, 0), tex)
    return img.filter(ImageFilter.SMOOTH)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for sku, spec in PRODUCTS.items():
        make(sku, spec).save(OUT / f"{sku}.png")
    # shelf-edge price tags
    for sku, spec in PRODUCTS.items():
        tag = Image.new("RGB", (256, 96), "#fde047")
        d = ImageDraw.Draw(tag)
        d.text((10, 8), spec[1][:14], font=font(FONT_B, 22), fill="black")
        d.text((10, 40), f"₹{spec[4]}", font=font(FONT_B, 40), fill="#b91c1c")
        d.text((150, 58), spec[3], font=font(FONT_R, 20), fill="black")
        tag.save(OUT / f"tag_{sku}.png")
    print("labels ->", OUT)


if __name__ == "__main__":
    main()
