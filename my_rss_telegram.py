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

def robust_parse_feed(rss_url):
    """Многоуровневый парсинг с fallback-методами"""
    methods = [
        # Метод 1: Прямой парсинг (основной)
        lambda: feedparser.parse(rss_url),

        # Метод 2: Через requests с текстом
        lambda: parse_with_requests_text(rss_url),

        # Метод 3: Через requests с байтами
        lambda: parse_with_requests_bytes(rss_url),

        # Метод 4: С пользовательским User-Agent
        lambda: parse_with_custom_headers(rss_url),
    ]

    for i, method in enumerate(methods):
        try:
            logger.info(f"🔄 Попытка {i+1} для {rss_url}")
            feed = method()
            if feed and feed.entries:
                logger.info(f"✅ Успех методом {i+1}, записей: {len(feed.entries)}")
                return feed
        except Exception as e:
            logger.warning(f"⚠️ Метод {i+1} не сработал: {e}")
            continue

    logger.error(f"❌ Все методы парсинга не сработали для {rss_url}")
    return None

def parse_with_requests_text(rss_url):
    """Парсинг через requests с текстом"""
    response = requests.get(rss_url, timeout=15, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; RSS-Bot/1.0)'
    })
    response.raise_for_status()
    return feedparser.parse(response.text)

def parse_with_requests_bytes(rss_url):
    """Парсинг через requests с байтами"""
    response = requests.get(rss_url, timeout=15, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; RSS-Bot/1.0)'
    })
    response.raise_for_status()
    return feedparser.parse(response.content)

def parse_with_custom_headers(rss_url):
    """Парсинг с разными User-Agent"""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Googlebot/2.1 (+http://www.google.com/bot.html)',
        'Mozilla/5.0 (compatible; RSS-Bot/1.0)'
    ]

    for ua in user_agents:
        try:
            response = requests.get(rss_url, timeout=10, headers={'User-Agent': ua})
            response.raise_for_status()
            feed = feedparser.parse(response.text)
            if feed and feed.entries:
                return feed
        except:
            continue
    return None

def translate_text(text):
    """Переводит текст на русский язык и возвращает (текст, был_ли_перевод)"""
    try:
        if re.search('[а-яА-Я]', text):
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
        logger.warning(f"Ошибка перевода: {e}")
        return text, False

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
    """Форматирует сообщение: ссылка → заголовок → пробел → превью → пробел → хэштег и автор"""
    translated_title, was_translated = translate_text(entry.title)

    # НЕВИДИМАЯ ССЫЛКА в начале сообщения
    invisible_link = f"[‎]({entry.link})"  # U+200E (left-to-right mark)

    hashtag = get_hashtag(rss_url)

    if hasattr(entry, 'author') and entry.author and not is_hashtag_text(entry.author):
        meta_line = f"🏷️ {hashtag} • 👤 {entry.author}"
    else:
        meta_line = f"🏷️ {hashtag}"

    # Структура: ссылка → заголовок (если есть) → пробелы → хэштег и автор
    if was_translated:
        return f"{invisible_link}\n{translated_title}\n\n\n{meta_line}"
    else:
        return f"{invisible_link}\n\n\n{meta_line}"

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
        if response.status_code == 200:
            return True
        else:
            logger.error(f"❌ Ошибка отправки: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        return False

def parse_feed(rss_url):
    """Парсит RSS ленту с улучшенной обработкой ошибок"""
    return robust_parse_feed(rss_url)

def initialize_processed_links():
    """Инициализация при первом запуске"""
    global processed_links, first_run

    logger.info("🚀 Первый запуск - инициализация базы ссылок...")

    for rss_url in RSS_FEED_URLS:
        feed = parse_feed(rss_url)
        if feed and feed.entries:
            latest_entry = feed.entries[0]
            processed_links.add(latest_entry.link)
            logger.info(f"📝 Запомнили: {latest_entry.title}")

    first_run = False
    logger.info(f"✅ Инициализация завершена. Запомнено {len(processed_links)} ссылок")

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
            time.sleep(8)
            return 1

    return 0

def rss_check_loop():
    """Основной цикл проверки"""
    global first_run

    if first_run:
        initialize_processed_links()
        logger.info("⏰ Ожидание 15 минут до первой проверки...")
        time.sleep(900)

    while True:
        try:
            total_new = 0

            for rss_url in RSS_FEED_URLS:
                new_entries = check_feed(rss_url)
                total_new += new_entries

            if total_new > 0:
                logger.info(f"🎉 Найдено {total_new} новых записей!")
            else:
                logger.info("✅ Проверка завершена, новых записей нет")

            logger.info("⏰ Ожидание 15 минут до следующей проверки...")
            time.sleep(900)

        except Exception as e:
            logger.error(f"❌ Ошибка в основном цикле: {e}")
            time.sleep(60)

@app.route('/')
def home():
    return 'RSS Bot is running!'

@app.route('/health')
def health():
    return 'OK'

@app.route('/ping')
def ping():
    return 'pong'

if __name__ == '__main__':
    logger.info("🤖 Запуск RSS бота...")
    logger.info(f"📡 Отслеживается {len(RSS_FEED_URLS)} RSS лент")

    Thread(target=rss_check_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)
