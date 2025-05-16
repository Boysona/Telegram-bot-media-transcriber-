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

# Bot token and required channel
TOKEN = "7648822901:AAFQEUx-S4bpD5qUMPHNB1P9jYCYSB4mzHU"
REQUIRED_CHANNEL = "@mediatranscriber"

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Admin ID
ADMIN_ID = 5978150981

# Download directory
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Whisper model for transcription
model = WhisperModel(model_size_or_path="tiny", device="cpu", compute_type="int8")

# User tracking file
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

# In-memory chat history and last transcription store
user_memory = {}
last_transcription = {}

# Statistics counters
total_files_processed = 0
total_audio_files = 0
total_voice_clips = 0
total_videos = 0
total_tiktok_downloads = 0
total_other_downloads = 0
total_processing_time = 0  # in seconds
processing_start_time = None

GEMINI_API_KEY = "AIzaSyAto78yGVZobxOwPXnl8wCE9ZW8Do2R8HA"

def ask_gemini(user_id, user_message):
    user_memory.setdefault(user_id, []).append({"role": "user", "text": user_message})
    history = user_memory[user_id][-10:]
    parts = [{"text": msg["text"]} for msg in history]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    resp = requests.post(url, headers={'Content-Type': 'application/json'}, json={"contents": [{"parts": parts}]})
    result = resp.json()
    if "candidates" in result:
        reply = result['candidates'][0]['content']['parts'][0]['text']
        user_memory[user_id].append({"role": "model", "text": reply})
        return reply
    return "Error: " + json.dumps(result)

FILE_SIZE_LIMIT = 20 * 1024 * 1024  # 20MB
admin_state = {}

def set_bot_info():
    commands = [
        telebot.types.BotCommand("start", "Restart the bot 🤖"),
        telebot.types.BotCommand("status", "Show bot statistics 📊"),
        telebot.types.BotCommand("info", "Show usage instructions ℹ️"),
        telebot.types.BotCommand("translate", "Translate last transcription 🌐"),
        telebot.types.BotCommand("summarize", "Summarize last transcription 📝"),
    ]
    bot.set_my_commands(commands)

    # Short description (About)
    bot.set_my_short_description(
        "Transcribe voice massages , audio files & video massages even tiktok videos into text — fast & easy! & free"
    )

    # Full description (What can this bot do?)
    bot.set_my_description(
        """This bot transcribes voice messages, audio files, video files, and even links from various platforms automatically.
• Supports multiple languages
• Fast and accurate transcriptions
• Includes translation & summarization features
• Downloads videos and extracts their audio content from supported platforms."""
    )

bot.set_my_description(
    description="This bot transcribes audio, video & links into text — fast, accurate, and free. Supports translation & summarization"
)

def check_subscription(user_id):
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except telebot.apihelper.ApiTelegramException:
        return False

def send_subscription_message(chat_id):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton("Join Channel", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}")
    )
    bot.send_message(
        chat_id,
        """🥺 𝗦𝗼𝗿𝗿𝘆 𝗱𝗲𝗮𝗿…
🔰 𝗣𝗹𝗲𝗮𝘀𝗲 𝗷𝗼𝗶𝗻 𝘁𝗵𝗲 𝗰𝗵𝗮𝗻𝗻𝗲𝗹 @mediatranscriber 𝘁𝗼 𝘂𝘀𝗲 𝘁𝗵𝗶𝘀 𝗯𝗼𝘁
‼️ 𝗔𝗳𝘁𝗲𝗿 𝗷𝗼𝗶𝗻𝗶𝗻𝗴, 𝘂𝘀𝗲 𝘁𝗵𝗲 𝗯𝗼𝘁""",
        reply_markup=markup
    )

def update_user_activity(user_id):
    user_data[str(user_id)] = datetime.now().isoformat()
    save_user_data()

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity(message.from_user.id)
    if user_id not in user_data:
        user_data[user_id] = datetime.now().isoformat()
        save_user_data()
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)
    if message.from_user.id == ADMIN_ID:
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Send Broadcast", "Total Users", "/status")
        bot.send_message(message.chat.id, "Admin Panel", reply_markup=keyboard)
    else:
        name = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
        bot.send_message(
            message.chat.id,
            f"""👋 Welcome! {name}\n
👻 Send me:

• Voice message
• Videos
• Audio files
• Links from YouTube, Instagram, Facebook, TikTok, Pinterest, X (Twitter), Snapchat, Likee
• to transcribe or download! More info with /info 👀"""
        )

@bot.message_handler(commands=['info'])
def help_handler(message):
    help_text = (
        """ℹ️ How to use this bot:

1. **Join the Channel:** Make sure you've joined our channel: @mediatranscriber. This is required to use the bot.

2. **Send a File or Link:** You can send voice messages, audio files, video files, video notes, or links from the following platforms:
   - YouTube
   - Instagram
   - Facebook
   - TikTok
   - Pinterest
   - X (Twitter)
   - Snapchat
   - Likee

3. **Transcription or Download:**
   - If you send a media file, it will be automatically transcribed.
   - If you send a link, you'll get options to **Download** the video or just **Transcribe** it.

4. **Receive Text:** Once the transcription is complete, the bot will send you the text back in the chat.
   - If the transcription is short, it will be sent as a reply to your original message.
   - If the transcription is longer than 4000 characters, it will be sent as a separate text file.

5. **Commands:**
   - `/start`: Restarts the bot and shows the welcome message.
   - `/status`: Displays bot statistics.
   - `/info`: Shows these usage instructions.
   - `/translate`: Translate your last transcription.
   - `/summarize`: Summarize your last transcription.

Enjoy transcribing and downloading your media files and videos quickly and easily!"""
    )
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def status_handler(message):
    update_user_activity(message.from_user.id)

    # Count active users today
    today = datetime.now().date()
    active_today = sum(
        1 for timestamp in user_data.values()
        if datetime.fromisoformat(timestamp).date() == today
    )

    # Calculate time components
    total_seconds = int(total_processing_time)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    text = (
        "📊 Overall Statistics\n\n"
        "👥 User Statistics\n"
        f"▫️ Total Users Today: {active_today}\n\n"
        "⚙️ Processing Statistics\n"
        f"▫️ Total Files Processed: {total_files_processed}\n"
        f"▫️ Audio Files: {total_audio_files}\n"
        f"▫️ Voice Clips: {total_voice_clips}\n"
        f"▫️ Videos: {total_videos}\n"
        f"▫️ TikTok Downloads: {total_tiktok_downloads}\n"
        f"▫️ Other Platform Downloads: {total_other_downloads}\n\n"
        f"⏱️ Total Processing Time: {hours} hours {minutes} minutes {seconds} seconds\n\n"
        "⸻\n\n"
        "Thanks for using our service! 🙌\n"
        "See you next time! 💫"
    )

    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def total_users(message):
    bot.send_message(message.chat.id, f"Total registered users: {len(user_data)}")

@bot.message_handler(func=lambda m: m.text == "Send Broadcast" and m.from_user.id == ADMIN_ID)
def send_broadcast(message):
    admin_state[message.from_user.id] = 'awaiting_broadcast'
    bot.send_message(message.chat.id, "Send the broadcast message now:")

@bot.message_handler(
    func=lambda m: m.from_user.id == ADMIN_ID and admin_state.get(m.from_user.id) == 'awaiting_broadcast',
    content_types=['text', 'photo', 'video', 'audio', 'document']
)
def broadcast_message(message):
    admin_state[message.from_user.id] = None
    success = fail = 0
    for uid in user_data:
        try:
            bot.copy_message(uid, message.chat.id, message.message_id)
            success += 1
        except telebot.apihelper.ApiTelegramException:
            fail += 1
    bot.send_message(
        message.chat.id,
        f"Broadcast complete.\nSuccessful: {success}\nFailed: {fail}"
    )

# Baraha kale ee la taageerayo iyo qaababka URL-yadooda
OTHER_PLATFORM_REGEX = re.compile(
    r'(https?://(pin\.it/\w+|www\.pinterest\.com/[^"\s]+|'
    r'(www\.)?facebook\.com/[^"\s]+|fb\.watch/[^"\s]+|www\.facebook\.com/share/r/[^"\s]+|'
    r'(www\.)?instagram\.com/(reel|p|tv)/[^"\s]+|'
    r'(www\.)?(youtube\.com|youtu\.be)/[^"\s]+|'
    r'snapchat\.com/t/\w+|l\.likee\.video/v/\w+|'
    r'(x\.com|twitter\.com)/[^"\s]+/status/\d+))'
)

# TikTok link detection
TIKTOK_REGEX = re.compile(r'(https?://(www\.)?(vm\.)?tiktok\.com/[^\s]+)')

@bot.message_handler(func=lambda m: m.text and (TIKTOK_REGEX.search(m.text) or OTHER_PLATFORM_REGEX.search(m.text)))
def media_link_handler(message):
    url = None
    platform = None

    if TIKTOK_REGEX.search(message.text):
        url = TIKTOK_REGEX.search(message.text).group(0)
        platform = "TikTok"
    elif OTHER_PLATFORM_REGEX.search(message.text):
        url = OTHER_PLATFORM_REGEX.search(message.text).group(0)
        if "pinterest" in url:
            platform = "Pinterest"
        elif "facebook" in url:
            platform = "Facebook"
        elif "instagram" in url:
            platform = "Instagram"
        elif "youtube" in url:
            platform = "YouTube"
        elif "snapchat" in url:
            platform = "Snapchat"
        elif "likee" in url:
            platform = "Likee"
        elif "x.com" in url or "twitter.com" in url:
            platform = "X (Twitter)"

    if url and platform:
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(
            telebot.types.InlineKeyboardButton(f"Download {platform} video 📥", callback_data=f"download|{url}"),
            telebot.types.InlineKeyboardButton(f"Transcribe {platform} 📝", callback_data=f"transcribe|{url}")
        )
        bot.send_message(message.chat.id, f"{platform} link detected—choose an action:", reply_markup=markup)
    else:
        bot.send_message(message.chat.id, "⚠️ Link detected but could not identify the platform.")

@bot.callback_query_handler(func=lambda c: c.data.startswith("download|"))
def callback_download_media(call):
    global total_tiktok_downloads, total_other_downloads
    _, url = call.data.split("|", 1)
    bot.send_chat_action(call.message.chat.id, 'typing')
    try:
        ydl_opts = {
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
            'format': 'mp4',
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            caption = info.get('description', 'No caption found.')
        bot.send_chat_action(call.message.chat.id, 'upload_video')
        with open(path, 'rb') as video:
            bot.send_video(call.message.chat.id, video, caption=caption)
        if "tiktok" in url:
            total_tiktok_downloads += 1
        else:
            total_other_downloads += 1
    except Exception as e:
        logging.error(f"Media download error ({url}): {e}")
        bot.send_message(call.message.chat.id, f"⚠️ Failed to download media from {url}.")
    finally:
        if 'path' in locals() and os.path.exists(path):
            os.remove(path)

@bot.callback_query_handler(func=lambda c: c.data.startswith("transcribe|"))
def callback_transcribe_media(call):
    _, url = call.data.split("|", 1)
    bot.send_chat_action(call.message.chat.id, 'typing')
    try:
        ydl_opts = {
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
            'format': 'bestaudio/best',
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            audio_path = ydl.prepare_filename(info)

        global processing_start_time
        processing_start_time = datetime.now()

        transcription = transcribe(audio_path) or ""
        uid = str(call.from_user.id)
        last_transcription[uid] = transcription

        processing_time = (datetime.now() - processing_start_time).total_seconds()
        global total_processing_time
        total_processing_time += processing_time

        if len(transcription) > 4000:
            fn = 'media_transcription.txt'
            with open(fn, 'w', encoding='utf-8') as f:
                f.write(transcription)
            bot.send_chat_action(call.message.chat.id, 'upload_document')
            with open(fn, 'rb') as doc:
                bot.send_document(call.message.chat.id, doc)
            os.remove(fn)
        else:
            bot.send_chat_action(call.message.chat.id, 'typing')
            bot.send_message(call.message.chat.id, transcription)
    except Exception as e:
        logging.error(f"Media transcribe error ({url}): {e}")
        bot.send_message(call.message.chat.id, f"⚠️ Failed to transcribe media from {url}.")
    finally:
        if 'audio_path' in locals() and os.path.exists(audio_path):
            os.remove(audio_path)

@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note'])
def handle_file(message):
    global total_files_processed, total_audio_files, total_voice_clips, total_videos, total_processing_time
    update_user_activity(message.from_user.id)
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    file_obj = message.voice or message.audio or message.video or message.video_note
    if file_obj.file_size > FILE_SIZE_LIMIT:
        return bot.send_message(message.chat.id, "⚠️ File too large (max allowed is 20MB).")

    info = bot.get_file(file_obj.file_id)
    local_path = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}.ogg")
    bot.send_chat_action(message.chat.id, 'typing')

    try:
        data = bot.download_file(info.file_path)
        with open(local_path, 'wb') as f:
            f.write(data)

        bot.send_chat_action(message.chat.id, 'typing')
        global processing_start_time
        processing_start_time = datetime.now()

        transcription = transcribe(local_path) or ""
        uid = str(message.from_user.id)
        last_transcription[uid] = transcription

        # Update statistics
        total_files_processed += 1
        if message.voice:
            total_voice_clips += 1
        elif message.audio:
            total_audio_files += 1
        elif message.video or message.video_note:
            total_videos += 1

        processing_time = (datetime.now() - processing_start_time).total_seconds()
        total_processing_time += processing_time

        if len(transcription) > 4000:
            fn = 'transcription.txt'
            with open(fn, 'w', encoding='utf-8') as f:
                f.write(transcription)
            bot.send_chat_action(message.chat.id, 'upload_document')
            with open(fn, 'rb') as doc:
                bot.send_document(message.chat.id, doc, reply_to_message_id=message.message_id)
            os.remove(fn)
        else:
            bot.reply_to(message, transcription)
    except Exception as e:
        logging.error(f"Error processing file: {e}")
        bot.send_message(message.chat.id, "⚠️ An error occurred during transcription.")
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)

@bot.message_handler(commands=['translate'])
def handle_translate(message):
    uid = str(message.from_user.id)
    if uid not in last_transcription:
        return bot.send_message(message.chat.id, "❌ No previous transcription found.")
    prompt_msg = bot.send_message(message.chat.id, "Please enter the target language (e.g. Spanish, Arabic):")
    bot.register_next_step_handler(prompt_msg, lambda resp: do_translate(resp, uid))

def do_translate(message, uid):
    lang = message.text.strip()
    original = last_transcription.get(uid, "")
    prompt = f"Translate the following text to {lang}:\n\n{original}"
    bot.send_chat_action(message.chat.id, 'typing')
    translated = ask_gemini(uid, prompt)
    if len(translated) > 4000:
        fn = 'translation.txt'
        with open(fn, 'w', encoding='utf-8') as f:
            f.write(translated)
        bot.send_chat_action(message.chat.id, 'upload_document')
        with open(fn, 'rb') as doc:
            bot.send_document(message.chat.id, doc)
        os.remove(fn)
    else:
        bot.send_message(message.chat.id, translated)

@bot.message_handler(commands=['summarize'])
def handle_summarize(message):
    uid = str(message.from_user.id)
    if uid not in last_transcription:
        return bot.send_message(message.chat.id, "❌ No previous transcription found.")
    prompt_msg = bot.send_message(message.chat.id, "Please enter the summary language (e.g. English):")
    bot.register_next_step_handler(prompt_msg, lambda resp: do_summarize(resp, uid))

def do_summarize(message, uid):
    lang = message.text.strip()
    original = last_transcription.get(uid, "")
    prompt = f"Summarize the following text in {lang}:\n\n{original}"
    bot.send_chat_action(message.chat.id, 'typing')
    summary = ask_gemini(uid, prompt)
    if len(summary) > 4000:
        fn = 'summary.txt'
        with open(fn, 'w', encoding='utf-8') as f:
            f.write(summary)
        bot.send_chat_action(message.chat.id, 'upload_document')
        with open(fn, 'rb') as doc:
            bot.send_document(message.chat.id, doc)
        os.remove(fn)
    else:
        bot.send_message(message.chat.id, summary)

def transcribe(path: str) -> str | None:
    try:
        segments, _ = model.transcribe(path, beam_size=1)
        return " ".join(segment.text for segment in segments)
    except Exception as e:
        logging.error(f"Transcription error: {e}")
        return None

@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'sticker', 'document'])
def fallback(message):
    update_user_activity(message.from_user.id)
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)
    bot.send_message(message.chat.id, "⚠️ Please send only voice, audio, video, or a link from a supported platform.")

@app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        update = telebot.types.Update.de_json(request.get_data().decode('utf-8'))
        bot.process_new_updates([update])
        return '', 200
    return abort(403)

@app.route('/set_webhook', methods=['GET','POST'])
def set_webhook():
    url = "https://telegram-bot-media-transcriber.onrender.com"
    bot.set_webhook(url=url)
    return f"Webhook set to {url}", 200

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
    bot.set_webhook(url="https://telegram-bot-media-transcriber-ihi5.onrender.com")
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 8080)))

