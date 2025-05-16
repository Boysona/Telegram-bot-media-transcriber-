import os
import re
import uuid
import shutil
import logging
import requests
import telebot
import json
from flask import Flask, request, abort
from faster_whisper import WhisperModel
from datetime import datetime
import yt_dlp

# Configure logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Bot token and required channel
TOKEN = "7648822901:AAFQEUx-S4bpD5qUMPHNB1P9jYCYSB4mzHU"
REQUIRED_CHANNEL = "@mediatranscriber"

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Admin ID
ADMIN_ID = 5978150981

# Download directory
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Whisper model for transcription
model = WhisperModel(model_size_or_path="tiny", device="cpu", compute_type="int8")

# User tracking file
users_file = 'users.json'
user_data = {}
if os.path.exists(users_file):
    with open(users_file, 'r') as f:
        try:
            user_data = json.load(f)
        except json.JSONDecodeError:
            user_data = {}

def save_user_data():
    with open(users_file, 'w') as f:
        json.dump(user_data, f, indent=4)

# In-memory chat history and last transcription store
user_memory = {}
last_transcription = {}

# Statistics counters
total_files_processed = 0
total_audio_files = 0
total_voice_clips = 0
total_videos = 0
total_tiktok_downloads = 0
total_other_downloads = 0
total_processing_time = 0  # in seconds
processing_start_time = None

GEMINI_API_KEY = "AIzaSyAto78yGVZobxOwPXnl8wCE9ZW8Do2R8HA"

def ask_gemini(user_id, user_message):
    user_memory.setdefault(user_id, []).append({"role": "user", "text": user_message})
    history = user_memory[user_id][-10:]
    parts = [{"text": msg["text"]} for msg in history]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    resp = requests.post(url, headers={'Content-Type': 'application/json'}, json={"contents": [{"parts": parts}]})
    result = resp.json()
    if "candidates" in result:
        reply = result['candidates'][0]['content']['parts'][0]['text']
        user_memory[user_id].append({"role": "model", "text": reply})
        return reply
    return "Error: " + json.dumps(result)

FILE_SIZE_LIMIT = 20 * 1024 * 1024  # 20MB
admin_state = {}

def set_bot_info():
    commands = [
        telebot.types.BotCommand("start", "Restart the bot 🤖"),
        telebot.types.BotCommand("status", "Show bot statistics 📊"),
        telebot.types.BotCommand("info", "Show usage instructions ℹ️"),
        telebot.types.BotCommand("translate", "Translate last transcription 🌐"),
        telebot.types.BotCommand("summarize", "Summarize last transcription 📝"),
    ]
    bot.set_my_commands(commands)
    bot.set_my_short_description("Transcribe voice, audio & video into text—fast & free")
    bot.set_my_description(
        """This bot transcribes voice messages, audio files, video files, and links from YouTube, Instagram, Facebook, TikTok, Pinterest, X (Twitter), Snapchat, Likee."""
    )

set_bot_info()

def check_subscription(user_id):
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except telebot.apihelper.ApiTelegramException:
        return False

def send_subscription_message(chat_id):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("Join Channel", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"))
    bot.send_message(
        chat_id,
        "🥺 Please join our channel to use this bot: @mediatranscriber",
        reply_markup=markup
    )

def update_user_activity(user_id):
    user_data[str(user_id)] = datetime.now().isoformat()
    save_user_data()

@bot.message_handler(commands=['start'])
def start_handler(message):
    update_user_activity(message.from_user.id)
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)
    if message.from_user.id == ADMIN_ID:
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Send Broadcast", "Total Users", "/status")
        bot.send_message(message.chat.id, "Admin Panel", reply_markup=keyboard)
    else:
        name = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
        bot.send_message(
            message.chat.id,
            f"👋 Welcome, {name}! Send me a voice, audio, video, or link to transcribe/download."
        )

@bot.message_handler(commands=['info'])
def help_handler(message):
    bot.send_message(
        message.chat.id,
        """ℹ️ How to use:
1. Join @mediatranscriber
2. Send a media file or a link from YouTube, Instagram, Facebook, TikTok, Pinterest, X, Snapchat, Likee.
3. Choose “Download” or “Transcribe” via the inline buttons that appear.""",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=['status'])
def status_handler(message):
    update_user_activity(message.from_user.id)
    today = datetime.now().date()
    active_today = sum(
        1 for ts in user_data.values()
        if datetime.fromisoformat(ts).date() == today
    )
    sec = int(total_processing_time)
    h, m = sec//3600, (sec%3600)//60
    text = (
        f"📊 Stats:\n"
        f"👥 Active today: {active_today}\n"
        f"⚙️ Files processed: {total_files_processed}\n"
        f"⏱️ Processing time: {h}h {m}m {sec%60}s"
    )
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: True, content_types=['voice', 'audio', 'video', 'video_note'])
def handle_file(message):
    global total_files_processed, total_audio_files, total_voice_clips, total_videos, total_processing_time
    update_user_activity(message.from_user.id)
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    file_obj = message.voice or message.audio or message.video or message.video_note
    if file_obj.file_size > FILE_SIZE_LIMIT:
        return bot.send_message(message.chat.id, "⚠️ File too large (max 20MB).")

    info = bot.get_file(file_obj.file_id)
    path = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}.ogg")
    data = bot.download_file(info.file_path)
    with open(path, 'wb') as f:
        f.write(data)

    start = datetime.now()
    transcription = transcribe(path) or ""
    total_processing_time += (datetime.now() - start).total_seconds()
    total_files_processed += 1
    if message.voice: total_voice_clips += 1
    elif message.audio: total_audio_files += 1
    else: total_videos += 1

    uid = str(message.from_user.id)
    last_transcription[uid] = transcription

    if len(transcription) > 4000:
        fn = 'trans.txt'
        with open(fn, 'w', encoding='utf-8') as f:
            f.write(transcription)
        bot.send_document(message.chat.id, open(fn, 'rb'))
        os.remove(fn)
    else:
        bot.reply_to(message, transcription)
    os.remove(path)

# Enhanced regex to catch youtu.be & pin.it too 
TIKTOK_REGEX = re.compile(r'https?://(?:www\.)?(?:vm\.)?tiktok\.com/[^\s]+')
OTHER_PLATFORM_REGEX = re.compile(
    r'https?://(?:'
    r'(?:pin\.it|www\.pinterest\.com)/[^\s]+|'
    r'(?:facebook\.com|fb\.watch)/[^\s]+|'
    r'(?:instagram\.com)/(?:reel|p|tv)/[^\s]+|'
    r'(?:youtube\.com|youtu\.be)/[^\s]+|'
    r'snapchat\.com/t/[^\s]+|'
    r'l\.likee\.video/v/[^\s]+|'
    r'(?:x\.com|twitter\.com)/[^\s]+/status/\d+'
    r')'
)

@bot.message_handler(func=lambda m: m.text and (TIKTOK_REGEX.search(m.text) or OTHER_PLATFORM_REGEX.search(m.text)))
def media_link_handler(message):
    text = message.text.strip()
    url = None
    platform = None

    # TikTok
    if TIKTOK_REGEX.search(text):
        url = TIKTOK_REGEX.search(text).group(0)
        platform = "TikTok"

    # Other platforms
    elif OTHER_PLATFORM_REGEX.search(text):
        url = OTHER_PLATFORM_REGEX.search(text).group(0)
        if "pin.it" in url or "pinterest.com" in url:
            platform = "Pinterest"
        elif "youtu.be" in url or "youtube.com" in url:
            platform = "YouTube"
        elif "instagram.com" in url:
            platform = "Instagram"
        elif "facebook.com" in url or "fb.watch" in url:
            platform = "Facebook"
        elif "snapchat.com" in url:
            platform = "Snapchat"
        elif "likee.video" in url:
            platform = "Likee"
        elif "x.com" in url or "twitter.com" in url:
            platform = "X (Twitter)"

    if not platform:
        return bot.send_message(message.chat.id, "⚠️ Link detected but could not identify the platform.")

    # Show action buttons
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton(f"Download {platform} 📥", callback_data=f"download|{url}"),
        telebot.types.InlineKeyboardButton(f"Transcribe {platform} 📝", callback_data=f"transcribe|{url}")
    )
    bot.send_message(message.chat.id, f"{platform} link detected—choose an action:", reply_markup=markup)

    # If you want to auto-start download without clicking, uncomment below:
    # callback_download_media(type('Call', (), {'data': f'download|{url}', 'message': message}))

@bot.callback_query_handler(func=lambda c: c.data.startswith("download|"))
def callback_download_media(call):
    global total_tiktok_downloads, total_other_downloads
    _, url = call.data.split("|", 1)
    bot.send_chat_action(call.message.chat.id, 'typing')

    ydl_opts = {
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
        'format': 'mp4',
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Referer':  'https://www.likee.video/'
        }
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
        with open(path, 'rb') as video:
            bot.send_video(call.message.chat.id, video, caption=info.get('description', ''))
        if "tiktok" in url:
            total_tiktok_downloads += 1
        else:
            total_other_downloads += 1
    except Exception as e:
        logging.error(f"Download error ({url}): {e}")
        bot.send_message(call.message.chat.id, f"⚠️ Failed to download media from {url}.")
    finally:
        if 'path' in locals() and os.path.exists(path):
            os.remove(path)

@bot.callback_query_handler(func=lambda c: c.data.startswith("transcribe|"))
def callback_transcribe_media(call):
    _, url = call.data.split("|", 1)
    bot.send_chat_action(call.message.chat.id, 'typing')

    ydl_opts = {
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
        'format': 'bestaudio/best',
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Referer':  'https://www.likee.video/'
        }
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            audio_path = ydl.prepare_filename(info)

        start = datetime.now()
        transcription = transcribe(audio_path) or ""
        total_processing_time += (datetime.now() - start).total_seconds()

        uid = str(call.from_user.id)
        last_transcription[uid] = transcription

        if len(transcription) > 4000:
            fn = 'trans.txt'
            with open(fn, 'w', encoding='utf-8') as f:
                f.write(transcription)
            bot.send_document(call.message.chat.id, open(fn, 'rb'))
            os.remove(fn)
        else:
            bot.send_message(call.message.chat.id, transcription)
    except Exception as e:
        logging.error(f"Transcribe error ({url}): {e}")
        bot.send_message(call.message.chat.id, f"⚠️ Failed to transcribe media from {url}.")
    finally:
        if 'audio_path' in locals() and os.path.exists(audio_path):
            os.remove(audio_path)

@bot.message_handler(commands=['translate'])
def handle_translate(message):
    uid = str(message.from_user.id)
    if uid not in last_transcription:
        return bot.send_message(message.chat.id, "❌ No previous transcription found.")
    prompt_msg = bot.send_message(message.chat.id, "Enter target language:")
    bot.register_next_step_handler(prompt_msg, lambda resp: do_translate(resp, uid))

def do_translate(message, uid):
    lang = message.text.strip()
    original = last_transcription.get(uid, "")
    translated = ask_gemini(uid, f"Translate to {lang}:\n\n{original}")
    if len(translated) > 4000:
        fn = 'trans.txt'
        with open(fn, 'w', encoding='utf-8') as f:
            f.write(translated)
        bot.send_document(message.chat.id, open(fn, 'rb'))
        os.remove(fn)
    else:
        bot.send_message(message.chat.id, translated)

@bot.message_handler(commands=['summarize'])
def handle_summarize(message):
    uid = str(message.from_user.id)
    if uid not in last_transcription:
        return bot.send_message(message.chat.id, "❌ No previous transcription found.")
    prompt_msg = bot.send_message(message.chat.id, "Enter summary language:")
    bot.register_next_step_handler(prompt_msg, lambda resp: do_summarize(resp, uid))

def do_summarize(message, uid):
    lang = message.text.strip()
    original = last_transcription.get(uid, "")
    summary = ask_gemini(uid, f"Summarize in {lang}:\n\n{original}")
    if len(summary) > 4000:
        fn = 'trans.txt'
        with open(fn, 'w', encoding='utf-8') as f:
            f.write(summary)
        bot.send_document(message.chat.id, open(fn, 'rb'))
        os.remove(fn)
    else:
        bot.send_message(message.chat.id, summary)

def transcribe(path: str) -> str | None:
    try:
        segments, _ = model.transcribe(path, beam_size=1)
        return " ".join(s.text for s in segments)
    except Exception as e:
        logging.error(f"Transcription error: {e}")
        return None

@app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        update = telebot.types.Update.de_json(request.get_data().decode('utf-8'))
        bot.process_new_updates([update])
        return '', 200
    return abort(403)

@app.route('/set_webhook', methods=['GET','POST'])
def set_webhook():
    bot.set_webhook(url="https://your-app-url.com")
    return 'Webhook set.', 200

@app.route('/delete_webhook', methods=['GET','POST'])
def delete_webhook():
    bot.delete_webhook()
    return 'Webhook deleted.', 200

if __name__ == "__main__":
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    bot.delete_webhook()
    bot.set_webhook(url="https://your-app-url.com")
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 8080)))
