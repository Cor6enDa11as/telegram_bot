#!/usr/bin/env python3
import os
import logging
import feedparser
import asyncio
from telegram.ext import Application, ContextTypes

# Настройки
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

async def check_rss(context: ContextTypes.DEFAULT_TYPE):
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
                await context.bot.send_message(
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

async def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Добавляем задачу проверки RSS
    job_queue = app.job_queue
    job_queue.run_repeating(check_rss, interval=CHECK_INTERVAL, first=1)

    # Запуск webhook (для работы на Render)
    port = int(os.environ.get("PORT", 8000))
    await app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=BOT_TOKEN,
        webhook_url=f"https://{os.environ['RENDER_EXTERNAL_HOSTNAME']}/{BOT_TOKEN}"
    )

if __name__ == "__main__":
    asyncio.run(main())
