import os
import re
import uuid
import shutil
import logging
import requests
import telebot
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

# User Tracking
existing_users = set()
users_file = 'users.txt'
if os.path.exists(users_file):
    with open(users_file, 'r') as f:
        existing_users = set(line.strip() for line in f)

# Processing Statistics
total_files_processed = 0
total_processing_time = 0.0  # in seconds
processing_start_time = None

# Constants
FILE_SIZE_LIMIT = 20 * 1024 * 1024

# Admin state
admin_state = {}

def check_subscription(user_id):
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Subscription check error for user {user_id}: {e}")
        return False

def send_subscription_message(chat_id):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton(
        text="Join the Channel",
        url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"
    ))
    bot.send_message(chat_id, "⚠️ first Please join the channel to continue using this bot!", reply_markup=markup)

def get_user_counts():
    total_users = len(existing_users)
    now = datetime.now()
    monthly_active_users = sum(1 for user_id in existing_users if is_active_within(user_id, 30))
    weekly_active_users = sum(1 for user_id in existing_users if is_active_within(user_id, 7))
    return total_users, monthly_active_users, weekly_active_users

def is_active_within(user_id, days):
    return True

def format_timedelta(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours} hours {minutes} minutes"

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = str(message.from_user.id)
    if user_id not in existing_users:
        existing_users.add(user_id)
        with open(users_file, 'a') as f:
            f.write(f"{user_id}\n")

    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    if message.from_user.id == ADMIN_ID:
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("Send Broadcast", "Total Users", "/status")
        bot.send_message(message.chat.id, "Admin Panel", reply_markup=markup)
    else:
        username = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
        text = f"👋 Hello {username}\n\n• Send a voice, video, or audio file.\n• I will transcribe it and send it back to you!"
        bot.send_message(message.chat.id, text)

@bot.message_handler(commands=['status'])
def status_handler(message):
    total_users, monthly_active_users, weekly_active_users = get_user_counts()
    status_text = f"""📈 Overall Statistics

👥 User Statistics:
▫️ Total Users: {total_users}
▫️ Active Users This Month: {monthly_active_users}
▫️ Active Users This Week: {weekly_active_users}

🎯 Processing Statistics:
▫️ Total Files Processed: {total_files_processed}
▫️ Total Processing Time: {format_timedelta(total_processing_time)}

Thank you for using our service! 🙏
"""
    bot.send_message(message.chat.id, status_text)

@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def total_users(message):
    bot.send_message(message.chat.id, f"Total users: {len(existing_users)}")

@bot.message_handler(func=lambda m: m.text == "Send Broadcast" and m.from_user.id == ADMIN_ID)
def send_broadcast(message):
    admin_state[message.from_user.id] = 'awaiting_broadcast'
    bot.send_message(message.chat.id, "Send the message you want to broadcast:")

@bot.message_handler(func=lambda m: m.from_user.id == ADMIN_ID and admin_state.get(m.from_user.id) == 'awaiting_broadcast',
                     content_types=['text', 'photo', 'video', 'audio', 'document'])
def broadcast_message(message):
    admin_state[message.from_user.id] = None
    success = 0
    fail = 0
    for user_id in existing_users:
        try:
            bot.copy_message(user_id, message.chat.id, message.message_id)
            success += 1
        except telebot.apihelper.ApiTelegramException as e:
            logging.error(f"Broadcast failed for user {user_id}: {e}")
            fail += 1
    bot.send_message(message.chat.id, f"Broadcast completed.\nSuccessful: {success}\nFailed: {fail}")

@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note'])
def handle_file(message):
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    file_size = (message.voice or message.audio or message.video or message.video_note).file_size

    if file_size > FILE_SIZE_LIMIT:
        bot.send_message(message.chat.id, "⚠️ The file is too large! Maximum allowed size is 20MB.")
        return

    file_info = bot.get_file((message.voice or message.audio or message.video or message.video_note).file_id)
    unique_filename = str(uuid.uuid4()) + ".ogg"
    file_path = os.path.join(DOWNLOAD_DIR, unique_filename)

    bot.send_chat_action(message.chat.id, 'typing')

    try:
        downloaded_file = bot.download_file(file_info.file_path)
        with open(file_path, 'wb') as f:
            f.write(downloaded_file)

        bot.send_chat_action(message.chat.id, 'typing')
        global processing_start_time
        processing_start_time = datetime.now()
        transcription = transcribe(file_path)
        global total_files_processed
        total_files_processed += 1
        if processing_start_time:
            processing_end_time = datetime.now()
            duration = (processing_end_time - processing_start_time).total_seconds()
            global total_processing_time
            total_processing_time += duration
            processing_start_time = None

        if transcription:
            if len(transcription) > 2000:
                with open('transcription.txt', 'w', encoding='utf-8') as f:
                    f.write(transcription)
                with open('transcription.txt', 'rb') as f:
                    bot.send_document(message.chat.id, f, reply_to_message_id=message.message_id)
                os.remove('transcription.txt')
            else:
                bot.reply_to(message, transcription)
        else:
            bot.send_message(message.chat.id, "⚠️ I could not transcribe the audio please clear your audio.")

    except Exception as e:
        logging.error(f"Error handling file: {e}")
        bot.send_message(message.chat.id, "⚠️ An error occurred while processing the file.")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

def transcribe(file_path: str) -> str | None:
    try:
        segments, _ = model.transcribe(file_path, beam_size=1)
        return " ".join(segment.text for segment in segments)
    except Exception as e:
        logging.error(f"Transcription error: {e}")
        return None

@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'sticker', 'document'])
def fallback(message):
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)
    bot.send_message(message.chat.id, "⚠️ Please send a voice, audio, video, or video note.")

@app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    else:
        abort(403)

@app.route('/set_webhook', methods=['GET', 'POST'])
def set_webhook():
    webhook_url = "https://telegram-bot-media-transcriber.onrender.com"
    if webhook_url:
        bot.set_webhook(url=webhook_url)
        return f'Webhook is set to: {webhook_url}', 200
    else:
        return 'Webhook URL not provided.', 400

@app.route('/delete_webhook', methods=['GET', 'POST'])
def delete_webhook():
    bot.delete_webhook()
    return 'Webhook deleted.', 200

if __name__ == "__main__":
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    bot.delete_webhook()
    bot.set_webhook(url="https://telegram-bot-media-transcriber.onrender.com")
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 8080)))
