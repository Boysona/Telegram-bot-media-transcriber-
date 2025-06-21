import os
import uuid
import logging
import requests
import telebot
import json
from flask import Flask, request, abort
from datetime import datetime, timedelta
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import asyncio
import threading
import time
import subprocess

# --- NEW: AssemblyAI setup ---
ASSEMBLYAI_API_KEY = "6dab0a0669624f44afa50d679242e473" # Replace with your actual AssemblyAI API Key

# --- KEEP: MSSpeech for Text-to-Speech ---
from msspeech import MSSpeech, MSSpeechError

# --- NEW: MongoDB client and collections ---
from pymongo import MongoClient, ASCENDING
from pymongo.errors import ConnectionFailure

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# --- BOT CONFIGURATION (Using Media Transcriber Bot's Token and Webhook) ---
TOKEN = "7790991731:AAFl7aS2kw4zONbxFi2XzWPRiWBA5T52Pyg"  # <-- your bot token
ADMIN_ID = 5978150981  # <-- admin Telegram ID
WEBHOOK_URL = "https://spam-remover-bot-r3lv.onrender.com"  # <-- your Render URL

REQUIRED_CHANNEL = "@transcriberbo"  # <-- required subscription channel

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

# Download directory (temporary WAV + intermediate files)
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- MONGODB CONFIGURATION (copy from your first bot) ---
MONGO_URI = "mongodb+srv://hoskasii:GHyCdwpI0PvNuLTg@cluster0.dy7oe7t.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
DB_NAME = "telegram_bot_db"

# Collections
mongo_client: MongoClient = None
db = None
users_collection = None
language_settings_collection = None
media_language_settings_collection = None
tts_users_collection = None
processing_stats_collection = None

# --- In-memory caches (to reduce DB hits) ---
# Global dictionaries to hold user data in RAM
local_user_data = {}            # { user_id: { "last_active": "...", "transcription_count": N, ... } }
_user_language_cache = {}       # { user_id: language_name } # For translation/summarization
_media_language_cache = {}      # { user_id: media_language } # For STT source language
_tts_voice_cache = {}           # { user_id: voice_name }

# --- User state for Text-to-Speech input mode ---
# { user_id: "tts" or "stt" or None, "voice": voice_name }
user_mode = {}

# --- Statistics counters (in-memory for quick access) ---
# These are still in-memory and will reset on bot restart as per your original design.
# If you want these to persist, they would also need to be stored in MongoDB.
total_files_processed = 0
total_audio_files = 0
total_voice_clips = 0
total_videos = 0
total_processing_time = 0.0
bot_start_time = datetime.now()

# Admin uptime message storage
admin_uptime_message = {}
admin_uptime_lock = threading.Lock()

# --- User transcription cache (short-lived in-memory for translation/summarization) ---
# This is explicitly designed to be temporary, so it will reset on bot restart.
# Your current code already handles its 10-minute deletion.
user_transcriptions = {} # { user_id: { message_id: "transcription text" } }


GEMINI_API_KEY = "AIzaSyCHrGhRKXAp3DuQGH8HLB60ggryZeUFA9E"  # <-- your Gemini API Key

# User memory for Gemini (in-memory, resets on bot restart)
user_memory = {} # { user_id: [ {"role": "user", "text": "..."}, {"role": "model", "text": "..."} ] }


def ask_gemini(user_id, user_message):
    """
    Send conversation history to Gemini and return the response text.
    """
    # Note: we only keep last 10 messages in-memory per user for context
    # user_memory is still in-memory, will reset on bot restart
    user_memory.setdefault(user_id, []).append({"role": "user", "text": user_message})
    history = user_memory[user_id][-10:]
    parts = [{"text": msg["text"]} for msg in history]
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    )
    resp = requests.post(
        url,
        headers={'Content-Type': 'application/json'},
        json={"contents": [{"parts": parts}]}
    )
    result = resp.json()
    if "candidates" in result:
        reply = result['candidates'][0]['content']['parts'][0]['text']
        user_memory[user_id].append({"role": "model", "text": reply})
        return reply
    return "Error: " + json.dumps(result)

FILE_SIZE_LIMIT = 20 * 1024 * 1024  # 20MB
admin_state = {}

# Placeholder for keeping track of typing threads
processing_message_ids = {}

# ────────────────────────────────────────────────────────────────────────────────
#   M O N G O   H E L P E R   F U N C T I O N S
# ────────────────────────────────────────────────────────────────────────────────

def connect_to_mongodb():
    """
    Connect to MongoDB at startup, set up collections and indexes.
    Also, load all user data into in-memory caches.
    """
    global mongo_client, db
    global users_collection, language_settings_collection, media_language_settings_collection, tts_users_collection, processing_stats_collection
    global local_user_data, _user_language_cache, _media_language_cache, _tts_voice_cache

    try:
        mongo_client = MongoClient(MONGO_URI)
        mongo_client.admin.command('ismaster')
        db = mongo_client[DB_NAME]
        users_collection = db["users"]
        language_settings_collection = db["user_language_settings"]
        media_language_settings_collection = db["user_media_language_settings"]
        tts_users_collection = db["tts_users"]
        processing_stats_collection = db["file_processing_stats"]

        # Create indexes (if not already created)
        users_collection.create_index([("last_active", ASCENDING)])
        language_settings_collection.create_index([("_id", ASCENDING)])
        media_language_settings_collection.create_index([("_id", ASCENDING)])
        tts_users_collection.create_index([("_id", ASCENDING)])
        processing_stats_collection.create_index([("user_id", ASCENDING)])
        processing_stats_collection.create_index([("type", ASCENDING)])
        processing_stats_collection.create_index([("timestamp", ASCENDING)])

        logging.info("Connected to MongoDB and indexes created. Loading data to memory...")

        # --- Load all user data into in-memory caches on startup ---
        for user_doc in users_collection.find({}):
            local_user_data[user_doc["_id"]] = user_doc
        logging.info(f"Loaded {len(local_user_data)} user documents into local_user_data.")

        for lang_setting in language_settings_collection.find({}):
            _user_language_cache[lang_setting["_id"]] = lang_setting.get("language")
        logging.info(f"Loaded {len(_user_language_cache)} user language settings.")

        for media_lang_setting in media_language_settings_collection.find({}):
            _media_language_cache[media_lang_setting["_id"]] = media_lang_setting.get("media_language")
        logging.info(f"Loaded {len(_media_language_cache)} media language settings.")

        for tts_user in tts_users_collection.find({}):
            _tts_voice_cache[tts_user["_id"]] = tts_user.get("voice", "en-US-AriaNeural")
        logging.info(f"Loaded {len(_tts_voice_cache)} TTS voice settings.")

        logging.info("All essential user data loaded into in-memory caches.")

    except ConnectionFailure as e:
        logging.error(f"MongoDB connection failed: {e}")
        exit(1)
    except Exception as e:
        logging.error(f"Error during MongoDB connection or initial data load: {e}")
        exit(1)


def update_user_activity_db(user_id: int):
    """
    Update user.last_active = now() in local_user_data cache and then in MongoDB.
    """
    user_id_str = str(user_id)
    now_iso = datetime.now().isoformat()

    # Update in-memory cache
    if user_id_str not in local_user_data:
        local_user_data[user_id_str] = {
            "_id": user_id_str,
            "last_active": now_iso,
            "transcription_count": 0 # Initialize for new users
        }
    else:
        local_user_data[user_id_str]["last_active"] = now_iso

    # Persist to MongoDB
    try:
        users_collection.update_one(
            {"_id": user_id_str},
            {"$set": {"last_active": now_iso}},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error updating user activity for {user_id_str} in DB: {e}")

def get_user_data_db(user_id: str) -> dict | None:
    """
    Return user document from local_user_data cache. If not found, try MongoDB
    and load into cache.
    """
    if user_id in local_user_data:
        return local_user_data[user_id]
    try:
        doc = users_collection.find_one({"_id": user_id})
        if doc:
            local_user_data[user_id] = doc # Load into cache
        return doc
    except Exception as e:
        logging.error(f"Error fetching user data for {user_id} from DB: {e}")
        return None

def increment_transcription_count_db(user_id: str):
    """
    Increment transcription_count in local_user_data cache and then in MongoDB,
    also update last_active.
    """
    now_iso = datetime.now().isoformat()

    # Update in-memory cache
    if user_id not in local_user_data:
        local_user_data[user_id] = {
            "_id": user_id,
            "last_active": now_iso,
            "transcription_count": 1
        }
    else:
        local_user_data[user_id]["transcription_count"] = local_user_data[user_id].get("transcription_count", 0) + 1
        local_user_data[user_id]["last_active"] = now_iso

    # Persist to MongoDB
    try:
        users_collection.update_one(
            {"_id": user_id},
            {
                "$inc": {"transcription_count": 1},
                "$set": {"last_active": now_iso}
            },
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error incrementing transcription count for {user_id} in DB: {e}")

def get_user_language_setting_db(user_id: str) -> str | None:
    """
    Return user's preferred language for translations/summaries from cache.
    """
    return _user_language_cache.get(user_id)

def set_user_language_setting_db(user_id: str, lang: str):
    """
    Save preferred language in DB and update cache.
    """
    _user_language_cache[user_id] = lang # Update in-memory cache
    try:
        language_settings_collection.update_one(
            {"_id": user_id},
            {"$set": {"language": lang}},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error setting preferred language for {user_id} in DB: {e}")

def get_user_media_language_setting_db(user_id: str) -> str | None:
    """
    Return language chosen for media transcription from cache.
    """
    return _media_language_cache.get(user_id)

def set_user_media_language_setting_db(user_id: str, lang: str):
    """
    Save media transcription language in DB and update cache.
    """
    _media_language_cache[user_id] = lang # Update in-memory cache
    try:
        media_language_settings_collection.update_one(
            {"_id": user_id},
            {"$set": {"media_language": lang}},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error setting media language for {user_id} in DB: {e}")

def get_tts_user_voice_db(user_id: str) -> str:
    """
    Return TTS voice from cache (default "en-US-AriaNeural").
    """
    return _tts_voice_cache.get(user_id, "en-US-AriaNeural")

def set_tts_user_voice_db(user_id: str, voice: str):
    """
    Save TTS voice in DB and update cache.
    """
    _tts_voice_cache[user_id] = voice # Update in-memory cache
    try:
        tts_users_collection.update_one(
            {"_id": user_id},
            {"$set": {"voice": voice}},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error setting TTS voice for {user_id} in DB: {e}")

# ────────────────────────────────────────────────────────────────────────────────
#   U T I L I T I E S   (keep typing, keep recording, update uptime)
# ────────────────────────────────────────────────────────────────────────────────

def keep_typing(chat_id, stop_event):
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, 'typing')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending typing action: {e}")
            break

def keep_recording(chat_id, stop_event):
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, 'record_audio')
            time.sleep(4)
        except Exception as e:
            logging.error(f"Error sending record_audio action: {e}")
            break

def update_uptime_message(chat_id, message_id):
    """
    Live-update the admin uptime message every second.
    """
    while True:
        try:
            elapsed = datetime.now() - bot_start_time
            total_seconds = int(elapsed.total_seconds())
            days, rem = divmod(total_seconds, 86400)
            hours, rem = divmod(rem, 3600)
            minutes, seconds = divmod(rem, 60)

            uptime_text = (
                f"**Bot Uptime:**\n"
                f"{days} days, {hours:02d} hours, {minutes:02d} minutes, {seconds:02d} seconds"
            )

            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=uptime_text,
                parse_mode="Markdown"
            )
            time.sleep(1)
        except telebot.apihelper.ApiTelegramException as e:
            if "message is not modified" not in str(e):
                logging.error(f"Error updating uptime message: {e}")
            break
        except Exception as e:
            logging.error(f"Unexpected error in uptime thread: {e}")
            break

# ────────────────────────────────────────────────────────────────────────────────
#   S U B S C R I P T I O N   C H E C K
# ────────────────────────────────────────────────────────────────────────────────

def check_subscription(user_id: int) -> bool:
    """
    If REQUIRED_CHANNEL is set, verify user is a member.
    """
    if not REQUIRED_CHANNEL:
        return True
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Error checking subscription for user {user_id}: {e}")
        return False

def send_subscription_message(chat_id: int):
    """
    Prompt user to join REQUIRED_CHANNEL.
    """
    if not REQUIRED_CHANNEL:
        return
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton(
            "Click here to join the channel",
            url=f"https://t.me/{REQUIRED_CHANNEL[1:]}"
        )
    )
    bot.send_message(
        chat_id,
        "😓 Sorry …\n🔰 To continue using this bot you must join the channel @transcriberbo ‼️"
        " After joining, come back to continue using the bot.",
        reply_markup=markup,
        disable_web_page_preview=True
    )

# ────────────────────────────────────────────────────────────────────────────────
#   B O T   H A N D L E R S
# ────────────────────────────────────────────────────────────────────────────────

def create_main_menu_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    markup.add(KeyboardButton("Speech to Text (STT)"))
    markup.add(KeyboardButton("Text to Speech (TTS)"))
    return markup

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id_str = str(message.from_user.id)
    chat_id = message.chat.id

    # Ensure user is in local_user_data and DB
    if user_id_str not in local_user_data:
        local_user_data[user_id_str] = {
            "_id": user_id_str,
            "last_active": datetime.now().isoformat(),
            "transcription_count": 0
        }
        # Immediately save new user to DB
        try:
            users_collection.insert_one(local_user_data[user_id_str])
            logging.info(f"New user {user_id_str} inserted into MongoDB.")
        except Exception as e:
            logging.error(f"Error inserting new user {user_id_str} into DB: {e}")
    else:
        # Just update activity if already exists
        update_user_activity_db(message.from_user.id)

    # Ensure user mode is OFF on /start
    user_mode[user_id_str] = None

    if message.from_user.id == ADMIN_ID:
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add("Send Broadcast", "Total Users", "/status")
        sent_message = bot.send_message(
            chat_id,
            "Admin Panel and Uptime (updating live)...",
            reply_markup=keyboard
        )
        with admin_uptime_lock:
            if (
                admin_uptime_message.get(ADMIN_ID)
                and admin_uptime_message[ADMIN_ID].get('thread')
                and admin_uptime_message[ADMIN_ID]['thread'].is_alive()
            ):
                pass

            admin_uptime_message[ADMIN_ID] = {
                'message_id': sent_message.message_id,
                'chat_id': message.chat.id
            }
            uptime_thread = threading.Thread(
                target=update_uptime_message,
                args=(message.chat.id, sent_message.message_id)
            )
            uptime_thread.daemon = True
            uptime_thread.start()
            admin_uptime_message[ADMIN_ID]['thread'] = uptime_thread
    else:
        # Prompt user to select STT or TTS
        bot.send_message(
            chat_id,
            "👋 Welcome! Please choose a service:",
            reply_markup=create_main_menu_keyboard()
        )

@bot.message_handler(func=lambda message: message.text == "Speech to Text (STT)")
def handle_stt_button(message):
    user_id_str = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    user_transcription_count = local_user_data.get(user_id_str, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    user_mode[user_id_str] = "stt" # Set user mode to STT
    markup = generate_language_keyboard("set_media_lang")
    bot.send_message(
        message.chat.id,
        "Please choose the language of your audio/video files for transcription:",
        reply_markup=markup
    )

@bot.message_handler(func=lambda message: message.text == "Text to Speech (TTS)")
def handle_tts_button(message):
    user_id_str = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    user_transcription_count = local_user_data.get(user_id_str, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    user_mode[user_id_str] = "tts" # Set user mode to TTS
    bot.send_message(
        message.chat.id,
        "🎙️ Choose a language for text-to-speech:",
        reply_markup=make_tts_language_keyboard()
    )


@bot.message_handler(commands=['help'])
def help_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    user_transcription_count = local_user_data.get(user_id, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    # Ensure user mode is OFF on /help
    user_mode[user_id] = None

    help_text = (
        """ℹ️ How to use this bot:

This bot transcribes voice messages, audio files, and videos using advanced AI, and can also convert text to speech!

1.  **Speech to Text (STT):**
    * First, tap the "Speech to Text (STT)" button or use the `/media_language` command to select the language of your audio/video file. This is crucial for accurate transcription.
    * Then, send a voice message, audio file, video note, or a video file (e.g., .mp4) as a document/attachment.
    * The bot will process your media and send back the transcribed text. If the transcription is very long, it will be sent as a text file.
    * After receiving the transcription, you'll see inline buttons with options to **Translate** or **Summarize** the text.

2.  **Text to Speech (TTS):**
    * First, tap the "Text to Speech (TTS)" button or use the `/text_to_speech` command to choose a language and voice.
    * After selecting your preferred voice, simply send any text message, and the bot will convert it into an audio file for you.

3.  **Commands:**
    * `/start`: Get a welcome message and main menu. (Admins see a live uptime panel).
    * `/status`: View detailed statistics about the bot's performance and usage.
    * `/help`: Display these instructions on how to use the bot.
    * `/language`: Change your preferred language for **translations and summaries**.
    * `/media_language`: Set the language of the audio in your media files for **transcription (STT)**.
    * `/text_to_speech`: Choose a language and voice for the **text-to-speech (TTS)** feature.
    * `/privacy`: Read the bot's privacy notice to understand how your data is handled.

Enjoy transcribing, translating, summarizing, and converting text to speech quickly and easily!
"""
    )
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['privacy'])
def privacy_notice_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    user_transcription_count = local_user_data.get(user_id, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    # Ensure user mode is OFF on /privacy
    user_mode[user_id] = None

    privacy_text = (
        """**Privacy Notice**

Your privacy is paramount. Here's a transparent look at how this bot handles your data in real-time:

1.  **Data We Process & Its Lifecycle:**
    * **Media Files (Voice, Audio, Video):** When you send a media file (voice, audio, video note, or a video file as a document), it's temporarily downloaded for **immediate transcription**. Crucially, these files are **deleted instantly** from our servers once the transcription is complete. We do not store your media content.
    * **Text for Speech Synthesis:** When you send text for conversion to speech, it is processed to generate the audio and then **not stored**. The generated audio file is also temporary and deleted after sending.
    * **Transcriptions:** The text generated from your media is held **temporarily in-memory** (for 10 minutes only). After 10 minutes, the transcription is automatically deleted and cannot be retrieved. This data is used only for immediate translation or summarization requests.
    * **User IDs, Language Preferences, TTS Voices, and Activity Data:** Your Telegram User ID and your chosen preferences (language for translations/summaries, media transcription language, TTS voice) are stored in MongoDB. Basic activity (like last active timestamp and transcription count) are also stored. This helps us remember your preferences and track basic, aggregated activity (like when you last used the bot) to improve service and understand overall usage patterns. This data is also kept in-memory for quick access during bot operation and is persisted to MongoDB.

2.  **How Your Data is Used:**
    * To deliver the bot's core services: transcribing, translating, summarizing your media, and converting text to speech.
    * To enhance bot performance and gain insights into general usage trends through anonymous, collective statistics (e.g., total files processed).
    * To maintain your personalized language settings and voice preferences across sessions.

3.  **Data Sharing Policy:**
    * We **do not share** your personal data, media files, or transcriptions with any third parties.
    * Transcription, translation, and summarization are facilitated by integrating with advanced AI models (specifically, AssemblyAI for transcription and the Gemini API for translation/summarization). Text-to-speech uses the Microsoft Cognitive Services Speech API. Your input sent to these models is governed by their respective privacy policies, but we ensure that your data is **not stored by us** after processing by these services.

4.  **Data Retention:**
    * **Media files and generated audio files:** Deleted immediately post-processing.
    * **Transcriptions:** Held in-memory for 10 minutes, then permanently deleted.
    * **User IDs and language/voice preferences:** Stored in MongoDB to support your settings and for anonymous usage statistics. This data is also cached in memory for performance. If you wish to have your stored preferences removed, you can cease using the bot or contact the bot administrator for explicit data deletion.

By using this bot, you acknowledge and agree to the data practices outlined in this Privacy Notice.

Should you have any questions or concerns regarding your privacy, please feel free to contact the bot administrator.
"""
    )
    bot.send_message(message.chat.id, privacy_text, parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def status_handler(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    user_transcription_count = local_user_data.get(user_id, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    # Ensure user mode is OFF on /status
    user_mode[user_id] = None

    uptime = datetime.now() - bot_start_time
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    # Count active today
    today_iso = datetime.now().date().isoformat()
    # Check local_user_data for active users today
    active_today_count = sum(1 for user_doc in local_user_data.values() if user_doc.get("last_active", "").startswith(today_iso))


    # Total registered users from local_user_data
    total_registered_users = len(local_user_data)

    # Processing stats (voice/audio/video counts + total processing time)
    try:
        total_processed = processing_stats_collection.count_documents({})
        voice_count = processing_stats_collection.count_documents({"type": "voice"})
        audio_count = processing_stats_collection.count_documents({"type": "audio"})
        video_count = processing_stats_collection.count_documents({"type": "video"})
        pipeline = [
            {"$group": {"_id": None, "total_time": {"$sum": "$processing_time"}}}
        ]
        agg_result = list(processing_stats_collection.aggregate(pipeline))
        total_proc_seconds = agg_result[0]["total_time"] if agg_result else 0
    except Exception as e:
        logging.error(f"Error fetching processing stats from DB: {e}")
        total_processed = voice_count = audio_count = video_count = 0
        total_proc_seconds = 0

    proc_hours = int(total_proc_seconds) // 3600
    proc_minutes = (int(total_proc_seconds) % 3600) // 60
    proc_seconds = int(total_proc_seconds) % 60

    text = (
        "📊 Bot Statistics\n\n"
        "🟢 **Bot Status: Online**\n"
        f"⏱️ The bot has been running for: {days} days, {hours:02d} hours, {minutes:02d} minutes, {seconds:02d} seconds\n\n"
        "👥 User Statistics\n"
        f"▫️ Total Users Today : {active_today_count}\n" # Updated to reflect in-memory data
        f"▫️ Total Registered Users : {total_registered_users}\n\n" # Updated to reflect in-memory data
        "⚙️ Processing Statistics \n" # This still comes from DB
        f"▫️ Total Files Processed: {total_processed}\n"
        f"▫️ Voice Clips: {voice_count}\n"
        f"▫️ Audio Files: {audio_count}\n"
        f"▫️ Videos: {video_count}\n"
        f"⏱️ Total Processing Time: {proc_hours} hours {proc_minutes} minutes {proc_seconds} seconds\n\n"
        "⸻\n\n"
        "Thanks for using our service! 🙌"
    )

    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "Total Users" and m.from_user.id == ADMIN_ID)
def total_users(message):
    total_registered = len(local_user_data) # Get total users from in-memory cache
    bot.send_message(message.chat.id, f"Total registered users (from memory): {total_registered}")

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
    # Broadcast to every user currently in local_user_data (which reflects MongoDB)
    for uid in local_user_data.keys():
        try:
            bot.copy_message(uid, message.chat.id, message.message_id)
            success += 1
        except telebot.apihelper.ApiTelegramException as e:
            logging.error(f"Failed to send broadcast to {uid}: {e}")
            fail += 1

    bot.send_message(
        message.chat.id,
        f"Broadcast complete.\nSuccessful: {success}\nFailed: {fail}"
    )

# ────────────────────────────────────────────────────────────────────────────────
#   M E D I A   H A N D L I N G  (voice, audio, video, video_note, document)
# ────────────────────────────────────────────────────────────────────────────────

@bot.message_handler(content_types=['voice', 'audio', 'video', 'video_note', 'document'])
def handle_file(message):
    uid_str = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    # Check subscription only if transcription_count >=5
    user_transcription_count = local_user_data.get(uid_str, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(message.from_user.id):
        send_subscription_message(message.chat.id)
        return

    # Check if user is in STT mode and has set media_language
    if user_mode.get(uid_str) != "stt":
        bot.send_message(
            message.chat.id,
            "Please select 'Speech to Text (STT)' from the main menu or use `/media_language` first, "
            "then send your media file."
        )
        return

    media_lang = get_user_media_language_setting_db(uid_str)
    if not media_lang:
        bot.send_message(
            message.chat.id,
            "⚠️ Please first select the language of the audio/video file using /media_language before sending the file."
        )
        return

    # Determine which file object to use
    file_obj = None
    is_document_video = False # This variable is not strictly needed for AssemblyAI, but kept for context.
    type_str = ""
    if message.voice:
        file_obj = message.voice
        type_str = "voice"
    elif message.audio:
        file_obj = message.audio
        type_str = "audio"
    elif message.video:
        file_obj = message.video
        type_str = "video"
    elif message.video_note:
        file_obj = message.video_note
        type_str = "video"
    elif message.document:
        mime = message.document.mime_type or ""
        if mime.startswith("video/"):
            file_obj = message.document
            is_document_video = True
            type_str = "video"
        elif mime.startswith("audio/"):
            file_obj = message.document
            is_document_video = True
            type_str = "audio"
        else:
            bot.send_message(
                message.chat.id,
                "❌ The file you sent is not a supported audio/video format. "
                "Please send a voice message, audio file, video note, or video file (e.g. .mp4)."
            )
            return
    else:
        bot.send_message(
            message.chat.id,
            "❌ Please send only voice messages, audio files, video notes, or video files."
        )
        return

    # Check file size limit
    size = file_obj.file_size
    if size and size > FILE_SIZE_LIMIT:
        bot.send_message(message.chat.id, "😓 Sorry, the file size you uploaded is too large (max allowed is 20MB).")
        return

    # Add “👀” reaction (if supported)
    try:
        bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=[{'type': 'emoji', 'emoji': '👀'}]
        )
    except Exception as e:
        logging.error(f"Error setting reaction: {e}")

    # Start typing indicator
    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=keep_typing, args=(message.chat.id, stop_typing))
    typing_thread.daemon = True
    typing_thread.start()
    processing_message_ids[message.chat.id] = stop_typing

    try:
        threading.Thread(
            target=process_media_file_assemblyai, # Call the new AssemblyAI processing function
            args=(message, stop_typing, type_str)
        ).start()
    except Exception as e:
        logging.error(f"Error initiating file processing: {e}")
        stop_typing.set()
        try:
            bot.set_message_reaction(
                chat_id=message.chat.id,
                message_id=message.message_id,
                reaction=[]
            )
        except Exception as remove_e:
            logging.error(f"Error removing reaction on early error: {remove_e}")
        bot.send_message(message.chat.id, "😓 Sorry, an unexpected error occurred. Please try again.")

def process_media_file_assemblyai(message, stop_typing, type_str):
    """
    Download media, upload to AssemblyAI, get transcription, store stats,
    send transcription, schedule deletion of the transcription after 10 minutes.
    """
    global total_files_processed, total_audio_files, total_voice_clips, total_videos, total_processing_time

    uid_str = str(message.from_user.id)
    file_obj = None
    if message.voice:
        file_obj = message.voice
    elif message.audio:
        file_obj = message.audio
    elif message.video:
        file_obj = message.video
    elif message.video_note:
        file_obj = message.video_note
    else:
        file_obj = message.document

    local_temp_file = None
    processing_start_time = datetime.now()

    try:
        info = bot.get_file(file_obj.file_id)

        # Download to temporary file
        file_extension = os.path.splitext(info.file_path)[1]
        local_temp_file = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}{file_extension}")
        data = bot.download_file(info.file_path)
        with open(local_temp_file, 'wb') as f:
            f.write(data)

        # Upload to AssemblyAI
        headers = {'authorization': ASSEMBLYAI_API_KEY}
        with open(local_temp_file, 'rb') as f:
            upload_response = requests.post("https://api.assemblyai.com/v2/upload", headers=headers, data=f)
        upload_data = upload_response.json()
        audio_url = upload_data.get('upload_url')

        if not audio_url:
            raise Exception(f"Failed to upload audio to AssemblyAI: {upload_data.get('error', 'Unknown error')}")

        # Transcribe with AssemblyAI
        media_lang_name = get_user_media_language_setting_db(uid_str)
        # AssemblyAI typically uses ISO 639-1 or BCP-47. Our LANGUAGES already provides this.
        # Ensure 'auto' is not sent if a specific language is selected, otherwise use it.
        assemblyai_lang_code = get_lang_code(media_lang_name) if media_lang_name else "en" # Default to English if not set

        json_data = {
            "audio_url": audio_url,
            "language_code": assemblyai_lang_code,
            "speech_model": "nano" # Use the nano model
        }

        transcript_response = requests.post("https://api.assemblyai.com/v2/transcript", headers=headers, json=json_data)
        transcript_data = transcript_response.json()
        transcript_id = transcript_data.get('id')

        if not transcript_id:
            raise Exception(f"Failed to start AssemblyAI transcription: {transcript_data.get('error', 'Unknown error')}")

        polling_endpoint = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
        while True:
            polling_response = requests.get(polling_endpoint, headers=headers)
            polling_data = polling_response.json()
            if polling_data['status'] == 'completed':
                transcription = polling_data.get('text')
                break
            elif polling_data['status'] == 'error':
                raise Exception(f"AssemblyAI transcription failed: {polling_data.get('error', 'Unknown error')}")
            else:
                time.sleep(1) # Poll every second

        # Store transcription in-memory
        user_transcriptions.setdefault(uid_str, {})[message.message_id] = transcription

        # Schedule deletion of this transcription after 10 minutes
        def delete_transcription_later(u_id, msg_id):
            time.sleep(600)
            if u_id in user_transcriptions and msg_id in user_transcriptions[u_id]:
                del user_transcriptions[u_id][msg_id]
                logging.info(f"Deleted transcription for user {u_id}, message {msg_id} after 10 minutes")

        threading.Thread(
            target=delete_transcription_later,
            args=(uid_str, message.message_id),
            daemon=True
        ).start()

        # Update counters (these are in-memory and will reset on bot restart)
        total_files_processed += 1
        if message.voice:
            total_voice_clips += 1
        elif message.audio:
            total_audio_files += 1
        else:
            total_videos += 1

        processing_time = (datetime.now() - processing_start_time).total_seconds()
        total_processing_time += processing_time

        increment_transcription_count_db(uid_str) # This now updates both in-memory and DB

        # Store processing stat for success
        try:
            processing_stats_collection.insert_one({
                "user_id": uid_str,
                "message_id": message.message_id,
                "type": type_str,
                "processing_time": processing_time,
                "timestamp": datetime.now().isoformat(),
                "status": "success"
            })
        except Exception as e:
            logging.error(f"Error inserting processing stat (success): {e}")

        # Build inline buttons for Translate / Summarize
        buttons = InlineKeyboardMarkup()
        buttons.add(
            InlineKeyboardButton("Translate", callback_data=f"btn_translate|{message.message_id}"),
            InlineKeyboardButton("Summarize", callback_data=f"btn_summarize|{message.message_id}")
        )

        # Remove "👀" reaction before sending result
        try:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
        except Exception as e:
            logging.error(f"Error removing reaction before sending result: {e}")

        # Send transcription (as file if too long)
        if transcription and len(transcription) > 4000:
            fn = f"{uuid.uuid4()}_transcription.txt"
            with open(fn, 'w', encoding='utf-8') as f:
                f.write(transcription)
            bot.send_chat_action(message.chat.id, 'upload_document')
            with open(fn, 'rb') as doc:
                bot.send_document(
                    message.chat.id,
                    doc,
                    reply_to_message_id=message.message_id,
                    reply_markup=buttons,
                    caption="Here’s your transcription. Tap a button below for more options."
                )
            os.remove(fn)
        elif transcription:
            bot.reply_to(
                message,
                transcription,
                reply_markup=buttons
            )
        else:
            bot.reply_to(message, "ℹ️ No transcription text was returned by AssemblyAI.")

        # After successful transcription, if user just hit 5, check subscription
        # Get transcription count from local_user_data
        trans_count_post = local_user_data.get(uid_str, {}).get('transcription_count', 0)
        if trans_count_post == 5 and not check_subscription(message.from_user.id):
            send_subscription_message(message.chat.id)

    except Exception as e:
        logging.error(f"Error processing file for user {uid_str}: {e}", exc_info=True) # Added exc_info for full traceback
        try:
            bot.set_message_reaction(chat_id=message.chat.id, message_id=message.message_id, reaction=[])
        except Exception as remove_e:
            logging.error(f"Error removing reaction on general processing error: {remove_e}")
        bot.send_message(
            message.chat.id,
            "😓𝗪𝗲’𝗿𝗲 𝘀𝗼𝗿𝗿𝘆, 𝗮𝗻 𝗲𝗿𝗿𝗼𝗿 𝗼𝗰𝗰𝘂𝗿𝗿𝗲𝗱 𝗱𝘂𝗿𝗶𝗻𝗴 𝘁𝗿𝗮𝗻𝘀𝗰𝗿𝗶𝗽𝘁𝗶𝗼𝗻.\n"
            "The audio might be noisy or spoken too quickly, or there was an issue with AssemblyAI.\n"
            "Please try again or upload a different file.\n"
            "Make sure the file you’re sending and the selected language match — otherwise, an error may occur."
        )
        # Log failure stat
        proc_time = (datetime.now() - processing_start_time).total_seconds()
        try:
            processing_stats_collection.insert_one({
                "user_id": uid_str,
                "message_id": message.message_id,
                "type": type_str,
                "processing_time": proc_time,
                "timestamp": datetime.now().isoformat(),
                "status": "fail"
            })
        except Exception as e2:
            logging.error(f"Error inserting processing stat (exception): {e2}")

    finally:
        # Stop typing indicator
        stop_typing.set()
        if message.chat.id in processing_message_ids:
            del processing_message_ids[message.chat.id]

        # Clean up temp files
        try:
            if local_temp_file and os.path.exists(local_temp_file):
                os.remove(local_temp_file)
        except Exception as e:
            logging.error(f"Error cleaning up temp files: {e}")

# --- Language list and helper functions (unchanged) ---

LANGUAGES = [
    {"name": "English", "flag": "🇬🇧", "code": "en"},
    {"name": "Arabic", "flag": "🇸🇦", "code": "ar"},
    {"name": "Spanish", "flag": "🇪🇸", "code": "es"},
    {"name": "Hindi", "flag": "🇮🇳", "code": "hi"},
    {"name": "French", "flag": "🇫🇷", "code": "fr"},
    {"name": "German", "flag": "🇩🇪", "code": "de"},
    {"name": "Chinese", "flag": "🇨🇳", "code": "zh"},
    {"name": "Japanese", "flag": "🇯🇵", "code": "ja"},
    {"name": "Portuguese", "flag": "🇵🇹", "code": "pt"},
    {"name": "Russian", "flag": "🇷🇺", "code": "ru"},
    {"name": "Turkish", "flag": "🇹🇷", "code": "tr"},
    {"name": "Korean", "flag": "🇰🇷", "code": "ko"},
    {"name": "Italian", "flag": "🇮🇹", "code": "it"},
    {"name": "Indonesian", "flag": "🇮🇩", "code": "id"},
    {"name": "Vietnamese", "flag": "🇻🇳", "code": "vi"},
    {"name": "Thai", "flag": "🇹🇭", "code": "th"},
    {"name": "Dutch", "flag": "🇳🇱", "code": "nl"},
    {"name": "Polish", "flag": "🇵🇱", "code": "pl"},
    {"name": "Swedish", "flag": "🇸🇪", "code": "sv"},
    {"name": "Filipino", "flag": "🇵🇭", "code": "tl"},
    {"name": "Greek", "flag": "🇬🇷", "code": "el"},
    {"name": "Hebrew", "flag": "🇮🇱", "code": "he"},
    {"name": "Hungarian", "flag": "🇭🇺", "code": "hu"},
    {"name": "Czech", "flag": "🇨🇿", "code": "cs"},
    {"name": "Danish", "flag": "🇩🇰", "code": "da"},
    {"name": "Finnish", "flag": "🇫🇮", "code": "fi"},
    {"name": "Norwegian", "flag": "🇳🇴", "code": "no"},
    {"name": "Romanian", "flag": "🇷🇴", "code": "ro"},
    {"name": "Slovak", "flag": "🇸🇰", "code": "sk"},
    {"name": "Ukrainian", "flag": "🇺🇦", "code": "uk"},
    {"name": "Malay", "flag": "🇲🇾", "code": "ms"},
    {"name": "Bengali", "flag": "🇧🇩", "code": "bn"},
    {"name": "Tamil", "flag": "🇮🇳", "code": "ta"},
    {"name": "Telugu", "flag": "🇮🇳", "code": "te"},
    {"name": "Kannada", "flag": "🇮🇳", "code": "kn"},
    {"name": "Malayalam", "flag": "🇮🇳", "code": "ml"},
    {"name": "Gujarati", "flag": "🇮🇳", "code": "gu"},
    {"name": "Marathi", "flag": "🇮🇳", "code": "mr"},
    {"name": "Urdu", "flag": "🇵🇰", "code": "ur"},
    {"name": "Nepali", "flag": "🇳🇵", "code": "ne"},
    {"name": "Sinhala", "flag": "🇱🇰", "code": "si"},
    {"name": "Khmer", "flag": "🇰🇭", "code": "km"},
    {"name": "Lao", "flag": "🇱🇦", "code": "lo"},
    {"name": "Burmese", "flag": "🇲🇲", "code": "my"},
    {"name": "Georgian", "flag": "🇬🇪", "code": "ka"},
    {"name": "Armenian", "flag": "🇦🇲", "code": "hy"},
    {"name": "Azerbaijani", "flag": "🇦🇿", "code": "az"},
    {"name": "Kazakh", "flag": "🇰🇿", "code": "kk"},
    {"name": "Uzbek", "flag": "🇺🇿", "code": "uz"},
    {"name": "Kyrgyz", "flag": "🇰🇬", "code": "ky"},
    {"name": "Tajik", "flag": "🇹🇯", "code": "tg"},
    {"name": "Turkmen", "flag": "🇹🇲", "code": "tk"},
    {"name": "Mongolian", "flag": "🇲🇳", "code": "mn"},
    {"name": "Estonian", "flag": "🇪🇪", "code": "et"},
    {"name": "Latvian", "flag": "🇱🇻", "code": "lv"},
    {"name": "Lithuanian", "flag": "🇱🇹", "code": "lt"},
    {"name": "Afrikaans", "flag": "🇿🇦", "code": "af"},
    {"name": "Albanian", "flag": "🇦🇱", "code": "sq"},
    {"name": "Bosnian", "flag": "🇧🇦", "code": "bs"},
    {"name": "Bulgarian", "flag": "🇧🇬", "code": "bg"},
    {"name": "Catalan", "flag": "🇪🇸", "code": "ca"},
    {"name": "Croatian", "flag": "🇭🇷", "code": "hr"},
    {"name": "Galician", "flag": "🇪🇸", "code": "gl"},
    {"name": "Icelandic", "flag": "🇮🇸", "code": "is"},
    {"name": "Irish", "flag": "🇮🇪", "code": "ga"},
    {"name": "Macedonian", "flag": "🇲🇰", "code": "mk"},
    {"name": "Maltese", "flag": "🇲🇹", "code": "mt"},
    {"name": "Serbian", "flag": "🇷🇸", "code": "sr"},
    {"name": "Slovenian", "flag": "🇸🇮", "code": "sl"},
    {"name": "Welsh", "flag": "🏴", "code": "cy"},
    {"name": "Zulu", "flag": "🇿🇦", "code": "zu"},
    {"name": "Somali", "flag": "🇸🇴", "code": "so"},
]

def get_lang_code(lang_name: str) -> str | None:
    for lang in LANGUAGES:
        if lang['name'].lower() == lang_name.lower():
            return lang['code']
    return None

def generate_language_keyboard(callback_prefix: str, message_id: int | None = None):
    """
    Create inline keyboard for selecting any language in LANGUAGES.
    """
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    for lang in LANGUAGES:
        cb_data = f"{callback_prefix}|{lang['name']}"
        if message_id is not None:
            cb_data += f"|{message_id}"
        buttons.append(InlineKeyboardButton(f"{lang['name']} {lang['flag']}", callback_data=cb_data))
    for i in range(0, len(buttons), 3):
        markup.add(*buttons[i:i+3])
    return markup

# --- NEW: TTS VOICES BY LANGUAGE (unchanged list) ---
TTS_VOICES_BY_LANGUAGE = {
    "English 🇬🇧": [
        "en-US-AriaNeural", "en-US-GuyNeural", "en-US-JennyNeural", "en-US-DavisNeural",
        "en-GB-LibbyNeural", "en-GB-RyanNeural", "en-GB-MiaNeural", "en-GB-ThomasNeural",
        "en-AU-NatashaNeural", "en-AU-WilliamNeural", "en-CA-LindaNeural", "en-CA-ClaraNeural",
        "en-IE-EmilyNeural", "en-IE-ConnorNeural", "en-IN-NeerjaNeural", "en-IN-PrabhatNeural"
    ],
    "Arabic 🇸🇦": [
        "ar-SA-HamedNeural", "ar-SA-ZariyahNeural", "ar-EG-SalmaNeural", "ar-EG-ShakirNeural",
        "ar-DZ-AminaNeural", "ar-DZ-IsmaelNeural", "ar-BH-LailaNeural", "ar-BH-AliNeural",
        "ar-IQ-RanaNeural", "ar-IQ-BasselNeural", "ar-KW-FahedNeural", "ar-KW-NouraNeural",
        "ar-OM-AishaNeural", "ar-OM-SamirNeural", "ar-QA-MoazNeural", "ar-QA-ZainabNeural",
        "ar-SY-AmiraNeural", "ar-SY-LaithNeural", "ar-AE-FatimaNeural", "ar-AE-HamdanNeural",
        "ar-YE-HamdanNeural", "ar-YE-SarimNeural"
    ],
    "Spanish 🇪🇸": [
        "es-ES-AlvaroNeural", "es-ES-ElviraNeural", "es-MX-DaliaNeural", "es-MX-JorgeNeural",
        "es-AR-ElenaNeural", "es-AR-TomasNeural", "es-CO-SalomeNeural", "es-CO-GonzaloNeural",
        "es-US-PalomaNeural", "es-US-JuanNeural", "es-CL-LorenzoNeural", "es-CL-CatalinaNeural",
        "es-PE-CamilaNeural", "es-PE-DiegoNeural", "es-VE-PaolaNeural", "es-VE-SebastianNeural",
        "es-CR-MariaNeural", "es-CR-JuanNeural",
        "es-DO-RamonaNeural", "es-DO-AntonioNeural"
    ],
    "Hindi 🇮🇳": [
        "hi-IN-SwaraNeural", "hi-IN-MadhurNeural"
    ],
    "French 🇫🇷": [
        "fr-FR-DeniseNeural", "fr-FR-HenriNeural", "fr-CA-SylvieNeural", "fr-CA-JeanNeural",
        "fr-CH-ArianeNeural", "fr-CH-FabriceNeural", "fr-CH-CharlineNeural", "fr-BE-CamilleNeural"
    ],
    "German 🇩🇪": [
        "de-DE-KatjaNeural", "de-DE-ConradNeural", "de-CH-LeniNeural", "de-CH-JanNeural",
        "de-AT-IngridNeural", "de-AT-JonasNeural"
    ],
    "Chinese 🇨🇳": [
        "zh-CN-XiaoxiaoNeural", "zh-CN-YunyangNeural", "zh-CN-YunjianNeural", "zh-CN-XiaoyunNeural",
        "zh-TW-HsiaoChenNeural", "zh-TW-YunJheNeural", "zh-HK-HiuMaanNeural", "zh-HK-WanLungNeural",
        "zh-SG-XiaoMinNeural", "zh-SG-YunJianNeural"
    ],
    "Japanese 🇯🇵": [
        "ja-JP-NanamiNeural", "ja-JP-KeitaNeural", "ja-JP-MayuNeural", "ja-JP-DaichiNeural"
    ],
    "Portuguese 🇧🇷": [
        "pt-BR-FranciscaNeural", "pt-BR-AntonioNeural", "pt-PT-RaquelNeural", "pt-PT-DuarteNeural"
    ],
    "Russian 🇷🇺": [
        "ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural", "ru-RU-LarisaNeural", "ru-RU-MaximNeural"
    ],
    "Turkish 🇹🇷": [
        "tr-TR-EmelNeural", "tr-TR-AhmetNeural"
    ],
    "Korean 🇰🇷": [
        "ko-KR-SunHiNeural", "ko-KR-InJoonNeural"
    ],
    "Italian 🇮🇹": [
        "it-IT-ElsaNeural", "it-IT-DiegoNeural"
    ],
    "Indonesian 🇮🇩": [
        "id-ID-GadisNeural", "id-ID-ArdiNeural"
    ],
    "Vietnamese 🇻🇳": [
        "vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"
    ],
    "Thai 🇹🇭": [
        "th-TH-PremwadeeNeural", "th-TH-NiwatNeural"
    ],
    "Dutch 🇳🇱": [
        "nl-NL-ColetteNeural", "nl-NL-MaartenNeural"
    ],
    "Polish 🇵🇱": [
        "pl-PL-ZofiaNeural", "pl-PL-MarekNeural"
    ],
    "Swedish 🇸🇪": [
        "sv-SE-SofieNeural", "sv-SE-MattiasNeural"
    ],
    "Filipino 🇵🇭": [
        "fil-PH-BlessicaNeural", "fil-PH-AngeloNeural"
    ],
    "Greek 🇬🇷": [
        "el-GR-AthinaNeural", "el-GR-NestorasNeural"
    ],
    "Hebrew 🇮🇱": [
        "he-IL-AvriNeural", "he-IL-HilaNeural"
    ],
    "Hungarian 🇭🇺": [
        "hu-HU-NoemiNeural", "hu-HU-AndrasNeural"
    ],
    "Czech 🇨🇿": [
        "cs-CZ-VlastaNeural", "cs-CZ-AntoninNeural"
    ],
    "Danish 🇩🇰": [
        "da-DK-ChristelNeural", "da-DK-JeppeNeural"
    ],
    "Finnish 🇫🇮": [
        "fi-FI-SelmaNeural", "fi-FI-HarriNeural"
    ],
    "Norwegian 🇳🇴": [
        "nb-NO-PernilleNeural", "nb-NO-FinnNeural"
    ],
    "Romanian 🇷🇴": [
        "ro-RO-AlinaNeural", "ro-RO-EmilNeural"
    ],
    "Slovak 🇸🇰": [
        "sk-SK-LukasNeural", "sk-SK-ViktoriaNeural"
    ],
    "Ukrainian 🇺🇦": [
        "uk-UA-PolinaNeural", "uk-UA-OstapNeural"
    ],
    "Malay 🇲🇾": [
        "ms-MY-YasminNeural", "ms-MY-OsmanNeural"
    ],
    "Bengali 🇧🇩": [
        "bn-BD-NabanitaNeural", "bn-BD-BasharNeural"
    ],
    "Tamil 🇮🇳": [
        "ta-IN-PallaviNeural", "ta-IN-ValluvarNeural"
    ],
    "Telugu 🇮🇳": [
        "te-IN-ShrutiNeural", "te-IN-RagavNeural"
    ],
    "Kannada 🇮🇳": [
        "kn-IN-SapnaNeural", "kn-IN-GaneshNeural"
    ],
    "Malayalam 🇮🇳": [
        "ml-IN-SobhanaNeural", "ml-IN-MidhunNeural"
    ],
    "Gujarati 🇮🇳": [
        "gu-IN-DhwaniNeural", "gu-IN-AvinashNeural"
    ],
    "Marathi 🇮🇳": [
        "mr-IN-AarohiNeural", "mr-IN-ManoharNeural"
    ],
    "Urdu 🇵🇰": [
        "ur-PK-AsmaNeural", "ur-PK-FaizanNeural"
    ],
    "Nepali 🇳🇵": [
        "ne-NP-SaritaNeural", "ne-NP-AbhisekhNeural"
    ],
    "Sinhala 🇱🇰": [
        "si-LK-SameeraNeural", "si-LK-ThiliniNeural"
    ],
    "Khmer 🇰🇭": [
        "km-KH-SreymomNeural", "km-KH-PannNeural"
    ],
    "Lao 🇱🇦": [
        "lo-LA-ChanthavongNeural", "lo-LA-KeomanyNeural"
    ],
    "Myanmar 🇲🇲": [
        "my-MM-NilarNeural", "my-MM-ThihaNeural"
    ],
    "Georgian 🇬🇪": [
        "ka-GE-EkaNeural", "ka-GE-GiorgiNeural"
    ],
    "Armenian 🇦🇲": [
        "hy-AM-AnahitNeural", "hy-AM-AraratNeural"
    ],
    "Azerbaijani 🇦🇿": [
        "az-AZ-BabekNeural", "az-AZ-BanuNeural"
    ],
    "Kazakh 🇰🇿": [
        "kk-KZ-AigulNeural", "kk-KZ-NurzhanNeural"
    ],
    "Uzbek 🇺🇿": [
        "uz-UZ-MadinaNeural", "uz-UZ-SuhrobNeural"
    ],
    "Serbian 🇷🇸": [
        "sr-RS-NikolaNeural", "sr-RS-SophieNeural"
    ],
    "Croatian 🇭🇷": [
        "hr-HR-GabrijelaNeural", "hr-HR-SreckoNeural"
    ],
    "Slovenian 🇸🇮": [
        "sl-SI-PetraNeural", "sl-SI-RokNeural"
    ],
    "Latvian 🇱🇻": [
        "lv-LV-EveritaNeural", "lv-LV-AnsisNeural"
    ],
    "Lithuanian 🇱🇹": [
        "lt-LT-OnaNeural", "lt-LT-LeonasNeural"
    ],
    "Estonian 🇪🇪": [
        "et-EE-LiisNeural", "et-EE-ErkiNeural"
    ],
    "Amharic 🇪🇹": [
        "am-ET-MekdesNeural", "am-ET-AbebeNeural"
    ],
    "Swahili 🇰🇪": [
        "sw-KE-ZuriNeural", "sw-KE-RafikiNeural"
    ],
    "Zulu 🇿🇦": [
        "zu-ZA-ThandoNeural", "zu-ZA-ThembaNeural"
    ],
    "Xhosa 🇿🇦": [
        "xh-ZA-NomusaNeural", "xh-ZA-DumisaNeural"
    ],
    "Afrikaans 🇿🇦": [
        "af-ZA-AdriNeural", "af-ZA-WillemNeural"
    ],
    "Somali 🇸🇴": [
        "so-SO-UbaxNeural", "so-SO-MuuseNeural"
    ],
}

def make_tts_language_keyboard():
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    for lang_name in TTS_VOICES_BY_LANGUAGE.keys():
        buttons.append(
            InlineKeyboardButton(lang_name, callback_data=f"tts_lang|{lang_name}")
        )
    for i in range(0, len(buttons), 3):
        markup.add(*buttons[i:i+3])
    return markup

def make_tts_voice_keyboard_for_language(lang_name: str):
    markup = InlineKeyboardMarkup(row_width=2)
    voices = TTS_VOICES_BY_LANGUAGE.get(lang_name, [])
    for voice in voices:
        markup.add(InlineKeyboardButton(voice, callback_data=f"tts_voice|{voice}"))
    markup.add(InlineKeyboardButton("⬅️ Back to Languages", callback_data="tts_back_to_languages"))
    return markup

@bot.message_handler(commands=['text_to_speech'])
def cmd_text_to_speech(message):
    user_id = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    user_transcription_count = local_user_data.get(user_id, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    # On /text_to_speech, set TTS mode but no voice yet
    user_mode[user_id] = "tts"
    bot.send_message(message.chat.id, "🎙️ Choose a language for text-to-speech:", reply_markup=make_tts_language_keyboard())

@bot.callback_query_handler(lambda c: c.data.startswith("tts_lang|"))
def on_tts_language_select(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    user_transcription_count = local_user_data.get(uid, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    _, lang_name = call.data.split("|", 1)
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"🎙️ Choose a voice for {lang_name}:",
        reply_markup=make_tts_voice_keyboard_for_language(lang_name)
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(lambda c: c.data.startswith("tts_voice|"))
def on_tts_voice_change(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    user_transcription_count = local_user_data.get(uid, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    _, voice = call.data.split("|", 1)
    set_tts_user_voice_db(uid, voice) # This now updates in-memory and DB

    # Store chosen voice in user_mode to indicate readiness
    user_mode[uid] = {"type": "tts", "voice": voice}

    bot.answer_callback_query(call.id, f"✔️ Voice changed to {voice}")
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"🔊 Now using: *{voice}*. You can start sending text messages to convert them to speech.",
        parse_mode="Markdown"
    )

@bot.callback_query_handler(lambda c: c.data == "tts_back_to_languages")
def on_tts_back_to_languages(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    user_transcription_count = local_user_data.get(uid, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    # Going back: reset user_mode (voice no longer selected)
    user_mode[uid] = "tts" # Still in TTS flow, but need to re-select language/voice

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="🎙️ Choose a language for text-to-speech:",
        reply_markup=make_tts_language_keyboard()
    )
    bot.answer_callback_query(call.id)

async def synth_and_send_tts(chat_id: int, user_id: str, text: str):
    """
    Use MSSpeech to synthesize text -> mp3, send and delete file.
    """
    voice = get_tts_user_voice_db(user_id) # This gets from cache
    filename = os.path.join(DOWNLOAD_DIR, f"tts_{user_id}_{uuid.uuid4()}.mp3")

    stop_recording = threading.Event()
    recording_thread = threading.Thread(target=keep_recording, args=(chat_id, stop_recording))
    recording_thread.daemon = True
    recording_thread.start()

    try:
        mss = MSSpeech()
        await mss.set_voice(voice)
        await mss.set_rate(0)
        await mss.set_pitch(0)
        await mss.set_volume(1.0)

        await mss.synthesize(text, filename)

        if not os.path.exists(filename) or os.path.getsize(filename) == 0:
            bot.send_message(chat_id, "❌ MP3 file not generated or empty. Please try again.")
            return

        with open(filename, "rb") as f:
            bot.send_audio(chat_id, f, caption=f"🎤 Voice: {voice}")
    except MSSpeechError as e:
        logging.error(f"TTS error: {e}")
        bot.send_message(chat_id, f"❌ An error occurred with the voice synthesis: {e}")
    except Exception as e:
        logging.exception("TTS error")
        bot.send_message(chat_id, "❌ An unexpected error occurred during text-to-speech conversion. Please try again.")
    finally:
        stop_recording.set()
        if os.path.exists(filename):
            try:
                os.remove(filename)
            except Exception as e:
                logging.error(f"Error deleting TTS file {filename}: {e}")

@bot.message_handler(commands=['language'])
def select_language_command(message):
    uid = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    user_transcription_count = local_user_data.get(uid, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    # Ensure user mode is OFF on /language
    user_mode[uid] = None

    markup = generate_language_keyboard("set_lang")
    bot.send_message(
        message.chat.id,
        "Please select your preferred language for future **translations and summaries**:",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_lang|"))
def callback_set_language(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    user_transcription_count = local_user_data.get(uid, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    # Ensure user mode is OFF when setting language
    user_mode[uid] = None

    _, lang = call.data.split("|", 1)
    set_user_language_setting_db(uid, lang) # This now updates in-memory and DB
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ Your preferred language for translations and summaries has been set to: **{lang}**",
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id, text=f"Language set to {lang}")

@bot.message_handler(commands=['media_language'])
def select_media_language_command(message):
    uid = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    user_transcription_count = local_user_data.get(uid, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    # Set user mode to STT when /media_language is used
    user_mode[uid] = "stt"

    markup = generate_language_keyboard("set_media_lang")
    bot.send_message(
        message.chat.id,
        "Please choose the language of the audio files that you need me to transcribe. This helps ensure accurate reading.",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_media_lang|"))
def callback_set_media_language(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    user_transcription_count = local_user_data.get(uid, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    # Ensure user mode is set to STT when setting media language
    user_mode[uid] = "stt"

    _, lang = call.data.split("|", 1)
    set_user_media_language_setting_db(uid, lang) # This now updates in-memory and DB

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"✅ The transcription language for your media is set to: **{lang}**\n\n"
             "Now, please send your voice message, audio file, video note, or video file for me to transcribe. I support media files up to 20MB in size.",
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id, text=f"Media language set to {lang}")

@bot.callback_query_handler(func=lambda c: c.data.startswith("btn_translate|"))
def button_translate_handler(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    user_transcription_count = local_user_data.get(uid, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    # Ensure user mode is OFF when using translate button
    user_mode[uid] = None

    _, message_id_str = call.data.split("|", 1)
    message_id = int(message_id_str)

    if uid not in user_transcriptions or message_id not in user_transcriptions[uid]:
        bot.answer_callback_query(call.id, "❌ No transcription found for this message.")
        return

    preferred_lang = get_user_language_setting_db(uid) # Gets from cache
    if preferred_lang:
        bot.answer_callback_query(call.id, "Translating with your preferred language...")
        threading.Thread(target=do_translate_with_saved_lang, args=(call.message, uid, preferred_lang, message_id)).start()
    else:
        markup = generate_language_keyboard("translate_to", message_id)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Please select the language you want to translate into:",
            reply_markup=markup
        )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("btn_summarize|"))
def button_summarize_handler(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    user_transcription_count = local_user_data.get(uid, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    # Ensure user mode is OFF when using summarize button
    user_mode[uid] = None

    _, message_id_str = call.data.split("|", 1)
    message_id = int(message_id_str)

    if uid not in user_transcriptions or message_id not in user_transcriptions[uid]:
        bot.answer_callback_query(call.id, "❌ No transcription found for this message.")
        return

    preferred_lang = get_user_language_setting_db(uid) # Gets from cache
    if preferred_lang:
        bot.answer_callback_query(call.id, "Summarizing with your preferred language...")
        threading.Thread(target=do_summarize_with_saved_lang, args=(call.message, uid, preferred_lang, message_id)).start()
    else:
        markup = generate_language_keyboard("summarize_in", message_id)
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Please select the language you want the summary in:",
            reply_markup=markup
        )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("translate_to|"))
def callback_translate_to(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    user_transcription_count = local_user_data.get(uid, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    # Ensure user mode is OFF when using translate_to callback
    user_mode[uid] = None

    parts = call.data.split("|")
    lang = parts[1]
    message_id = int(parts[2]) if len(parts) > 2 else None

    set_user_language_setting_db(uid, lang) # This now updates in-memory and DB

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"Translating to **{lang}**...",
        parse_mode="Markdown"
    )

    if message_id:
        threading.Thread(target=do_translate_with_saved_lang, args=(call.message, uid, lang, message_id)).start()
    else:
        if uid in user_transcriptions and call.message.reply_to_message and call.message.reply_to_message.message_id in user_transcriptions[uid]:
            threading.Thread(target=do_translate_with_saved_lang, args=(call.message, uid, lang, call.message.reply_to_message.message_id)).start()
        else:
            bot.send_message(call.message.chat.id, "❌ No transcription found for this message to translate. Please use the inline buttons on the transcription.")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("summarize_in|"))
def callback_summarize_in(call):
    uid = str(call.from_user.id)
    update_user_activity_db(call.from_user.id)

    user_transcription_count = local_user_data.get(uid, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(call.message.chat.id):
        send_subscription_message(call.message.chat.id)
        bot.answer_callback_query(call.id)
        return

    # Ensure user mode is OFF when using summarize_in callback
    user_mode[uid] = None

    parts = call.data.split("|")
    lang = parts[1]
    message_id = int(parts[2]) if len(parts) > 2 else None

    set_user_language_setting_db(uid, lang) # This now updates in-memory and DB

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"Summarizing in **{lang}**...",
        parse_mode="Markdown"
    )

    if message_id:
        threading.Thread(target=do_summarize_with_saved_lang, args=(call.message, uid, lang, message_id)).start()
    else:
        if uid in user_transcriptions and call.message.reply_to_message and call.message.reply_to_message.message_id in user_transcriptions[uid]:
            threading.Thread(target=do_summarize_with_saved_lang, args=(call.message, uid, lang, call.message.reply_to_message.message_id)).start()
        else:
            bot.send_message(call.message.chat.id, "❌ No transcription found for this message to summarize. Please use the inline buttons on the transcription.")
    bot.answer_callback_query(call.id)

def do_translate_with_saved_lang(message, uid, lang, message_id):
    """
    Use Gemini to translate saved transcription into lang.
    """
    original = user_transcriptions.get(uid, {}).get(message_id, "")
    if not original:
        bot.send_message(message.chat.id, "❌ No transcription available for this specific message to translate.")
        return

    prompt = (
        f"Translate the following text into {lang}. Provide only the translated text, "
        f"with no additional notes, explanations, or introductory/concluding remarks:\n\n{original}"
    )
    bot.send_chat_action(message.chat.id, 'typing')
    # ask_gemini stores history in user_memory, which is in-memory
    translated = ask_gemini(uid, prompt)

    if translated.startswith("Error:"):
        bot.send_message(message.chat.id, f"😓 Sorry, an error occurred during translation: {translated}. Please try again later.")
        return

    if len(translated) > 4000:
        fn = f"{uuid.uuid4()}_translation.txt"
        with open(fn, 'w', encoding='utf-8') as f:
            f.write(translated)
        bot.send_chat_action(message.chat.id, 'upload_document')
        with open(fn, 'rb') as doc:
            bot.send_document(message.chat.id, doc, caption=f"Translation to {lang}", reply_to_message_id=message_id)
        os.remove(fn)
    else:
        bot.send_message(message.chat.id, translated, reply_to_message_id=message_id)

def do_summarize_with_saved_lang(message, uid, lang, message_id):
    """
    Use Gemini to summarize saved transcription into lang.
    """
    original = user_transcriptions.get(uid, {}).get(message_id, "")
    if not original:
        bot.send_message(message.chat.id, "❌ No transcription available for this specific message to summarize.")
        return

    prompt = (
        f"Summarize the following text in {lang}. Provide only the summarized text, "
        f"with no additional notes, explanations, or different versions:\n\n{original}"
    )
    bot.send_chat_action(message.chat.id, 'typing')
    # ask_gemini stores history in user_memory, which is in-memory
    summary = ask_gemini(uid, prompt)

    if summary.startswith("Error:"):
        bot.send_message(chat_id=message.chat.id, text=f"😓 Sorry, an error occurred during summarization: {summary}. Please try again later.")
        return

    if len(summary) > 4000:
        fn = f"{uuid.uuid4()}_summary.txt"
        with open(fn, 'w', encoding='utf-8') as f:
            f.write(summary)
        bot.send_chat_action(message.chat.id, 'upload_document')
        with open(fn, 'rb') as doc:
            bot.send_document(message.chat.id, doc, caption=f"Summary in {lang}", reply_to_message_id=message_id)
        os.remove(fn)
    else:
        bot.send_message(message.chat.id, summary, reply_to_message_id=message_id)

@bot.message_handler(commands=['translate'])
def handle_translate(message):
    uid = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    user_transcription_count = local_user_data.get(uid, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    # Ensure user mode is OFF on /translate
    user_mode[uid] = None

    if not message.reply_to_message or uid not in user_transcriptions or message.reply_to_message.message_id not in user_transcriptions[uid]:
        return bot.send_message(message.chat.id, "❌ Please reply to a transcription message to translate it.")

    transcription_message_id = message.reply_to_message.message_id
    preferred_lang = get_user_language_setting_db(uid) # Gets from cache
    if preferred_lang:
        threading.Thread(target=do_translate_with_saved_lang, args=(message, uid, preferred_lang, transcription_message_id)).start()
    else:
        markup = generate_language_keyboard("translate_to", transcription_message_id)
        bot.send_message(
            message.chat.id,
            "Please select the language you want to translate into:",
            reply_markup=markup
        )

@bot.message_handler(commands=['summarize'])
def handle_summarize(message):
    uid = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    user_transcription_count = local_user_data.get(uid, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    # Ensure user mode is OFF on /summarize
    user_mode[uid] = None

    if not message.reply_to_message or uid not in user_transcriptions or message.reply_to_message.message_id not in user_transcriptions[uid]:
        return bot.send_message(message.chat.id, "❌ Please reply to a transcription message to summarize it.")

    transcription_message_id = message.reply_to_message.message_id
    preferred_lang = get_user_language_setting_db(uid) # Gets from cache
    if preferred_lang:
        threading.Thread(target=do_summarize_with_saved_lang, args=(message, uid, preferred_lang, transcription_message_id)).start()
    else:
        markup = generate_language_keyboard("summarize_in", transcription_message_id)
        bot.send_message(
            message.chat.id,
            "Please select the language you want the summary in:",
            reply_markup=markup
        )

@bot.message_handler(func=lambda message: message.content_type == 'text' and not message.text.startswith('/'))
def handle_text_for_tts_or_fallback(message):
    uid = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    user_transcription_count = local_user_data.get(uid, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    # If user_mode[uid] is set to "tts" (voice chosen), or user has a saved voice, do TTS
    current_user_mode = user_mode.get(uid)
    if isinstance(current_user_mode, dict) and current_user_mode.get("type") == "tts" and current_user_mode.get("voice"):
        threading.Thread(
            target=lambda: asyncio.run(synth_and_send_tts(message.chat.id, uid, message.text))
        ).start()
    elif current_user_mode == "tts": # User is in TTS mode but hasn't picked a voice yet
        bot.send_message(
            message.chat.id,
            "Please select a voice from the options above before sending text for Text-to-Speech."
        )
    else:
        # Check if user has a saved voice in DB (and it was loaded to cache)
        saved_voice = get_tts_user_voice_db(uid) # This gets from cache
        if saved_voice != "en-US-AriaNeural": # If it's not the default, reactivate TTS mode
            user_mode[uid] = {"type": "tts", "voice": saved_voice}
            threading.Thread(
                target=lambda: asyncio.run(synth_and_send_tts(message.chat.id, uid, message.text))
            ).start()
        else:
            bot.send_message(
                message.chat.id,
                "I only transcribe voice messages, audio files, video notes, or video files. "
                "To convert text to speech, please use the 'Text to Speech (TTS)' button or the `/text_to_speech` command first."
            )

@bot.message_handler(func=lambda m: True, content_types=['photo', 'sticker', 'document'])
def fallback_non_text_or_media(message):
    uid = str(message.from_user.id)
    update_user_activity_db(message.from_user.id)

    user_transcription_count = local_user_data.get(uid, {}).get('transcription_count', 0)
    if user_transcription_count >= 5 and not check_subscription(message.chat.id):
        send_subscription_message(message.chat.id)
        return

    # Ensure user mode is OFF when a non-text/non-media message is sent
    user_mode[uid] = None

    bot.send_message(
        message.chat.id,
        "Please send only voice messages, audio files, video notes, or video files for transcription, "
        "or use the 'Text to Speech (TTS)' button/`/text_to_speech` command for text to speech."
    )

# ────────────────────────────────────────────────────────────────────────────────
#   F L A S K   R O U T E S   (Webhook setup)
# ────────────────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST", "HEAD"])
def webhook():
    if request.method in ("GET", "HEAD"):
        return "OK", 200
    if request.method == "POST":
        content_type = request.headers.get("Content-Type", "")
        if content_type and content_type.startswith("application/json"):
            update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
            bot.process_new_updates([update])
            return "", 200
    return abort(403)

@app.route("/set_webhook", methods=["GET", "POST"])
def set_webhook_route():
    try:
        bot.set_webhook(url=WEBHOOK_URL)
        return f"Webhook set to {WEBHOOK_URL}", 200
    except Exception as e:
        logging.error(f"Failed to set webhook: {e}")
        return f"Failed to set webhook: {e}", 500

@app.route("/delete_webhook", methods=["GET", "POST"])
def delete_webhook_route():
    try:
        bot.delete_webhook()
        return "Webhook deleted.", 200
    except Exception as e:
        logging.error(f"Failed to delete webhook: {e}")
        return f"Failed to delete webhook: {e}", 500

def set_webhook_on_startup():
    try:
        bot.set_webhook(url=WEBHOOK_URL)
        logging.info(f"Webhook set successfully to {WEBHOOK_URL}")
    except Exception as e:
        logging.error(f"Failed to set webhook on startup: {e}")

def set_bot_info_and_startup():
    # This function will now also load data into caches
    connect_to_mongodb()
    set_webhook_on_startup()

if __name__ == "__main__":
    set_bot_info_and_startup()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
