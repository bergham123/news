import requests
import xml.etree.ElementTree as ET
import re
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
import arabic_reshaper
from bidi.algorithm import get_display
from datetime import datetime
import os
from typing import List, Dict, Optional
import asyncio
from telegram import Bot
from telegram.error import TelegramError
from openai import OpenAI
from moviepy.editor import *
from moviepy.audio.AudioClip import CompositeAudioClip, concatenate_audioclips

# ==================== ELEVENLABS IMPORTS ====================
from elevenlabs.client import ElevenLabs
import io
import time

# ============================================================
#  RESOLUTION PRESETS  ← change OUTPUT_FORMAT to switch mode
# ============================================================
PRESETS = {
    # name           : (width, height,  label)
    "landscape"      : (1280,  720,   "YouTube / Landscape 16:9"),
    "shorts"         : (1080,  1920,  "YouTube Shorts / Reels 9:16"),
    "square"         : (1080,  1080,  "Instagram Square 1:1"),
    "portrait"       : (1080,  1350,  "Instagram Portrait 4:5"),
    "twitter"        : (1280,  720,   "Twitter / X Card"),
    "facebook"       : (1200,  630,   "Facebook Link Card"),
}

# ─── ✅ CHANGE THIS to switch output format ───────────────────
OUTPUT_FORMAT = "shorts"          # "landscape" | "shorts" | "square" | "portrait"
# ─────────────────────────────────────────────────────────────

WIDTH, HEIGHT = PRESETS[OUTPUT_FORMAT][:2]
IS_VERTICAL   = HEIGHT > WIDTH     # True for Shorts/Reels/Portrait

print(f"📐 Output format: {PRESETS[OUTPUT_FORMAT][2]}  ({WIDTH}×{HEIGHT})")

# ==================== CONFIG ====================
RSS_URL     = "https://www.telegraphe.ma/rss/latest-posts"
LAST_FILE   = "last_news.txt"
OUTPUT_IMAGE = "output.webp"
OUTPUT_VIDEO = "final_news_video.mp4"

VIDEO_START = "video.mp4"
VIDEO_END   = "videoend.mp4"
LOGO_FILE   = "logo.png"          # ← place your logo here


# ==================== AI CONFIG ====================
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# ==================== ELEVENLABS VOICE CONFIG ====================
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

VOICE_ID = "pCKbQ4EPGE06zpEPGNvS"
VOICE_VOLUME = 0.8
VOICE_START_SEC = 7.0

client_eleven = ElevenLabs(api_key=ELEVENLABS_API_KEY)

# ==================== TELEGRAM CONFIG ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = "@natureptv"

# # ==================== AI CONFIG ====================

# ==================== COLORS ====================
BG_COLOR  = (10,  22,  40)
WHITE     = (232, 237, 245)
RED       = (196, 30,  58)
GRAY      = (143, 163, 191)
GOLD      = (212, 175, 55)
DARK_OVERLAY = (0, 0, 0, 180)     # RGBA

SUMMARY_BG_COLORS = [
    (25,  40,  65),
    (45,  30,  55),
    (30,  50,  45),
]

# ==================== FONT SIZES (scale with resolution) ====================


# ==================== FONT SIZES (scale with resolution) ====================
_SCALE = min(WIDTH, HEIGHT) / 700   # baseline 700px

def _fs(size: int) -> int:
    return max(16, int(size * _SCALE))

def _load_font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except Exception as e:
        print(f"⚠️ Font load failed: {path} -> {e}")
        return ImageFont.load_default()

# ==================== FONT PATHS ====================
FONT_DIR = "fonts"

FONT_REGULAR_PATH = os.path.join(FONT_DIR, "Amiri-Regular.ttf")
FONT_BOLD_PATH    = os.path.join(FONT_DIR, "Amiri-Bold.ttf")

# ==================== LOAD FONTS ====================
FONT_TITLE   = _load_font(FONT_BOLD_PATH,    _fs(46))
FONT_SMALL   = _load_font(FONT_REGULAR_PATH, _fs(30))
FONT_TINY    = _load_font(FONT_REGULAR_PATH, _fs(22))
FONT_SUMMARY = _load_font(FONT_BOLD_PATH,    _fs(40))
FONT_LABEL   = _load_font(FONT_BOLD_PATH,    _fs(26))

# ==================== ARABIC HELPERS ====================
def fix_arabic(text: str) -> str:
    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped)

def clean_html(text: str) -> str:
    from html import unescape
    return unescape(re.sub(r'<[^>]+>', '', text))

def text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]

def text_height(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[3] - bbox[1]

def draw_rtl(draw, right_x, y, text, fill, font, shadow=False):
    fixed = fix_arabic(text)
    w = text_width(draw, fixed, font)
    if shadow:
        draw.text((right_x - w + 2, y + 2), fixed, fill=(0, 0, 0, 160), font=font)
    draw.text((right_x - w, y), fixed, fill=fill, font=font)
    return w

def wrap_arabic(draw, text, font, max_width):
    words = text.split()
    lines, current = [], []
    for word in words:
        candidate = " ".join(current + [word])
        if text_width(draw, fix_arabic(candidate), font) <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines

def draw_rtl_multiline(draw, right_x, y, text, fill, font, max_width, line_gap=15, shadow=False):
    lines = wrap_arabic(draw, text, font, max_width)
    lh = font.size + line_gap
    for line in lines:
        draw_rtl(draw, right_x, y, line, fill, font, shadow=shadow)
        y += lh
    return y

# ==================== DATE ====================
def format_date(date_string: str) -> str:
    if not date_string:
        return datetime.now().strftime("%d/%m/%Y")
    fmts = [
        '%a, %d %b %Y %H:%M:%S %z',
        '%a, %d %b %Y %H:%M:%S GMT',
        '%Y-%m-%dT%H:%M:%S%z',
        '%Y-%m-%d %H:%M:%S',
        '%d/%m/%Y', '%m/%d/%Y',
    ]
    for fmt in fmts:
        try:
            return datetime.strptime(date_string.strip(), fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue
    m = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', date_string)
    if m:
        d, mo, y = m.groups()
        return f"{int(d):02d}/{int(mo):02d}/{y}"
    return datetime.now().strftime("%d/%m/%Y")

# ==================== AI SUMMARIZATION ====================
def summarize_with_ai(title: str, description: str) -> List[str]:
    try:
        prompt = f"""
        لديك الخبر التالي:
        العنوان: {title}
        المحتوى: {description}
        
        المطلوب: قم بتلخيص هذا الخبر إلى 3 نقاط رئيسية قصيرة (كل نقطة سطر واحد).
        أخرج النقاط الثلاث فقط، كل نقطة في سطر منفصل.
        """
        response = client.chat.completions.create(
            model="nvidia/nemotron-3-super-120b-a12b:free",
            messages=[{"role": "user", "content": prompt}],
            extra_body={"reasoning": {"enabled": True}}
        )
        ai_response = response.choices[0].message.content
        summaries = [ln.strip() for ln in ai_response.split('\n') if ln.strip()]
        while len(summaries) < 3:
            summaries.append(f"ملخص {len(summaries)+1}")
        return summaries[:3]
    except Exception as e:
        print(f"AI summarization failed: {e}")
        words = title.split()
        if len(words) >= 6:
            return [" ".join(words[:3]), " ".join(words[2:5]),
                    " ".join(words[4:7]) if len(words) > 6 else " ".join(words[-3:])]
        return [title, f"تفاصيل {title}", f"متابعة {title}"]

def generate_question_ai(title: str, description: str) -> str:
    """Generate one short, engaging question about the news description."""
    try:
        prompt = f"""
        بناءً على الخبر التالي:
        العنوان: {title}
        المحتوى: {description}
        
        أخرج سؤالاً واحداً قصيراً وجذاباً (في سطر واحد) يحث على التفكير أو النقاش.
        يجب أن يكون السؤال باللغة العربية فقط، ولا يزيد عن 15 كلمة.
        """
        response = client.chat.completions.create(
            model="nvidia/nemotron-3-super-120b-a12b:free",
            messages=[{"role": "user", "content": prompt}],
            extra_body={"reasoning": {"enabled": True}}
        )
        question = response.choices[0].message.content.strip()
        if not question:
            return "ما هو رأيك في هذا الخبر؟"
        return question
    except Exception as e:
        print(f"AI question generation failed: {e}")
        return "ما هو رأيك في هذا الخبر؟"

# ==================== ELEVENLABS TEXT-TO-SPEECH ====================
def text_to_speech_clip(text: str, volume: float = VOICE_VOLUME) -> Optional[AudioFileClip]:
    """Convert Arabic text to an audio clip using ElevenLabs."""
    try:
        print(f"🔊 Generating speech: {text[:40]}...")
        audio_generator = client_eleven.text_to_speech.convert(
            text=text,
            voice_id=VOICE_ID,
            model_id="eleven_multilingual_v2",
            output_format="mp3_44100_128",
        )
        # Collect bytes from generator
        audio_bytes = b"".join(audio_generator)
        # Save to temporary file
        temp_audio_path = f"temp_voice_{int(time.time())}_{hash(text) & 0xffff}.mp3"
        with open(temp_audio_path, "wb") as f:
            f.write(audio_bytes)
        # Load as moviepy AudioClip and adjust volume
        audio_clip = AudioFileClip(temp_audio_path).volumex(volume)
        return audio_clip
    except Exception as e:
        print(f"⚠️ ElevenLabs TTS failed: {e}")
        return None

# ==================== RSS FETCHING ====================
def load_previous_news() -> set:
    if not os.path.exists(LAST_FILE):
        return set()
    with open(LAST_FILE, 'r', encoding='utf-8') as f:
        return set(ln.strip() for ln in f if ln.strip())

def save_news_id(news_id: str):
    with open(LAST_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{news_id}\n")

def fetch_rss_feed() -> List[Dict]:
    try:
        response = requests.get(RSS_URL, timeout=30)
        response.raise_for_status()
        root  = ET.fromstring(response.content)
        items = []
        for item in root.findall('.//item'):
            title       = item.find('title')
            link        = item.find('link')
            pub_date    = item.find('pubDate')
            description = item.find('description')
            category    = item.find('category')

            image_url = None
            enclosure = item.find('enclosure')
            if enclosure is not None and enclosure.get('type', '').startswith('image/'):
                image_url = enclosure.get('url')
            media = item.find('{http://search.yahoo.com/mrss/}content')
            if media is not None and image_url is None:
                image_url = media.get('url')
            if not image_url and description is not None and description.text:
                m = re.search(r'src="([^"]+)"', description.text)
                if m:
                    image_url = m.group(1)

            clean_title    = clean_html(title.text)       if title       is not None else "No title"
            clean_desc     = clean_html(description.text) if description is not None else ""
            clean_category = clean_html(category.text)    if category    is not None else "أخبار"
            unique_id      = link.text                    if link        is not None else clean_title
            formatted_date = format_date(pub_date.text    if pub_date    is not None else None)

            items.append({
                'id': unique_id, 'title': clean_title, 'description': clean_desc,
                'category': clean_category, 'link': link.text if link else '',
                'date': formatted_date, 'image': image_url
            })
        return items
    except Exception as e:
        print(f"Error fetching RSS: {e}")
        return []

def get_latest_news() -> Optional[Dict]:
    previous_ids = load_previous_news()
    news_items   = fetch_rss_feed()
    new_items    = [i for i in news_items if i['id'] not in previous_ids]
    if not new_items:
        print("No new news items found.")
        return None
    latest = new_items[0]
    save_news_id(latest['id'])
    return latest

# ==================== SHARED DRAWING UTILITIES ====================

def _fetch_image(url: str) -> Optional[Image.Image]:
    """Download and return a PIL image, or None on failure."""
    try:
        r = requests.get(url, stream=True, timeout=10)
        return Image.open(r.raw).convert("RGB")
    except Exception as e:
        print(f"Image download failed: {e}")
        return None

def _paste_cover(canvas: Image.Image, src: Image.Image,
                 x: int = 0, y: int = 0,
                 w: int = None, h: int = None,
                 darken: float = 0.55) -> Image.Image:
    """Paste src onto canvas cropped to (w×h) box at (x,y), optional darken."""
    w = w or (canvas.width  - x)
    h = h or (canvas.height - y)
    ratio_src = src.width  / src.height
    ratio_box = w / h
    if ratio_src > ratio_box:
        new_h = h
        new_w = int(h * ratio_src)
    else:
        new_w = w
        new_h = int(w / ratio_src)
    src_r = src.resize((new_w, new_h), Image.LANCZOS)
    ox = (new_w - w) // 2
    oy = (new_h - h) // 2
    cropped = src_r.crop((ox, oy, ox + w, oy + h))
    if darken < 1.0:
        cropped = ImageEnhance.Brightness(cropped).enhance(darken)
    canvas.paste(cropped, (x, y))
    return canvas

def _gradient_overlay(canvas: Image.Image,
                       start_rgba=(0, 0, 0, 0),
                       end_rgba=(0, 0, 0, 210),
                       vertical: bool = True):
    """Draw a linear gradient overlay."""
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)
    n = canvas.height if vertical else canvas.width
    for i in range(n):
        t = i / (n - 1)
        r = int(start_rgba[0] + t * (end_rgba[0] - start_rgba[0]))
        g = int(start_rgba[1] + t * (end_rgba[1] - start_rgba[1]))
        b = int(start_rgba[2] + t * (end_rgba[2] - start_rgba[2]))
        a = int(start_rgba[3] + t * (end_rgba[3] - start_rgba[3]))
        if vertical:
            draw.line([(0, i), (canvas.width, i)], fill=(r, g, b, a))
        else:
            draw.line([(i, 0), (i, canvas.height)], fill=(r, g, b, a))
    return Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")

def _paste_logo(canvas: Image.Image, padding: int = 20, max_h: int = None):
    """Paste logo.png top-left if the file exists."""
    if not os.path.exists(LOGO_FILE):
        return canvas
    try:
        logo = Image.open(LOGO_FILE).convert("RGBA")
        max_h = max_h or int(canvas.height * 0.09)
        ratio = max_h / logo.height
        logo  = logo.resize((int(logo.width * ratio), max_h), Image.LANCZOS)
        # Paste with alpha mask
        temp = canvas.convert("RGBA")
        temp.paste(logo, (padding, padding), logo)
        return temp.convert("RGB")
    except Exception as e:
        print(f"Logo paste failed: {e}")
        return canvas

def _accent_bar(draw: ImageDraw.ImageDraw, x, y, w, h=4, color=None):
    color = color or RED
    draw.rectangle([x, y, x + w, y + h], fill=color)

# ==================== MAIN IMAGE ====================
def create_main_image(news: Dict) -> str:
    """
    Layout adapts automatically:
      • Landscape  → left half = photo, right half = text panel
      • Vertical   → full-bleed photo, gradient overlay, text at bottom
    """
    canvas = Image.new("RGB", (WIDTH, HEIGHT), BG_COLOR)
    news_img = _fetch_image(news["image"]) if news.get("image") else None

    if IS_VERTICAL:
        # ── VERTICAL layout (Shorts / Reels / Portrait) ──────────────
        if news_img:
            canvas = _paste_cover(canvas, news_img, darken=0.45)
        canvas = _gradient_overlay(canvas,
                                   start_rgba=(0, 0, 0, 0),
                                   end_rgba=(10, 22, 40, 240))

        draw    = ImageDraw.Draw(canvas)
        PAD     = int(WIDTH * 0.06)
        RIGHT   = WIDTH - PAD
        MAX_W   = WIDTH - PAD * 2
        TEXT_TOP = int(HEIGHT * 0.50)

        # category pill
        cat_fixed = fix_arabic(news["category"])
        cat_w     = text_width(draw, cat_fixed, FONT_LABEL)
        pill_pad  = 18
        pill_x    = RIGHT - cat_w - pill_pad * 2
        pill_y    = TEXT_TOP
        pill_h    = FONT_LABEL.size + pill_pad
        draw.rounded_rectangle([pill_x, pill_y, RIGHT, pill_y + pill_h],
                                radius=pill_h // 2, fill=RED)
        draw.text((pill_x + pill_pad, pill_y + pill_pad // 2),
                  cat_fixed, fill=WHITE, font=FONT_LABEL)

        y = pill_y + pill_h + int(HEIGHT * 0.03)
        _accent_bar(draw, WIDTH - MAX_W - PAD, y, MAX_W)
        y += 14
        y = draw_rtl_multiline(draw, RIGHT, y, news["title"],
                               WHITE, FONT_TITLE, MAX_W, line_gap=14, shadow=True)
        y += int(HEIGHT * 0.025)
        draw_rtl(draw, RIGHT, y, news["date"], GRAY, FONT_TINY)

    else:
        # ── LANDSCAPE layout ─────────────────────────────────────────
        PHOTO_W = int(WIDTH * 0.50)
        if news_img:
            canvas = _paste_cover(canvas, news_img, w=PHOTO_W, h=HEIGHT, darken=0.75)

        # right text panel – slight dark tint
        draw = ImageDraw.Draw(canvas)
        draw.rectangle([PHOTO_W, 0, WIDTH, HEIGHT], fill=(*BG_COLOR, 245))

        PAD   = int(WIDTH * 0.04)
        RIGHT = WIDTH - PAD
        MAX_W = WIDTH - PHOTO_W - PAD * 2

        # vertical red stripe
        draw.rectangle([PHOTO_W, 0, PHOTO_W + 5, HEIGHT], fill=RED)

        y = int(HEIGHT * 0.12)
        draw_rtl(draw, RIGHT, y, "السفير",       RED,  FONT_TITLE)
        y += FONT_TITLE.size + 10
        draw_rtl(draw, RIGHT, y, "مركز الإعلام", GRAY, FONT_SMALL)
        y += FONT_SMALL.size + int(HEIGHT * 0.06)

        _accent_bar(draw, PHOTO_W + PAD, y, MAX_W)
        y += 12
        y = draw_rtl_multiline(draw, RIGHT, y, news["title"],
                               WHITE, FONT_TITLE, MAX_W, line_gap=12)
        y += int(HEIGHT * 0.04)

        cat_fixed = fix_arabic(news["category"])
        cat_w     = text_width(draw, cat_fixed, FONT_SMALL)
        pill_pad  = 14
        pill_x    = RIGHT - cat_w - pill_pad * 2
        pill_y    = y
        pill_h    = FONT_SMALL.size + pill_pad
        draw.rounded_rectangle([pill_x, pill_y, RIGHT, pill_y + pill_h],
                                radius=pill_h // 2, fill=RED)
        draw.text((pill_x + pill_pad, pill_y + pill_pad // 2),
                  cat_fixed, fill=WHITE, font=FONT_SMALL)

        y = pill_y + pill_h + int(HEIGHT * 0.04)
        draw_rtl(draw, RIGHT, y, news["date"], GRAY, FONT_TINY)

    canvas = _paste_logo(canvas)
    canvas.save(OUTPUT_IMAGE, "WEBP", quality=95)
    return OUTPUT_IMAGE


# ==================== SUMMARY IMAGES ====================
def create_summary_image(summary_text: str, index: int,
                          news_image_url: str = None) -> str:
    """
    Vertical  → full-bleed sharp photo + bottom card
    Landscape → split: left photo strip, right text
    """
    canvas   = Image.new("RGB", (WIDTH, HEIGHT), SUMMARY_BG_COLORS[index % 3])
    news_img = _fetch_image(news_image_url) if news_image_url else None

    # Determine label and header based on index (0,1,2 = summaries, 3 = question)
    if index == 3:
        point_label = "❓ "
        header_text = "سؤال الخبر"
    else:
        point_label = ["① ", "② ", "③ "][index]
        header_text = "ملخص الخبر"

    if IS_VERTICAL:
        # ── VERTICAL summary layout (BLUR REMOVED) ─────────────────---
        if news_img:
            # Directly paste the sharp image, darkened (no blur)
            canvas = _paste_cover(canvas, news_img, darken=0.35)
        canvas = _gradient_overlay(canvas,
                                   start_rgba=(0, 0, 0, 30),
                                   end_rgba=(10, 22, 40, 255))

        draw  = ImageDraw.Draw(canvas)
        PAD   = int(WIDTH * 0.07)
        RIGHT = WIDTH - PAD
        MAX_W = WIDTH - PAD * 2

        # Card background
        CARD_TOP = int(HEIGHT * 0.42)
        draw.rounded_rectangle([PAD // 2, CARD_TOP, WIDTH - PAD // 2, HEIGHT - PAD // 2],
                                radius=28, fill=(15, 28, 50, 230))

        y = CARD_TOP + int(HEIGHT * 0.04)

        # Header
        draw_rtl(draw, RIGHT, y, header_text, RED, FONT_SMALL)
        y += FONT_SMALL.size + 6
        _accent_bar(draw, PAD, y, MAX_W, h=3)
        y += 16

        # Point / Question label (left-aligned)
        label_color = [RED, GOLD, GRAY][index % 3] if index != 3 else GOLD
        draw.text((PAD, y), point_label, fill=label_color, font=FONT_TITLE)
        y += FONT_TITLE.size + 10

        draw_rtl_multiline(draw, RIGHT, y, summary_text,
                           WHITE, FONT_SUMMARY, MAX_W, line_gap=18, shadow=True)

    else:
        # ── LANDSCAPE summary layout (BLUR REMOVED) ─────────────────--
        STRIP_W = int(WIDTH * 0.35)
        if news_img:
            # Directly paste the sharp image, darkened (no blur)
            canvas = _paste_cover(canvas, news_img, w=STRIP_W, h=HEIGHT, darken=0.5)

        draw  = ImageDraw.Draw(canvas)
        PAD   = int(WIDTH * 0.04)
        RIGHT = WIDTH - PAD
        MAX_W = WIDTH - STRIP_W - PAD * 2

        # Divider stripe
        draw.rectangle([STRIP_W, 0, STRIP_W + 5, HEIGHT],
                       fill=[RED, GOLD, GRAY][index % 3] if index != 3 else GOLD)

        y = int(HEIGHT * 0.15)
        draw_rtl(draw, RIGHT, y, header_text, RED, FONT_TITLE)
        y += FONT_TITLE.size + 8
        _accent_bar(draw, STRIP_W + PAD, y, MAX_W, h=3)
        y += 20

        label_color = [RED, GOLD, GRAY][index % 3] if index != 3 else GOLD
        draw.text((STRIP_W + PAD, y), point_label, fill=label_color, font=FONT_TITLE)
        y += FONT_TITLE.size + 12

        draw_rtl_multiline(draw, RIGHT, y, summary_text,
                           WHITE, FONT_SUMMARY, MAX_W, line_gap=20)

    canvas = _paste_logo(canvas)
    out_path = f"summary_{index+1}.webp"
    canvas.save(out_path, "WEBP", quality=95)
    return out_path


# ==================== RESIZE IMAGE TO MATCH VIDEO ====================
def resize_to_match_video(image_path: str, target_width: int, target_height: int) -> str:
    img     = Image.open(image_path)
    new_img = Image.new("RGB", (target_width, target_height), BG_COLOR)

    img_ratio    = img.width  / img.height
    target_ratio = target_width / target_height

    if img_ratio > target_ratio:
        new_width  = target_width
        new_height = int(target_width / img_ratio)
    else:
        new_height = target_height
        new_width  = int(target_height * img_ratio)

    img_resized = img.resize((new_width, new_height), Image.LANCZOS)
    x = (target_width  - new_width)  // 2
    y = (target_height - new_height) // 2
    new_img.paste(img_resized, (x, y))

    temp_path = f"temp_resized_{os.path.basename(image_path)}"
    new_img.save(temp_path, "WEBP", quality=95)
    return temp_path


# ==================== ENHANCED VIDEO CREATION WITH VOICEOVER ====================
def create_simple_video(image_paths: List[str], voiceover_texts: List[str] = None) -> str:
    clips = []
    target_width  = WIDTH
    target_height = HEIGHT

    if os.path.exists(VIDEO_START):
        print(f"📐 Getting resolution from {VIDEO_START}...")
        tmp = VideoFileClip(VIDEO_START)
        target_width, target_height = tmp.w, tmp.h
        tmp.close()
        print(f"📐 {target_width}×{target_height}")
    else:
        print(f"⚠️  {VIDEO_START} not found — using {target_width}×{target_height}")

    # Build video clips (without audio yet)
    if os.path.exists(VIDEO_START):
        print(f"🎬 Adding start video")
        clips.append(VideoFileClip(VIDEO_START))

    for img_path in image_paths:
        print(f"🎬 Adding image: {img_path}")
        resized = resize_to_match_video(img_path, target_width, target_height)
        clips.append(ImageClip(resized).set_duration(5))

    if os.path.exists(VIDEO_END):
        print(f"🎬 Adding end video")
        clips.append(VideoFileClip(VIDEO_END))

    print("🎬 Combining clips…")
    final_video = concatenate_videoclips(clips, method="compose")

    temp_video = "temp_video_no_audio.mp4"
    print("💾 Writing temporary video…")
    final_video.write_videofile(
        temp_video, fps=24, codec='libx264', verbose=False, logger=None
    )

    # Clean up resized images
    for img_path in image_paths:
        tp = f"temp_resized_{os.path.basename(img_path)}"
        if os.path.exists(tp):
            os.remove(tp)

    # ========== AUDIO PROCESSING ==========
    video_clip = VideoFileClip(temp_video)
    video_duration = video_clip.duration
    audio_tracks = []

    # 1. Background music (sound.mp3) – quiet
    if os.path.exists("sound.mp3"):
        print("🎵 Adding background music...")
        music = AudioFileClip("sound.mp3").volumex(0.3)   # 30% volume
        if music.duration < video_duration:
            loops = int(video_duration / music.duration) + 1
            music = concatenate_audioclips([music] * loops)
        music = music.subclip(0, video_duration)
        audio_tracks.append(music)
    else:
        print("⚠️  No sound.mp3 — skipping background music")

    # 2. Voiceover from ElevenLabs (starts at VOICE_START_SEC)
    if voiceover_texts:
        print("🗣️ Generating voiceover from summaries & question...")
        full_voice = None
        for idx, txt in enumerate(voiceover_texts):
            voice_clip = text_to_speech_clip(txt, volume=VOICE_VOLUME)
            if voice_clip:
                if full_voice is None:
                    full_voice = voice_clip
                else:
                    full_voice = concatenate_audioclips([full_voice, voice_clip])
        if full_voice:
            # Add silence at beginning so voice starts at desired second
            silence_dur = max(0, VOICE_START_SEC)
            if silence_dur > 0:
                silence = AudioClip(lambda t: 0, duration=silence_dur, fps=44100)
                full_voice = concatenate_audioclips([silence, full_voice])
            # Trim if voice exceeds video duration
            if full_voice.duration > video_duration:
                full_voice = full_voice.subclip(0, video_duration)
            audio_tracks.append(full_voice)
    else:
        print("⚠️  No voiceover texts provided")

    # Mix all audio tracks
    if audio_tracks:
        final_audio = CompositeAudioClip(audio_tracks)
        final_with_audio = video_clip.set_audio(final_audio)
    else:
        final_with_audio = video_clip

    print("💾 Writing final video with audio...")
    final_with_audio.write_videofile(
        OUTPUT_VIDEO, fps=24, codec='libx264',
        audio_codec='aac', verbose=False, logger=None
    )

    # Cleanup
    video_clip.close()
    for track in audio_tracks:
        try:
            track.close()
        except:
            pass
    if os.path.exists(temp_video):
        os.remove(temp_video)

    # Remove temporary voice files (optional)
    for f in os.listdir("."):
        if f.startswith("temp_voice_") and f.endswith(".mp3"):
            try:
                os.remove(f)
            except:
                pass

    print("✅ Video creation complete!")
    return OUTPUT_VIDEO


# ==================== TELEGRAM ====================
async def send_video_to_telegram(video_path: str, caption: str):
    try:
        bot = Bot(token=BOT_TOKEN)
        with open(video_path, 'rb') as video:
            await bot.send_video(
                chat_id=CHAT_ID, video=video, caption=caption,
                parse_mode='HTML', supports_streaming=True
            )
        print(f"✅ Video sent to {CHAT_ID}")
        return True
    except TelegramError as e:
        print(f"❌ Failed to send: {e}")
        return False

def send_video_sync(video_path: str, caption: str):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(send_video_to_telegram(video_path, caption))
        loop.close()
        return result
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


# ==================== MAIN ====================
if __name__ == "__main__":
    print("🚀 Starting news processing pipeline…")
    latest_news = get_latest_news()

    if latest_news:
        print(f"📰 {latest_news['title']}")
        print(f"📂 {latest_news['category']}  |  📅 {latest_news['date']}")

        print("🎨 Creating main image…")
        main_image = create_main_image(latest_news)
        print(f"✅ {main_image}")

        print("🤖 Generating AI summaries…")
        summaries = summarize_with_ai(latest_news['title'], latest_news['description'])
        for i, s in enumerate(summaries, 1):
            print(f"   {i}. {s[:60]}…")

        print("🤖 Generating AI question…")
        question = generate_question_ai(latest_news['title'], latest_news['description'])
        print(f"   ❓ {question}")

        print("🎨 Creating summary images…")
        summary_images = []
        for i, summary in enumerate(summaries):
            p = create_summary_image(summary, i, latest_news.get('image'))
            summary_images.append(p)
            print(f"✅ summary_{i+1}.webp")

        # Create 4th summary image (question)
        p4 = create_summary_image(question, 3, latest_news.get('image'))
        summary_images.append(p4)
        print(f"✅ summary_4.webp (question)")

        all_images = [main_image] + summary_images

        # ── Prepare voiceover texts (3 summaries + question) ──
        voiceover_texts = summaries + [question]

        print("🎬 Creating video with voiceover…")
        try:
            video_path = create_simple_video(all_images, voiceover_texts=voiceover_texts)
            print(f"✅ Video: {video_path}")

            caption = (
                f"📰 <b>{latest_news['title']}</b>\n\n"
                f"📂 {latest_news['category']}\n"
                f"📅 {latest_news['date']}\n\n"
                f"#اخبار #السفير"
            )

            print("📤 Sending to Telegram…")
            success = send_video_sync(video_path, caption)
            print("✅ Published!" if success else "⚠️  Video created but failed to send")

        except Exception as e:
            print(f"❌ Video creation failed: {e}")
    else:
        print("❌ No new news to process.")
