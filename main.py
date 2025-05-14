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
import yt_dlp  # <-- Ku dar yt_dlp

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
total_processing_time = 0.0  # in seconds
processing_start_time = None

# Constants
FILE_SIZE_LIMIT = 20 * 1024 * 1024

# Admin state
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
            description="Transcribe voice, audio, video & TikTok links to text. Download TikToks 📥 or transcribe 📝—fast, easy, free!"
        )
        bot.set_my_short_description(
            short_description="Transcribe media & TikTok links — download & transcribe with one tap!"
        )
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
    markup.add(telebot.types.InlineKeyboardButton(
        text="Join the Channel",
        url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"
    ))
    bot.send_message(chat_id, """🥺 𝗦𝗼𝗿𝗿𝘆 𝗱𝗲𝗮𝗿…
🔰 𝗣𝗹𝗲𝗮𝘀𝗲 𝗷𝗼𝗶𝗻 𝘁𝗵𝗲 𝗰𝗵𝗮𝗻𝗻𝗲𝗹 @mediatranscriber 𝘁𝗼 𝘂𝘀𝗲 𝘁𝗵𝗶𝘀 𝗯𝗼𝘁
‼️ 𝗔𝗳𝘁𝗲𝗿 𝗷𝗼𝗶𝗻𝗶𝗻𝗴, 𝘀𝗲𝗻𝗱 /start 𝘁𝗼 𝗰𝗼𝗻𝘁𝗶𝗻𝘂𝗲""", reply_markup=markup)

def get_user_counts():
    total_users = len(user_data)
    now = datetime.now()
    monthly_active_users = sum(1 for user_id, last_active in user_data.items() if (now - datetime.fromisoformat(last_active)).days < 30)
    weekly_active_users = sum(1 for user_id, last_active in user_data.items() if (now - datetime.fromisoformat(last_active)).days < 7)
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
        text = f"👋 Hello {username}\n\n• Send a voice, video, audio file, or a TikTok link.\n• I’ll transcribe media and TikToks quickly!"
        bot.send_message(message.chat.id, text)

@bot.message_handler(commands=['help'])
def help_handler(message):
    help_text = """ℹ️ How to use this bot:

1. **Join the Channel:** @mediatranscriber is required.
2. **Send Media:** Voice, audio, video, video note, or a TikTok link.
3. **Download TikTok:** Click 📥 to download video + caption.
4. **Transcribe TikTok:** Click 📝 to get full transcript (saved for /translate & /summarize).
5. **Receive Transcription:** Automatic for files & TikToks.
6. **Commands:**
   - `/start`: Restart.
   - `/status`: Bot stats.
   - `/help`: This guide.
   - `/translate`: Translate last transcription.
   - `/summarize`: Summarize last transcription.
Enjoy! 🚀"""
    bot.send_message(message.chat.id, help_text)

@bot.message_handler(commands=['status'])
def status_handler(message):
    total_users, _, _ = get_user_counts()
    status_text = f"""Today’s Activity – 🗓️

📊 Users Today: {total_users}

Total Files Processing 🎯
📁 Files Handled So Far: {total_files_processed}
🎵 Audio Files: {total_audio_files}
🎙️ Voice Clips: {total_voice_clips}
🎬 Videos: {total_videos}

⏱️ Total Time Spent: {format_timedelta(total_processing_time)}

"""
    bot.send_message(message.chat.id, status_text)

@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def total_users(message):
    bot.send_message(message.chat.id, f"Total users: {len(user_data)}")

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
    for user_id in user_data:
        try:
            bot.copy_message(user_id, message.chat.id, message.message_id)
            success += 1
        except telebot.apihelper.ApiTelegramException as e:
            logging.error(f"Broadcast failed for user {user_id}: {e}")
            fail += 1
    bot.send_message(message.chat.id, f"Broadcast completed.\nSuccessful: {success}\nFailed: {fail}")

# === HANDLE TIKTOK LINKS: BUTTONS ===

TIKTOK_REGEX = re.compile(
    r'(https?://)?(www\.)?(vm\.)?tiktok\.com/[^\s]+'
)

@bot.message_handler(func=lambda m: m.text and TIKTOK_REGEX.search(m.text))
def tiktok_link_handler(message):
    url = TIKTOK_REGEX.search(message.text).group(0)
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton("Download 📥", callback_data=f"download_tiktok|{url}"),
        telebot.types.InlineKeyboardButton("Transcribe 📝", callback_data=f"transcribe_tiktok|{url}")
    )
    bot.send_message(message.chat.id, "TikTok link detected! Choose an action:", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("download_tiktok"))
def callback_download_tiktok(call):
    _, url = call.data.split("|", 1)
    # Muujin send chat action inta lagu jiro download-ka iyo upload-ka
    bot.send_chat_action(call.message.chat.id, 'typing')
    try:
        ydl_opts = {
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
            'format': 'mp4',
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_path = ydl.prepare_filename(info)
            caption = info.get('description', 'No description found.')
        bot.send_chat_action(call.message.chat.id, 'upload_video')
        with open(video_path, 'rb') as video:
            bot.send_video(call.message.chat.id, video)
        bot.send_message(call.message.chat.id, f"📄 Description:\n{caption}")
    except Exception as e:
        logging.error(f"TikTok download error: {e}")
        bot.send_message(call.message.chat.id, "⚠️ Failed to download TikTok.")
    finally:
        if 'video_path' in locals() and os.path.exists(video_path):
            os.remove(video_path)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("transcribe_tiktok"))
def callback_transcribe_tiktok(call):
    _, url = call.data.split("|", 1)
    bot.send_chat_action(call.message.chat.id, 'typing')
    try:
        ydl_opts = {
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
            'format': 'mp4',
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_path = ydl.prepare_filename(info)

        # Transcribe
        transcription = transcribe(video_path)
        user_id = str(call.from_user.id)
        if transcription:
            # kaydi last transcription TikTok-ka
            last_transcription[user_id] = transcription

            # dirso send action ka hor inta aan dirin qoraalka
            if len(transcription) > 2000:
                with open('tiktok_transcription.txt', 'w', encoding='utf-8') as f:
                    f.write(transcription)
                bot.send_chat_action(call.message.chat.id, 'upload_document')
                with open('tiktok_transcription.txt', 'rb') as f:
                    bot.send_document(call.message.chat.id, f)
                os.remove('tiktok_transcription.txt')
            else:
                bot.send_chat_action(call.message.chat.id, 'typing')
                bot.send_message(call.message.chat.id, transcription)
        else:
            bot.send_message(call.message.chat.id, "⚠️ Could not transcribe TikTok audio.")
    except Exception as e:
        logging.error(f"TikTok transcribe error: {e}")
        bot.send_message(call.message.chat.id, "⚠️ Failed to download/transcribe TikTok.")
    finally:
        if 'video_path' in locals() and os.path.exists(video_path):
            os.remove(video_path)

# === EXISTING FILE HANDLERS (voice, audio, video, etc.) ===

@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note'])
def handle_file(message):
    user_id = str(message.from_user.id)
    update_user_activity(user_id)

    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

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
            f.write
        (downloaded_file)

        bot.send_chat_action(message.chat.id, 'typing')
        global processing_start_time
        processing_start_time = datetime.now()
        transcription = transcribe(file_path)

        # Store last transcription for /translate and /summarize
        if transcription:
            last_transcription[user_id] = transcription

        global total_files_processed
        total_files_processed += 1
        if message.content_type == 'audio':
            global total_audio_files
            total_audio_files += 1
        elif message.content_type == 'voice':
            global total_voice_clips
            total_voice_clips += 1
        elif message.content_type in ['video', 'video_note']:
            global total_videos
            total_videos += 1

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
                bot.send_chat_action(message.chat.id, 'upload_document')
                with open('transcription.txt', 'rb') as f:
                    bot.send_document(message.chat.id, f, reply_to_message_id=message.message_id)
                os.remove('transcription.txt')
            else:
                bot.reply_to(message, transcription)
        else:
            bot.send_message(message.chat.id, "⚠️ I could not transcribe the audio please clear your voice.")

    except Exception as e:
        logging.error(f"Error handling file: {e}")
        bot.send_message(message.chat.id, "⚠️ An error occurred while processing the file.")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

# === TRANSLATE & SUMMARIZE HANDLERS ===

@bot.message_handler(commands=['translate'])
def handle_translate(message):
    user_id = str(message.from_user.id)
    if user_id not in last_transcription:
        return bot.send_message(message.chat.id, "No previous transcription found.")
    msg = bot.send_message(
        message.chat.id,
        """Please type and send the target language for translation (e.g., Arabic, Spanish) etc.

⚠️ Note: Translation of transcriptions in .txt files is currently unavailable"""
    )
    bot.register_next_step_handler(msg, lambda m: translate_text(m, user_id))

def translate_text(message, user_id):
    lang = message.text.strip()
    original = last_transcription.get(user_id, "")
    prompt = f"Translate the following text to {lang}:\n\n{original}"
    bot.send_chat_action(message.chat.id, 'typing')
    translation = ask_gemini(user_id, prompt)
    bot.send_message(
        message.chat.id,
        f"**Translation ({lang})**:\n{translation}",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=['summarize'])
def handle_summarize(message):
    user_id = str(message.from_user.id)
    if user_id not in last_transcription:
        return bot.send_message(message.chat.id, "No previous transcription found.")
    msg = bot.send_message(
        message.chat.id,
        """Please type and send the language for summarization (e.g., English, Somali)."""
    )
    bot.register_next_step_handler(msg, lambda m: summarize_text(m, user_id))

def summarize_text(message, user_id):
    lang = message.text.strip()
    original = last_transcription.get(user_id, "")
    prompt = f"Summarize the following text in {lang}:\n\n{original}"
    bot.send_chat_action(message.chat.id, 'typing')
    summary = ask_gemini(user_id, prompt)
    bot.send_message(
        message.chat.id,
        f"**Summary ({lang})**:\n{summary}",
        parse_mode="Markdown"
    )

def transcribe(file_path: str) -> str | None:
    try:
        segments, _ = model.transcribe(file_path, beam_size=1)
        return " ".join(segment.text for segment in segments)
    except Exception as e:
        logging.error(f"Transcription error: {e}")
        return None

@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'sticker', 'document'])
def fallback(message):
    user_id = str(message.from_user.id)
    update_user_activity(user_id)
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)
    bot.send_message(message.chat.id, "⚠️ Please send a voice message, audio, video, or a TikTok link only.")

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
    set_bot_info()  # Call the function to set bot info on startup
    bot.delete_webhook()
    bot.set_webhook(url="https://telegram-bot-media-transcriber.onrender.com")
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 8080)))
