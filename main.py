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
TOKEN = "7627284411:AAF39NuD9RAZRpYE5rYQGaKojMnX2pTnvXE"
REQUIRED_CHANNEL = "@Mediatotxtbot"

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
today_users = set()
today_transcriptions = 0
file_types_processed = {"voice": 0, "audio": 0, "video": 0, "document": 0}
language_counts = {"en": 0} # Waxaan u maleyneynaa in luqadda ugu badan ay tahay English

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
    bot.send_message(chat_id, "⚠️ Please join the channel to continue using this bot!", reply_markup=markup)

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

def update_daily_stats(user_id):
    global today_users
    if user_id not in today_users:
        today_users.add(user_id)
    global today_transcriptions
    today_transcriptions += 1

def update_file_type(content_type):
    if content_type == 'voice':
        file_types_processed['voice'] += 1
    elif content_type == 'audio':
        file_types_processed['audio'] += 1
    elif content_type == 'video' or content_type == 'video_note':
        file_types_processed['video'] += 1
    elif content_type == 'document':
        file_types_processed['document'] += 1

def get_top_languages():
    total = sum(language_counts.values())
    top_languages_formatted = []
    for lang, count in language_counts.items():
        percentage = (count / total) * 100 if total > 0 else 0
        top_languages_formatted.append(f"• {lang}: {percentage:.1f}%")
    return "\n".join(top_languages_formatted)

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
    status_text = f"""📊 Bot Usage Statistics:

👥 User Statistics (Today):
▫️ Users: {len(today_users)}
▫️ Transcriptions: {today_transcriptions}

📊 File Types Processed:
  🎤 Voice Messages: {file_types_processed.get('voice', 0)}
  🎵 Audio Files: {file_types_processed.get('audio', 0)}
  🎥 Video Files: {file_types_processed.get('video', 0)}
  📄 Documents: {file_types_processed.get('document', 0)}

🌍 Top Languages:
  {get_top_languages()}
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

    user_id = str(message.from_user.id)
    update_daily_stats(user_id)

    content_type = None
    if message.voice:
        content_type = 'voice'
    elif message.audio:
        content_type = 'audio'
    elif message.video or message.video_note:
        content_type = 'video'
    update_file_type(content_type)

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
            bot.send_message(message.chat.id, "⚠️ I could not transcribe the audio.")

    except Exception as e:
        logging.error(f"Error handling file: {e}")
        bot.send_message(message.chat.id, "⚠️ An error occurred while processing the file.")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

def transcribe(file_path: str) -> str | None:
    try:
        segments, _ = model.transcribe(file_path, beam_size=1)
        # Halkan waxaan ku dari karnaa ogaanshaha luqadda haddii loo baahdo
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
