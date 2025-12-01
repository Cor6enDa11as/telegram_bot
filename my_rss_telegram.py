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
POST_CHANNEL = os.getenv('POST_CHANNEL')
RSS_FEED_URLS = [url.strip() for url in os.getenv('RSS_FEED_URLS', '').split(',') if url.strip()]

if not all([BOT_TOKEN, CHANNEL_ID, POST_CHANNEL, RSS_FEED_URLS]):
    logger.error("❌ Отсутствуют необходимые переменные окружения!")
    exit(1)

# Словарь для отслеживания последних новостей
last_links = {}

def extract_clean_text(html):
    """Очистка HTML от тегов и обрезка"""
    if not html:
        return ""
    # Удаляем HTML
    clean = re.sub(r'<[^>]+>', ' ', html)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean[:300] + "…" if len(clean) > 300 else clean

def fetch_rss_with_browser_headers(rss_url):
    """Надёжный запрос RSS с браузероподобными заголовками"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,ru;q=0.8',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0'
    }

    response = requests.get(rss_url, headers=headers, timeout=20)
    response.raise_for_status()
    return feedparser.parse(response.content)

def robust_parse_feed(rss_url):
    """Парсинг RSS с несколькими попытками"""
    methods = [
        lambda: fetch_rss_with_browser_headers(rss_url),
        lambda: feedparser.parse(rss_url),
    ]

    for method in methods:
        try:
            feed = method()
            if feed and hasattr(feed, 'entries') and len(feed.entries) > 0:
                entry = feed.entries[0]
                if entry.get('link') or entry.get('title'):
                    logger.info(f"✅ Успешно: {urlparse(rss_url).netloc}")
                    return feed
        except Exception as e:
            logger.debug(f"Метод не сработал для {rss_url}: {e}")
            continue

    logger.error(f"❌ Все методы парсинга провалились: {rss_url}")
    return None

def publish_and_forward(entry, rss_url):
    """
    1. Публикует пост в POST_CHANNEL
    2. Отправляет t.me ссылку + хэштег в CHANNEL_ID
    """
    try:
        # Генерация хэштега
        domain = urlparse(rss_url).netloc.replace('www.', '').split('.')[0].lower()
        hashtag = "#" + re.sub(r'[^a-zA-Z0-9а-яА-ЯёЁ]', '', domain)

        # Заголовок и описание
        title = entry.get('title', 'Новая новость').strip()
        summary = extract_clean_text(entry.get('summary', entry.get('description', '')))

        # Пост в промежуточном канале
        post_text = f"{title}\n\n{summary}\n\n{hashtag}"

        # Публикация в POST_CHANNEL
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                'chat_id': POST_CHANNEL,
                'text': post_text,
                'disable_web_page_preview': False,
                'parse_mode': 'HTML'
            },
            timeout=10
        )

        if not resp.ok:
            logger.error(f"Ошибка публикации в POST_CHANNEL: {resp.text}")
            return False

        msg_id = resp.json()['result']['message_id']
        channel_name = POST_CHANNEL.lstrip('@')
        tme_link = f"https://t.me/{channel_name}/{msg_id}"

        # Отправка в основной канал
        main_message = f"{tme_link}\n\n{hashtag}"
        resp2 = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                'chat_id': CHANNEL_ID,
                'text': main_message,
                'disable_web_page_preview': False
            },
            timeout=10
        )

        return resp2.ok

    except Exception as e:
        logger.error(f"Ошибка в publish_and_forward: {e}")
        return False

def rss_check_loop():
    """Главный цикл мониторинга RSS"""
    global last_links

    logger.info("🚀 Запуск RSS бота")

    # Инициализация: запоминаем последнюю запись из каждой ленты
    for url in RSS_FEED_URLS:
        try:
            feed = robust_parse_feed(url)
            if feed and feed.entries:
                last_links[url] = feed.entries[0].link
                logger.info(f"✅ Инициализировано: {urlparse(url).netloc}")
        except Exception as e:
            logger.error(f"Ошибка инициализации {url}: {e}")

    logger.info(f"✅ Отслеживается {len(last_links)} лент")
    time.sleep(60)

    while True:
        try:
            for url in RSS_FEED_URLS:
                try:
                    feed = robust_parse_feed(url)
                    if not feed or not feed.entries:
                        continue

                    latest = feed.entries[0]
                    link = latest.link

                    if last_links.get(url) != link:
                        logger.info(f"🎉 Новая новость: {urlparse(url).netloc}")
                        if publish_and_forward(latest, url):
                            last_links[url] = link
                            time.sleep(5)  # пауза между публикациями
                        else:
                            logger.error(f"❌ Ошибка отправки новости из {url}")

                except Exception as e:
                    logger.error(f"Ошибка обработки {url}: {e}")

            logger.info("✅ Цикл проверки завершён")
            time.sleep(900)  # 15 минут

        except Exception as e:
            logger.error(f"Критическая ошибка в цикле: {e}")
            time.sleep(60)

@app.route('/')
def home():
    return 'RSS Bot is running!'

if __name__ == '__main__':
    logger.info(f"📡 Отслеживается {len(RSS_FEED_URLS)} RSS лент")
    Thread(target=rss_check_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
