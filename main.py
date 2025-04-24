import os
import uuid
import shutil
import requests
import asyncio
from faster_whisper import WhisperModel
from telebot import TeleBot, types
from telebot.types import InputFile
from flask import Flask, request
from dotenv import load_dotenv

# --- Load environment variables ---
load_dotenv()
TOKEN = os.getenv("8191487892:AAEdaDeZ2EwBLA90RrjU1nuR0nkfitpZo5o")  # make sure your .env has e.g. TOKEN=8191487892:AAE...
REQUIRED_CHANNEL = "@qolkaqarxiska2"

# --- Configuration ---
DOWNLOAD_DIR = "downloads"
WEBHOOK_HOST = "https://telegram-bot-media-transcriber-iy2x.onrender.com"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = int(os.getenv("PORT", 8080))

if os.path.exists(DOWNLOAD_DIR):
    shutil.rmtree(DOWNLOAD_DIR)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# load Whisper once
model = WhisperModel(
    model_size_or_path="tiny",
    device="cpu",
    compute_type="int8"
)

bot = TeleBot(TOKEN)
app = Flask(__name__)

# --- Subscription Check ---
def check_subscription(user_id: int) -> bool:
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception:
        return False

def send_subscription_message(chat_id: int):
    message = f"⚠️ You must join {REQUIRED_CHANNEL} to use this bot!\n\nJoin the channel and try again."
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton(text="Join Channel", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"))
    bot.send_message(chat_id, message, reply_markup=keyboard)

# --- Download Helper ---
def download_file(file_path_on_telegram: str, destination: str):
    file_info = bot.get_file(file_path_on_telegram)
    file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
    resp = requests.get(file_url, stream=True)
    resp.raise_for_status()
    with open(destination, 'wb') as f:
        for chunk in resp.iter_content(1024):
            f.write(chunk)

# --- Transcription ---
def transcribe_audio(file_path: str) -> str | None:
    try:
        segments, _ = asyncio.run(asyncio.to_thread(model.transcribe, file_path, beam_size=1))
        return " ".join(s.text for s in segments)
    except Exception:
        return None

# --- Handlers ---
@bot.message_handler(commands=['start'])
def start_handler(message: types.Message):
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    username = f"@{message.from_user.username}" if message.from_user.username else (message.from_user.first_name or "there")
    text = (
        f"👋 Salom {username}\n"
        "•Send me any of these types of files:\n"
        "  • Voice message 🎤\n"
        "  • Video message 🎥\n"
        "  • Audio file 🎵\n"
        "  • Video file 📹\n\n"
        "I will convert them to text!"
    )
    bot.send_message(message.chat.id, text)

@bot.message_handler(content_types=['voice', 'video_note', 'audio', 'video'])
def handle_media(message: types.Message):
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    # pick file_id and size
    file_id = None
    file_size = 0
    if message.voice:
        file_id = message.voice.file_id; file_size = message.voice.file_size
    elif message.video_note:
        file_id = message.video_note.file_id; file_size = message.video_note.file_size
    elif message.video:
        file_id = message.video.file_id; file_size = message.video.file_size
    elif message.audio:
        file_id = message.audio.file_id; file_size = message.audio.file_size

    if file_size > 20 * 1024 * 1024:
        return bot.reply_to(
            message,
            "⚠️ Sorry, the file is too large. Please send a file smaller than 20MB "
            "or use @Video_to_audio_robot to convert it to audio if it’s a video, "
            "or send the video in a lower resolution like 256p."
        )

    unique_id = str(uuid.uuid4())
    ext = 'ogg'  # whisper prefers ogg/opus
    file_path = os.path.join(DOWNLOAD_DIR, f"{unique_id}.{ext}")

    status_msg = bot.reply_to(message, "📥 Downloading file, please wait...")
    bot.send_chat_action(message.chat.id, 'typing')

    try:
        download_file(file_id, file_path)
        bot.edit_message_text("🔄Processing audio, this may take some time if the audio is very long...",
                              chat_id=status_msg.chat.id, message_id=status_msg.message_id)

        transcription = transcribe_audio(file_path)
        bot.delete_message(status_msg.chat.id, status_msg.message_id)

        if transcription:
            if len(transcription) > 4000:
                txt_path = os.path.join(DOWNLOAD_DIR, "transcription.txt")
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(transcription)
                with open(txt_path, 'rb') as f:
                    bot.send_document(message.chat.id, f)
                os.remove(txt_path)
            else:
                bot.send_message(message.chat.id, transcription)
        else:
            bot.send_message(message.chat.id, "Ma awoodo inaan qoro qoraalka.")
    except Exception as e:
        bot.send_message(message.chat.id, f"Error: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

@bot.message_handler(func=lambda m: True)
def handle_other(message: types.Message):
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)
    bot.send_message(
        message.chat.id,
        "😞 I’m sorry, just send the media file so I can write it and send it back to you."
    )

# --- Webhook setup via Flask ---
@app.route(WEBHOOK_PATH, methods=['POST'])
def webhook():
    json_str = request.get_data().decode('utf-8')
    update = types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return '', 200

def setup_webhook():
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)

if __name__ == "__main__":
    setup_webhook()
    # start Flask server
    app.run(host=WEBAPP_HOST, port=WEBAPP_PORT)
