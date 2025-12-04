#!/usr/bin/env python3
import os
import logging
import feedparser
import threading
import time
from flask import Flask, request
from telegram.ext import Updater

# --- Настройки ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
RSS_URL = os.getenv("RSS_URL")
CHECK_INTERVAL = 300  # 5 минут

# Храним ID уже отправленных новостей
SENT_ENTRIES = set()

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# --- Telegram Bot ---
updater = Updater(token=BOT_TOKEN, use_context=True)
bot = updater.bot

def check_rss_periodically():
    """Фоновая функция для проверки RSS."""
    while True:
        try:
            feed = feedparser.parse(RSS_URL)
            for entry in feed.entries:
                entry_id = entry.get("id", entry.link)
                if entry_id in SENT_ENTRIES:
                    continue

                # Формируем сообщение: только заголовок как ссылка
                title = entry.title
                link = entry.link

                # Отправляем сообщение с превью
                message = f'<a href="{link}">{title}</a>'
                try:
                    bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=message,
                        parse_mode="HTML",
                        disable_web_page_preview=False
                    )
                    SENT_ENTRIES.add(entry_id)
                except Exception as e:
                    logging.error(f"Ошибка при отправке: {e}")

        except Exception as e:
            logging.error(f"Ошибка при парсинге RSS: {e}")
        time.sleep(CHECK_INTERVAL)

# --- Flask ---
app = Flask(__name__)

@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook_handler():
    # Telegram будет слать сюда обновления, если настроите webhook
    # Но мы не обрабатываем команды бота, так что просто отвечаем OK
    return 'OK', 200

@app.route('/health', methods=['GET'])
def health_check():
    # Эндпоинт для проверки, что сервис жив
    return 'Healthy', 200

def start_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    # Запускаем проверку RSS в отдельном потоке
    rss_thread = threading.Thread(target=check_rss_periodically, daemon=True)
    rss_thread.start()

    # Запускаем Flask
    start_flask()
