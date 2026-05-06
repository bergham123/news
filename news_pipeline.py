import requests
import xml.etree.ElementTree as ET
import re
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display
from datetime import datetime
import os
from typing import List, Dict, Optional
import asyncio
from telegram import Bot
from telegram.error import TelegramError
from openai import OpenAI
from moviepy.editor import (
    VideoFileClip, ImageClip, concatenate_videoclips, AudioFileClip
)
from moviepy.audio.AudioClip import CompositeAudioClip, concatenate_audioclips

# ==================== CONFIG ====================
RSS_URL       = "https://www.telegraphe.ma/rss/latest-posts"
LAST_FILE     = "last_news.txt"
WIDTH, HEIGHT = 1200, 700
OUTPUT_IMAGE  = "output.webp"
OUTPUT_VIDEO  = "final_news_video.mp4"
VIDEO_START   = "video.mp4"
VIDEO_END     = "videoend.mp4"

# ==================== AI CONFIG ====================
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# ==================== TELEGRAM CONFIG ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = "@natureptv"

# ==================== COLORS ====================
BG_COLOR = (10, 22, 40)
WHITE    = (232, 237, 245)
RED      = (196, 30, 58)
GRAY     = (143, 163, 191)

SUMMARY_BG_COLORS = [
    (25, 40, 65),
    (45, 30, 55),
    (30, 50, 45),
]

# ==================== FONTS ====================
# Amiri font files must be in the fonts/ folder of your repo:
#   fonts/Amiri-Regular.ttf
#   fonts/Amiri-Bold.ttf
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR  = os.path.join(BASE_DIR, "fonts")
AMIRI_REG  = os.path.join(FONTS_DIR, "Amiri-Regular.ttf")
AMIRI_BOLD = os.path.join(FONTS_DIR, "Amiri-Bold.ttf")

def load_amiri(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = AMIRI_BOLD if (bold and os.path.exists(AMIRI_BOLD)) else AMIRI_REG
    if not os.path.exists(path):
        print(f"⚠️  Amiri font not found at {path} — Arabic will show as squares.")
        return ImageFont.load_default()
    font = ImageFont.truetype(path, size)
    return font

FONT_TITLE   = load_amiri(45, bold=True)
FONT_SMALL   = load_amiri(28)
FONT_TINY    = load_amiri(22)
FONT_SUMMARY = load_amiri(38)

# ==================== ARABIC HELPERS ====================
def fix_arabic(text: str) -> str:
    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped)

def clean_html(text: str) -> str:
    return re.sub(r'<[^>]+>', '', text)

def text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]

def draw_rtl(draw: ImageDraw.ImageDraw, right_x: int, y: int,
             text: str, fill, font) -> int:
    fixed = fix_arabic(text)
    w = text_width(draw, fixed, font)
    draw.text((right_x - w, y), fixed, fill=fill, font=font)
    return w

def wrap_arabic(draw: ImageDraw.ImageDraw, text: str, font,
                max_width: int) -> List[str]:
    words   = text.split()
    lines:   List[str] = []
    current: List[str] = []
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

def draw_rtl_multiline(draw: ImageDraw.ImageDraw, right_x: int, y: int,
                       text: str, fill, font,
                       max_width: int, line_gap: int = 15) -> int:
    lines       = wrap_arabic(draw, text, font, max_width)
    line_height = font.size + line_gap
    for line in lines:
        draw_rtl(draw, right_x, y, line, fill, font)
        y += line_height
    return y

# ==================== DATE FORMATTING ====================
def format_date(date_string: str) -> str:
    if not date_string:
        return datetime.now().strftime("%d/%m/%Y")

    date_formats = [
        '%a, %d %b %Y %H:%M:%S %z',
        '%a, %d %b %Y %H:%M:%S GMT',
        '%Y-%m-%dT%H:%M:%S%z',
        '%Y-%m-%d %H:%M:%S',
        '%d/%m/%Y',
        '%m/%d/%Y',
    ]
    for fmt in date_formats:
        try:
            return datetime.strptime(date_string.strip(), fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue

    match = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', date_string)
    if match:
        day, month, year = match.groups()
        return f"{int(day):02d}/{int(month):02d}/{year}"

    return datetime.now().strftime("%d/%m/%Y")

# ==================== AI SUMMARIZATION ====================
def summarize_with_ai(title: str, description: str) -> List[str]:
    try:
        prompt = (
            f"لديك الخبر التالي:\n"
            f"العنوان: {title}\n"
            f"المحتوى: {description}\n\n"
            f"المطلوب: قم بتلخيص هذا الخبر إلى 3 نقاط رئيسية قصيرة (كل نقطة سطر واحد).\n"
            f"أخرج النقاط الثلاث فقط، كل نقطة في سطر منفصل."
        )
        response = client.chat.completions.create(
            model="nvidia/nemotron-3-super-120b-a12b:free",
            messages=[{"role": "user", "content": prompt}],
            extra_body={"reasoning": {"enabled": True}},
        )
        ai_response = response.choices[0].message.content
        summaries = [l.strip() for l in ai_response.split('\n') if l.strip()]
        while len(summaries) < 3:
            summaries.append(f"ملخص {len(summaries) + 1}")
        return summaries[:3]

    except Exception as e:
        print(f"AI summarization failed: {e}")
        words = title.split()
        if len(words) >= 6:
            return [
                " ".join(words[:3]),
                " ".join(words[2:5]),
                " ".join(words[4:7]) if len(words) > 6 else " ".join(words[-3:]),
            ]
        return [title, f"تفاصيل {title}", f"متابعة {title}"]

# ==================== RSS FETCHING ====================
def load_previous_news() -> set:
    if not os.path.exists(LAST_FILE):
        return set()
    with open(LAST_FILE, 'r', encoding='utf-8') as f:
        return {line.strip() for line in f if line.strip()}

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

            # image extraction — 3 fallbacks
            image_url = None
            enclosure = item.find('enclosure')
            if enclosure is not None and enclosure.get('type', '').startswith('image/'):
                image_url = enclosure.get('url')
            if image_url is None:
                media = item.find('{http://search.yahoo.com/mrss/}content')
                if media is not None:
                    image_url = media.get('url')
            if image_url is None and description is not None and description.text:
                m = re.search(r'src="([^"]+)"', description.text)
                if m:
                    image_url = m.group(1)

            clean_title       = clean_html(title.text)       if title       is not None else "No title"
            clean_description = clean_html(description.text) if description is not None else ""
            clean_category    = clean_html(category.text)    if category    is not None else "أخبار"
            unique_id         = link.text                    if link        is not None else clean_title
            formatted_date    = format_date(pub_date.text    if pub_date    is not None else None)

            items.append({
                'id':          unique_id,
                'title':       clean_title,
                'description': clean_description,
                'category':    clean_category,
                'link':        link.text if link is not None else '',
                'date':        formatted_date,
                'image':       image_url,
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

# ==================== IMAGE CREATION ====================
def create_main_image(news: Dict) -> str:
    img  = Image.new("RGB", (WIDTH, HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    RIGHT_EDGE = 1150
    TEXT_MAX_W = 500

    draw_rtl(draw, RIGHT_EDGE, 80,  "السفير",       RED,  FONT_TITLE)
    draw_rtl(draw, RIGHT_EDGE, 150, "مركز الإعلام", GRAY, FONT_SMALL)

    current_y = draw_rtl_multiline(
        draw, RIGHT_EDGE, 250, news["title"], WHITE, FONT_TITLE, TEXT_MAX_W, line_gap=12
    )
    current_y += 30
    draw_rtl(draw, RIGHT_EDGE, current_y, news["category"], RED,  FONT_SMALL)
    current_y += 50
    draw_rtl(draw, RIGHT_EDGE, current_y, news["date"],     GRAY, FONT_TINY)

    try:
        if news.get("image"):
            resp     = requests.get(news["image"], stream=True, timeout=10)
            news_img = Image.open(resp.raw).convert("RGB").resize((600, 700))
            img.paste(news_img, (0, 0))
        else:
            for i in range(600):
                color = (30 + i // 10, 40 + i // 8, 50 + i // 6)
                draw.line([(i, 0), (i, 700)], fill=color)
    except Exception as e:
        print(f"Image download failed: {e}")

    img.save(OUTPUT_IMAGE, "WEBP", quality=95)
    return OUTPUT_IMAGE


def create_summary_image(summary_text: str, index: int,
                         news_image_url: str = None) -> str:
    bg_color = SUMMARY_BG_COLORS[index % len(SUMMARY_BG_COLORS)]
    img      = Image.new("RGB", (WIDTH, HEIGHT), bg_color)
    draw     = ImageDraw.Draw(img)

    if news_image_url:
        try:
            resp    = requests.get(news_image_url, stream=True, timeout=10)
            bg_img  = Image.open(resp.raw).convert("RGB").resize((WIDTH, HEIGHT))
            overlay = Image.new('RGBA', (WIDTH, HEIGHT), (*bg_color, 200))
            img     = Image.alpha_composite(bg_img.convert('RGBA'), overlay).convert('RGB')
            draw    = ImageDraw.Draw(img)
        except Exception:
            pass

    RIGHT_EDGE = 1100
    TEXT_MAX_W = 550

    draw_rtl(draw, RIGHT_EDGE, 80, "ملخص الخبر", RED, FONT_TITLE)
    draw.line([WIDTH - 600, 130, WIDTH - 50, 130], fill=RED, width=3)
    draw_rtl_multiline(draw, RIGHT_EDGE, 200, summary_text, WHITE,
                       FONT_SUMMARY, TEXT_MAX_W, line_gap=20)
    draw.line([WIDTH - 600, HEIGHT - 80, WIDTH - 50, HEIGHT - 80], fill=GRAY, width=1)

    path = f"summary_{index + 1}.webp"
    img.save(path, "WEBP", quality=95)
    return path

# ==================== VIDEO CREATION ====================
def resize_to_match_video(image_path: str,
                          target_width: int, target_height: int) -> str:
    img       = Image.open(image_path)
    new_img   = Image.new("RGB", (target_width, target_height), BG_COLOR)
    img_ratio = img.width / img.height
    tgt_ratio = target_width / target_height

    if img_ratio > tgt_ratio:
        new_w = target_width
        new_h = int(target_width / img_ratio)
    else:
        new_h = target_height
        new_w = int(target_height * img_ratio)

    img_resized = img.resize((new_w, new_h), Image.LANCZOS)
    x = (target_width  - new_w) // 2
    y = (target_height - new_h) // 2
    new_img.paste(img_resized, (x, y))

    temp_path = f"temp_resized_{os.path.basename(image_path)}"
    new_img.save(temp_path, "WEBP", quality=95)
    return temp_path


def create_video(image_paths: List[str]) -> str:
    clips         = []
    target_width  = WIDTH
    target_height = HEIGHT

    if os.path.exists(VIDEO_START):
        probe = VideoFileClip(VIDEO_START)
        target_width, target_height = probe.w, probe.h
        probe.close()
        print(f"📐 Resolution: {target_width}x{target_height}")
        clips.append(VideoFileClip(VIDEO_START))

    for img_path in image_paths:
        resized = resize_to_match_video(img_path, target_width, target_height)
        clips.append(ImageClip(resized).set_duration(5))

    if os.path.exists(VIDEO_END):
        clips.append(VideoFileClip(VIDEO_END))

    temp_video = "temp_video_no_audio.mp4"
    concatenate_videoclips(clips, method="compose").write_videofile(
        temp_video, fps=24, codec='libx264', verbose=False, logger=None
    )

    for img_path in image_paths:
        tmp = f"temp_resized_{os.path.basename(img_path)}"
        if os.path.exists(tmp):
            os.remove(tmp)

    if os.path.exists("sound.mp3"):
        try:
            video_clip     = VideoFileClip(temp_video)
            video_duration = video_clip.duration

            music = AudioFileClip("sound.mp3").volumex(0.3)
            if music.duration < video_duration:
                loops = int(video_duration / music.duration) + 1
                music = concatenate_audioclips([music] * loops)
            music = music.subclip(0, video_duration)

            final_audio = (
                CompositeAudioClip([video_clip.audio, music])
                if video_clip.audio is not None
                else music
            )
            video_clip.set_audio(final_audio).write_videofile(
                OUTPUT_VIDEO, fps=24, codec='libx264',
                audio_codec='aac', verbose=False, logger=None
            )
            video_clip.close()
            music.close()
            os.remove(temp_video)
            print("✅ Audio added successfully!")

        except Exception as e:
            print(f"⚠️ Failed to add audio: {e}")
            os.rename(temp_video, OUTPUT_VIDEO)
    else:
        print("⚠️ No sound.mp3 found")
        os.rename(temp_video, OUTPUT_VIDEO)

    return OUTPUT_VIDEO

# ==================== TELEGRAM ====================
async def _send_video(video_path: str, caption: str) -> bool:
    try:
        bot = Bot(token=BOT_TOKEN)
        with open(video_path, 'rb') as f:
            await bot.send_video(
                chat_id=CHAT_ID,
                video=f,
                caption=caption,
                parse_mode='HTML',
                supports_streaming=True,
            )
        print(f"✅ Sent to Telegram: {CHAT_ID}")
        return True
    except TelegramError as e:
        print(f"❌ Telegram error: {e}")
        return False


def send_video(video_path: str, caption: str) -> bool:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_send_video(video_path, caption))
    except Exception as e:
        print(f"❌ Error: {e}")
        return False
    finally:
        loop.close()

# ==================== MAIN ====================
if __name__ == "__main__":
    print("🚀 Starting news pipeline...")

    news = get_latest_news()
    if not news:
        print("❌ No new news.")
    else:
        print(f"📰 {news['title']}")

        main_img     = create_main_image(news)
        summaries    = summarize_with_ai(news['title'], news['description'])
        summary_imgs = [
            create_summary_image(s, i, news.get('image'))
            for i, s in enumerate(summaries)
        ]

        video_path = create_video([main_img] + summary_imgs)
        print(f"✅ Video: {video_path}")

        caption = (
            f"📰 <b>{news['title']}</b>\n\n"
            f"📂 {news['category']}\n"
            f"📅 {news['date']}\n\n"
            f"#اخبار #السفير"
        )
        success = send_video(video_path, caption)
        print("✅ Published!" if success else "⚠️ Send failed.")
