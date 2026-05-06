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
from moviepy.editor import *
from moviepy.audio.AudioClip import CompositeAudioClip, concatenate_audioclips

os.environ["PYTHONIOENCODING"] = "utf-8"

# ==================== CONFIG ====================
RSS_URL = "https://www.telegraphe.ma/rss/latest-posts"

LAST_FILE = "last_news.txt"

WIDTH, HEIGHT = 1200, 700

OUTPUT_IMAGE = "output.webp"
OUTPUT_VIDEO = "final_news_video.mp4"

VIDEO_START = "video.mp4"
VIDEO_END = "videoend.mp4"

# ==================== API ====================
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")

CHAT_ID = "@natureptv"

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# ==================== COLORS ====================
BG_COLOR = (10, 22, 40)
WHITE = (232, 237, 245)
RED = (196, 30, 58)
GRAY = (143, 163, 191)

SUMMARY_BG_COLORS = [
    (25, 40, 65),
    (45, 30, 55),
    (30, 50, 45),
]

# ==================== FONT ====================
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

try:
    FONT_TITLE = ImageFont.truetype(FONT_PATH, 45)
    FONT_SMALL = ImageFont.truetype(FONT_PATH, 28)
    FONT_TINY = ImageFont.truetype(FONT_PATH, 22)
    FONT_SUMMARY = ImageFont.truetype(FONT_PATH, 38)

except:
    FONT_TITLE = ImageFont.load_default()
    FONT_SMALL = ImageFont.load_default()
    FONT_TINY = ImageFont.load_default()
    FONT_SUMMARY = ImageFont.load_default()

# ==================== ARABIC ====================
def fix_arabic(text: str) -> str:

    if not text:
        return ""

    text = text.strip()

    reshaped = arabic_reshaper.reshape(text)

    return get_display(reshaped)

def clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)

def text_width(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]

def draw_rtl(draw, right_x, y, text, fill, font):

    fixed = fix_arabic(text)

    w = text_width(draw, fixed, font)

    draw.text(
        (right_x - w, y),
        fixed,
        fill=fill,
        font=font
    )

    return w

def wrap_arabic(draw, text, font, max_width):

    words = text.split()

    lines = []

    current = []

    for word in words:

        candidate = " ".join(current + [word])

        candidate_fixed = fix_arabic(candidate)

        if text_width(draw, candidate_fixed, font) <= max_width:
            current.append(word)

        else:
            if current:
                lines.append(" ".join(current))

            current = [word]

    if current:
        lines.append(" ".join(current))

    return lines

def draw_rtl_multiline(
    draw,
    right_x,
    y,
    text,
    fill,
    font,
    max_width,
    line_gap=15
):

    lines = wrap_arabic(
        draw,
        text,
        font,
        max_width
    )

    bbox = draw.textbbox((0, 0), "Ag", font=font)

    line_height = (bbox[3] - bbox[1]) + line_gap

    for line in lines:

        draw_rtl(
            draw,
            right_x,
            y,
            line,
            fill,
            font
        )

        y += line_height

    return y

# ==================== DATE ====================
def format_date(date_string):

    if not date_string:
        return datetime.now().strftime("%d/%m/%Y")

    formats = [
        '%a, %d %b %Y %H:%M:%S %z',
        '%a, %d %b %Y %H:%M:%S GMT',
        '%Y-%m-%dT%H:%M:%S%z',
        '%Y-%m-%d %H:%M:%S',
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(date_string.strip(), fmt)
            return dt.strftime("%d/%m/%Y")

        except:
            pass

    return datetime.now().strftime("%d/%m/%Y")

# ==================== AI ====================
def summarize_with_ai(title, description):

    try:

        prompt = f"""
        لديك الخبر التالي:

        العنوان:
        {title}

        المحتوى:
        {description}

        المطلوب:
        قم بتلخيص الخبر إلى 3 نقاط قصيرة فقط.

        بدون ترقيم.
        """

        response = client.chat.completions.create(
            model="nvidia/nemotron-3-super-120b-a12b:free",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        content = response.choices[0].message.content

        summaries = [
            line.strip()
            for line in content.split("\n")
            if line.strip()
        ]

        while len(summaries) < 3:
            summaries.append("تفاصيل إضافية")

        return summaries[:3]

    except Exception as e:

        print("AI ERROR:", e)

        return [
            title[:50],
            title[20:70],
            title[-50:]
        ]

# ==================== RSS ====================
def load_previous_news():

    if not os.path.exists(LAST_FILE):
        return set()

    with open(LAST_FILE, "r", encoding="utf-8") as f:
        return set(
            line.strip()
            for line in f
            if line.strip()
        )

def save_news_id(news_id):

    with open(LAST_FILE, "a", encoding="utf-8") as f:
        f.write(news_id + "\n")

def fetch_rss_feed():

    try:

        response = requests.get(
            RSS_URL,
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0"
            }
        )

        root = ET.fromstring(response.content)

        items = []

        for item in root.findall(".//item"):

            title = item.find("title")
            link = item.find("link")
            pub_date = item.find("pubDate")
            description = item.find("description")
            category = item.find("category")

            image_url = None

            enclosure = item.find("enclosure")

            if enclosure is not None:
                image_url = enclosure.get("url")

            if not image_url and description is not None and description.text:

                img_match = re.search(
                    r'src="([^"]+)"',
                    description.text
                )

                if img_match:
                    image_url = img_match.group(1)

            items.append({
                "id": link.text if link is not None else "",
                "title": clean_html(title.text if title is not None else ""),
                "description": clean_html(description.text if description is not None else ""),
                "category": clean_html(category.text if category is not None else "أخبار"),
                "date": format_date(pub_date.text if pub_date is not None else ""),
                "image": image_url
            })

        return items

    except Exception as e:

        print("RSS ERROR:", e)

        return []

def get_latest_news():

    previous = load_previous_news()

    news = fetch_rss_feed()

    new_items = [
        item
        for item in news
        if item["id"] not in previous
    ]

    if not new_items:
        return None

    latest = new_items[0]

    save_news_id(latest["id"])

    return latest

# ==================== MAIN IMAGE ====================
def create_main_image(news):

    img = Image.new(
        "RGB",
        (WIDTH, HEIGHT),
        BG_COLOR
    )

    draw = ImageDraw.Draw(img)

    RIGHT_EDGE = 1150

    TEXT_MAX_W = 500

    try:

        if news.get("image"):

            response = requests.get(
                news["image"],
                stream=True,
                timeout=15,
                headers={
                    "User-Agent": "Mozilla/5.0"
                }
            )

            news_img = Image.open(response.raw).convert("RGB")

            news_img = news_img.resize((600, 700))

            img.paste(news_img, (0, 0))

    except Exception as e:
        print("IMAGE ERROR:", e)

    draw_rtl(
        draw,
        RIGHT_EDGE,
        80,
        "السفير",
        RED,
        FONT_TITLE
    )

    draw_rtl(
        draw,
        RIGHT_EDGE,
        150,
        "مركز الإعلام",
        GRAY,
        FONT_SMALL
    )

    current_y = draw_rtl_multiline(
        draw,
        RIGHT_EDGE,
        250,
        news["title"],
        WHITE,
        FONT_TITLE,
        TEXT_MAX_W,
        line_gap=15
    )

    current_y += 30

    draw_rtl(
        draw,
        RIGHT_EDGE,
        current_y,
        news["category"],
        RED,
        FONT_SMALL
    )

    current_y += 50

    draw_rtl(
        draw,
        RIGHT_EDGE,
        current_y,
        news["date"],
        GRAY,
        FONT_TINY
    )

    img.save(
        OUTPUT_IMAGE,
        "WEBP",
        quality=95
    )

    return OUTPUT_IMAGE

# ==================== SUMMARY IMAGE ====================
def create_summary_image(summary_text, index, news_image_url=None):

    bg_color = SUMMARY_BG_COLORS[index % len(SUMMARY_BG_COLORS)]

    img = Image.new(
        "RGB",
        (WIDTH, HEIGHT),
        bg_color
    )

    if news_image_url:

        try:

            response = requests.get(
                news_image_url,
                stream=True,
                timeout=15,
                headers={
                    "User-Agent": "Mozilla/5.0"
                }
            )

            bg_img = Image.open(response.raw).convert("RGB")

            bg_img = bg_img.resize((WIDTH, HEIGHT))

            overlay = Image.new(
                "RGBA",
                (WIDTH, HEIGHT),
                (*bg_color, 210)
            )

            img = Image.alpha_composite(
                bg_img.convert("RGBA"),
                overlay
            ).convert("RGB")

        except:
            pass

    draw = ImageDraw.Draw(img)

    RIGHT_EDGE = 1100

    TEXT_MAX_W = 600

    current_y = draw_rtl_multiline(
        draw,
        RIGHT_EDGE,
        230,
        summary_text,
        WHITE,
        FONT_SUMMARY,
        TEXT_MAX_W,
        line_gap=25
    )

    draw.line(
        [WIDTH - 600, HEIGHT - 80, WIDTH - 50, HEIGHT - 80],
        fill=GRAY,
        width=2
    )

    output = f"summary_{index+1}.webp"

    img.save(
        output,
        "WEBP",
        quality=95
    )

    return output

# ==================== RESIZE ====================
def resize_to_match_video(image_path, target_width, target_height):

    img = Image.open(image_path)

    new_img = Image.new(
        "RGB",
        (target_width, target_height),
        BG_COLOR
    )

    img_ratio = img.width / img.height

    target_ratio = target_width / target_height

    if img_ratio > target_ratio:

        new_width = target_width

        new_height = int(target_width / img_ratio)

    else:

        new_height = target_height

        new_width = int(target_height * img_ratio)

    img_resized = img.resize(
        (new_width, new_height),
        Image.LANCZOS
    )

    x = (target_width - new_width) // 2
    y = (target_height - new_height) // 2

    new_img.paste(img_resized, (x, y))

    temp_path = f"temp_{os.path.basename(image_path)}"

    new_img.save(temp_path)

    return temp_path

# ==================== VIDEO ====================
def create_simple_video(image_paths):

    clips = []

    target_width = WIDTH
    target_height = HEIGHT

    if os.path.exists(VIDEO_START):

        temp_clip = VideoFileClip(VIDEO_START)

        target_width = temp_clip.w
        target_height = temp_clip.h

        temp_clip.close()

    if os.path.exists(VIDEO_START):
        clips.append(VideoFileClip(VIDEO_START))

    for img_path in image_paths:

        resized = resize_to_match_video(
            img_path,
            target_width,
            target_height
        )

        clip = ImageClip(resized).set_duration(5)

        clips.append(clip)

    if os.path.exists(VIDEO_END):
        clips.append(VideoFileClip(VIDEO_END))

    final_video = concatenate_videoclips(
        clips,
        method="compose"
    )

    temp_video = "temp_video.mp4"

    final_video.write_videofile(
        temp_video,
        fps=24,
        codec="libx264",
        audio_codec="aac",
        verbose=False,
        logger=None
    )

    if os.path.exists("sound.mp3"):

        try:

            video_clip = VideoFileClip(temp_video)

            music = AudioFileClip("sound.mp3").volumex(0.3)

            if music.duration < video_clip.duration:

                loops = int(video_clip.duration / music.duration) + 1

                music = concatenate_audioclips([music] * loops)

            music = music.subclip(0, video_clip.duration)

            if video_clip.audio is not None:

                final_audio = CompositeAudioClip([
                    video_clip.audio,
                    music
                ])

            else:
                final_audio = music

            final_output = video_clip.set_audio(final_audio)

            final_output.write_videofile(
                OUTPUT_VIDEO,
                fps=24,
                codec="libx264",
                audio_codec="aac",
                verbose=False,
                logger=None
            )

            os.remove(temp_video)

            return OUTPUT_VIDEO

        except Exception as e:

            print("AUDIO ERROR:", e)

    os.rename(temp_video, OUTPUT_VIDEO)

    return OUTPUT_VIDEO

# ==================== TELEGRAM ====================
async def send_video_to_telegram(video_path, caption):

    try:

        bot = Bot(token=BOT_TOKEN)

        with open(video_path, "rb") as video:

            await bot.send_video(
                chat_id=CHAT_ID,
                video=video,
                caption=caption,
                parse_mode="HTML",
                supports_streaming=True
            )

        return True

    except TelegramError as e:

        print("TELEGRAM ERROR:", e)

        return False

def send_video_sync(video_path, caption):

    loop = asyncio.new_event_loop()

    asyncio.set_event_loop(loop)

    result = loop.run_until_complete(
        send_video_to_telegram(video_path, caption)
    )

    loop.close()

    return result

# ==================== MAIN ====================
if __name__ == "__main__":

    print("STARTING BOT")

    latest_news = get_latest_news()

    if not latest_news:

        print("NO NEW NEWS")

        exit()

    print("NEWS:", latest_news["title"])

    main_image = create_main_image(latest_news)

    summaries = summarize_with_ai(
        latest_news["title"],
        latest_news["description"]
    )

    summary_images = []

    for i, summary in enumerate(summaries):

        path = create_summary_image(
            summary,
            i,
            latest_news.get("image")
        )

        summary_images.append(path)

    all_images = [main_image] + summary_images

    video_path = create_simple_video(all_images)

    caption = (
        f"📰 <b>{latest_news['title']}</b>\n\n"
        f"📅 {latest_news['date']}\n\n"
        f"#اخبار"
    )

    send_video_sync(video_path, caption)

    print("DONE")
