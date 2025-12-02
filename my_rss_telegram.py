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
from urllib.parse import urlparse

# Логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()
app = Flask(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')
RSS_FEED_URLS = [url.strip() for url in os.getenv('RSS_FEED_URLS', '').split(',') if url.strip()]

if not all([BOT_TOKEN, CHANNEL_ID, RSS_FEED_URLS]):
    logger.error("❌ Отсутствуют переменные окружения!")
    exit(1)

last_links = {}

def extract_clean_text(html):
    if not html:
        return ""
    clean = re.sub(r'<[^>]+>', ' ', html)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean[:300] + "…" if len(clean) > 300 else clean

def fetch_rss_with_browser_headers(rss_url):
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
    logger.error(f"❌ Все методы провалились: {rss_url}")
    return None

def create_telegraph_page(title, summary, source_url, image_url=None):
    """Создаёт страницу на telegra.ph и возвращает ссылку"""
    content = []
    if image_url:
        content.append({"tag": "img", "attrs": {"src": image_url}})
    content.append({"tag": "p", "children": [summary]})
    content.append({
        "tag": "p",
        "children": [
            {"tag": "em", "children": ["Источник: "]},
            {"tag": "a", "attrs": {"href": source_url}, "children": [source_url]}
        ]
    })
    payload = {
        "title": title,
        "author_name": "RSS Bot",
        "content": content,
        "return_content": False
    }
    try:
        resp = requests.post("https://api.telegra.ph/createPage", json=payload, timeout=10)
        if resp.ok:
            data = resp.json()
            if data.get("ok"):
                return data["result"]["url"]
    except Exception as e:
        logger.error(f"❌ Telegraph ошибка: {e}")
    return None

def send_via_telegraph(entry, rss_url):
    title = entry.get('title', 'Новость').strip()
    summary = extract_clean_text(entry.get('summary', entry.get('description', '')))
    source_url = entry.link

    # Картинки по домену
    cover_map = {
        'opennet': 'https://i.imgur.com/5XJmVQl.png',
        '4pda': 'https://i.imgur.com/rKzB0yP.png',
        'gsmarena': 'https://i.imgur.com/9WzFQ4a.png',
    }
    domain = urlparse(rss_url).netloc.replace('www.', '').split('.')[0].lower()
    image_url = cover_map.get(domain)

    telegraph_url = create_telegraph_page(title, summary, source_url, image_url)
    if not telegraph_url:
        return False

    hashtag = "#" + re.sub(r'[^a-zA-Z0-9а-яА-ЯёЁ]', '', domain)
    message = f"{telegraph_url}\n\n{hashtag}"

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={'chat_id': CHANNEL_ID, 'text': message, 'disable_web_page_preview': False},
            timeout=10
        )
        return resp.ok
    except Exception as e:
        logger.error(f"❌ Ошибка отправки в Telegram: {e}")
        return False

def rss_check_loop():
    global last_links

    logger.info("🚀 Запуск RSS бота (Telegraph mode)")

    # Инициализация
    for url in RSS_FEED_URLS:
        try:
            feed = robust_parse_feed(url)
            if feed and feed.entries:
                last_links[url] = feed.entries[0].link
                logger.info(f"✅ Инициализировано: {urlparse(url).netloc}")
        except Exception as e:
            logger.error(f"Ошибка инициализации {url}: {e}")

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
                        if send_via_telegraph(latest, url):
                            last_links[url] = link
                            time.sleep(5)
                        else:
                            logger.error(f"❌ Не удалось отправить новость из {url}")

                except Exception as e:
                    logger.error(f"Ошибка обработки {url}: {e}")

            logger.info("✅ Цикл завершён")
            time.sleep(900)

        except Exception as e:
            logger.error(f"Критическая ошибка: {e}")
            time.sleep(60)

@app.route('/')
def home():
    return 'RSS Bot + Telegraph is running!'

if __name__ == '__main__':
    logger.info(f"📡 Отслеживается {len(RSS_FEED_URLS)} лент")
    Thread(target=rss_check_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
