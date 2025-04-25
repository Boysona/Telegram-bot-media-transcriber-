# Telegram Media Transcriber Bot

A Telegram bot that accepts voice messages, video notes, audio/video files, and transcribes them to text using the Python `SpeechRecognition` library (via the Google Web Speech API). Includes admin-only broadcast and user-subscription gating.

---

## Features

- ✅ Accepts voice messages, video notes, audio files, and video files (up to 20 MB).  
- ✅ Converts any media format to WAV (16 kHz mono) via `ffmpeg` for reliable speech recognition.  
- ✅ Uses Python’s [SpeechRecognition](https://pypi.org/project/SpeechRecognition/) library with Google’s free web API.  
- ✅ Subscription check: only users who join a specified Telegram channel can use the bot.  
- ✅ Admin panel (via in-chat keyboard) for:  
  - Viewing total registered users  
  - Broadcasting a message (text/photo/video/audio/document/voice/sticker) to all users  
- ✅ Deployable on any Flask-capable host (Heroku, Render.com, AWS, etc.)  

---

## Prerequisites

1. **Python 3.8+**  
2. **ffmpeg** installed and on your system `PATH`  
   ```bash
   # Ubuntu / Debian
   sudo apt-get update && sudo apt-get install -y ffmpeg

   # macOS (Homebrew)
   brew install ffmpeg

	3.	A Telegram bot token. Create one via @BotFather.
	4.	A Telegram channel for gating (optional but recommended).

⸻

Setup
	1.	Clone this repository

git clone https://github.com/your-username/telegram-media-transcriber.git
cd telegram-media-transcriber


	2.	Create & activate a virtual environment

python3 -m venv venv
source venv/bin/activate


	3.	Install Python dependencies

pip install --upgrade pip
pip install -r requirements.txt


	4.	Configure the bot
Rename .env.example to .env (or set environment variables directly):

BOT_TOKEN=7920977306:AAFRR5ZIaPcD1rbmjSKxsNisQZZpPa7zWPs
REQUIRED_CHANNEL=@qolkaqarxiska2
ADMIN_ID=6964068910
WEBHOOK_URL=https://your-domain.com/

Or edit the top of bot.py (or your main script) to set:

TOKEN        = os.getenv("BOT_TOKEN", "YOUR_TOKEN_HERE")
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "@yourchannel")
ADMIN_ID     = int(os.getenv("ADMIN_ID", "123456789"))
WEBHOOK_URL  = os.getenv("WEBHOOK_URL", "https://example.com/")



⸻

Running Locally

# Ensure .env is loaded (if using dotenv), or export variables manually:
export BOT_TOKEN="…"
export REQUIRED_CHANNEL="@…"
export ADMIN_ID="…"
export WEBHOOK_URL="https://…"

# Start the Flask app
python bot.py

The bot will:
	1.	Delete any existing webhook.
	2.	Register a new webhook at WEBHOOK_URL.
	3.	Start a web server on port 8080 (or $PORT if set).

You can test locally using a tunnel (e.g. ngrok):

ngrok http 8080
# Copy the HTTPS forwarding address, then:
curl "http://localhost:8080/set_webhook?url=https://abcd1234.ngrok.io/"



⸻

Deployment
	•	Render.com: Add build and start commands in render.yaml or via the dashboard.
	•	Heroku:
	•	Procfile:

web: python bot.py


	•	Set your config vars in the Heroku dashboard.

	•	Docker: You can containerize with a simple Dockerfile:

FROM python:3.10-slim
WORKDIR /app
COPY . /app
RUN apt-get update && apt-get install -y ffmpeg && \
    pip install --no-cache-dir -r requirements.txt
ENV PORT=8080
CMD ["python", "bot.py"]



⸻

Usage
	1.	Start the bot in Telegram: /start
	2.	Join the required channel if prompted.
	3.	Send a voice message, video note, audio file, or video file (< 20 MB).
	4.	The bot will reply with the transcribed text (or a .txt file if > 2 000 characters).

Admin Commands
	•	“Total Users”: Shows how many unique users have interacted with the bot.
	•	“Send Ads (Broadcast)”: Next message you send (any media type) will be forwarded to all users.

⸻

File Structure

.
├── bot.py            # Main application
├── requirements.txt  # Python dependencies
├── README.md         # You are here
└── users.txt         # Persisted user IDs (auto-created)



⸻

Troubleshooting
	•	“ffmpeg: command not found”
→ Make sure ffmpeg is installed and on your PATH.
	•	Recognition errors
	•	Google Web API is free but rate-limited/unreliable for long audio. Consider switching to a paid API or an offline model if needed.
	•	Webhook failures
	•	Verify your HTTPS certificate.
	•	Check Flask logs for incoming POSTs from Telegram.

⸻

License

MIT License © 2025 Boysona


