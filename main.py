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
from typing import Optional

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

# User Tracking
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

# Stats
total_files_processed = 0
total_audio_files = 0
total_voice_clips = 0
total_videos = 0
total_processing_time = 0.0

# Constants
FILE_SIZE_LIMIT = 20 * 1024 * 1024

# Admin state
admin_state = {}

def set_bot_info():
    try:
        commands = [
            telebot.types.BotCommand(command="start", description="Restart the bot"),
            telebot.types.BotCommand(command="status", description="Show bot statistics"),
            telebot.types.BotCommand(command="help", description="Show help information")
        ]
        bot.set_my_commands(commands=commands)
        bot.set_my_description(description="Transcribe voice, audio, and video files into text quickly and for free. Multi-language support with automatic detection.")
        bot.set_my_short_description(short_description="Fast & free media transcription bot")
        logging.info("Bot info set successfully.")
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Error setting bot info: {e}")

def check_subscription(user_id):
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Subscription check failed for user {user_id}: {e}")
        return False

def send_subscription_message(chat_id):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton(
        text="Join the Channel",
        url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"
    ))
    bot.send_message(chat_id, "🥺 Sorry...\n\nPlease join our channel @mediatranscriber to use this bot.\n\nAfter joining, send /start to continue.", reply_markup=markup)

def get_user_counts():
    total_users = len(user_data)
    now = datetime.now()
    monthly_active = sum(1 for _, last_active in user_data.items() if (now - datetime.fromisoformat(last_active)).days < 30)
    weekly_active = sum(1 for _, last_active in user_data.items() if (now - datetime.fromisoformat(last_active)).days < 7)
    return total_users, monthly_active, weekly_active

def update_user_activity(user_id):
    user_data[str(user_id)] = datetime.now().isoformat()
    save_user_data()

def format_timedelta(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours} hrs {minutes} mins"

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(user_id)

    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    if message.from_user.id == ADMIN_ID:
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("Send Broadcast", "Total Users", "/status")
        bot.send_message(message.chat.id, "Welcome to the Admin Panel", reply_markup=markup)
    else:
        username = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
        bot.send_message(message.chat.id, f"👋 Hello {username}\n\n• Send a voice, video, or audio file.\n• I will transcribe it and send it back to you!")

@bot.message_handler(commands=['help'])
def help_handler(message):
    bot.send_message(message.chat.id, """ℹ️ How to use this bot:

1. **Join the Channel:** You must join @mediatranscriber to use this bot.
2. **Send a File:** Send a voice, audio, or video file.
3. **Transcription:** The bot will convert it into text.
4. **Output:** You'll receive the transcription as a message or file.

Commands:
/start - Restart the bot
/status - Show usage statistics
/help - Show this help message
""")

@bot.message_handler(commands=['status'])
def status_handler(message):
    total_users, _, _ = get_user_counts()
    bot.send_message(message.chat.id, f"""📊 Bot Statistics:

Users: {total_users}
Files Processed: {total_files_processed}
- Audio: {total_audio_files}
- Voice: {total_voice_clips}
- Video: {total_videos}
Processing Time: {format_timedelta(total_processing_time)}
""")

@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def show_total_users(message):
    bot.send_message(message.chat.id, f"Total users: {len(user_data)}")

@bot.message_handler(func=lambda m: m.text == "Send Broadcast" and m.from_user.id == ADMIN_ID)
def send_broadcast(message):
    admin_state[message.from_user.id] = 'awaiting_broadcast'
    bot.send_message(message.chat.id, "Please send the message you want to broadcast.")

@bot.message_handler(func=lambda m: m.from_user.id == ADMIN_ID and admin_state.get(m.from_user.id) == 'awaiting_broadcast', content_types=['text', 'photo', 'video', 'audio', 'document'])
def broadcast_handler(message):
    admin_state[message.from_user.id] = None
    success, failed = 0, 0
    for user_id in user_data:
        try:
            bot.copy_message(user_id, message.chat.id, message.message_id)
            success += 1
        except Exception as e:
            logging.error(f"Broadcast failed for {user_id}: {e}")
            failed += 1
    bot.send_message(message.chat.id, f"Broadcast finished.\n✅ Sent: {success}\n❌ Failed: {failed}")

@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note'])
def handle_file(message):
    global total_files_processed, total_audio_files, total_voice_clips, total_videos, total_processing_time

    user_id = str(message.from_user.id)
    update_user_activity(user_id)

    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    # Get file size
    media = message.voice or message.audio or message.video or message.video_note
    file_size = media.file_size

    if file_size > FILE_SIZE_LIMIT:
        bot.send_message(message.chat.id, "⚠️ The file is too large. Max allowed size is 20MB.")
        return

    file_info = bot.get_file(media.file_id)
    file_path = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}.ogg")

    try:
        bot.send_chat_action(message.chat.id, 'typing')
        downloaded = bot.download_file(file_info.file_path)
        with open(file_path, 'wb') as f:
            f.write(downloaded)

        start = datetime.now()
        text = transcribe(file_path)
        end = datetime.now()

        duration = (end - start).total_seconds()
        total_processing_time += duration
        total_files_processed += 1

        if message.content_type == 'audio':
            total_audio_files += 1
        elif message.content_type == 'voice':
            total_voice_clips += 1
        elif message.content_type in ['video', 'video_note']:
            total_videos += 1

        if text:
            if len(text) > 2000:
                with open('transcription.txt', 'w', encoding='utf-8') as f:
                    f.write(text)
                with open('transcription.txt', 'rb') as f:
                    bot.send_document(message.chat.id, f, reply_to_message_id=message.message_id)
                os.remove('transcription.txt')
            else:
                bot.reply_to(message, text)
        else:
            bot.send_message(message.chat.id, "⚠️ Could not transcribe the audio. Please try again.")
    except Exception as e:
        logging.error(f"File processing failed: {e}")
        bot.send_message(message.chat.id, "⚠️ An error occurred during transcription.")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

def transcribe(file_path: str) -> Optional[str]:
    try:
        segments, _ = model.transcribe(file_path, beam_size=1)
        return " ".join(segment.text for segment in segments)
    except Exception as e:
        logging.error(f"Transcription error: {e}")
        return None

@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'sticker', 'document'])
def fallback_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(user_id)

    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    bot.send_message(message.chat.id, "⚠️ Please send only a voice message, audio, or video file.")

@app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        update = telebot.types.Update.de_json(request.get_data().decode('utf-8'))
        bot.process_new_updates([update])
        return '', 200
    else:
        abort(403)

@app.route('/set_webhook', methods=['GET', 'POST'])
def set_webhook():
    webhook_url = "https://telegram-bot-media-transcriber.onrender.com"
    bot.set_webhook(url=webhook_url)
    return f"Webhook is set to: {webhook_url}", 200

@app.route('/delete_webhook', methods=['GET', 'POST'])
def delete_webhook():
    bot.delete_webhook()
    return "Webhook deleted.", 200

if __name__ == "__main__":
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    set_bot_info()
    bot.delete_webhook()
    bot.set_webhook(url="https://telegram-bot-media-transcriber.onrender.com")
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 8080)))
