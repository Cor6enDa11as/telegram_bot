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

def should_translate_text(text):
    """Определяет, нужно ли переводить текст"""
    if not text or not text.strip():
        return False

    if re.search('[а-яА-Я]', text):
        total_letters = len([c for c in text if c.isalpha()])
        if total_letters == 0:
            return False

        cyrillic_count = len([c for c in text if re.match('[а-яА-Я]', c)])
        cyrillic_ratio = cyrillic_count / total_letters

        if total_letters < 3:
            return False

        return cyrillic_ratio <= 0.3

    return True

def translate_text(text):
    """Переводит текст на русский язык"""
    try:
        if not should_translate_text(text):
            return text, False

        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            'client': 'gtx',
            'sl': 'auto',
            'tl': 'ru',
            'dt': 't',
            'q': text
        }
        response = requests.get(url, params=params, timeout=5)
        if response.status_code == 200:
            data = response.json()
            translated = ''.join([item[0] for item in data[0] if item[0]])
            return translated, True
        return text, False
    except Exception as e:
        return text, False

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
    """Форматирует сообщение: заголовок + ссылка для переведенных, только ссылка для английских"""
    try:
        if not entry.title or not entry.link:
            logger.error(f"❌ Отсутствует заголовок или ссылка")
            return None

        translated_title, was_translated = translate_text(entry.title)

        if was_translated:
            # Для переведенных: заголовок + ссылка
            message = f"\n{translated_title}\n{entry.link}\n"
            logger.info(f"📝 Сформирован переведенный заголовок")
        else:
            # Для английских: просто ссылка
            message = f"\n{entry.link}\n"
            logger.info("📝 Сформирована только ссылка (не переведено)")

        return message

    except Exception as e:
        logger.error(f"❌ Ошибка форматирования: {e}")
        return f"\n{entry.link}\n"

def send_to_telegram(message, entry_link, rss_url, entry_title):
    """Отправляет сообщение в Telegram с кнопкой хэштега"""
    if not message:
        logger.error("❌ Пустое сообщение")
        return False

    # Генерируем хэштег
    try:
        domain = urlparse(rss_url).netloc.replace('www.', '').split('.')[0]
        hashtag = f"#{domain}"
    except:
        hashtag = "#news"

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        'chat_id': CHANNEL_ID,
        'text': message,
        'disable_web_page_preview': False,
        'disable_notification': False,
        'reply_markup': {
            'inline_keyboard': [[
                {'text': hashtag, 'url': f"https://t.me/{CHANNEL_ID.replace('@', '')}?q={hashtag}"}
            ]]
        }
    }

    try:
        logger.info(f"📤 Отправка сообщения: {message[:50]}...")
        response = requests.post(url, json=payload, timeout=10)

        if response.status_code == 200:
            logger.info("✅ Сообщение отправлено успешно")
            return True
        else:
            logger.error(f"❌ Ошибка отправки: {response.status_code}")
            logger.error(f"❌ Текст ошибки: {response.text}")

            # Пробуем без кнопки если не работает
            payload.pop('reply_markup', None)
            logger.info("🔄 Пробуем отправить без кнопки...")
            response2 = requests.post(url, json=payload, timeout=10)
            return response2.status_code == 200

    except Exception as e:
        logger.error(f"❌ Ошибка соединения: {e}")
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

                        if send_to_telegram(message, latest.link, url, latest.title):
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
