import os
import uuid
import shutil
import logging
import threading
from datetime import datetime

import telebot
from flask import Flask, request, abort
from faster_whisper import WhisperModel

# BOT CONFIGURATION
TOKEN = "7648822901:AAGi4gZ8R3Xk9yT3nbXnB20Spv5BiTE2QWQ"
REQUIRED_CHANNEL = "@mediatranscriber"
ADMIN_ID = 5978150981

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Whisper Model - loaded once
model = WhisperModel("tiny", device="cpu", compute_type="int8")

# Load users
users_file = 'users.txt'
existing_users = set()
if os.path.exists(users_file):
    with open(users_file, 'r') as f:
        existing_users = set(line.strip() for line in f)

# Subscription check
def check_subscription(user_id):
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return False

def send_subscription_message(chat_id):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("Join Channel", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"))
    bot.send_message(chat_id, "⚠️ Please join the channel to use this bot!", reply_markup=markup)

# Transcription
def transcribe(file_path):
    try:
        segments, _ = model.transcribe(file_path, beam_size=1)
        return " ".join(segment.text for segment in segments)
    except Exception as e:
        logging.error(f"Transcription error: {e}")
        return None

# Commands
@bot.message_handler(commands=['start'])
def start(message):
    user_id = str(message.from_user.id)
    if user_id not in existing_users:
        existing_users.add(user_id)
        with open(users_file, 'a') as f:
            f.write(f"{user_id}\n")

    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    bot.send_message(message.chat.id, "👋 Send me a voice, audio, or video file to transcribe.")

# Voice handler in new thread
@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note'])
def handle_voice(message):
    threading.Thread(target=process_file, args=(message,)).start()

def process_file(message):
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    media = message.voice or message.audio or message.video or message.video_note
    if media.file_size > 20 * 1024 * 1024:
        bot.send_message(message.chat.id, "⚠️ File too large! Max 20MB.")
        return

    file_info = bot.get_file(media.file_id)
    file_path = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}.ogg")

    try:
        downloaded_file = bot.download_file(file_info.file_path)
        with open(file_path, 'wb') as f:
            f.write(downloaded_file)

        bot.send_chat_action(message.chat.id, 'typing')
        result = transcribe(file_path)

        if result:
            if len(result) > 2000:
                with open('transcript.txt', 'w', encoding='utf-8') as f:
                    f.write(result)
                with open('transcript.txt', 'rb') as f:
                    bot.send_document(message.chat.id, f)
                os.remove('transcript.txt')
            else:
                bot.send_message(message.chat.id, result)
        else:
            bot.send_message(message.chat.id, "⚠️ Couldn't transcribe the audio.")
    except Exception as e:
        logging.error(f"Processing error: {e}")
        bot.send_message(message.chat.id, "⚠️ Error while processing your file.")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

# Other files/text fallback
@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'sticker', 'document'])
def fallback(message):
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)
    bot.send_message(message.chat.id, "⚠️ Please send a voice, audio, or video file.")

# Webhook setup
@app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        update = telebot.types.Update.de_json(request.get_data().decode('utf-8'))
        bot.process_new_updates([update])
        return '', 200
    else:
        abort(403)

@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    url = "https://telegram-bot-media-transcriber.onrender.com"
    bot.set_webhook(url=url)
    return f"Webhook set to {url}", 200

@app.route('/delete_webhook', methods=['GET'])
def delete_webhook():
    bot.delete_webhook()
    return "Webhook deleted", 200

if __name__ == '__main__':
    shutil.rmtree(DOWNLOAD_DIR, ignore_errors=True)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    bot.delete_webhook()
    bot.set_webhook(url="https://telegram-bot-media-transcriber.onrender.com")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
