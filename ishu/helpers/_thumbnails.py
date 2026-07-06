# Copyright (c) 2025 TheHamkerAlone 
# Licensed under the MIT License.
# This file is part of AloneX

import os
import asyncio
import numpy as np
import random
import aiohttp
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageChops
from collections import Counter
from ishu import config
from ishu.helpers import Track

try:
    from unidecode import unidecode
except ImportError:
    def unidecode(text):
        return text

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_TITLE_PATH = os.path.join(BASE_DIR, "font.ttf")
FONT_INFO_PATH = os.path.join(BASE_DIR, "font2.ttf")
TEMPLATE_PATH = os.path.join(BASE_DIR, "..", "assets", "template.png")

def safe_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

class Thumbnail:
    def __init__(self):
        self.size = (1280, 720)
        self.font_title = safe_font(FONT_TITLE_PATH, 28)
        self.font_info = safe_font(FONT_INFO_PATH, 22)

    async def start(self):
        os.makedirs("cache", exist_ok=True)

        if not os.path.exists(FONT_TITLE_PATH):
            print(f"Missing font: {FONT_TITLE_PATH}")

        if not os.path.exists(FONT_INFO_PATH):
            print(f"Missing font: {FONT_INFO_PATH}")

        if not os.path.exists(TEMPLATE_PATH):
            print(f"Missing template: {TEMPLATE_PATH}")

        return True

    async def save_thumb(self, output_path: str, url: str) -> str:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        for attempt in range(3):
            try:
                if url.startswith("http"):
                    async with aiohttp.ClientSession(headers=headers) as session:
                        async with session.get(url, timeout=15) as resp:
                            if resp.status == 200:
                                content = await resp.read()
                                with open(output_path, "wb") as f:
                                    f.write(content)
                                return output_path
            except Exception as e:
                if attempt == 2:
                    print(f"Error saving thumb: {e}")
                await asyncio.sleep(1)
        return output_path

    def get_dominant_color(self, img):
        # Resize for performance
        small_img = img.resize((50, 50), Image.Resampling.LANCZOS)
        pixels = list(small_img.getdata())
        count = Counter(pixels)
        # Get the most common color
        dominant = count.most_common(1)[0][0]
        return dominant

    def create_rounded_rect(self, size, radius, color):
        w, h = size
        img = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
        color_img = Image.new("RGBA", (w, h), color)
        color_img.putalpha(img)
        return color_img

    def create_glow(self, size, radius, color, intensity=150):
        base = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(base)
        draw.ellipse([0, 0, size[0], size[1]], fill=(*color, intensity))
        for i in range(5):
            base = base.filter(ImageFilter.GaussianBlur(radius / 5))
        return base

    def create_waveform(self, width, height, color):
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Generate random waveform bars
        bar_count = int(width / 6)
        bar_width = 4
        gap = 2
        for i in range(bar_count):
            x = i * (bar_width + gap)
            # Random height with some symmetry
            bar_height = random.randint(int(height * 0.2), int(height * 0.8))
            y_top = (height - bar_height) // 2
            y_bottom = y_top + bar_height
            draw.rectangle([x, y_top, x + bar_width, y_bottom], fill=(*color, 180))
        
        # Blur slightly
        img = img.filter(ImageFilter.GaussianBlur(0.7))
        return img

    async def generate(self, song: Track) -> str:
        try:
            os.makedirs("cache", exist_ok=True)
            temp = f"cache/temp_{song.id}.jpg"
            final_path = f"cache/{song.id}.png"
            if os.path.exists(final_path):
                return final_path

            await self.save_thumb(temp, song.thumbnail)
            
            try:
                src = Image.open(temp).convert("RGBA")
            except Exception:
                try:
                    src = Image.new("RGBA", (1280, 720), (30, 30, 30, 255))
                except Exception:
                    return config.DEFAULT_THUMB

            W, H = self.size

            # --- 1. ENHANCED ALBUM ART ---
            # Enhance sharpness
            enhancer = ImageEnhance.Sharpness(src)
            src_enhanced = enhancer.enhance(1.1)
            # Enhance color
            color_enhancer = ImageEnhance.Color(src_enhanced)
            src_enhanced = color_enhancer.enhance(1.15)
            # Enhance contrast
            contrast_enhancer = ImageEnhance.Contrast(src_enhanced)
            src_enhanced = contrast_enhancer.enhance(1.05)

            # --- 2. BLURRED BACKGROUND WITH COLOR TINT ---
            bg_ratio = W / H
            src_ratio = src.width / src.height
            if src_ratio > bg_ratio:
                new_w = int(src.height * bg_ratio)
                offset = (src.width - new_w) // 2
                bg = src.crop((offset, 0, offset + new_w, src.height))
            else:
                new_h = int(src.width / bg_ratio)
                offset = (src.height - new_h) // 2
                bg = src.crop((0, offset, src.width, offset + new_h))

            bg = bg.resize((W, H), Image.Resampling.LANCZOS)
            bg = bg.filter(ImageFilter.GaussianBlur(35))
            
            # Darken more and add subtle vignette
            bg_overlay = Image.new("RGBA", (W, H), (0, 0, 0, 140))
            bg = Image.alpha_composite(bg, bg_overlay)
            
            # Add vignette
            vignette = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            draw_vignette = ImageDraw.Draw(vignette)
            # Outer ellipse (darkest)
            draw_vignette.ellipse([-100, -100, W + 100, H + 100], fill=(0, 0, 0, 0))
            # Inner ellipse
            draw_vignette.ellipse([200, 100, W - 200, H - 100], fill=(0, 0, 0, 200))
            vignette = vignette.filter(ImageFilter.GaussianBlur(100))
            bg = Image.alpha_composite(bg, vignette)

            # --- 3. COVER ART WITH GLOW AND SHADOW ---
            cover_x, cover_y = 140, 100
            cover_w, cover_h = 520, 520
            cover_radius = 45

            # Glow behind cover art (using dominant color)
            dominant_color = self.get_dominant_color(src_enhanced)[:3]
            glow_size = (cover_w + 120, cover_h + 120)
            glow_img = self.create_glow(glow_size, 60, dominant_color, 100)
            glow_x = cover_x - 60
            glow_y = cover_y - 60
            bg.paste(glow_img, (glow_x, glow_y), glow_img)

            # Large soft shadow
            shadow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            shadow_draw = ImageDraw.Draw(shadow_layer)
            shadow_draw.rounded_rectangle(
                (cover_x + 10, cover_y + 12, cover_x + cover_w + 10, cover_y + cover_h + 12),
                radius=cover_radius + 6,
                fill=(0, 0, 0, 160),
            )
            shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(25))
            bg = Image.alpha_composite(bg, shadow_layer)

            # Cover art
            cover_resized = src_enhanced.resize((cover_w, cover_h), Image.Resampling.LANCZOS)
            cover_mask = Image.new("L", (cover_w, cover_h), 0)
            ImageDraw.Draw(cover_mask).rounded_rectangle(
                (0, 0, cover_w, cover_h), radius=cover_radius, fill=255
            )
            bg.paste(cover_resized, (cover_x, cover_y), cover_mask)

            # --- 4. MINIMALIST WAVEFORM ---
            waveform_x = 720
            waveform_y = cover_y + cover_h - 80
            waveform_w = 420
            waveform_h = 60
            waveform = self.create_waveform(waveform_w, waveform_h, (255, 255, 255))
            bg.paste(waveform, (waveform_x, waveform_y), waveform)

            # --- 5. TEXT ---
            draw = ImageDraw.Draw(bg)
            text_x = 720
            text_max_w = 420

            def ellipsize(s, font, max_w):
                if draw.textbbox((0, 0), s, font=font)[2] <= max_w:
                    return s
                lo, hi = 1, len(s)
                best = "…"
                while lo <= hi:
                    mid = (lo + hi) // 2
                    cand = s[:mid].rstrip() + "…"
                    if draw.textbbox((0, 0), cand, font=font)[2] <= max_w:
                        best = cand
                        lo = mid + 1
                    else:
                        hi = mid - 1
                return best

            # Title
            title_str = ellipsize(unidecode(str(song.title)), self.font_title, text_max_w)
            title_y = cover_y + 60
            # Draw subtle text shadow
            draw.text((text_x + 2, title_y + 2), title_str, fill=(0, 0, 0, 150), font=self.font_title)
            draw.text((text_x, title_y), title_str, fill=(255, 255, 255, 255), font=self.font_title)

            # Artist/Channel
            artist_str = ellipsize(unidecode(str(song.channel_name)), self.font_info, text_max_w + 40)
            artist_y = title_y + 48
            # Draw subtle text shadow
            draw.text((text_x + 1, artist_y + 1), artist_str, fill=(0, 0, 0, 100), font=self.font_info)
            draw.text((text_x, artist_y), artist_str, fill=(220, 220, 220, 255), font=self.font_info)

            # --- 6. MUSIC-THEMED ACCENT ---
            # Small music note icon (simple)
            note_x = text_x + text_max_w - 40
            note_y = cover_y + 20
            draw_note = ImageDraw.Draw(bg)
            # Draw a simple note shape
            draw_note.ellipse([note_x, note_y + 18, note_x + 16, note_y + 34], fill=(*dominant_color, 200))
            draw_note.rectangle([note_x + 14, note_y, note_x + 18, note_y + 26], fill=(*dominant_color, 200))
            draw_note.ellipse([note_x - 20, note_y + 8, note_x + 0, note_y + 24], fill=(*dominant_color, 150))
            draw_note.rectangle([note_x - 2, note_y - 10, note_x + 2, note_y + 16], fill=(*dominant_color, 150))
            
            out = bg.convert("RGB")
            out.save(final_path, "PNG", optimize=True, quality=95)

            try:
                if os.path.exists(temp):
                    os.remove(temp)
            except Exception:
                pass

            return final_path

        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
            return config.DEFAULT_THUMB
