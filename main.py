import os
import uuid
import logging
import subprocess
import requests
from flask import Flask, request, abort
import telebot
import speech_recognition as sr

# ─── Configuration ────────────────────────────────────────────────────────────
TOKEN            = os.getenv("BOT_TOKEN", "8191487892:AAEdaDeZ2EwBLA90RrjU1nuR0nkfitpZo5o")
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "@qolkaqarxiska2")
ADMIN_ID         = int(os.getenv("ADMIN_ID", "6964068910"))
WEBHOOK_URL      = os.getenv("WEBHOOK_URL", "https://telegram-bot-media-transcriber-iy2x.onrender.com")

DOWNLOAD_DIR     = "downloads"
FILE_SIZE_LIMIT  = 20 * 1024 * 1024  # 20 MB

# ─── Setup ─────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
bot        = telebot.TeleBot(TOKEN)
app        = Flask(__name__)
recognizer = sr.Recognizer()

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
existing_users = set()
if os.path.exists('users.txt'):
    with open('users.txt') as f:
        existing_users.update(line.strip() for line in f)

admin_state = {}

# ─── Helpers ───────────────────────────────────────────────────────────────────
def check_subscription(user_id: int) -> bool:
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logging.warning(f"Subscription check failed: {e}")
        return False

def send_subscription_message(chat_id: int):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton(
            text="Join Channel",
            url=f"https://t.me/{REQUIRED_CHANNEL.lstrip('@')}"
        )
    )
    bot.send_message(
        chat_id,
        f"⚠️ Please join {REQUIRED_CHANNEL} to use this bot.",
        reply_markup=markup
    )

def transcribe_audio(input_path: str) -> str | None:
    wav_path = input_path + ".wav"
    try:
        # convert to WAV
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-ar", "16000", "-ac", "1", wav_path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        with sr.AudioFile(wav_path) as src:
            audio = recognizer.record(src)
        text = recognizer.recognize_google(audio, language="en-US")
        return text
    except Exception as e:
        logging.error(f"Transcription failed: {e}")
        return None
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)

# ─── Telegram Handlers ─────────────────────────────────────────────────────────
@bot.message_handler(commands=['start'])
def on_start(msg):
    if not check_subscription(msg.from_user.id):
        return send_subscription_message(msg.chat.id)

    uid = str(msg.from_user.id)
    if uid not in existing_users:
        existing_users.add(uid)
        with open('users.txt','a') as f:
            f.write(uid+"\n")

    if msg.from_user.id == ADMIN_ID:
        kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("Total Users","Send Ads (Broadcast)")
        bot.send_message(msg.chat.id, "Admin Panel", reply_markup=kb)
    else:
        bot.send_message(
            msg.chat.id,
            "👋 Send me a voice/video/audio file (≤20 MB) and I'll transcribe it."
        )

@bot.message_handler(func=lambda m: m.text=="Total Users" and m.from_user.id==ADMIN_ID)
def cmd_total(m):
    bot.send_message(m.chat.id, f"Total users: {len(existing_users)}")

@bot.message_handler(func=lambda m: m.text=="Send Ads (Broadcast)" and m.from_user.id==ADMIN_ID)
def cmd_broadcast(m):
    admin_state[m.from_user.id] = 'awaiting'
    bot.send_message(m.chat.id, "Send the broadcast message now.")

@bot.message_handler(func=lambda m: m.from_user.id==ADMIN_ID and admin_state.get(m.from_user.id)=='awaiting',
                     content_types=['text','photo','video','audio','document','voice','sticker'])
def do_broadcast(m):
    admin_state[m.from_user.id] = None
    success=fail=0
    for uid in existing_users:
        try:
            bot.copy_message(uid, m.chat.id, m.message_id)
            success+=1
        except:
            fail+=1
    bot.send_message(m.chat.id, f"Done! ✓{success} ✗{fail}")

@bot.message_handler(content_types=['voice','audio','video','video_note'])
def handle_media(m):
    if not check_subscription(m.from_user.id):
        return send_subscription_message(m.chat.id)

    size = (m.voice or m.audio or m.video or m.video_note).file_size
    if size and size>FILE_SIZE_LIMIT:
        return bot.send_message(m.chat.id, "⚠️ File too large (<20 MB).")

    fi = bot.get_file((m.voice or m.audio or m.video or m.video_note).file_id)
    ext = os.path.splitext(fi.file_path)[1]
    dst = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}{ext}")
    data = bot.download_file(fi.file_path)
    with open(dst,'wb') as f: f.write(data)

    bot.send_chat_action(m.chat.id,'typing')
    txt = transcribe_audio(dst)
    os.remove(dst)
    if txt:
        if len(txt)>2000:
            with open("out.txt","w") as f: f.write(txt)
            bot.send_document(m.chat.id, open("out.txt","rb"))
            os.remove("out.txt")
        else:
            bot.send_message(m.chat.id, txt)
    else:
        bot.send_message(m.chat.id, "❌ Could not understand audio.")

@bot.message_handler(func=lambda m: True, content_types=['text','photo','document','sticker'])
def catch_all(m):
    if not check_subscription(m.from_user.id):
        return send_subscription_message(m.chat.id)
    bot.send_message(m.chat.id, "Send me voice/video/audio and I'll transcribe it.")

# ─── Webhook & Server ─────────────────────────────────────────────────────────
@app.route('/', methods=['POST'])
def webhook():
    if request.headers.get('content-type')=='application/json':
        upd = telebot.types.Update.de_json(request.get_data(), bot)
        bot.process_new_updates([upd])
        return '',200
    else:
        abort(403)

@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    bot.delete_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    return "Webhook set",200

@app.route('/delete_webhook', methods=['GET'])
def del_webhook():
    bot.delete_webhook()
    return "Webhook deleted",200

# ─── Entrypoint ───────────────────────────────────────────────────────────────
if __name__=="__main__":
    # clean downloads
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
        os.makedirs(DOWNLOAD_DIR)
    bot.delete_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","8080")))

