# main.py

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

# Logger setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Constants
TOKEN = "7648822901:AAG3ZJADuvTP_9Gmx0matFCsJU6aWeRJstk"
REQUIRED_CHANNEL = "@mediatranscriber"
ADMIN_ID = 5978150981
DOWNLOAD_DIR = "downloads"
GEMINI_API_KEY = "AIzaSyAto78yGVZobxOwPXnl8wCE9ZW8Do2R8HA"
FILE_SIZE_LIMIT = 20 * 1024 * 1024

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

model = WhisperModel(model_size_or_path="tiny", device="cpu", compute_type="int8")

# Track users
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

# In-memory
user_memory = {}
last_transcription = {}

# Bot stats
total_files_processed = total_audio_files = total_voice_clips = total_videos = 0
total_processing_time = 0.0
processing_start_time = None
admin_state = {}

# Set bot descriptions
def set_bot_info():
    commands = [
        telebot.types.BotCommand("start", "Restart the bot 🤖"),
        telebot.types.BotCommand("status", "Show bot statistics 👀"),
        telebot.types.BotCommand("help", "Show usage instructions ℹ️"),
        telebot.types.BotCommand("translate", "Translate last transcription 🌐"),
        telebot.types.BotCommand("summarize", "Summarize last transcription 📝"),
    ]
    bot.set_my_commands(commands)
    bot.set_my_description(
        description=(
            "This Bot Does It All!\n"
            "🎧 Automatically transcribes audio, voice notes & videos\n"
            "• Detects and supports multiple languages\n"
            "• ⚡ Super fast, highly accurate\n"
            "• Downloads TikTok videos — and transcribes them too!\n\n"
            "✨ Features:\n"
            "▫️ Summarization\n"
            "▫️ Translation\n"
            "▫️ Or both at once!\n\n"
            "Totally FREE — no sign-up, no cost, just pure convenience.\n"
            "⏳ Save time. Get more done. Effortlessly!"
        )
    )
    bot.set_my_short_description(
        short_description="🎙️ Audio → Text | 📥 TikTok Downloader\n⚡ Fast • Easy • 100% Free"
    )

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

def check_subscription(user_id):
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return False

def send_subscription_message(chat_id):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("Join Channel", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"))
    bot.send_message(chat_id, "❗ Please join our channel to use this bot:", reply_markup=markup)

def update_user_activity(user_id):
    user_data[str(user_id)] = datetime.now().isoformat()
    save_user_data()

@bot.message_handler(commands=['start'])
def start_handler(message):
    update_user_activity(message.from_user.id)
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)
    name = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
    if message.from_user.id == ADMIN_ID:
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Send Broadcast", "Total Users", "/status")
        bot.send_message(message.chat.id, "Admin Panel", reply_markup=keyboard)
    else:
        bot.send_message(
            message.chat.id,
            f"👋 Salaam, {name}!\n"
            "Welcome!\n"
            "• Send me a voice note, audio, video, or even a TikTok link\n"
            "• I’ll download it, transcribe it, and send it back — fast & easy!"
        )

@bot.message_handler(commands=['help'])
def help_handler(message):
    bot.send_message(
        message.chat.id,
        (
            "ℹ️ *How to use this bot:*\n\n"
            "1. *Join our channel:* @mediatranscriber\n"
            "2. *Send media:* voice, audio, video, or TikTok link\n"
            "3. *Download TikTok:* tap 📥\n"
            "4. *Transcribe TikTok:* tap 📝\n"
            "5. *Translate:* /translate\n"
            "6. *Summarize:* /summarize\n"
            "7. *Status:* /status\n\n"
            "Enjoy fast, easy, and free transcriptions!"
        ),
        parse_mode="Markdown"
    )

# --- Remaining handlers (status, files, TikTok, translate/summarize, etc) ---
# (Use your original code from previous message for those)

# Example for webhook setup
@app.route('/set_webhook', methods=['GET', 'POST'])
def set_webhook():
    url = "https://telegram-bot-media-transcriber.onrender.com"
    bot.set_webhook(url=url)
    return f"Webhook set to {url}", 200

@app.route('/delete_webhook', methods=['GET', 'POST'])
def delete_webhook():
    bot.delete_webhook()
    return 'Webhook deleted.', 200

@app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        update = telebot.types.Update.de_json(request.get_data().decode('utf-8'))
        bot.process_new_updates([update])
        return '', 200
    return abort(403)

if __name__ == "__main__":
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    set_bot_info()
    bot.delete_webhook()
    bot.set_webhook(url="https://telegram-bot-media-transcriber.onrender.com")
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 8080)))
