# Task Nag Bot

A Telegram bot that nags you about tasks using AI-powered reminders. Built with Telegram Bot API and NVIDIA Nemotron for intelligent nagging.

## Features

- Natural language task scheduling
- AI-powered nagging with personality
- Flexible scheduling (one-time, recurring, relative times)
- Telegram bot interface

## Setup

1. Clone the repo
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Set environment variables:
   ```bash
   export TELEGRAM_BOT_TOKEN=your_bot_token
   export NVIDIA_API_KEY=your_nvidia_key
   ```
4. Run the bot:
   ```bash
   python task_nag_bot.py
   ```

## Deployment (Railway)

1. Connect GitHub repo to Railway
2. Add environment variables in Railway dashboard:
   - `TELEGRAM_BOT_TOKEN`
   - `NVIDIA_API_KEY`
3. Deploy - Railway auto-detects Python from `requirements.txt`

## Usage

Start a chat with the bot and use natural language:
- "Remind me to call mom at 5pm"
- "Nag me about the report every hour until it's done"
- "Remind me to stretch every 30 minutes"