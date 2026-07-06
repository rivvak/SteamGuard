#!/usr/bin/env python3
"""
generate_covers.py

Programmatically generates dark, glassmorphic, high-tech cover-art images
for product cards (SteamGuard, Roblox Copier, Coming Soon) used in a
desktop loader UI with an ImGui blue accent (#3B82F6) on very dark
backgrounds (#0B0D10).

Pure PIL/Pillow — no external image generation APIs.

Output: 600x360 PNGs (2x a 300x180 card art area) written to
/home/user/workspace/SteamGuard/assets/
"""

import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageChops

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

W, H = 600, 360
OUT_DIR = "/home/user/workspace/SteamGuard/assets"

DARK_BASE = (11, 13, 16)        # #0B0D10
BLUE_ACCENT = (59, 130, 246)    # #3B82F6
GREEN_ACCENT = (34, 197, 94)    # security / money green
PURPLE_ACCENT = (168, 85, 247)  # creation / animation purple
GREY_ACCENT = (148, 163, 184)   # muted grey-blue for "coming soon"

random.seed(42)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Bold.ttf",
    "/usr/share/fonts/truetype/JetBrainsMono/JetBrainsMono-Bold.ttf",
    # Noto Sans Mono Condensed Bold closely matches "bold condensed monospace"
    "/usr/share/fonts/truetype/noto/NotoSansMono-CondensedBold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansMono-SemiCondensedBlack.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
]


def find_font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def clamp(v, lo=0, hi=255):
    return max(lo, min(hi, int(v)))


def lerp_color(c1, c2, t):
    return tuple(clamp(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def radial_gradient_corner(size, color_inner, color_outer, center, radius, base):
    """Create an RGBA image with a radial gradient emanating from `center`,
    blended over `base` background color, fading to transparent/base at radius."""
    w, h = size
    grad = Image.new("RGB", size, base)
    px = grad.load()
    cx, cy = center
    max_d = radius
    # Downsample for speed then upscale (gradient is smooth, no need for full res loop)
    small_w, small_h = w // 3, h // 3
    small = Image.new("RGB", (small_w, small_h), base)
    spx = small.load()
    sx_scale = w / small_w
    sy_scale = h / small_h
    for y in range(small_h):
        for x in range(small_w):
            rx = x * sx_scale
            ry = y * sy_scale
            d = math.hypot(rx - cx, ry - cy)
            t = min(1.0, d / max_d)
            # ease-out for a nicer falloff
            t_eased = t ** 1.4
            col = lerp_color(color_inner, color_outer, t_eased)
            # blend further toward base as t exceeds 1 isn't needed since clamped
            final = lerp_color(col, base, min(1.0, t_eased))
            spx[x, y] = final
    grad = small.resize((w, h), Image.BICUBIC)
    return grad


def add_scan_lines(base_img, opacity=8, spacing=20, angle=45, line_color=(255, 255, 255)):
    """Overlay faint diagonal scan lines onto base_img (RGBA)."""
    w, h = base_img.size
    # Make an oversized canvas so rotated lines cover the whole frame
    diag = int(math.hypot(w, h)) + spacing * 2
    lines_layer = Image.new("L", (diag, diag), 0)
    ldraw = ImageDraw.Draw(lines_layer)
    alpha_val = int(255 * (opacity / 100.0))
    for x in range(0, diag, spacing):
        ldraw.line([(x, 0), (x, diag)], fill=alpha_val, width=1)
    lines_layer = lines_layer.rotate(angle, resample=Image.BICUBIC, expand=False)
    # crop centered
    lx = (diag - w) // 2
    ly = (diag - h) // 2
    lines_layer = lines_layer.crop((lx, ly, lx + w, ly + h))

    color_layer = Image.new("RGBA", (w, h), line_color + (0,))
    color_layer.putalpha(lines_layer)
    return Image.alpha_composite(base_img.convert("RGBA"), color_layer)


def glow_shape(size, draw_fn, blur_radius=18, color=(59, 130, 246), alpha=140):
    """Render a shape via draw_fn(draw) onto a transparent layer of `size`,
    then return a glow (blurred colored copy) and the crisp layer separately."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    draw_fn(d)
    # Recolor alpha channel with desired color+alpha for glow
    r, g, b, a = layer.split()
    glow_alpha = a.point(lambda p: int(p * (alpha / 255.0)))
    glow_colored = Image.new("RGBA", size, color + (0,))
    glow_colored.putalpha(glow_alpha)
    glow = glow_colored.filter(ImageFilter.GaussianBlur(blur_radius))
    return layer, glow


def draw_hexagon(draw, cx, cy, r, outline, width=4, fill=None, rotation=0):
    pts = []
    for i in range(6):
        ang = math.radians(60 * i + rotation)
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    if fill:
        draw.polygon(pts, fill=fill)
    draw.polygon(pts, outline=outline, width=width)
    return pts


def draw_shield(draw, cx, cy, w, h, outline, width=4, fill=None):
    """Simple shield / Steam-like guard shape."""
    top = cy - h / 2
    bottom = cy + h / 2
    left = cx - w / 2
    right = cx + w / 2
    pts = [
        (left, top),
        (right, top),
        (right, top + h * 0.55),
        (cx, bottom),
        (left, top + h * 0.55),
    ]
    if fill:
        draw.polygon(pts, fill=fill)
    draw.polygon(pts, outline=outline, width=width)
    # inner circle suggestion (like Steam icon center)
    inner_r = w * 0.18
    bbox = [cx - inner_r, cy - h * 0.12 - inner_r, cx + inner_r, cy - h * 0.12 + inner_r]
    draw.ellipse(bbox, outline=outline, width=max(2, width - 1))
    return pts


def draw_iso_cube(draw, cx, cy, size, outline, width=4, top_fill=None, left_fill=None, right_fill=None):
    """Draw an isometric-looking 3D cube (Roblox-studio-ish block)."""
    s = size
    # top face (rhombus)
    top = [
        (cx, cy - s),
        (cx + s * 0.87, cy - s * 0.5),
        (cx, cy),
        (cx - s * 0.87, cy - s * 0.5),
    ]
    # left face
    left = [
        (cx - s * 0.87, cy - s * 0.5),
        (cx, cy),
        (cx, cy + s),
        (cx - s * 0.87, cy + s * 0.5),
    ]
    # right face
    right = [
        (cx, cy),
        (cx + s * 0.87, cy - s * 0.5),
        (cx + s * 0.87, cy + s * 0.5),
        (cx, cy + s),
    ]
    if right_fill:
        draw.polygon(right, fill=right_fill)
    if left_fill:
        draw.polygon(left, fill=left_fill)
    if top_fill:
        draw.polygon(top, fill=top_fill)
    draw.polygon(top, outline=outline, width=width)
    draw.polygon(left, outline=outline, width=width)
    draw.polygon(right, outline=outline, width=width)
    return top, left, right


def draw_lock(draw, cx, cy, w, h, outline, width=4, fill=None):
    """Simple padlock icon: shackle arc + body rectangle."""
    body_top = cy - h * 0.05
    body_bottom = cy + h / 2
    body_left = cx - w / 2
    body_right = cx + w / 2
    body_box = [body_left, body_top, body_right, body_bottom]
    if fill:
        draw.rounded_rectangle(body_box, radius=w * 0.12, fill=fill)
    draw.rounded_rectangle(body_box, radius=w * 0.12, outline=outline, width=width)

    # shackle (arc)
    shackle_w = w * 0.62
    shackle_h = h * 0.62
    arc_box = [cx - shackle_w / 2, cy - h * 0.55, cx + shackle_w / 2, cy - h * 0.55 + shackle_h]
    draw.arc(arc_box, start=180, end=360, fill=outline, width=width)
    # vertical sides connecting arc to body
    draw.line([(cx - shackle_w / 2, cy - h * 0.55 + shackle_h / 2),
               (cx - shackle_w / 2, body_top)], fill=outline, width=width)
    draw.line([(cx + shackle_w / 2, cy - h * 0.55 + shackle_h / 2),
               (cx + shackle_w / 2, body_top)], fill=outline, width=width)

    # keyhole
    key_r = w * 0.06
    key_cx, key_cy = cx, (body_top + body_bottom) / 2 - h * 0.05
    draw.ellipse([key_cx - key_r, key_cy - key_r, key_cx + key_r, key_cy + key_r], fill=outline)
    draw.polygon([
        (key_cx - key_r * 0.5, key_cy),
        (key_cx + key_r * 0.5, key_cy),
        (key_cx + key_r * 0.9, key_cy + h * 0.12),
        (key_cx - key_r * 0.9, key_cy + h * 0.12),
    ], fill=outline)


def add_particles(img, count, center_region, color, seed_offset=0):
    """Add small glowing accent dots in a given region (x0,y0,x1,y1)."""
    rnd = random.Random(1000 + seed_offset)
    w, h = img.size
    dots_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dots_layer)
    x0, y0, x1, y1 = center_region
    for _ in range(count):
        x = rnd.uniform(x0, x1)
        y = rnd.uniform(y0, y1)
        r = rnd.uniform(1.5, 4.5)
        alpha = rnd.randint(90, 220)
        dd.ellipse([x - r, y - r, x + r, y + r], fill=color + (alpha,))
    glow = dots_layer.filter(ImageFilter.GaussianBlur(3))
    combined = Image.alpha_composite(glow, dots_layer)
    return Image.alpha_composite(img.convert("RGBA"), combined)


def draw_text_with_glow(img, text, pos, font, text_color=(255, 255, 255), alpha=204,
                          glow_color=BLUE_ACCENT, glow_alpha=160, blur=6):
    """Draw bottom-left product-name text with a soft glow behind it."""
    w, h = img.size
    txt_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    td = ImageDraw.Draw(txt_layer)
    td.text(pos, text, font=font, fill=text_color + (alpha,))

    # glow layer: colored blur of the same glyph mask
    r, g, b, a = txt_layer.split()
    glow_a = a.point(lambda p: int(p * (glow_alpha / 255.0)))
    glow_layer = Image.new("RGBA", (w, h), glow_color + (0,))
    glow_layer.putalpha(glow_a)
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(blur))

    out = Image.alpha_composite(img.convert("RGBA"), glow_layer)
    out = Image.alpha_composite(out, txt_layer)
    return out


def vignette(img, strength=90):
    """Subtle dark vignette to focus attention centrally / add depth."""
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([-w * 0.25, -h * 0.3, w * 1.25, h * 1.3], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(80))
    dark = Image.new("RGBA", (w, h), (0, 0, 0, strength))
    inv_mask = ImageChops.invert(mask)
    dark.putalpha(inv_mask.point(lambda p: int(p * (strength / 255.0))))
    return Image.alpha_composite(img.convert("RGBA"), dark)


def fit_text_font(text, max_width, start_size=64, min_size=28):
    size = start_size
    font = find_font(size)
    tmp = Image.new("RGB", (10, 10))
    d = ImageDraw.Draw(tmp)
    while size > min_size:
        font = find_font(size)
        bbox = d.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        if w <= max_width:
            break
        size -= 2
    return font


# ----------------------------------------------------------------------------
# Cover builders
# ----------------------------------------------------------------------------

def build_base(gradient_inner, gradient_outer):
    grad = radial_gradient_corner(
        (W, H),
        color_inner=gradient_inner,
        color_outer=gradient_outer,
        center=(W * 0.22, H * 0.18),
        radius=W * 0.85,
        base=DARK_BASE,
    ).convert("RGBA")
    return grad


def build_steamguard():
    img = build_base(gradient_inner=(20, 70, 55), gradient_outer=DARK_BASE)

    # secondary subtle blue glow bottom-right for depth
    blue_glow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(blue_glow_layer)
    bd.ellipse([W * 0.55, H * 0.35, W * 1.15, H * 1.05], fill=BLUE_ACCENT + (40,))
    blue_glow_layer = blue_glow_layer.filter(ImageFilter.GaussianBlur(60))
    img = Image.alpha_composite(img, blue_glow_layer)

    # scan lines
    img = add_scan_lines(img, opacity=8, spacing=20, angle=45)

    # central shield mark with blue glow
    cx, cy = W * 0.36, H * 0.46
    shield_layer, shield_glow = glow_shape(
        (W, H),
        lambda d: draw_shield(d, cx, cy, w=210, h=230, outline=BLUE_ACCENT + (230,), width=5,
                               fill=BLUE_ACCENT + (26,)),
        blur_radius=22,
        color=BLUE_ACCENT,
        alpha=170,
    )
    img = Image.alpha_composite(img, shield_glow)
    img = Image.alpha_composite(img, shield_layer)

    # a thin green inner hexagon accent overlapping for "security" flavor
    hex_layer, hex_glow = glow_shape(
        (W, H),
        lambda d: draw_hexagon(d, cx, cy - 4, r=70, outline=GREEN_ACCENT + (210,), width=3, rotation=90),
        blur_radius=14,
        color=GREEN_ACCENT,
        alpha=150,
    )
    img = Image.alpha_composite(img, hex_glow)
    img = Image.alpha_composite(img, hex_layer)

    # green accent particles top-right
    img = add_particles(img, count=14, center_region=(W * 0.62, H * 0.06, W * 0.96, H * 0.32),
                         color=GREEN_ACCENT, seed_offset=1)

    # a couple of soft blue particles mixed in for cohesion
    img = add_particles(img, count=6, center_region=(W * 0.62, H * 0.06, W * 0.96, H * 0.32),
                         color=BLUE_ACCENT, seed_offset=2)

    img = vignette(img, strength=70)

    font = fit_text_font("SteamGuard", max_width=W * 0.46, start_size=58)
    text_y = H - 80
    img = draw_text_with_glow(img, "SteamGuard", (28, text_y), font,
                                text_color=(255, 255, 255), alpha=204,
                                glow_color=BLUE_ACCENT, glow_alpha=170, blur=7)

    # small green accent tick to the left of the text baseline (not overlapping glyphs)
    tick_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    td = ImageDraw.Draw(tick_layer)
    tick_y0 = H - 16
    td.rectangle([28, tick_y0, 28 + 40, tick_y0 + 4], fill=GREEN_ACCENT + (200,))
    tick_glow = tick_layer.filter(ImageFilter.GaussianBlur(6))
    img = Image.alpha_composite(img, tick_glow)
    img = Image.alpha_composite(img, tick_layer)

    return img.convert("RGB")


def build_roblox_copier():
    img = build_base(gradient_inner=(48, 24, 80), gradient_outer=DARK_BASE)

    blue_glow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(blue_glow_layer)
    bd.ellipse([W * 0.55, H * 0.35, W * 1.15, H * 1.05], fill=BLUE_ACCENT + (40,))
    blue_glow_layer = blue_glow_layer.filter(ImageFilter.GaussianBlur(60))
    img = Image.alpha_composite(img, blue_glow_layer)

    img = add_scan_lines(img, opacity=8, spacing=20, angle=45)

    cx, cy = W * 0.36, H * 0.48
    # Purple glow behind cube first (broad glow)
    purple_backglow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pg = ImageDraw.Draw(purple_backglow)
    pg.ellipse([cx - 130, cy - 130, cx + 130, cy + 130], fill=PURPLE_ACCENT + (60,))
    purple_backglow = purple_backglow.filter(ImageFilter.GaussianBlur(40))
    img = Image.alpha_composite(img, purple_backglow)

    cube_layer, cube_glow = glow_shape(
        (W, H),
        lambda d: draw_iso_cube(
            d, cx, cy, size=95,
            outline=BLUE_ACCENT + (230,), width=5,
            top_fill=BLUE_ACCENT + (34,),
            left_fill=PURPLE_ACCENT + (26,),
            right_fill=BLUE_ACCENT + (18,),
        ),
        blur_radius=20,
        color=PURPLE_ACCENT,
        alpha=170,
    )
    img = Image.alpha_composite(img, cube_glow)
    img = Image.alpha_composite(img, cube_layer)

    # small floating secondary cube (top-right-ish of main cube) for "copies" motif
    cx2, cy2 = cx + 118, cy - 88
    cube2_layer, cube2_glow = glow_shape(
        (W, H),
        lambda d: draw_iso_cube(
            d, cx2, cy2, size=42,
            outline=PURPLE_ACCENT + (200,), width=3,
            top_fill=PURPLE_ACCENT + (40,),
            left_fill=PURPLE_ACCENT + (22,),
            right_fill=BLUE_ACCENT + (18,),
        ),
        blur_radius=12,
        color=PURPLE_ACCENT,
        alpha=140,
    )
    img = Image.alpha_composite(img, cube2_glow)
    img = Image.alpha_composite(img, cube2_layer)

    img = add_particles(img, count=14, center_region=(W * 0.62, H * 0.06, W * 0.96, H * 0.32),
                         color=PURPLE_ACCENT, seed_offset=3)
    img = add_particles(img, count=6, center_region=(W * 0.62, H * 0.06, W * 0.96, H * 0.32),
                         color=BLUE_ACCENT, seed_offset=4)

    img = vignette(img, strength=70)

    font = fit_text_font("Roblox Copier", max_width=W * 0.48, start_size=52)
    text_y = H - 80
    img = draw_text_with_glow(img, "Roblox Copier", (28, text_y), font,
                                text_color=(255, 255, 255), alpha=204,
                                glow_color=BLUE_ACCENT, glow_alpha=170, blur=7)

    tick_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    td = ImageDraw.Draw(tick_layer)
    tick_y0 = H - 16
    td.rectangle([28, tick_y0, 28 + 40, tick_y0 + 4], fill=PURPLE_ACCENT + (200,))
    tick_glow = tick_layer.filter(ImageFilter.GaussianBlur(6))
    img = Image.alpha_composite(img, tick_glow)
    img = Image.alpha_composite(img, tick_layer)

    return img.convert("RGB")


def build_coming_soon():
    img = build_base(gradient_inner=(46, 52, 60), gradient_outer=DARK_BASE)

    blue_glow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(blue_glow_layer)
    bd.ellipse([W * 0.55, H * 0.35, W * 1.15, H * 1.05], fill=BLUE_ACCENT + (26,))
    blue_glow_layer = blue_glow_layer.filter(ImageFilter.GaussianBlur(60))
    img = Image.alpha_composite(img, blue_glow_layer)

    # lower opacity scan lines
    img = add_scan_lines(img, opacity=5, spacing=20, angle=45)

    cx, cy = W * 0.32, H * 0.42
    lock_layer, lock_glow = glow_shape(
        (W, H),
        lambda d: draw_lock(d, cx, cy, w=170, h=190, outline=GREY_ACCENT + (200,), width=5,
                             fill=GREY_ACCENT + (22,)),
        blur_radius=20,
        color=BLUE_ACCENT,
        alpha=110,
    )
    img = Image.alpha_composite(img, lock_glow)
    img = Image.alpha_composite(img, lock_layer)

    # no accent dots per spec
    img = vignette(img, strength=80)

    font = fit_text_font("Coming Soon", max_width=W * 0.48, start_size=54)
    img = draw_text_with_glow(img, "Coming Soon", (28, H - 74), font,
                                text_color=(210, 214, 220), alpha=190,
                                glow_color=GREY_ACCENT, glow_alpha=120, blur=7)

    return img.convert("RGB")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    covers = {
        "cover_steamguard.png": build_steamguard,
        "cover_roblox_copier.png": build_roblox_copier,
        "cover_coming_soon.png": build_coming_soon,
    }

    for filename, builder in covers.items():
        img = builder()
        out_path = os.path.join(OUT_DIR, filename)
        img.save(out_path, format="PNG", optimize=True)
        size_kb = os.path.getsize(out_path) / 1024
        print(f"Saved {out_path} ({img.size[0]}x{img.size[1]}, {size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
