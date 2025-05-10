import os
import json
import uuid
import shutil
import logging
import telebot
from flask import Flask, request, abort
from faster_whisper import WhisperModel
from datetime import datetime
from telebot.types import BotCommand

# ─── CONFIG ────────────────────────────────────────────────────────────────────
TOKEN = "7648822901:AAG3ZJADuvTP_9Gmx0matFCsJU6aWeRJstk"
REQUIRED_CHANNEL = "@mediatranscriber"
ADMIN_ID = 5978150981
DOWNLOAD_DIR = "downloads"
USERS_FILE = "users.json"
FILE_SIZE_LIMIT = 20 * 1024 * 1024  # 20MB
# ────────────────────────────────────────────────────────────────────────────────

# Configure logger
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# ─── PROGRAMMATIC BOT INFO ────────────────────────────────────────────────────
def set_bot_info():
    # 1) commands
    commands = [
        BotCommand("start", "Restart the robot🤖"),
        BotCommand("status", "Show bot statistics👀"),
        BotCommand("help", "Show usage instructionsℹ️")
    ]
    bot.set_my_commands(commands)

    # 2) full description (“About”)
    bot.set_my_description(
        "This bot can transcribe voice, audio, and video to text in multiple languages "
        "with automatic detection - fast, easy, and free! Use new translate transcription "
        "results after transcribing. Try it now."
    )

    # 3) short description (appears in chat list)
    bot.set_my_short_description(
        "Transcribe voice, audio & video to text — fast & easy! & free"
    )

# Call once at startup
set_bot_info()
# ────────────────────────────────────────────────────────────────────────────────

# ─── USER STORAGE (JSON) ───────────────────────────────────────────────────────
# Load or init users.json
if os.path.exists(USERS_FILE):
    with open(USERS_FILE, 'r', encoding='utf-8') as f:
        users = json.load(f)
else:
    users = {}

def save_users():
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
# ────────────────────────────────────────────────────────────────────────────────

# ─── STAT TRACKING ─────────────────────────────────────────────────────────────
total_files_processed = 0
total_processing_time = 0.0
processing_start_time = None
# ────────────────────────────────────────────────────────────────────────────────

# ─── MODEL ─────────────────────────────────────────────────────────────────────
model = WhisperModel(
    model_size_or_path="tiny",
    device="cpu",
    compute_type="int8"
)
# ────────────────────────────────────────────────────────────────────────────────

def check_subscription(user_id: int) -> bool:
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Subscription check error for {user_id}: {e}")
        return False

def send_subscription_message(chat_id: int):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton(
        text="Join the Channel",
        url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"
    ))
    bot.send_message(chat_id,
                     "⚠️ Please join the channel to continue using this bot!",
                     reply_markup=markup)

def format_timedelta(seconds: float) -> str:
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    return f"{hrs}h {mins}m"

def transcribe(file_path: str) -> str | None:
    try:
        segments, _ = model.transcribe(file_path, beam_size=1)
        return " ".join(seg.text for seg in segments)
    except Exception as e:
        logging.error(f"Transcription error: {e}")
        return None

# ─── HANDLERS ─────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['start'])
def start_handler(msg):
    uid = str(msg.from_user.id)
    now = datetime.utcnow().isoformat()

    # Add or update user in JSON
    if uid not in users:
        users[uid] = {'first_seen': now, 'last_seen': now}
    else:
        users[uid]['last_seen'] = now
    save_users()

    if not check_subscription(msg.from_user.id):
        return send_subscription_message(msg.chat.id)

    if msg.from_user.id == ADMIN_ID:
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("Send Broadcast", "Total Users", "/status")
        bot.send_message(msg.chat.id, "Admin Panel", reply_markup=markup)
    else:
        name = (f"@{msg.from_user.username}"
                if msg.from_user.username else msg.from_user.first_name)
        bot.send_message(msg.chat.id,
                         f"👋 Hello {name}!\n\n"
                         "• Send a voice, video, or audio file.\n"
                         "• I will transcribe it and send it back to you!")

@bot.message_handler(commands=['help'])
def help_handler(msg):
    help_text = (
        "ℹ️ *How to use MediaTranscriber Bot*\n\n"
        "1. Send /start to (re)start the bot and register yourself.\n"
        "2. Send any voice note, audio file, or video.\n"
        "3. Wait a few seconds while I transcribe it.\n"
        "4. If the transcription is long, I'll send it as a text file.\n"
        "5. Use /status to see overall stats.\n"
        "6. If you get stuck, just send /help again!"
    )
    bot.send_message(msg.chat.id, help_text, parse_mode='Markdown')

@bot.message_handler(commands=['status'])
def status_handler(msg):
    total_u = len(users)
    # For demo purposes, everyone is “active”
    monthly = weekly = total_u
    stats = (
        f"📈 *Overall Statistics*\n\n"
        f"👥 Total Users: {total_u}\n"
        f"📅 Active This Month: {monthly}\n"
        f"📅 Active This Week: {weekly}\n\n"
        f"🎯 Files Processed: {total_files_processed}\n"
        f"⏱️ Total Processing Time: {format_timedelta(total_processing_time)}"
    )
    bot.send_message(msg.chat.id, stats, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.from_user.id == ADMIN_ID and
                     m.text == "Total Users")
def admin_total_users(msg):
    bot.send_message(msg.chat.id, f"Total users: {len(users)}")

@bot.message_handler(func=lambda m: m.from_user.id == ADMIN_ID and
                     m.text == "Send Broadcast")
def admin_broadcast_start(msg):
    admin_state[msg.from_user.id] = 'awaiting_broadcast'
    bot.send_message(msg.chat.id, "Send the message to broadcast:")

@bot.message_handler(func=lambda m: admin_state.get(m.from_user.id)=='awaiting_broadcast',
                     content_types=['text', 'photo', 'video', 'audio', 'document'])
def admin_broadcast_send(msg):
    admin_state[msg.from_user.id] = None
    success = fail = 0
    for uid in users:
        try:
            bot.copy_message(int(uid), msg.chat.id, msg.message_id)
            success += 1
        except telebot.apihelper.ApiTelegramException:
            fail += 1
    bot.send_message(msg.chat.id,
                     f"Broadcast done ✅\nSuccess: {success}\nFail: {fail}")

@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note'])
def file_handler(msg):
    if not check_subscription(msg.from_user.id):
        return send_subscription_message(msg.chat.id)

    media = msg.voice or msg.audio or msg.video or msg.video_note
    if media.file_size > FILE_SIZE_LIMIT:
        return bot.send_message(msg.chat.id,
                                "⚠️ File too large! Max 20 MB.")

    bot.send_chat_action(msg.chat.id, 'typing')
    info = bot.get_file(media.file_id)
    unique_name = f"{uuid.uuid4()}.ogg"
    path = os.path.join(DOWNLOAD_DIR, unique_name)

    # Download
    data = bot.download_file(info.file_path)
    with open(path, 'wb') as f:
        f.write(data)

    # Transcribe
    global processing_start_time
    processing_start_time = datetime.utcnow()
    text = transcribe(path)

    # Stats
    global total_files_processed, total_processing_time
    total_files_processed += 1
    delta = (datetime.utcnow() - processing_start_time).total_seconds()
    total_processing_time += delta
    processing_start_time = None

    # Return
    if text:
        if len(text) > 2000:
            with open('transcription.txt','w',encoding='utf-8') as f:
                f.write(text)
            with open('transcription.txt','rb') as f:
                bot.send_document(msg.chat.id, f,
                                  reply_to_message_id=msg.message_id)
            os.remove('transcription.txt')
        else:
            bot.reply_to(msg, text)
    else:
        bot.send_message(msg.chat.id,
                         "⚠️ Sorry, I couldn't transcribe that.")

    os.remove(path)

@bot.message_handler(func=lambda m: True, content_types=['text','photo','sticker','document'])
def fallback(msg):
    if not check_subscription(msg.from_user.id):
        return send_subscription_message(msg.chat.id)
    bot.send_message(msg.chat.id,
                     "⚠️ Please send a voice, audio, video, or video note.")

# ─── WEBHOOK & FLASK ─────────────────────────────────────────────────────────
@app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        update = telebot.types.Update.de_json(request.get_data().decode(), bot)
        bot.process_new_updates([update])
        return '', 200
    else:
        abort(403)

@app.route('/set_webhook', methods=['GET','POST'])
def set_webhook():
    url = "https://telegram-bot-media-transcriber.onrender.com"
    bot.delete_webhook()
    bot.set_webhook(url=url)
    return f"Webhook set to {url}", 200

@app.route('/delete_webhook', methods=['GET','POST'])
def delete_webhook():
    bot.delete_webhook()
    return "Webhook deleted.", 200

if __name__ == "__main__":
    # Clean download dir
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    # Ensure bot info is up‑to‑date
    set_bot_info()

    bot.delete_webhook()
    bot.set_webhook(url="https://telegram-bot-media-transcriber.onrender.com")
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 8080)))
