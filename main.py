
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
from urllib.parse import urlparse, parse_qs

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
total_tiktok_links = 0
total_txt_files = 0
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
        bot.set_my_description(description="This bot can transcribe voice, audio, and video to text in multiple languages with automatic detection - fast, easy, and free! It can also download TikTok videos and transcribe them. Additionally, you can translate and summarize transcription results or upload .txt files for translation and summarization. Try it now.")
        bot.set_my_short_description(short_description="Transcribe voice, audio & video to text — fast & easy! Download & transcribe TikTok videos. Translate & summarize text.")
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
        text = f"👋 Hello {username}\n\n• Send a voice, video, or audio file to transcribe.\n• You can also send a TikTok video link to download or transcribe it.\n• Use /translate to translate the last transcription.\n• Use /summarize to summarize the last transcription.\n• Send a .txt file to translate or summarize its content."
        bot.send_message(message.chat.id, text)

@bot.message_handler(commands=['help'])
def help_handler(message):
    help_text = """ℹ️ How to use this bot:

1. **Join the Channel:** Make sure you've joined our channel: @mediatranscriber. This is required to use the bot.

2. **Send a File:** You can send voice messages, audio files, video files, or video notes directly to the bot for transcription.

3. **TikTok Videos:** Send a TikTok video link (short or full URL) to download the video or transcribe its audio. You will see "Download 📥" and "Transcribe 📝" buttons.

4. **Transcription:** The bot will automatically process your file or the TikTok video and transcribe the content into text.

5. **Receive Text:** Once the transcription is complete, the bot will send you the text back in the chat.
   - If the transcription is short, it will be sent as a reply to your original message.
   - If the transcription is longer than 2000 characters, it will be sent as a separate text file.

6. **Translate:** Use the `/translate` command to translate the last transcribed text. You will be asked to provide the target language.

7. **Summarize:** Use the `/summarize` command to get a summary of the last transcribed text. You will be asked to provide the language for the summary.

8. **.txt Files:** You can send a `.txt` file containing text. The bot can then translate or summarize the content of this file using the `/translate` and `/summarize` commands after you send the file.

9. **Commands:**
   - `/start`: Restarts the bot and shows the welcome message.
   - `/status`: Displays bot statistics, including the number of users and processing information.
   - `/help`: Shows these usage instructions.
   - `/translate`: Translate your last transcription or the content of a sent `.txt` file.
   - `/summarize`: Summarize your last transcription or the content of a sent `.txt` file.

Enjoy using the bot for transcribing, downloading TikToks, translating, and summarizing! If you have any questions or feedback, feel free to reach out in the channel."""
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
🔗 TikTok Links: {total_tiktok_links}
📄 TXT Files: {total_txt_files}

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

def is_tiktok_url(url):
    return re.match(r'https?://(?:m|www|vm)\.tiktok\.com/(?:.+)', url) or \
           re.match(r'https?://vt\.tiktok\.com/(?:.+)', url)

def download_tiktok_video(url):
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        filename = os.path.join(DOWNLOAD_DIR, f"tiktok_{uuid.uuid4()}.mp4")
        with open(filename, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return filename
    except requests.exceptions.RequestException as e:
        logging.error(f"Error downloading TikTok video: {e}")
        return None

def get_tiktok_description(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        html_content = response.text
        match = re.search(r'<meta property="og:description" content="([^"]*)"', html_content)
        if match:
            return match.group(1)
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Error getting TikTok description: {e}")
        return None

@bot.message_handler(func=lambda message: is_tiktok_url(message.text))
def handle_tiktok_url(message):
    user_id = str(message.from_user.id)
    update_user_activity(user_id)

    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    download_button = telebot.types.InlineKeyboardButton("Download 📥", callback_data=f"tiktok_download:{message.text}")
    transcribe_button = telebot.types.InlineKeyboardButton("Transcribe 📝", callback_data=f"tiktok_transcribe:{message.text}")
    markup.add(download_button, transcribe_button)
    bot.send_message(message.chat.id, "Choose an action for the TikTok video:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('tiktok_'))
def tiktok_callback_handler(call):
    action, url = call.data.split(':', 1)
    user_id = str(call.from_user.id)
    update_user_activity(user_id)

    if not check_subscription(call.from_user.id):
        bot.answer_callback_query(call.id, "You need to join the channel to use this bot.")
        return send_subscription_message(call.message.chat.id)

    if action == 'tiktok_download':
        bot.answer_callback_query(call.id, "Downloading TikTok video...")
        bot.send_chat_action(call.message.chat.id, 'upload_video')
        video_path = download_tiktok_video(url)
        description = get_tiktok_description(url)
        if video_path:
            with open(video_path, 'rb') as video_file:
                bot.send_video(call.message.chat.id, video_file, caption=description, reply_to_message_id=call.message.message_id)
            os.remove(video_path)
            global total_tiktok_links
            total_tiktok_links += 1
        else:
            bot.send_message(call.message.chat.id, "⚠️ Failed to download the TikTok video.")
    elif action == 'tiktok_transcribe':
        bot.answer_callback_query(call.id, "Transcribing TikTok video...")
        bot.send_chat_action(call.message.chat.id, 'typing')
        video_path = download_tiktok_video(url)
        if video_path:
            transcription = transcribe(video_path)
            os.remove(video_path)
            if transcription:
                last_transcription[user_id] = transcription
                if len(transcription) > 2000:
                    with open('transcription.txt', 'w', encoding='utf-8') as f:
                        f.write(transcription)
                    with open('transcription.txt', 'rb') as f:
                        bot.send_

                    document(call.message.chat.id, f, reply_to_message_id=call.message.message_id)
                    os.remove('transcription.txt')
                else:
                    bot.send_message(call.message.chat.id, transcription, reply_to_message_id=call.message.message_id)
                global total_tiktok_links
                total_tiktok_links += 1
            else:
                bot.send_message(call.message.chat.id, "⚠️ Could not transcribe the TikTok video.")
        else:
            bot.send_message(call.message.chat.id, "⚠️ Failed to download the TikTok video for transcription.")

@bot.message_handler(content_types=['document'])
def handle_document(message):
    user_id = str(message.from_user.id)
    update_user_activity(user_id)

    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)

    if message.document.mime_type == 'text/plain':
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        unique_filename = os.path.join(DOWNLOAD_DIR, f"doc_{uuid.uuid4()}.txt")
        with open(unique_filename, 'wb') as f:
            f.write(downloaded_file)
        try:
            with open(unique_filename, 'r', encoding='utf-8') as f:
                text_content = f.read()
            last_transcription[user_id] = text_content
            bot.send_message(message.chat.id, "Text file processed. You can now use /translate and /summarize commands.")
            global total_txt_files
            total_txt_files += 1
        except Exception as e:
            bot.send_message(message.chat.id, f"⚠️ Error reading the text file: {e}")
        finally:
            os.remove(unique_filename)
    else:
        bot.reply_to(message, "⚠️ Please send a plain text (.txt) file for translation or summarization.")

@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note'])
def handle_media_file(message):
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
            f.write(downloaded_file)

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

# === ADDED: /translate COMMAND HANDLER (MODIFIED FOR TXT FILES) ===

@bot.message_handler(commands=['translate'])
def handle_translate(message):
    user_id = str(message.from_user.id)
    if user_id not in last_transcription:
        return bot.send_message(message.chat.id, "No previous transcription or text file found.")
    msg = bot.send_message(
        message.chat.id,
        "Please type and send the target language for translation (e.g., Arabic, Spanish) etc."
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

# === ADDED: /summarize COMMAND HANDLER (MODIFIED FOR TXT FILES) ===

@bot.message_handler(commands=['summarize'])
def handle_summarize(message):
    user_id = str(message.from_user.id)
    if user_id not in last_transcription:
        return bot.send_message(message.chat.id, "No previous transcription or text file found.")
    msg = bot.send_message(
        message.chat.id,
        "Please type and send the language for summarization (e.g., English, Somali)."
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

@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'sticker'])
def fallback(message):
    user_id = str(message.from_user.id)
    update_user_activity(user_id)
    if not check_subscription(message.from_user.id):
        return send_subscription_message(message.chat.id)
    if is_tiktok_url(message.text):
        handle_tiktok_url(message)
    else:
        bot.send_message(message.chat.id, "⚠️ Please send a voice message, audio, video, video note, TikTok link, or a .txt file.")

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
