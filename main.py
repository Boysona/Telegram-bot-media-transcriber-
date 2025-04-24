import re
import uuid
import os
import logging
import shutil
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, ChatMemberUpdated
from aiogram.utils.markdown import hbold
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ContentType
from aiogram.utils.callback_answer import CallbackAnswerMiddleware
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from faster_whisper import WhisperModel
import yt_dlp

# Bot settings
TOKEN = "8191487892:AAEdaDeZ2EwBLA90RrjU1nuR0nkfitpZo5o"
WEBHOOK_HOST = "https://telegram-bot-media-transcriber-iy2x.onrender.com"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
DOWNLOAD_DIR = "downloads"
REQUIRED_CHANNEL = "@qolkaqarxiska2"
ADMIN_ID = 6964068910
FILE_SIZE_LIMIT = 50 * 1024 * 1024

bot = Bot(token=TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

model = WhisperModel(model_size_or_path="tiny", device="cpu", compute_type="int8")
existing_users = set()
admin_state = {}

if os.path.exists('users.txt'):
    with open('users.txt', 'r') as f:
        existing_users.update(line.strip() for line in f.readlines())

URL_PATTERN = re.compile(r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|vm\.tiktok\.com/|tiktok\.com/)[^\s]+)')

async def check_subscription(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except Exception as e:
        logging.error(f"Subscription check error: {e}")
        return False

async def send_subscription_message(chat_id: int):
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Join Channel", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}")]
    ])
    await bot.send_message(chat_id, f"⚠️ Please join {REQUIRED_CHANNEL} to use this bot.", reply_markup=markup)

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    if not await check_subscription(message.from_user.id):
        return await send_subscription_message(message.chat.id)

    user_id = str(message.from_user.id)
    if user_id not in existing_users:
        existing_users.add(user_id)
        with open('users.txt', 'a') as f:
            f.write(f"{user_id}\n")

    if message.from_user.id == ADMIN_ID:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Send Ads (Broadcast)", callback_data="broadcast")],
            [InlineKeyboardButton(text="Total Users", callback_data="total_users")]
        ])
        await message.answer("Admin Panel", reply_markup=markup)
    else:
        await message.answer(
            "👋 Salaan! Fadlan ii soo dir mid ka mid ah waxyaabaha hoos ku xusan:\n\n"
            "• Voice message 🎤\n• Video message 🎥\n• Audio file 🎵\n• Video file 📹\n"
            "• TikTok or YouTube Shorts URL\n\n"
            "Waxaan kuu soo celin doonaa qoraalka laga helay!"
        )

@dp.message(F.content_type.in_({ContentType.VOICE, ContentType.VIDEO_NOTE, ContentType.AUDIO, ContentType.VIDEO}))
async def handle_audio_video(message: Message):
    if not await check_subscription(message.from_user.id):
        return await send_subscription_message(message.chat.id)

    file_info = None
    file_size = None
    if message.voice:
        file_info = await bot.get_file(message.voice.file_id)
        file_size = message.voice.file_size
    elif message.video_note:
        file_info = await bot.get_file(message.video_note.file_id)
        file_size = message.video_note.file_size
    elif message.audio:
        file_info = await bot.get_file(message.audio.file_id)
        file_size = message.audio.file_size
    elif message.video:
        file_info = await bot.get_file(message.video.file_id)
        file_size = message.video.file_size

    if file_size and file_size > FILE_SIZE_LIMIT:
        return await message.answer("File size exceeds 50MB limit.")

    path = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}.ogg")
    downloaded = await bot.download_file(file_info.file_path)
    with open(path, 'wb') as f:
        f.write(downloaded.getvalue())

    await bot.send_chat_action(message.chat.id, "typing")
    transcription = await transcribe_audio(path)
    os.remove(path)

    if transcription:
        if len(transcription) > 2000:
            with open("transcription.txt", "w") as f:
                f.write(transcription)
            await bot.send_document(message.chat.id, FSInputFile("transcription.txt"))
            os.remove("transcription.txt")
        else:
            await message.reply(transcription)
    else:
        await message.reply("Qoraalka lama helin.")

@dp.message(F.text.regexp(URL_PATTERN))
async def handle_urls(message: Message):
    if not await check_subscription(message.from_user.id):
        return await send_subscription_message(message.chat.id)

    url = message.text.strip()
    out_path = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}.mp4")

    try:
        ydl_opts = {
            'format': 'best',
            'outtmpl': out_path,
            'quiet': True,
            'max_filesize': FILE_SIZE_LIMIT,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        transcription = await transcribe_audio(out_path)
        os.remove(out_path)

        if transcription:
            if len(transcription) > 2000:
                with open("transcription.txt", "w") as f:
                    f.write(transcription)
                await bot.send_document(message.chat.id, FSInputFile("transcription.txt"))
                os.remove("transcription.txt")
            else:
                await message.reply(transcription)
        else:
            await message.reply("Qoraalka lama helin.")
    except Exception as e:
        await message.reply(f"Error: {e}")
        if os.path.exists(out_path):
            os.remove(out_path)

async def transcribe_audio(file_path: str) -> str | None:
    try:
        segments, _ = model.transcribe(file_path, beam_size=1)
        return " ".join(segment.text for segment in segments)
    except Exception as e:
        logging.error(f"Transcription error: {e}")
        return None

async def on_startup(bot: Bot):
    await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(bot: Bot):
    await bot.delete_webhook()

app = web.Application()
SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
setup_application(app, dp, bot=bot)

if __name__ == "__main__":
    web.run_app(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        on_startup=[lambda app: on_startup(bot)],
        on_shutdown=[lambda app: on_shutdown(bot)]
    )
