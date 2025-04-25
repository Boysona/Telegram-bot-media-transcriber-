import os
import uuid
import shutil
import requests
from faster_whisper import WhisperModel
from telebot import TeleBot, types
from telebot.types import InputFile
from flask import Flask, request
from dotenv import load_dotenv
import subprocess

# --- Config ---
TOKEN = "8191487892:AAEdaDeZ2EwBLA90RrjU1nuR0nkfitpZo5o"  # BAD practice: use .env in production
REQUIRED_CHANNEL = "@qolkaqarxiska2"
DOWNLOAD_DIR = "downloads"
WEBHOOK_HOST = "https://telegram-bot-media-transcriber-iy2x.onrender.com"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = int(os.getenv("PORT", 8080))

# Clean old files
if os.path.exists(DOWNLOAD_DIR):
    shutil.rmtree(DOWNLOAD_DIR)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Load whisper once
model = WhisperModel(model_size_or_path="tiny", device="cpu", compute_type="int8")

bot = TeleBot(TOKEN)
app = Flask(__name__)

# --- Helpers ---
def check_subscription(user_id: int) -> bool:
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception:
        return False

def send_subscription_message(chat_id: int):
    msg = f"⚠️ You must join {REQUIRED_CHANNEL} to use this bot!"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton(text="Join Channel", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"))
    bot.send_message(chat_id, msg, reply_markup=keyboard)

def download_file(file_path_on_telegram: str, destination: str):
    file_info = bot.get_file(file_path_on_telegram)
    file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
    response = requests.get(file_url, stream=True)
    response.raise_for_status()
    with open(destination, 'wb') as f:
        for chunk in response.iter_content(1024):
            f.write(chunk)

def convert_to_wav(input_path: str, output_path: str):
    subprocess.run([
        "ffmpeg", "-y", "-i", input_path,
        "-ar", "16000", "-ac", "1", output_path
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def transcribe_audio(file_path: str) -> str | None:
    try:
        segments, _ = model.transcribe(file_path, beam_size=1)
        return " ".join(s.text for s in segments)
    except Exception as e:
        print(f"Transcription error: {e}")
        return None

# --- Handlers ---
@bot.message_handler(commands=['start'])
def start_handler(message: types.Message):
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    name = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
    bot.send_message(message.chat.id,
        f"👋 Salam {name}!\n\n"
        "• Send me voice, video, or audio file and I’ll transcribe it to text!")

@bot.message_handler(content_types=['voice', 'video_note', 'audio', 'video'])
def handle_media(message: types.Message):
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

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
        return bot.reply_to(message, "⚠️ Sorry, the file is too large. Please send a file smaller than 20MB "
            "or use @Video_to_audio_robot to convert it to audio if it’s a video, "
            "or send the video in a lower resolution like 256p.")

    unique_id = str(uuid.uuid4())
    raw_path = os.path.join(DOWNLOAD_DIR, f"{unique_id}.input")
    wav_path = os.path.join(DOWNLOAD_DIR, f"{unique_id}.wav")

    status = bot.reply_to(message, "📥 Downloading file...")
    bot.send_chat_action(message.chat.id, 'typing')

    try:
        download_file(file_id, raw_path)
        bot.edit_message_text("🔄 Converting and transcribing this may take some time if the audio is very long...", status.chat.id, status.message_id)

        convert_to_wav(raw_path, wav_path)
        transcription = transcribe_audio(wav_path)
        bot.delete_message(status.chat.id, status.message_id)

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
        for f in [raw_path, wav_path]:
            if os.path.exists(f):
                os.remove(f)

@bot.message_handler(func=lambda m: True)
def handle_other(message: types.Message):
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)
    bot.send_message(message.chat.id, "😞 I’m sorry, just send the media file so I can write it and send it back to you.")

# --- Webhook ---
@app.route(WEBHOOK_PATH, methods=['POST'])
def webhook():
    update = types.Update.de_json(request.get_data().decode('utf-8'))
    bot.process_new_updates([update])
    return '', 200

def setup_webhook():
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)

if __name__ == "__main__":
    setup_webhook()
    app.run(host=WEBAPP_HOST, port=WEBAPP_PORT)

