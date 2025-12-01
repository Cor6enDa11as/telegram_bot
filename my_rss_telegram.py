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
from datetime import datetime
from urllib.parse import urlparse

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()
app = Flask(__name__)

# Конфигурация
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')
RSS_FEED_URLS = [url.strip() for url in os.getenv('RSS_FEED_URLS', '').split(',') if url.strip()]

if not all([BOT_TOKEN, CHANNEL_ID, RSS_FEED_URLS]):
    logger.error("❌ Отсутствуют необходимые переменные окружения!")
    exit(1)

# Словарь для отслеживания последних новостей
last_links = {}

def robust_parse_feed(rss_url):
    """Парсинг RSS с обходом защиты"""
    methods = [
        lambda: feedparser.parse(rss_url),
        lambda: parse_with_headers(rss_url),
        lambda: parse_with_session(rss_url),
    ]

    for i, method in enumerate(methods):
        try:
            feed = method()
            if feed and feed.entries:
                logger.info(f"✅ Успех для {rss_url}")
                return feed
        except:
            continue

    logger.error(f"❌ Не удалось получить RSS: {rss_url}")
    return None

def parse_with_headers(rss_url):
    """Парсинг с заголовками"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/rss+xml, application/xml, text/xml, */*',
    }

    response = requests.get(rss_url, timeout=20, headers=headers)
    response.raise_for_status()
    return feedparser.parse(response.content)

def parse_with_session(rss_url):
    """Парсинг с сессией"""
    session = requests.Session()
    domain = urlparse(rss_url).netloc

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }

    try:
        main_page_url = f"https://{domain}"
        session.get(main_page_url, timeout=10, headers=headers)
    except:
        pass

    response = session.get(rss_url, timeout=15, headers=headers)
    response.raise_for_status()
    return feedparser.parse(response.content)

def format_message(entry, rss_url):
    """Форматирует сообщение: просто ссылка и хэштег"""
    try:
        if not entry.link:
            return None

        # Генерируем хэштег
        domain = urlparse(rss_url).netloc.replace('www.', '').split('.')[0]
        hashtag = f"#{domain}"

        # Просто ссылка и хэштег
        message = f"{entry.link}\n\n{hashtag}"

        return message

    except Exception as e:
        return f"{entry.link}\n\n#news"

def send_to_telegram(message):
    """Отправляет сообщение в Telegram"""
    if not message:
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        'chat_id': CHANNEL_ID,
        'text': message,
        'disable_web_page_preview': False,
        'disable_notification': False
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except:
        return False

def rss_check_loop():
    """Главный цикл мониторинга"""
    global last_links

    logger.info("🚀 Запуск RSS бота")

    # Инициализация
    if not last_links:
        for url in RSS_FEED_URLS:
            try:
                feed = robust_parse_feed(url)
                if feed and feed.entries:
                    latest = feed.entries[0]
                    last_links[url] = latest.link
                    logger.info(f"✅ Лента: {urlparse(url).netloc}")
            except:
                pass

    logger.info(f"✅ Инициализировано {len(last_links)} лент")
    time.sleep(900)

    while True:
        try:
            for url in RSS_FEED_URLS:
                try:
                    if url not in last_links:
                        continue

                    feed = robust_parse_feed(url)

                    if not feed or not feed.entries:
                        continue

                    latest = feed.entries[0]
                    link = latest.link

                    if last_links[url] != link:
                        logger.info(f"🎉 Новая новость: {urlparse(url).netloc}")

                        message = format_message(latest, url)
                        if not message:
                            continue

                        if send_to_telegram(message):
                            last_links[url] = link
                            time.sleep(10)
                        else:
                            logger.error(f"❌ Ошибка отправки")

                except:
                    continue

            logger.info(f"✅ Проверка завершена")
            time.sleep(900)

        except:
            time.sleep(60)

@app.route('/')
def home():
    return 'RSS Bot is running!'

if __name__ == '__main__':
    logger.info(f"📡 Отслеживается {len(RSS_FEED_URLS)} RSS лент")

    Thread(target=rss_check_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)
