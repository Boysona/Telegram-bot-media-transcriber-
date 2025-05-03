



# Telegram Media Transcriber Bot

A powerful Telegram bot built with Python that automatically transcribes **voice notes**, **audio files**, and **video messages** into text using the **faster-whisper** speech-to-text model.

> **Bot Username**: [@transcriber1bot](https://t.me/transcriber1bot)

---

## Features

- Convert **voice messages**, **audio files**, and **video notes** to text
- Uses [faster-whisper](https://github.com/guillaumekln/faster-whisper) for fast and accurate transcription
- Supports text output for long audio (splits or sends as document)
- Subscription check: only users who join a specific channel can use the bot
- Admin panel for:
  - Broadcasting messages
  - Viewing user and usage statistics

---

## Requirements

- Python 3.9+
- ffmpeg (for audio processing)
- A Telegram bot token from [BotFather](https://t.me/BotFather)

---

## Setup & Deployment

### 1. Clone the repository

```bash
git clone https://github.com/yourusername/transcriber-bot.git
cd transcriber-bot

2. Install dependencies

pip install -r requirements.txt

3. Set your bot token and required channel

Edit these lines in the main script:

TOKEN = "YOUR_BOT_TOKEN"
REQUIRED_CHANNEL = "@yourchannel"

4. Run locally (for testing)

python bot.py

5. Deploy on Render or any platform with webhook support

Make sure your webhook URL matches the one in the code:

https://yourdomain.com/set_webhook



⸻

File Size Limit

Maximum file size supported is 20 MB.

⸻

Admin Features
	•	/status — Get real-time usage and file processing statistics
	•	Send Broadcast — Send messages to all users
	•	User count — Check total registered users

⸻

Stats Tracked
	•	Daily active users
	•	Total transcriptions
	•	File types breakdown (voice, audio, video, documents)
	•	Language usage distribution (basic, can be extended)

⸻

License

MIT © 2025 – Developed by Boysona

⸻



