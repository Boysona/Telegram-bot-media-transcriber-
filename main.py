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
total_processing_time = 0.0
processing_start_time = None

FILE_SIZE_LIMIT = 20 * 1024 * 1024
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
        bot.set_my_description(
            description=(
                "Transcribe voice, audio, video & TikTok — heli description-ka video-ga, "
                "download 📥, transcribe 📝, turjumo & soo koob .txt files!"
            )
        )
        bot.set_my_short_description(
            short_description="Transcribe & download media, TikTok, translate & summarize .txt!"
        )
        logging.info("Bot info set successfully.")
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Error setting bot info: {e}")

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
    bot.send_message(chat_id, 
        "🥺 𝗦𝗼𝗿𝗿𝘆… Join @mediatranscriber first!",
        reply_markup=markup
    )

def get_user_counts():
    total = len(user_data)
    now = datetime.now()
    monthly = sum(1 for _, t in user_data.items() if (now - datetime.fromisoformat(t)).days < 30)
    weekly = sum(1 for _, t in user_data.items() if (now - datetime.fromisoformat(t)).days < 7)
    return total, monthly, weekly

def update_user_activity(user_id):
    user_data[str(user_id)] = datetime.now().isoformat()
    save_user_data()

def format_timedelta(seconds):
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    return f"{hrs} hrs {mins} mins"

@bot.message_handler(commands=['start'])
def start_handler(msg):
    uid = msg.from_user.id
    update_user_activity(uid)
    if not check_subscription(uid):
        return send_subscription_message(msg.chat.id)
    if uid == ADMIN_ID:
        mk = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        mk.add("Send Broadcast", "Total Users", "/status")
        bot.send_message(msg.chat.id, "Admin Panel", reply_markup=mk)
    else:
        name = f"@{msg.from_user.username}" if msg.from_user.username else msg.from_user.first_name
        text = (
            f"👋 Hello {name}\n\n"
            "• Send voice, audio, video, or TikTok link.\n"
            "• I’ll download, transcribe, translate & summarize!"
        )
        bot.send_message(msg.chat.id, text)

@bot.message_handler(commands=['help'])
def help_handler(msg):
    help_text = (
        "ℹ️ **How to use:**\n\n"
        "• **TikTok:** Send TikTok link → Download 📥 or Transcribe 📝\n"
        "• **Media:** Send voice/audio/video → get transcription\n"
        "• **.txt files:** Send transcription file → Translate 🌐 or Summarize 📝\n"
        "• **Commands:** /translate, /summarize, /status, /start, /help"
    )
    bot.send_message(msg.chat.id, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def status_handler(msg):
    total, monthly, weekly = get_user_counts()
    text = (
        f"📊 Users: {total} (30d: {monthly}, 7d: {weekly})\n"
        f"Files➡️ {total_files_processed}, Audio: {total_audio_files}, Voice: {total_voice_clips}, Videos: {total_videos}\n"
        f"⏱️ Time: {format_timedelta(total_processing_time)}"
    )
    bot.send_message(msg.chat.id, text)

# TikTok URL regex
TIKTOK_REGEX = re.compile(r'(https?://)?(vm|vt)\.tiktok\.com/[A-Za-z0-9]+|https?://(www\.)?tiktok\.com/.+')

def download_tiktok_video(url: str):
    ydl_opts = {
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
        'format': 'mp4',
        'quiet': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)
        desc = info.get('description', '')
        return filepath, desc

@bot.message_handler(func=lambda m: m.text and TIKTOK_REGEX.search(m.text))
def tiktok_handler(msg):
    uid = msg.from_user.id
    update_user_activity(uid)
    if not check_subscription(uid):
        return send_subscription_message(msg.chat.id)
    url = msg.text.strip()
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(
        telebot.types.InlineKeyboardButton("Download 📥", callback_data=f"tt_dl|{url}"),
        telebot.types.InlineKeyboardButton("Transcribe 📝", callback_data=f"tt_tx|{url}")
    )
    bot.send_message(msg.chat.id, "TikTok link detected! What do you want?", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("tt_dl") or c.data.startswith("tt_tx"))
def tiktok_callback(c):
    action, url = c.data.split("|", 1)
    chat_id = c.message.chat.id
    bot.answer_callback_query(c.id, "Processing…")
    try:
        video_path, desc = download_tiktok_video(url)
        if action == "tt_dl":
            with open(video_path, 'rb') as f:
                bot.send_video(chat_id, f, caption=desc or "No description.")
        else:
            txt = transcribe(video_path)
            bot.send_message(chat_id, txt or "⚠️ Ma awoodo transcription-ka.")
        os.remove(video_path)
    except Exception as e:
        logging.error(f"TikTok error: {e}")
        bot.send_message(chat_id, "⚠️ TikTok processing error.")

# Handle .txt transcription files
@bot.message_handler(content_types=['document'])
def handle_document(msg):
    uid = msg.from_user.id
    update_user_activity(uid)
    if not check_subscription(uid):
        return send_subscription_message(msg.chat.id)
    doc = msg.document
    if doc.file_name.lower().endswith('.txt'):
        file_info = bot.get_file(doc.file_id)
        downloaded = bot.download_file(file_info.file_path)
        path = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}.txt")
        with open(path, 'wb') as f:
            f.write(downloaded)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        last_transcription[str(uid)] = content
        os.remove(path)
        kb = telebot.types.InlineKeyboardMarkup()
        kb.add(
            telebot.types.InlineKeyboardButton("Translate 🌐", callback_data="file_translate"),
            telebot.types.InlineKeyboardButton("Summarize 📝", callback_data="file_summarize")
        )
        bot.send_message(msg.chat.id, "File received! Do you want to Translate or Summarize?", reply_markup=kb)
    else:
        # pass to fallback or other handlers
        return

@bot.callback_query_handler(func=lambda c: c.data in ["file_translate","file_summarize"])
def file_cb(c):
    uid = str(c.from_user.id)
    action = c.data
    bot.answer_callback_query(c.id)
    if uid not in last_transcription:
        return bot.send_message(c.message.chat.id, "No transcription found.")
    if action == "file_translate":
        msg = bot.send_message(c.message.chat.id, "Enter target language for translation:")
        bot.register_next_step_handler(msg, lambda m: translate_text(m, uid))
    else:
        msg = bot.send_message(c.message.chat.id, "Enter language for summarization:")
        bot.register_next_step_handler(msg, lambda m: summarize_text(m, uid))

# --- existing media transcription handler unchanged ---
@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note'])
def handle_file(msg):
    uid = str(msg.from_user.id)
    update_user_activity(uid)
    if not check_subscription(msg.from_user.id):
        return send_subscription_message(msg.chat.id)

    file_size = (msg.voice or msg.audio or msg.video or msg.video_note).file_size
    if file_size > FILE_SIZE_LIMIT:
        bot.send_message(msg.chat.id, "⚠️ File too large! Max 20MB.")
        return

    info = bot.get_file((msg.voice or msg.audio or msg.video or msg.video_note).file_id)
    fname = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}.ogg")
    bot.send_chat_action(msg.chat.id, 'typing')
    try:
        data = bot.download_file(info.file_path)
        with open(fname, 'wb') as f:
            f.write(data)

        bot.send_chat_action(msg.chat.id, 'typing')
        global processing_start_time
        processing_start_time = datetime.now()
        transcription = transcribe(fname)

        if transcription:
            last_transcription[uid] = transcription

        # update stats...
        global total_files_processed, total_audio_files, total_voice_clips, total_videos, total_processing_time
        total_files_processed += 1
        if msg.content_type == 'audio': total_audio_files += 1
        elif msg.content_type == 'voice': total_voice_clips += 1
        else: total_videos += 1

        if processing_start_time:
            duration = (datetime.now() - processing_start_time).total_seconds()
            total_processing_time += duration
            processing_start_time = None

        if transcription:
            if len(transcription) > 2000:
                with open('transcription.txt','w',encoding='utf-8') as f:
                    f.write(transcription)
                with open('transcription.txt','rb') as f:
                    bot.send_document(msg.chat.id, f, reply_to_message_id=msg.message_id)
                os.remove('transcription.txt')
            else:
                bot.reply_to(msg, transcription)
        else:
            bot.send_message(msg.chat.id, "⚠️ Cannot transcribe.")

    except Exception as e:
        logging.error(f"Error: {e}")
        bot.send_message(msg.chat.id, "⚠️ Processing error.")
    finally:
        if os.path.exists(fname):
            os.remove(fname)

# Translate & Summarize handlers (unchanged)
@bot.message_handler(commands=['translate'])
def handle_translate(msg):
    uid = str(msg.from_user.id)
    if uid not in last_transcription:
        return bot.send_message(msg.chat.id, "No previous transcription found.")
    m = bot.send_message(msg.chat.id, "Target language for translation:")
    bot.register_next_step_handler(m, lambda m2: translate_text(m2, uid))

@bot.message_handler(commands=['summarize'])
def handle_summarize(msg):
    uid = str(msg.from_user.id)
    if uid not in last_transcription:
        return bot.send_message(msg.chat.id, "No previous transcription found.")
    m = bot.send_message(msg.chat.id, "Language for summarization:")
    bot.register_next_step_handler(m, lambda m2: summarize_text(m2, uid))

def translate_text(message, user_id):
    lang = message.text.strip()
    original = last_transcription.get(user_id, "")
    prompt = f"Translate the following text to {lang}:\n\n{original}"
    bot.send_chat_action(message.chat.id, 'typing')
    translation = ask_gemini(user_id, prompt)
    bot.send_message(message.chat.id, f"**Translation ({lang})**:\n{translation}", parse_mode="Markdown")

def summarize_text(message, user_id):
    lang = message.text.strip()
    original = last_transcription.get(user_id, "")
    prompt = f"Summarize the following text in {lang}:\n\n{original}"
    bot.send_chat_action(message.chat.id, 'typing')
    summary = ask_gemini(user_id, prompt)
    bot.send_message(message.chat.id, f"**Summary ({lang})**:\n{summary}", parse_mode="Markdown")

def transcribe(path: str) -> str | None:
    try:
        segments, _ = model.transcribe(path, beam_size=1)
        return " ".join(s.text for s in segments)
    except Exception as e:
        logging.error(f"Transcription error: {e}")
        return None

@bot.message_handler(func=lambda m: True, content_types=['text','photo','sticker','document'])
def fallback(msg):
    uid = str(msg.from_user.id)
    update_user_activity(uid)
    if not check_subscription(msg.from_user.id):
        return send_subscription_message(msg.chat.id)
    bot.send_message(msg.chat.id, "⚠️ Please send voice, audio, video, TikTok link, or .txt file.")

@app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        update = telebot.types.Update.de_json(request.get_data().decode('utf-8'))
        bot.process_new_updates([update])
        return '', 200
    else:
        abort(403)

@app.route('/set_webhook', methods=['GET','POST'])
def set_webhook():
    url = "https://telegram-bot-media-transcriber.onrender.com"
    bot.set_webhook(url=url)
    return f'Webhook set: {url}', 200

@app.route('/delete_webhook', methods=['GET','POST'])
def delete_webhook():
    bot.delete_webhook()
    return 'Webhook deleted.', 200

if __name__ == "__main__":
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    set_bot_info()
    bot.delete_webhook()
    bot.set_webhook(url="https://telegram-bot-media-transcriber.onrender.com")
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 8080)))
