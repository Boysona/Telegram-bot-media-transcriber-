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

# Configure logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# YOUR BOT TOKEN and CHANNEL
TOKEN = "7648822901:AAG3ZJADuvTP_9Gmx0matFCsJU6aWeRJstk"
REQUIRED_CHANNEL = "@mediatranscriber"

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Admin
ADMIN_ID = 5978150981

# Download Directory
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Whisper Model
model = WhisperModel(
    model_size_or_path="tiny",
    device="cpu",
    compute_type="int8"
)

# User Tracking (using JSON)
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

# In-memory per-user chat history for Gemini
user_memory = {}
# Store last transcription per user for translation and summary
last_transcription = {}

GEMINI_API_KEY = "AIzaSyAto78yGVZobxOwPXnl8wCE9ZW8Do2R8HA"

def ask_gemini(user_id, user_message):
    if user_id not in user_memory:
        user_memory[user_id] = []
    user_memory[user_id].append({"role": "user", "text": user_message})
    history = user_memory[user_id][-10:]
    parts = [{"text": msg["text"]} for msg in history]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    payload = {"contents": [{"parts": parts}]}
    try:
        resp = requests.post(url, headers=headers, data=json.dumps(payload))
        result = resp.json()
        if "candidates" in result:
            reply = result['candidates'][0]['content']['parts'][0]['text']
            user_memory[user_id].append({"role": "model", "text": reply})
            return reply
        else:
            return "Gemini API error: " + json.dumps(result)
    except Exception as e:
        return f"Error: {e}"

# Processing Statistics
total_files_processed = 0
total_audio_files = 0
total_voice_clips = 0
total_videos = 0
total_processing_time = 0.0  # in seconds
processing_start_time = None

# Constants
FILE_SIZE_LIMIT = 20 * 1024 * 1024

# Admin state
admin_state = {}

def set_bot_info():
    """Sets the bot's commands, description, and short description."""
    try:
        commands = [
            telebot.types.BotCommand(command="start", description="Restart the robot🤖"),
            telebot.types.BotCommand(command="status", description="Show bot statistics👀"),
            telebot.types.BotCommand(command="help", description="Show usage instructions ℹ️"),
            telebot.types.BotCommand(command="translate", description="Translate last transcription 🌐"),
            telebot.types.BotCommand(command="summarize", description="Summarize last transcription 📝"),
        ]
        bot.set_my_commands(commands=commands)
        bot.set_my_description(description="Download TikTok videos, transcribe media to text, and translate/summarize content. Supports voice, audio, video files and TikTok links - fast, easy, and free!")
        bot.set_my_short_description(short_description="Download TikTok videos & transcribe media - with translation & summarization!")
        logging.info("Bot commands, description, and short description set successfully.")
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Error setting bot info: {e}")

def is_tiktok_url(text):
    pattern = r'https?://(www\.)?(vm\.|vt\.)?tiktok\.com/.+'
    return re.match(pattern, text) is not None

def get_tiktok_video(url):
    try:
        api_url = "https://api.tikmate.app/api/upload"
        response = requests.post(api_url, data={"url": url})
        data = response.json()
        if data.get("success"):
            return data["video_url"], data.get("description", "No description available")
        return None, None
    except Exception as e:
        logging.error(f"TikTok API error: {e}")
        return None, None

@bot.message_handler(func=lambda m: is_tiktok_url(m.text))
def handle_tiktok_url(message):
    user_id = str(message.from_user.id)
    update_user_activity(user_id)

    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    url = message.text.strip()
    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(
        telebot.types.InlineKeyboardButton("Download 📥", callback_data=f"download_{url}"),
        telebot.types.InlineKeyboardButton("Transcribe 📝", callback_data=f"transcribe_{url}")
    )
    bot.reply_to(message, "Choose an action:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('download_'))
def handle_download(call):
    user_id = str(call.from_user.id)
    update_user_activity(user_id)
    
    if not check_subscription(call.from_user.id):
        bot.answer_callback_query(call.id, "Please join our channel first!", show_alert=True)
        return

    url = call.data.split('_', 1)[1]
    video_url, description = get_tiktok_video(url)
    
    if not video_url:
        bot.answer_callback_query(call.id, "Failed to download video", show_alert=True)
        return

    try:
        response = requests.get(video_url)
        if response.status_code == 200:
            file_path = os.path.join(DOWNLOAD_DIR, f"tiktok_{uuid.uuid4()}.mp4")
            with open(file_path, 'wb') as f:
                f.write(response.content)
            
            with open(file_path, 'rb') as video_file:
                bot.send_video(call.message.chat.id, video_file, caption=description)
            
            os.remove(file_path)
        else:
            bot.answer_callback_query(call.id, "Failed to download video", show_alert=True)
    except Exception as e:
        logging.error(f"Error downloading TikTok: {e}")
        bot.answer_callback_query(call.id, "Error processing video", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('transcribe_'))
def handle_transcribe(call):
    user_id = str(call.from_user.id)
    update_user_activity(user_id)
    
    if not check_subscription(call.from_user.id):
        bot.answer_callback_query(call.id, "Please join our channel first!", show_alert=True)
        return

    url = call.data.split('_', 1)[1]
    video_url, _ = get_tiktok_video(url)
    
    if not video_url:
        bot.answer_callback_query(call.id, "Failed to download video", show_alert=True)
        return

    try:
        response = requests.get(video_url)
        if response.status_code == 200:
            file_path = os.path.join(DOWNLOAD_DIR, f"tiktok_{uuid.uuid4()}.mp4")
            with open(file_path, 'wb') as f:
                f.write(response.content)
            
            transcription = transcribe(file_path)
            os.remove(file_path)
            
            if transcription:
                last_transcription[user_id] = transcription
                if len(transcription) > 2000:
                    with open('transcription.txt', 'w', encoding='utf-8') as f:
                        f.write(transcription)
                    with open('transcription.txt', 'rb') as f:
                        bot.send_document(call.message.chat.id, f)
                    os.remove('transcription.txt')
                else:
                    bot.send_message(call.message.chat.id, transcription)
            else:
                bot.send_message(call.message.chat.id, "Failed to transcribe video")
        else:
            bot.answer_callback_query(call.id, "Failed to download video", show_alert=True)
    except Exception as e:
        logging.error(f"Error transcribing TikTok: {e}")
        bot.answer_callback_query(call.id, "Error processing video", show_alert=True)

# ... (keep existing functions for check_subscription, send_subscription_message, 
# get_user_counts, update_user_activity, etc. unchanged except where noted below)

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(user_id)

    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    if message.from_user.id == ADMIN_ID:
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("Send Broadcast", "Total Users", "/status")
        bot.send_message(message.chat.id, "Admin Panel", reply_markup=markup)
    else:
        username = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
        text = f"👋 Hello {username}\n\n• Send a voice, video, audio file, or TikTok link\n• I will transcribe/download it and send it back to you!"
        bot.send_message(message.chat.id, text)

@bot.message_handler(commands=['help'])
def help_handler(message):
    help_text = """ℹ️ How to use this bot:

1. **For Media Files:**
   - Send voice messages, audio files, or video files
   - Automatic transcription to text

2. **For TikTok Videos:**
   - Send any TikTok video link
   - Choose to download video or transcribe its audio

3. **Additional Features:**
   - /translate - Translate last transcription
   - /summarize - Summarize last transcription

4. **Supported Formats:**
   - All common audio/video formats
   - TikTok links (public videos)

📌 Remember to join @mediatranscriber to use the bot"""
    bot.send_message(message.chat.id, help_text)

# ... (rest of the existing code remains the same)

if __name__ == "__main__":
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    set_bot_info()
    bot.delete_webhook()
    bot.set_webhook(url="https://telegram-bot-media-transcriber.onrender.com")
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 8080)))
