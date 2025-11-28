#!/usr/bin/env python3
import os
import feedparser
import requests
from flask import Flask
from threading import Thread
import time
import logging
from dotenv import load_dotenv
import re

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()
app = Flask(__name__)

# Конфигурация
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')
RSS_FEED_URLS = [url.strip() for url in os.getenv('RSS_FEED_URLS', '').split(',') if url.strip()]

# Валидация конфигурации
if not all([BOT_TOKEN, CHANNEL_ID, RSS_FEED_URLS]):
    logger.error("❌ Отсутствуют необходимые переменные окружения!")
    exit(1)

# Состояние приложения
processed_links = set()
first_run = True

def get_hashtag(rss_url):
    """Генерирует хэштег на основе домена"""
    try:
        from urllib.parse import urlparse
        domain = urlparse(rss_url).netloc.replace('www.', '').split('.')[0]
        return f"#{domain}"
    except:
        return "#news"

def is_hashtag_text(text):
    """Проверяет, является ли текст набором хэштегов"""
    if not text:
        return False
    words = text.split()
    hashtag_words = [word for word in words if word.startswith('#')]
    return len(hashtag_words) > 0 and len(hashtag_words) / len(words) > 0.5

def format_message(entry, rss_url):
    """Форматирует сообщение"""
    # Невидимая ссылка как первая строка
    invisible_link = f"[\u200B]({entry.link})"

    hashtag = get_hashtag(rss_url)

    # Мета-информация
    if hasattr(entry, 'author') and entry.author and not is_hashtag_text(entry.author):
        meta_line = f"🏷️ {hashtag} • 👤 {entry.author}"
    else:
        meta_line = f"🏷️ {hashtag}"

    return f"{invisible_link}\n\n{meta_line}"

def send_to_telegram(message):
    """Отправляет сообщение в Telegram"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': CHANNEL_ID,
        'text': message,
        'parse_mode': 'Markdown',
        'disable_web_page_preview': False
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {e}")
        return False

def parse_feed(rss_url):
    """Парсит RSS ленту"""
    try:
        feed = feedparser.parse(rss_url)
        return feed if feed.entries else None
    except Exception as e:
        logger.error(f"❌ Ошибка парсинга {rss_url}: {e}")
        return None

def initialize_processed_links():
    """Инициализация при первом запуске"""
    global processed_links, first_run

    logger.info("🚀 Инициализация базы ссылок...")

    for rss_url in RSS_FEED_URLS:
        feed = parse_feed(rss_url)
        if feed:
            processed_links.add(feed.entries[0].link)

    first_run = False
    logger.info("✅ Инициализация завершена")

def check_feed(rss_url):
    """Проверяет RSS ленту на новые записи"""
    global processed_links

    feed = parse_feed(rss_url)
    if not feed:
        return 0

    latest_entry = feed.entries[0]

    if latest_entry.link not in processed_links:
        logger.info(f"🆕 Новая запись: {latest_entry.title}")

        if send_to_telegram(format_message(latest_entry, rss_url)):
            processed_links.add(latest_entry.link)
            time.sleep(8)  # Фиксированная задержка между сообщениями
            return 1

    return 0

def rss_check_loop():
    """Основной цикл проверки"""
    global first_run

    # Первый запуск
    if first_run:
        initialize_processed_links()
        time.sleep(900)  # Ждем 15 минут перед первой проверкой

    # Основной цикл
    while True:
        try:
            new_count = sum(check_feed(url) for url in RSS_FEED_URLS)

            if new_count:
                logger.info(f"🎉 Найдено {new_count} новых записей!")

            logger.info("⏰ Ожидание 15 минут...")
            time.sleep(900)

        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            time.sleep(60)

# Инициализация приложения
@app.route('/')
def home():
    return 'RSS Bot is running!'

@app.route('/health')
def health():
    return 'OK'

@app.route('/ping')
def ping():
    return 'pong'

# Запуск
if __name__ == '__main__':
    logger.info("🤖 Запуск RSS бота...")

    # Запускаем фоновый поток
    Thread(target=rss_check_loop, daemon=True).start()

    # Запускаем Flask
    app.run(host='0.0.0.0', port=5000)
