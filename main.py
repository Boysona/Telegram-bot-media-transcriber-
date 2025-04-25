Speech recognition 

import re
import uuid
import os
import shutil
import logging
import subprocess
import requests
from flask import Flask, request, abort
import telebot
import speech_recognition as sr

# Configure logger
logging.basicConfig(level=logging.INFO)

# Replace with your actual token
TOKEN = "7920977306:AAFRR5ZIaPcD1rbmjSKxsNisQZZpPa7zWPs"
bot = telebot.TeleBot(TOKEN)

# Replace with your channel
REQUIRED_CHANNEL = "@qolkaqarxiska2"

# Initialize Flask app
app = Flask(__name__)

# User tracking
existing_users = set()
if os.path.exists('users.txt'):
    with open('users.txt', 'r') as f:
        for line in f:
            existing_users.add(line.strip())

# Admin configuration
ADMIN_ID = 6964068910
admin_state = {}

# File download directory
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# File size limit (20MB in bytes)
FILE_SIZE_LIMIT = 20 * 1024 * 1024

# SpeechRecognition setup
recognizer = sr.Recognizer()

def check_subscription(user_id):
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Error checking subscription: {e}")
        return False

def send_subscription_message(chat_id):
    message = f"⚠️ Please join {REQUIRED_CHANNEL} to use this bot!\n\nJoin the channel and try again."
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton(
        text="Join Channel",
        url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"
    ))
    bot.send_message(chat_id, message, reply_markup=markup)

@bot.message_handler(commands=['start'])
def start_handler(message):
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    user_id = str(message.from_user.id)
    if user_id not in existing_users:
        existing_users.add(user_id)
        with open('users.txt', 'a') as f:
            f.write(f"{user_id}\n")

    if message.from_user.id == ADMIN_ID:
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("Send Ads (Broadcast)", "Total Users")
        bot.send_message(message.chat.id, "Admin Panel", reply_markup=markup)
    else:
        first_name = message.from_user.first_name or "there"
        username = f"@{message.from_user.username}" if message.from_user.username else first_name
        text = (
            f"👋 Salam {username}\n• Please send me any of the following:\n\n"
            f"• Voice message 🎤\n"
            f"• Video message 🎥\n"
            f"• Audio file 🎵\n"
            f"• Video file 📹\n"
            f"• I will transcribe it and send you the text."
        )
        bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda msg: msg.text == "Total Users" and msg.from_user.id == ADMIN_ID)
def show_total_users(message):
    bot.send_message(message.chat.id, f"Total users: {len(existing_users)}")

@bot.message_handler(func=lambda msg: msg.text == "Send Ads (Broadcast)" and msg.from_user.id == ADMIN_ID)
def start_broadcast(message):
    admin_state[message.from_user.id] = 'awaiting_broadcast'
    bot.send_message(message.chat.id, "Send the message you want to broadcast:")

@bot.message_handler(func=lambda msg: msg.from_user.id == ADMIN_ID and admin_state.get(msg.from_user.id) == 'awaiting_broadcast',
                    content_types=['text', 'photo', 'video', 'audio', 'document', 'voice', 'sticker'])
def handle_broadcast(message):
    admin_state[message.from_user.id] = None
    success = 0
    failures = 0

    for user_id in existing_users:
        try:
            bot.copy_message(user_id, message.chat.id, message.message_id)
            success += 1
        except Exception as e:
            logging.error(f"Failed to send to {user_id}: {e}")
            failures += 1

    bot.send_message(message.chat.id, f"Broadcast complete!\nSuccess: {success}\nFailures: {failures}")

@bot.message_handler(content_types=['voice', 'video_note', 'audio', 'video'])
def handle_audio_message(message):
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    # Determine file size
    file_size = None
    if message.voice:
        file_size = message.voice.file_size
    elif message.video_note:
        file_size = message.video_note.file_size
    elif message.video:
        file_size = message.video.file_size
    elif message.audio:
        file_size = message.audio.file_size

    if file_size and file_size > FILE_SIZE_LIMIT:
        bot.send_message(
            message.chat.id,
            f"⚠️ Sorry, the file is too large. Please send a file smaller than 20MB or convert it to audio."
        )
        return

    file_path = None
    try:
        # Download the file
        if message.voice:
            file_info = bot.get_file(message.voice.file_id)
        elif message.video_note:
            file_info = bot.get_file(message.video_note.file_id)
        elif message.video:
            file_info = bot.get_file(message.video.file_id)
        else:
            file_info = bot.get_file(message.audio.file_id)

        unique_id = str(uuid.uuid4())
        # Save original container (ogg/m4a/mp4, etc.)
        file_path = os.path.join(DOWNLOAD_DIR, f"{unique_id}")
        ext = os.path.splitext(file_info.file_path)[1]
        file_path += ext

        downloaded_file = bot.download_file(file_info.file_path)
        with open(file_path, 'wb') as f:
            f.write(downloaded_file)

        bot.send_chat_action(message.chat.id, 'typing')
        transcription = transcribe_audio(file_path)
        if transcription:
            # If too long, send as file
            if len(transcription) > 2000:
                with open("transcription.txt", "w", encoding='utf-8') as f:
                    f.write(transcription)
                with open("transcription.txt", "rb") as f:
                    bot.reply_to(message, document=f)
                os.remove("transcription.txt")
            else:
                bot.reply_to(message, transcription)
        else:
            bot.send_message(message.chat.id, "Could not transcribe the audio.")

    except Exception as e:
        bot.send_message(message.chat.id, f"Error: {e}")
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

@bot.message_handler(func=lambda m: True, content_types=['text', 'sticker', 'document', 'photo'])
def handle_other_messages(message):
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)
    bot.send_message(
        message.chat.id,
        "Please send one of these file types:\n"
        "• Voice message 🎤\n• Video message 🎥\n"
        "• Audio file 🎵\n• Video file 📹\n\n"
        "I'll transcribe it to text!"
    )

def transcribe_audio(input_path: str) -> str | None:
    """
    Convert input file to WAV via ffmpeg, then run speech_recognition on it.
    Returns the recognized text or None on failure.
    """
    try:
        # Convert any input container to standard WAV
        wav_path = input_path + ".wav"
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-ar", "16000",       # 16 kHz sample rate
            "-ac", "1",           # mono
            wav_path
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Recognize with SpeechRecognition
        with sr.AudioFile(wav_path) as source:
            audio = recognizer.record(source)

        text = recognizer.recognize_google(audio, language="en-US")
        # Clean up the wav
        os.remove(wav_path)
        return text

    except subprocess.CalledProcessError as e:
        logging.error(f"ffmpeg conversion failed: {e}")
    except sr.UnknownValueError:
        logging.error("SpeechRecognition could not understand audio")
    except sr.RequestError as e:
        logging.error(f"SpeechRecognition request failed: {e}")
    except Exception as e:
        logging.error(f"Unexpected error in transcription: {e}")

    # attempt to remove wav if it exists
    try:
        if os.path.exists(wav_path):
            os.remove(wav_path)
    except:
        pass

    return None

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
def set_webhook_route():
    webhook_url = request.args.get('url')
    if webhook_url:
        bot.set_webhook(url=webhook_url)
        return f'Webhook set to: {webhook_url}', 200
    else:
        return 'Please provide a webhook URL as a query parameter.', 400

@app.route('/delete_webhook', methods=['GET', 'POST'])
def delete_webhook_route():
    bot.delete_webhook()
    return 'Webhook deleted', 200

def set_telegram_webhook(webhook_url, bot_token):
    """Sets the Telegram bot webhook."""
    url = f"https://api.telegram.org/bot{bot_token}/setWebhook?url={webhook_url}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        result = response.json()
        if result.get('ok'):
            print(f"Webhook successfully set to: {webhook_url}")
        else:
            print(f"Failed to set webhook: {result}")
    except requests.exceptions.RequestException as e:
        print(f"Error setting webhook: {e}")

if __name__ == "__main__":
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    bot.delete_webhook()
    WEBHOOK_URL = "https://telegram-bot-media-transcriber-iy2x.onrender.com"
    set_telegram_webhook(WEBHOOK_URL, TOKEN)
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 8080)))

