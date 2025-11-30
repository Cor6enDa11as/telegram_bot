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
import calendar

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

# ОПТИМИЗИРОВАННО: Храним текущие ссылки И даты публикаций
current_items = {}  # Формат: {'rss_url': {'link': 'latest_link', 'published': timestamp}}
first_run = True

def parse_date(date_string):
    """Парсит дату из RSS в timestamp"""
    if not date_string:
        return None

    try:
        # Пробуем разные форматы дат
        time_tuple = feedparser._parse_date(date_string)
        if time_tuple:
            return calendar.timegm(time_tuple)
    except:
        pass

    # Fallback: текущее время
    return time.time()

def robust_parse_feed(rss_url):
    """Многоуровневый парсинг с fallback-методами"""
    methods = [
        lambda: feedparser.parse(rss_url),
        lambda: parse_with_requests_text(rss_url),
        lambda: parse_with_requests_bytes(rss_url),
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

def format_message(entry, rss_url):
    """Форматирует сообщение: невидимая ссылка → заголовок (если переведен)"""
    translated_title, was_translated = translate_text(entry.title)

    # Невидимая ссылка (U+200E - left-to-right mark)
    invisible_link = f"[‎]({entry.link})"

    # Только невидимая ссылка и заголовок для переведенных
    if was_translated:
        return f"{invisible_link}\n{translated_title}\n{invisible_link}"
    else:
        # Для непереведенных - только невидимая ссылка
        return f"{invisible_link}"

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

def is_new_entry(entry, saved_item):
    """
    Определяет, является ли запись новой.
    Сравнивает сначала по дате, потом по ссылке.
    """
    if not saved_item:
        return True

    current_published = parse_date(entry.get('published', entry.get('updated')))
    saved_published = saved_item.get('published')

    # Если есть даты публикаций, сравниваем их
    if current_published and saved_published:
        if current_published > saved_published:
            logger.info(f"📅 Новая запись по дате: {current_published} > {saved_published}")
            return True
        elif current_published < saved_published:
            logger.info(f"📅 Старая запись по дате: {current_published} < {saved_published}")
            return False
        # Если даты равны, сравниваем по ссылкам
        else:
            if entry.link != saved_item.get('link'):
                logger.info("🔗 Разные ссылки при одинаковой дате")
                return True

    # Fallback: сравниваем только по ссылкам если нет дат
    elif entry.link != saved_item.get('link'):
        logger.info("🔗 Новая запись по ссылке (даты недоступны)")
        return True

    return False

def initialize_current_items():
    """Инициализация при первом запуске - запоминаем текущие записи"""
    global current_items, first_run

    logger.info("🚀 Первый запуск - инициализация текущих записей...")

    for rss_url in RSS_FEED_URLS:
        feed = robust_parse_feed(rss_url)
        if feed and feed.entries:
            latest_entry = feed.entries[0]
            latest_published = parse_date(latest_entry.get('published', latest_entry.get('updated')))

            current_items[rss_url] = {
                'link': latest_entry.link,
                'published': latest_published
            }

            logger.info(f"📝 Запомнили для {rss_url}: {latest_entry.link} (дата: {latest_published})")

    first_run = False
    logger.info(f"✅ Инициализация завершена. Запомнено {len(current_items)} текущих записей")

def check_feed(rss_url):
    """Проверяет RSS ленту на новые записи с учетом дат и ссылок"""
    global current_items

    feed = robust_parse_feed(rss_url)
    if not feed or not feed.entries:
        return 0

    latest_entry = feed.entries[0]
    saved_item = current_items.get(rss_url)

    # Проверяем, является ли запись новой
    if is_new_entry(latest_entry, saved_item):
        logger.info(f"🆕 Новая запись в {rss_url}: {latest_entry.title}")

        if send_to_telegram(format_message(latest_entry, rss_url)):
            # Обновляем и ссылку, и дату публикации
            current_items[rss_url] = {
                'link': latest_entry.link,
                'published': parse_date(latest_entry.get('published', latest_entry.get('updated')))
            }
            logger.info(f"🔄 Обновили данные для {rss_url}")
            time.sleep(8)  # Задержка между отправками
            return 1
    else:
        logger.info(f"⏩ Нет новых записей в {rss_url}")

    return 0

def rss_check_loop():
    """Основной цикл проверки"""
    global first_run

    if first_run:
        initialize_current_items()
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

@app.route('/status')
def status():
    """Статус бота с информацией о текущих отслеживаемых записях"""
    status_info = {
        'feeds_count': len(RSS_FEED_URLS),
        'tracked_items': len(current_items),
        'current_items': current_items
    }
    return status_info

if __name__ == '__main__':
    logger.info("🤖 Запуск RSS бота...")
    logger.info(f"📡 Отслеживается {len(RSS_FEED_URLS)} RSS лент")

    Thread(target=rss_check_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)
