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

# === CONFIGURATION ===
TOKEN = "7648822901:AAG3ZJADuvTP_9Gmx0matFCsJU6aWeRJstk"
REQUIRED_CHANNEL = "@mediatranscriber"
GEMINI_API_KEY = "AIzaSyAto78yGVZobxOwPXnl8wCE9ZW8Do2R8HA"
ADMIN_ID = 5978150981
WEBHOOK_URL = "https://telegram-bot-media-transcriber.onrender.com"
DOWNLOAD_DIR = "downloads"
FILE_SIZE_LIMIT = 20 * 1024 * 1024

# === SETUP ===
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
model = WhisperModel(model_size_or_path="small", device="cpu", compute_type="int8")

# === USER DATA & MEMORY ===
users_file = 'users.json'
user_data = {}
user_memory = {}
if os.path.exists(users_file):
    with open(users_file, 'r') as f:
        try:
            user_data = json.load(f)
        except json.JSONDecodeError:
            user_data = {}

def save_user_data():
    with open(users_file, 'w') as f:
        json.dump(user_data, f, indent=4)

# === STATISTICS ===
total_files_processed = 0
total_processing_time = 0.0
processing_start_time = None
admin_state = {}

# === GEMINI ===
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
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        result = response.json()
        if "candidates" in result:
            reply_text = result['candidates'][0]['content']['parts'][0]['text']
            user_memory[user_id].append({"role": "model", "text": reply_text})
            return reply_text
        else:
            return "Gemini API error: " + json.dumps(result)
    except Exception as e:
        return f"Error: {str(e)}"

# === BOT INFO SETUP ===
def set_bot_info():
    commands = [
        telebot.types.BotCommand("start", "Start the bot"),
        telebot.types.BotCommand("help", "Get usage instructions"),
        telebot.types.BotCommand("status", "Show bot statistics"),
        telebot.types.BotCommand("reset", "Clear AI memory")
    ]
    bot.set_my_commands(commands)
    bot.set_my_description("A Telegram bot that transcribes media files and chats with Gemini AI.")
    bot.set_my_short_description("AI + Transcriber Bot")

# === HELPERS ===
def check_subscription(user_id):
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return False

def send_subscription_message(chat_id):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("Join Channel", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"))
    bot.send_message(chat_id, "⚠️ Please join the channel first!", reply_markup=markup)

def update_user_activity(user_id):
    user_data[str(user_id)] = datetime.now().isoformat()
    save_user_data()

def is_active_within(last_active_str, days):
    if last_active_str:
        last_active = datetime.fromisoformat(last_active_str)
        return (datetime.now() - last_active).days < days
    return False

def get_user_counts():
    total_users = len(user_data)
    monthly = sum(1 for u in user_data.values() if is_active_within(u, 30))
    weekly = sum(1 for u in user_data.values() if is_active_within(u, 7))
    return total_users, monthly, weekly

def format_timedelta(seconds):
    return f"{int(seconds//3600)} hours {(int(seconds)%3600)//60} minutes"

# === COMMAND HANDLERS ===
@bot.message_handler(commands=['start'])
def handle_start(message):
    update_user_activity(str(message.from_user.id))
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    if message.from_user.id == ADMIN_ID:
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("Send Broadcast", "Total Users", "/status")
        bot.send_message(message.chat.id, "Welcome to Admin Panel", reply_markup=markup)
    else:
        bot.send_message(message.chat.id, "Welcome! Send media (voice/video) or ask anything (text) to begin.")

@bot.message_handler(commands=['help'])
def handle_help(message):
    bot.send_message(message.chat.id, """**How to Use the Bot:**

• Send a voice, audio, or video to transcribe.
• Or send a text message to chat with Gemini AI.
• Haddii aad ku dartid `@Mission` fariintaada qoraalka ah, AI ayaa turjumi doona ama falanqeyn doona.
• /reset - Clear AI memory
• /status - Stats (admin only)

Max file size: 20MB
Channel join required: @mediatranscriber
""", parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def handle_status(message):
    if message.from_user.id != ADMIN_ID:
        return
    total, monthly, weekly = get_user_counts()
    bot.send_message(message.chat.id, f"""📊 **Stats:**
• Total Users: {total}
• Monthly Active: {monthly}
• Weekly Active: {weekly}
• Files Processed: {total_files_processed}
• Time Spent: {format_timedelta(total_processing_time)}""", parse_mode="Markdown")

@bot.message_handler(commands=['reset'])
def reset_memory(message):
    user_memory.pop(message.from_user.id, None)
    bot.send_message(message.chat.id, "AI memory cleared.")

# === ADMIN BROADCAST ===
@bot.message_handler(func=lambda m: m.text == "Send Broadcast" and m.from_user.id == ADMIN_ID)
def send_broadcast(message):
    admin_state[message.from_user.id] = 'awaiting_broadcast'
    bot.send_message(message.chat.id, "Send the broadcast message now:")

@bot.message_handler(func=lambda m: m.from_user.id == ADMIN_ID and admin_state.get(m.from_user.id) == 'awaiting_broadcast',
                     content_types=['text', 'photo', 'video', 'audio', 'document'])
def handle_broadcast(message):
    admin_state[message.from_user.id] = None
    success, fail = 0, 0
    for user_id in user_data:
        try:
            bot.copy_message(user_id, message.chat.id, message.message_id)
            success += 1
        except:
            fail += 1
    bot.send_message(message.chat.id, f"Done!\nSuccess: {success}\nFailed: {fail}")

# === MEDIA HANDLER ===
@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note'])
def handle_media(message):
    update_user_activity(str(message.from_user.id))
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    file_info = bot.get_file(message.json['voice']['file_id'] if 'voice' in message.json else
                             message.json['audio']['file_id'] if 'audio' in message.json else
                             message.json['video']['file_id'] if 'video' in message.json else
                             message.json['video_note']['file_id'])

    file_size = message.voice.file_size if message.voice else \
                message.audio.file_size if message.audio else \
                message.video.file_size if message.video else \
                message.video_note.file_size

    if file_size > FILE_SIZE_LIMIT:
        return bot.send_message(message.chat.id, "⚠️ File too large! Max 20MB.")

    file_path = os.path.join(DOWNLOAD_DIR, str(uuid.uuid4()) + ".ogg")
    with open(file_path, 'wb') as f:
        f.write(bot.download_file(file_info.file_path))

    bot.send_chat_action(message.chat.id, 'typing')
    global processing_start_time, total_processing_time, total_files_processed
    processing_start_time = datetime.now()

    transcription = transcribe(file_path)

    total_files_processed += 1
    total_processing_time += (datetime.now() - processing_start_time).total_seconds()
    processing_start_time = None

    os.remove(file_path)

    if transcription:
        if len(transcription) > 2000:
            with open('transcription.txt', 'w', encoding='utf-8') as f:
                f.write(transcription)
            with open('transcription.txt', 'rb') as f:
                bot.send_document(message.chat.id, f)
            os.remove('transcription.txt')
        else:
            bot.reply_to(message, transcription)
    else:
        bot.send_message(message.chat.id, "❗ Could not transcribe. Please try again.")

def transcribe(file_path):
    try:
        segments, _ = model.transcribe(file_path, beam_size=1)
        return " ".join(segment.text for segment in segments)
    except Exception as e:
        logging.error(f"Transcription error: {e}")
        return None

# === TEXT (GEMINI AI) HANDLER ===
@bot.message_handler(func=lambda message: message.content_type == "text")
def handle_text(message):
    update_user_activity(str(message.from_user.id))
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    if "@Mission" in message.text:
        # Ka saar @Mission qoraalka si AI-gu u falanqeeyo qoraalka intiisa kale
        query = message.text.replace("@Mission", "").strip()
        if query:
            bot.send_chat_action(message.chat.id, 'typing')
            reply = ask_gemini(message.from_user.id, query)
            bot.reply_to(message, reply)
        else:
            bot.reply_to(message, "Fadlan ku dar qoraal aad rabto inaan falanqeeyo ama turjumo ka dib `@Mission`.")
    else:
        # Haddii @Mission uusan ku jirin, ula dhaqan sidii fariin caadi ah oo AI ah
        bot.send_chat_action(message.chat.id, 'typing')
        reply = ask_gemini(message.from_user.id, message.text)
        bot.reply_to(message, reply)

# === WEBHOOK HANDLERS ===
@app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        update = telebot.types.Update.de_json(request.data.decode("utf-8"))
        bot.process_new_updates([update])
        return '', 200
    else:
        abort(403)

@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    bot.set_webhook(url=WEBHOOK_URL)
    return f"Webhook set to {WEBHOOK_URL}"

@app.route('/delete_webhook', methods=['GET'])
def delete_webhook():
    bot.delete_webhook()
    return "Webhook deleted"

# === MAIN ===
if __name__ == "__main__":
    set_bot_info()
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    bot.set_webhook(url=WEBHOOK_URL)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
