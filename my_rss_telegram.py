#!/usr/bin/env python3
import os
import feedparser
import requests
from flask import Flask
from threading import Thread
import time
import logging
from dotenv import load_dotenv
from urllib.parse import urlparse

# Попытка импорта cloudscraper
try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False
    logging.warning("cloudscraper не установлен — 4pda и подобные сайты могут не работать")

# Логирование
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

last_links = {}

def build_headers(rss_url):
    domain = urlparse(rss_url).netloc
    return {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
        'Accept': 'application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8',
        'Accept-Language': 'ru-RU,ru,en-US,en;q=0.9',
        'Referer': f'https://{domain}/',
        'Connection': 'keep-alive',
    }

def parse_with_requests(rss_url):
    headers = build_headers(rss_url)
    resp = requests.get(rss_url, timeout=25, headers=headers)
    resp.raise_for_status()
    return feedparser.parse(resp.content)

def parse_with_cloudscraper(rss_url):
    scraper = cloudscraper.create_scraper()
    headers = build_headers(rss_url)
    resp = scraper.get(rss_url, timeout=25, headers=headers)
    resp.raise_for_status()
    return feedparser.parse(resp.content)

def parse_with_session(rss_url):
    session = requests.Session()
    domain = urlparse(rss_url).netloc
    headers = build_headers(rss_url)
    try:
        session.get(f'https://{domain}', timeout=10, headers=headers)
    except:
        pass
    resp = session.get(rss_url, timeout=20, headers=headers)
    resp.raise_for_status()
    return feedparser.parse(resp.content)

def robust_parse_feed(rss_url):
    methods = [
        parse_with_requests,
        (parse_with_cloudscraper if HAS_CLOUDSCRAPER else None),
        parse_with_session
    ]

    for method in methods:
        if method is None:
            continue
        try:
            feed = method(rss_url)
            if feed and hasattr(feed, 'entries') and feed.entries:
                return feed
        except Exception as e:
            logger.debug(f"Метод {method.__name__} не сработал для {rss_url}: {e}")
            continue
    logger.error(f"❌ Все методы парсинга провалились для: {rss_url}")
    return None

def format_message(entry, rss_url):
    """Возвращает гарантированно непустое HTML-сообщение со скрытой ссылкой"""
    link = getattr(entry, 'link', '').strip()
    if not link:
        link = getattr(entry, 'id', '').strip()

    if not link or not link.startswith(('http://', 'https://')):
        logger.warning(f"Некорректная ссылка в RSS из {rss_url}: {link}")
        return None

    # Zero Width Joiner (U+200D) — надёжный невидимый символ для Telegram
    return f'<a href="{link}">\u200d</a>'

def send_to_telegram(message):
    if not message:
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"  # исправлено

    payload = {
        'chat_id': CHANNEL_ID,
        'text': message,
        'parse_mode': 'HTML',
        'disable_web_page_preview': False,
        'disable_notification': False
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            logger.error(f"Telegram API error: {resp.text}")
        return resp.status_code == 200
    except Exception as e:
        logger.exception("Ошибка при отправке в Telegram")
        return False

def rss_check_loop():
    global last_links
    logger.info("🚀 Запуск RSS-бота")

    # Инициализация
    for url in RSS_FEED_URLS:
        try:
            feed = robust_parse_feed(url)
            if feed and feed.entries:
                last_links[url] = feed.entries[0].link or feed.entries[0].id
                logger.info(f"✅ Инициализирована: {urlparse(url).netloc}")
            else:
                logger.warning(f"⚠️ Пустая или недоступная лента: {url}")
        except Exception as e:
            logger.exception(f"Ошибка инициализации {url}")

    logger.info(f"✅ Отслеживается {len(last_links)} лент")
    time.sleep(900)

    while True:
        for url in RSS_FEED_URLS:
            try:
                feed = robust_parse_feed(url)
                if not feed or not feed.entries:
                    continue

                latest = feed.entries[0]
                current_link = latest.link or latest.id
                if not current_link:
                    continue

                prev_link = last_links.get(url)
                if prev_link != current_link:
                    logger.info(f"🎉 Новая новость: {urlparse(url).netloc}")

                    msg = format_message(latest, url)
                    if msg and send_to_telegram(msg):
                        last_links[url] = current_link
                        time.sleep(5)
                    else:
                        logger.error(f"❌ Не удалось отправить новость из {url}")
            except Exception as e:
                logger.exception(f"Ошибка при обработке {url}")

        logger.info("✅ Цикл проверки завершён")
        time.sleep(900)

@app.route('/')
def home():
    return '✅ RSS Bot is running!'

if __name__ == '__main__':
    logger.info(f"📡 Отслеживается {len(RSS_FEED_URLS)} RSS-лент")

    Thread(target=rss_check_loop, daemon=True).start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
