#!/usr/bin/env python3
import os
import feedparser
import requests
from flask import Flask
from threading import Thread
import time
import logging
from dotenv import load_dotenv
from datetime import datetime
from urllib.parse import urlparse

# Попытка импорта cloudscraper (опционально, но рекомендуется)
try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False
    logging.warning("cloudscraper не установлен — некоторые сайты (например, 4pda) могут не парситься")

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
    """Парсинг RSS с несколькими методами обхода защиты"""
    methods = [
        lambda: parse_with_headers(rss_url),
        lambda: parse_with_cloudscraper(rss_url) if HAS_CLOUDSCRAPER else None,
        lambda: parse_with_session(rss_url),
    ]

    for method in methods:
        if method is None:
            continue
        try:
            feed = method()
            if feed and hasattr(feed, 'entries') and feed.entries:
                logger.info(f"✅ Успешно загружено: {rss_url}")
                return feed
        except Exception as e:
            logger.debug(f"Метод парсинга не сработал для {rss_url}: {e}")
            continue

    logger.error(f"❌ Не удалось получить RSS: {rss_url}")
    return None

def build_headers(rss_url):
    domain = urlparse(rss_url).netloc
    return {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
        'Accept': 'application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8',
        'Accept-Language': 'ru-RU,ru,en-US,en;q=0.9',
        'Referer': f'https://{domain}/',
        'Connection': 'keep-alive',
    }

def parse_with_headers(rss_url):
    headers = build_headers(rss_url)
    response = requests.get(rss_url, timeout=25, headers=headers)
    response.raise_for_status()
    return feedparser.parse(response.content)

def parse_with_cloudscraper(rss_url):
    scraper = cloudscraper.create_scraper()
    headers = build_headers(rss_url)
    response = scraper.get(rss_url, timeout=25, headers=headers)
    response.raise_for_status()
    return feedparser.parse(response.content)

def parse_with_session(rss_url):
    session = requests.Session()
    domain = urlparse(rss_url).netloc
    headers = build_headers(rss_url)

    # Попытка загрузить главную страницу (для кук/сессии)
    try:
        session.get(f'https://{domain}', timeout=10, headers=headers)
    except:
        pass

    response = session.get(rss_url, timeout=20, headers=headers)
    response.raise_for_status()
    return feedparser.parse(response.content)

def format_message(entry, rss_url):
    """Форматирует сообщение: только скрытая ссылка для превью (без хэштега)"""
    try:
        if not entry.link:
            return None
        # Zero-width space внутри ссылки — Telegram сгенерирует превью, но пользователь не увидит URL
        return f'<a href="{entry.link}">&#8203;</a>'
    except Exception as e:
        logger.exception("Ошибка форматирования сообщения")
        return f'<a href="{entry.link}">&#8203;</a>'

def send_to_telegram(message):
    """Отправка сообщения в Telegram с HTML-парсингом"""
    if not message:
        return False

    # 🔥 ИСПРАВЛЕНО: убраны пробелы в URL
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        'chat_id': CHANNEL_ID,
        'text': message,
        'parse_mode': 'HTML',
        'disable_web_page_preview': False,  # Обязательно False для превью
        'disable_notification': False
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            logger.error(f"Telegram API error: {response.text}")
        return response.status_code == 200
    except Exception as e:
        logger.exception("Ошибка отправки в Telegram")
        return False

def rss_check_loop():
    """Главный цикл мониторинга RSS"""
    global last_links

    logger.info("🚀 Запуск RSS бота")

    # Инициализация: запоминаем последнюю новость из каждой ленты
    for url in RSS_FEED_URLS:
        try:
            feed = robust_parse_feed(url)
            if feed and feed.entries:
                last_links[url] = feed.entries[0].link
                logger.info(f"✅ Инициализирована лента: {urlparse(url).netloc}")
            else:
                logger.warning(f"⚠️ Пустая лента: {url}")
        except Exception as e:
            logger.exception(f"Ошибка инициализации {url}")

    logger.info(f"✅ Инициализировано {len(last_links)} лент")
    time.sleep(900)  # первая пауза

    while True:
        try:
            for url in RSS_FEED_URLS:
                try:
                    feed = robust_parse_feed(url)
                    if not feed or not feed.entries:
                        continue

                    latest = feed.entries[0]
                    link = latest.link

                    prev_link = last_links.get(url)
                    if prev_link != link:
                        logger.info(f"🎉 Новая новость: {urlparse(url).netloc}")

                        message = format_message(latest, url)
                        if message and send_to_telegram(message):
                            last_links[url] = link
                            time.sleep(5)  # не спамить
                        else:
                            logger.error(f"❌ Не удалось отправить новость из {url}")
                except Exception as e:
                    logger.exception(f"Ошибка обработки ленты {url}")

            logger.info("✅ Цикл проверки завершён")
            time.sleep(900)  # проверка каждые 15 минут

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.exception("Критическая ошибка в основном цикле")
            time.sleep(60)

@app.route('/')
def home():
    return '✅ RSS Bot is running!'

if __name__ == '__main__':
    logger.info(f"📡 Отслеживается {len(RSS_FEED_URLS)} RSS лент")

    # Запуск фонового потока
    Thread(target=rss_check_loop, daemon=True).start()

    # Render.com требует привязку к 0.0.0.0 и порту из переменной окружения
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
