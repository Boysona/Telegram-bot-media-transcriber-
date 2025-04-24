# Telegram Media Transcriber Bot

This is a simple Telegram bot that receives audio and video files, transcribes them into text using OpenAI's Whisper model, and sends the result back to the user. It also ensures users are subscribed to a specific Telegram channel before using the bot.

## Features

- Accepts:
  - Voice messages 🎤
  - Video notes 🎥
  - Audio files 🎵
  - Video files 📹
- Converts speech to text using `faster-whisper`
- Sends back the transcription as a message or a `.txt` file if too long
- Checks if the user has joined a required Telegram channel
- Flask-based webhook for easy deployment on platforms like Render

## Requirements

- Python 3.9+
- Telegram Bot Token
- A publicly accessible domain (for webhook)
- ffmpeg (installed in your environment)

## Installation

1. **Clone the repository**

```bash
git clone https://github.com/yourusername/telegram-transcriber-bot.git
cd telegram-transcriber-bot
