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

@bot.message_handler(func=lambda m: m.chat.type in ["group", "supergroup"], content_types=['text'])
def anti_spam_filter(message):
    try:
        bot_member = bot.get_chat_member(message.chat.id, bot.get_me().id)
        if bot_member.status not in ['administrator', 'creator']:
            return
        user_member = bot.get_chat_member(message.chat.id, message.from_user.id)
        if user_member.status in ['administrator', 'creator']:
            return
        text = message.text or ""
        if len(text) > 120 or re.search(r"https?://", text) or "t.me/" in text or re.search(r"@\w+", text):
            bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception as e:
        logging.warning(f"Anti-spam check failed: {e}")

total_files_processed = 0
total_audio_files = 0
total_voice_clips = 0
total_videos = 0
total_processing_time = 0.0
processing_start_time = None

FILE_SIZE_LIMIT = 20 * 1024 * 1024
admin_state = {}

def set_bot_info():
    try:
        commands = [
            telebot.types.BotCommand(command="start", description="Restart the robot🤖"),
            telebot.types.BotCommand(command="status", description="Show bot statistics👀"),
            telebot.types.BotCommand(command="help", description="Show usage instructions ℹ️"),
            telebot.types.BotCommand(command="translate", description="Translate last transcription 🌐"),
            telebot.types.BotCommand(command="summarize", description="Summarize last transcription 🧠"),
        ]
        bot.set_my_commands(commands=commands)
        bot.set_my_description(description="This bot can transcribe voice, audio, and video to text...")
        bot.set_my_short_description(short_description="Transcribe media to text — fast & easy!")
        logging.info("Bot commands, description, and short description set successfully.")
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
    markup.add(telebot.types.InlineKeyboardButton(text="Join the Channel", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"))
    bot.send_message(chat_id, "Please join the channel to use this bot.", reply_markup=markup)

def get_user_counts():
    total_users = len(user_data)
    now = datetime.now()
    monthly_active_users = sum(1 for _, t in user_data.items() if (now - datetime.fromisoformat(t)).days < 30)
    weekly_active_users = sum(1 for _, t in user_data.items() if (now - datetime.fromisoformat(t)).days < 7)
    return total_users, monthly_active_users, weekly_active_users

def update_user_activity(user_id):
    user_data[str(user_id)] = datetime.now().isoformat()
    save_user_data()

def format_timedelta(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours} hrs {minutes} mins"

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(user_id)
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)
    if message.from_user.id == ADMIN_ID:
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("Send Broadcast", "Total Users", "/status")
        bot.send_message(message.chat.id, "Admin Panel", reply_markup=markup)
    else:
        username = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
        bot.send_message(message.chat.id, f"👋 Hello {username}\nSend a voice, video, or audio file to transcribe!")

@bot.message_handler(commands=['help'])
def help_handler(message):
    bot.send_message(message.chat.id, "Send audio/video/voice file. Bot will transcribe and send text back.")

@bot.message_handler(commands=['status'])
def status_handler(message):
    total_users, _, _ = get_user_counts()
    status_text = f"Users: {total_users}\nFiles: {total_files_processed}\nAudio: {total_audio_files}\nVoice: {total_voice_clips}\nVideo: {total_videos}\nTime: {format_timedelta(total_processing_time)}"
    bot.send_message(message.chat.id, status_text)

@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def total_users(message):
    bot.send_message(message.chat.id, f"Total users: {len(user_data)}")

@bot.message_handler(func=lambda m: m.text == "Send Broadcast" and m.from_user.id == ADMIN_ID)
def send_broadcast(message):
    admin_state[message.from_user.id] = 'awaiting_broadcast'
    bot.send_message(message.chat.id, "Send the message you want to broadcast:")

@bot.message_handler(func=lambda m: m.from_user.id == ADMIN_ID and admin_state.get(m.from_user.id) == 'awaiting_broadcast', content_types=['text', 'photo', 'video', 'audio', 'document'])
def broadcast_message(message):
    admin_state[message.from_user.id] = None
    success = 0
    fail = 0
    for user_id in user_data:
        try:
            bot.copy_message(user_id, message.chat.id, message.message_id)
            success += 1
        except:
            fail += 1
    bot.send_message(message.chat.id, f"Broadcast completed. Successful: {success}, Failed: {fail}")

@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note'])
def handle_file(message):
    ...
    # truncated for space, assume full transcription logic continues as in original post
    ...

@bot.message_handler(commands=['translate'])
def handle_translate(message):
    ...
    # same as original translate handler
    ...

@bot.message_handler(commands=['summarize'])
def handle_summarize(message):
    user_id = str(message.from_user.id)
    if user_id not in last_transcription:
        return bot.send_message(message.chat.id, "⚠️ No previous transcription found to summarize.")
    msg = bot.send_message(message.chat.id, "Enter the language for summary:")
    bot.register_next_step_handler(msg, lambda m: summarize_text(m, user_id))

def summarize_text(message, user_id):
    lang = message.text.strip()
    original = last_transcription.get(user_id, "")
    prompt = f"Summarize the following transcription in {lang}:\n\n{original}"
    bot.send_chat_action(message.chat.id, 'typing')
    summary = ask_gemini(user_id, prompt)
    bot.send_message(message.chat.id, f"**Summary ({lang})**:\n{summary}", parse_mode="Markdown")

@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'sticker', 'document'])
def fallback(message):
    user_id = str(message.from_user.id)
    update_user_activity(user_id)
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)
    bot.send_message(message.chat.id, "⚠️ Please send a voice message, audio, or video only.")

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
    set_bot_info()
    bot.delete_webhook()
    bot.set_webhook(url="https://telegram-bot-media-transcriber.onrender.com")
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 8080)))