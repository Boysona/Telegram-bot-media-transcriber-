# ==================== START OF TELEGRAM BOT SCRIPT ====================
# Telegram Bot to Transcribe and Download Media from Multiple Platforms
# Updated for improved platform detection and error handling
# Author: ChatGPT (based on user-provided base)

import os
import re
import uuid
import logging
import json
import requests
import telebot
from flask import Flask
from faster_whisper import WhisperModel
from datetime import datetime
import yt_dlp

# --- Config ---
TOKEN = "7648822901:AAFQEUx-S4bpD5qUMPHNB1P9jYCYSB4mzHU"
REQUIRED_CHANNEL = "@mediatranscriber"
GEMINI_API_KEY = "AIzaSyAto78yGVZobxOwPXnl8wCE9ZW8Do2R8HA"
FILE_SIZE_LIMIT = 20 * 1024 * 1024  # 20MB

# --- Init ---
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)
model = WhisperModel("tiny", device="cpu", compute_type="int8")
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

ADMIN_ID = 5978150981
user_data = {}
users_file = 'users.json'
if os.path.exists(users_file):
    with open(users_file, 'r') as f:
        try:
            user_data = json.load(f)
        except:
            user_data = {}

def save_user_data():
    with open(users_file, 'w') as f:
        json.dump(user_data, f, indent=4)

# --- Memory ---
user_memory = {}
last_transcription = {}
total_files_processed = total_audio_files = total_voice_clips = total_videos = 0
total_tiktok_downloads = total_other_downloads = total_processing_time = 0
processing_start_time = None
admin_state = {}

# --- Platform regex ---
PLATFORM_REGEX = re.compile(r'(https?://(?:www\.)?(?:youtube\.com|youtu\.be|facebook\.com|fb\.watch|'
                            r'instagram\.com/(?:reel|p|tv)|tiktok\.com|vm\.tiktok\.com|x\.com|twitter\.com|'
                            r'pin\.it|pinterest\.com|snapchat\.com/t|likee\.video)/[^\s]+)')

# --- Gemini ---
def ask_gemini(user_id, user_message):
    user_memory.setdefault(user_id, []).append({"role": "user", "text": user_message})
    history = user_memory[user_id][-10:]
    parts = [{"text": msg["text"]} for msg in history]
    resp = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}",
                         headers={'Content-Type': 'application/json'},
                         json={"contents": [{"parts": parts}]})
    result = resp.json()
    if "candidates" in result:
        reply = result['candidates'][0]['content']['parts'][0]['text']
        user_memory[user_id].append({"role": "model", "text": reply})
        return reply
    return "Error: " + json.dumps(result)

# --- Subscription Check ---
def check_subscription(user_id):
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return False

def send_subscription_message(chat_id):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("Join Channel", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"))
    bot.send_message(chat_id, "Please join the channel to use this bot.", reply_markup=markup)

# --- Transcription ---
def transcribe(path):
    try:
        segments, _ = model.transcribe(path, beam_size=1)
        return " ".join(segment.text for segment in segments)
    except Exception as e:
        logging.error(f"Transcription error: {e}")
        return None

# --- Download & Transcribe Handler ---
@bot.message_handler(func=lambda m: m.text and PLATFORM_REGEX.search(m.text))
def handle_link(message):
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)
    url = PLATFORM_REGEX.search(message.text).group(0)
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton("Download Video", callback_data=f"download|{url}"),
        telebot.types.InlineKeyboardButton("Transcribe Audio", callback_data=f"transcribe|{url}")
    )
    bot.send_message(message.chat.id, "Choose an action:", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("download|"))
def download_media(call):
    url = call.data.split("|", 1)[1]
    try:
        ydl_opts = {'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'), 'format': 'mp4'}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)
        with open(filepath, 'rb') as f:
            bot.send_video(call.message.chat.id, f, caption=info.get("title", ""))
    except Exception as e:
        logging.error(f"Download failed: {e}")
        bot.send_message(call.message.chat.id, f"Failed to download media from {url}.")
    finally:
        if 'filepath' in locals() and os.path.exists(filepath):
            os.remove(filepath)

@bot.callback_query_handler(func=lambda c: c.data.startswith("transcribe|"))
def transcribe_media(call):
    url = call.data.split("|", 1)[1]
    try:
        ydl_opts = {'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'), 'format': 'bestaudio/best'}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)
        transcription = transcribe(filepath)
        if transcription:
            bot.send_message(call.message.chat.id, transcription)
        else:
            bot.send_message(call.message.chat.id, "Transcription failed.")
    except Exception as e:
        logging.error(f"Transcribe failed: {e}")
        bot.send_message(call.message.chat.id, f"Failed to transcribe media from {url}.")
    finally:
        if 'filepath' in locals() and os.path.exists(filepath):
            os.remove(filepath)

# --- Voice/Audio/Video Handler ---
@bot.message_handler(content_types=['voice', 'audio', 'video'])
def handle_file(message):
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)
    file_obj = message.voice or message.audio or message.video
    if file_obj.file_size > FILE_SIZE_LIMIT:
        return bot.send_message(message.chat.id, "File too large (max 20MB).")
    file_info = bot.get_file(file_obj.file_id)
    filepath = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}.ogg")
    with open(filepath, 'wb') as f:
        f.write(bot.download_file(file_info.file_path))
    transcription = transcribe(filepath)
    if transcription:
        bot.reply_to(message, transcription)
    else:
        bot.reply_to(message, "Failed to transcribe audio.")
    os.remove(filepath)

# --- Bot Start Command ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    user_data[str(message.from_user.id)] = datetime.now().isoformat()
    save_user_data()
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)
    bot.send_message(message.chat.id, "Send me a voice/audio/video or link to transcribe or download.")
    @app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        update = telebot.types.Update.de_json(request.get_data().decode('utf-8'))
        bot.process_new_updates([update])
        return '', 200
    return abort(403)

@app.route('/set_webhook', methods=['GET','POST'])
def set_webhook():
    url = "https://telegram-bot-media-transcriber.onrender.com"
    bot.set_webhook(url=url)
    return f"Webhook set to {url}", 200

@app.route('/delete_webhook', methods=['GET','POST'])
def delete_webhook():
    bot.delete_webhook()
    return 'Webhook deleted.', 200

if __name__ == "__main__":
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    set_bot_info()
    bot.delete_webhook()
    bot.set_webhook(url="https://telegram-bot-media-transcriber-ihi5.onrender.com")
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 8080)))

