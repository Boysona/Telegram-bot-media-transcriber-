import os
import logging
import threading
from io import BytesIO
from datetime import datetime
import telebot
from flask import Flask, request, abort
from faster_whisper import WhisperModel

# BOT TOKEN & CHANNEL
TOKEN = "7648822901:AAGi4gZ8R3Xk9yT3nbXnB20Spv5BiTE2QWQ"
REQUIRED_CHANNEL = "@mediatranscriber"
ADMIN_ID = 5978150981

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# Load Whisper model once into memory
model = WhisperModel(
    model_size_or_path="tiny",
    device="cpu",
    compute_type="int8",
    in_memory=True
)

# Track users
existing_users = set()
users_file = 'users.txt'
if os.path.exists(users_file):
    with open(users_file, 'r') as f:
        existing_users = set(line.strip() for line in f)

def check_subscription(user_id):
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logging.error(f"Subscription check error: {e}")
        return False

def send_subscription_message(chat_id):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("Join the Channel", url=REQUIRED_CHANNEL))
    bot.send_message(chat_id, "⚠️ Please join the channel to continue using this bot!", reply_markup=markup)

def transcribe(file_stream) -> str | None:
    try:
        segments, _ = model.transcribe(file_stream, beam_size=1)
        return " ".join(segment.text for segment in segments)
    except Exception as e:
        logging.error(f"Transcription error: {e}")
        return None

def process_file_async(message, audio_stream):
    try:
        transcription = transcribe(audio_stream)
        if transcription:
            if len(transcription) > 2000:
                with open('transcription.txt', 'w', encoding='utf-8') as f:
                    f.write(transcription)
                with open('transcription.txt', 'rb') as f:
                    bot.send_document(message.chat.id, f)
                os.remove('transcription.txt')
            else:
                bot.send_message(message.chat.id, transcription)
        else:
            bot.send_message(message.chat.id, "⚠️ Couldn't transcribe the audio.")
    except Exception as e:
        logging.error(f"Error in async processing: {e}")
        bot.send_message(message.chat.id, "⚠️ An error occurred while processing.")

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = str(message.from_user.id)
    if user_id not in existing_users:
        existing_users.add(user_id)
        with open(users_file, 'a') as f:
            f.write(f"{user_id}\n")

    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    bot.send_message(message.chat.id, "👋 Send me a voice, audio, or video file and I'll transcribe it!")

@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note'])
def handle_file(message):
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    file_obj = message.voice or message.audio or message.video or message.video_note
    if file_obj.file_size > 20 * 1024 * 1024:
        bot.send_message(message.chat.id, "⚠️ The file is too large. Max allowed size is 20MB.")
        return

    bot.send_chat_action(message.chat.id, 'typing')
    file_info = bot.get_file(file_obj.file_id)
    file_bytes = bot.download_file(file_info.file_path)
    audio_stream = BytesIO(file_bytes)
    audio_stream.name = "input.ogg"

    threading.Thread(target=process_file_async, args=(message, audio_stream)).start()

@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'sticker', 'document'])
def fallback(message):
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)
    bot.send_message(message.chat.id, "⚠️ Please send a voice, audio, or video file.")

# Webhook
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
    bot.set_webhook(url=webhook_url)
    return f'Webhook set to {webhook_url}', 200

@app.route('/delete_webhook', methods=['GET', 'POST'])
def delete_webhook():
    bot.delete_webhook()
    return 'Webhook deleted.', 200

if __name__ == '__main__':
    bot.delete_webhook()
    bot.set_webhook(url="https://telegram-bot-media-transcriber.onrender.com")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
