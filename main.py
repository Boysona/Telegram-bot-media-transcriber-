import asyncio
import os
import uuid
import shutil
from faster_whisper import WhisperModel
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.enums import ChatAction
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from dotenv import load_dotenv
import aiohttp

# --- Load environment variables ---
load_dotenv()
TOKEN = os.getenv("8191487892:AAEdaDeZ2EwBLA90RrjU1nuR0nkfitpZo5o")
REQUIRED_CHANNEL = "@qolkaqarxiska2"

# --- Configuration ---
DOWNLOAD_DIR = "downloads"
WEBHOOK_HOST = "https://telegram-bot-media-transcriber-iy2x.onrender.com"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = 8080

if os.path.exists(DOWNLOAD_DIR):
    shutil.rmtree(DOWNLOAD_DIR)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

model = WhisperModel(
    model_size_or_path="tiny",
    device="cpu",
    compute_type="int8"
)

dp = Dispatcher()
bot = Bot(TOKEN)

# --- Subscription Check ---
async def check_subscription(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception:
        return False

async def send_subscription_message(chat_id: int):
    message = f"⚠️ You must join {REQUIRED_CHANNEL} to use this bot!\n\nJoin the channel and try again."
    keyboard = [[types.InlineKeyboardButton(text="Join Channel", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}")]]
    markup = types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    await bot.send_message(chat_id, message, reply_markup=markup)

# --- Command Handlers ---
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    if not await check_subscription(message.from_user.id):
        return await send_subscription_message(message.chat.id)

    username = f"@{message.from_user.username}" if message.from_user.username else (message.from_user.first_name or "there")
    text = f"👋 Salom {username}\n•Send me any of these types of files:\n" \
           "• Voice message 🎤\n• Video message 🎥\n• Audio file 🎵\n• Video file 📹\n\n" \
           "I will convert them to text!"
    await message.answer(text)

# --- Download Helper ---
async def download_file(file_id: str, destination: str):
    file = await bot.get_file(file_id)
    url = f"https://api.telegram.org/file/bot{TOKEN}/{file.file_path}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            with open(destination, 'wb') as f:
                f.write(await resp.read())

# --- Transcription ---
async def transcribe_audio(file_path: str) -> str | None:
    try:
        segments, _ = await asyncio.to_thread(model.transcribe, file_path, beam_size=1)
        return " ".join(segment.text for segment in segments)
    except Exception:
        return None

# --- Audio/Video Handler ---
@dp.message(F.voice | F.video_note | F.audio | F.video)
async def handle_audio_message(message: types.Message):
    if not await check_subscription(message.from_user.id):
        return await send_subscription_message(message.chat.id)

    file_id = None
    file_size = 0

    if message.voice:
        file_id = message.voice.file_id
        file_size = message.voice.file_size
    elif message.video_note:
        file_id = message.video_note.file_id
        file_size = message.video_note.file_size
    elif message.video:
        file_id = message.video.file_id
        file_size = message.video.file_size
    elif message.audio:
        file_id = message.audio.file_id
        file_size = message.audio.file_size

    if file_size > 20 * 1024 * 1024:
        return await message.reply("⚠️ Sorry, the file is too large. Please send a file smaller than 20MB or use @Video_to_audio_robot to convert it.")

    unique_id = str(uuid.uuid4())
    file_path = os.path.join(DOWNLOAD_DIR, f"{unique_id}.ogg")

    status_msg = await message.answer("⏳ Downloading file, please wait...")
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    try:
        await download_file(file_id, file_path)
        await status_msg.edit_text("🔄 Processing audio, this may take a while...")

        transcription = await transcribe_audio(file_path)
        await status_msg.delete()

        if transcription:
            if len(transcription) > 4000:
                with open("transcription.txt", "w", encoding="utf-8") as f:
                    f.write(transcription)
                await message.reply_document(types.FSInputFile("transcription.txt"))
                os.remove("transcription.txt")
            else:
                await message.reply(transcription)
        else:
            await message.answer("Ma awoodo inaan qoro qoraalka.")
    except Exception as e:
        await message.answer(f"Error: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

# --- Other Messages ---
@dp.message()
async def handle_other_messages(message: types.Message):
    if not await check_subscription(message.from_user.id):
        return await send_subscription_message(message.chat.id)

    await message.answer(
        "Send me only any of these types of files:\n"
        "• Voice message 🎤\n"
        "• Video message 🎥\n"
        "• Audio file 🎵\n"
        "• Video file 📹\n\n"
        "I will convert them to text!"
    )

# --- Webhook Setup ---
async def on_startup(bot: Bot):
    await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(bot: Bot):
    await bot.delete_webhook()

async def main():
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    app = web.Application()
    app.add_routes([web.post(WEBHOOK_PATH, webhook_handler)])
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEBAPP_HOST, WEBAPP_PORT)
    await site.start()
    print(f"Webhook server running at http://{WEBAPP_HOST}:{WEBAPP_PORT}{WEBHOOK_PATH}")

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
