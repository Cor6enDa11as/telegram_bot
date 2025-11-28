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

# Проверяем переменные окружения
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')
RSS_FEED_URLS = [url.strip() for url in os.getenv('RSS_FEED_URLS', '').split(',') if url.strip()]

logger.info(f"BOT_TOKEN: {'***' if BOT_TOKEN else 'MISSING'}")
logger.info(f"CHANNEL_ID: {CHANNEL_ID}")
logger.info(f"RSS_FEED_URLS: {RSS_FEED_URLS}")

if not BOT_TOKEN or not CHANNEL_ID or not RSS_FEED_URLS:
    logger.error("❌ Отсутствуют необходимые переменные окружения!")
    exit(1)

# СЛОВАРЬ для хранения обработанных ссылок
processed_links = set()
first_run = True

def clean_title(title):
    """Очищает заголовок от лишних символов и исправляет проблему с [Перевод]"""
    cleaned = re.sub(r'\[.*?\]', '', title).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned

def translate_text(text):
    """Переводит текст на русский язык"""
    try:
        if re.search('[а-яА-Я]', text):
            return text

        url = "https://translate.googleapis.com/translate_a/single"
        params = {'client': 'gtx', 'sl': 'auto', 'tl': 'ru', 'dt': 't', 'q': text}
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            translated = ''.join([item[0] for item in data[0] if item[0]])
            return translated
        return text
    except Exception as e:
        logger.warning(f"Ошибка перевода: {e}")
        return text

def get_hashtag(rss_url):
    """Генерирует хэштег на основе домена RSS ленты"""
    try:
        from urllib.parse import urlparse
        domain = urlparse(rss_url).netloc.replace('www.', '').split('.')[0]
        hashtag = f"#{domain}"
        return hashtag
    except:
        return "#news"

def format_message(entry, rss_url):
    """Форматирует сообщение с кликабельным заголовком"""
    clean_title_text = clean_title(entry.title)
    translated_title = translate_text(clean_title_text)
    clickable_title = f"📰 [{translated_title}]({entry.link})"
    hashtag = get_hashtag(rss_url)

    if hasattr(entry, 'author') and entry.author:
        author_emoji = "👤"
        meta_line = f"{author_emoji} {entry.author} • 🏷️ {hashtag}"
    else:
        meta_line = f"🏷️ {hashtag}"

    message = f"{clickable_title}\n\n{meta_line}"
    return message

def send_to_telegram(message):
    """Отправляет сообщение в Telegram канал"""
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
            logger.info("✅ Сообщение успешно отправлено в Telegram")
            return True
        elif response.status_code == 429:
            error_data = response.json()
            retry_after = error_data.get('parameters', {}).get('retry_after', 30)
            logger.warning(f"⚠️ Telegram ограничил запросы. Ждем {retry_after} секунд")
            time.sleep(retry_after + 5)
            return False
        else:
            logger.error(f"❌ Ошибка отправки в Telegram: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"❌ Исключение при отправке в Telegram: {e}")
        return False

def safe_parse_feed(rss_url, max_retries=3):
    """Безопасный парсинг RSS с повторными попытками и заголовками"""
    for attempt in range(max_retries):
        try:
            logger.info(f"📡 Попытка {attempt + 1} загрузки RSS: {rss_url}")

            # Добавляем заголовки для обхода блокировок
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'application/rss+xml, application/xml, text/xml, */*',
                'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            }

            # Используем requests для загрузки с заголовками
            response = requests.get(rss_url, headers=headers, timeout=15)

            if response.status_code == 200:
                # Парсим содержимое через feedparser
                feed = feedparser.parse(response.content)

                # Проверяем на ошибки парсинга
                if hasattr(feed, 'bozo') and feed.bozo:
                    logger.warning(f"⚠️ Проблема с RSS лентой: {feed.bozo_exception}")

                # Проверяем есть ли записи
                if not feed.entries:
                    logger.warning(f"⚠️ RSS лента загрузилась, но записей нет: {rss_url}")
                    logger.info(f"📊 Заголовки ответа: {response.headers}")

                return feed
            else:
                logger.warning(f"⚠️ HTTP ошибка {response.status_code} для {rss_url}")
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 5
                    logger.info(f"⏰ Ждем {wait_time} секунд перед повторной попыткой...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"❌ Не удалось загрузить RSS после {max_retries} попыток: {rss_url}")
                    return None

        except requests.exceptions.Timeout:
            logger.warning(f"⚠️ Таймаут при загрузке RSS (попытка {attempt + 1}): {rss_url}")
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 5
                logger.info(f"⏰ Ждем {wait_time} секунд перед повторной попыткой...")
                time.sleep(wait_time)
            else:
                logger.error(f"❌ Таймаут после {max_retries} попыток: {rss_url}")
                return None

        except Exception as e:
            logger.warning(f"⚠️ Ошибка при загрузке RSS (попытка {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 5
                logger.info(f"⏰ Ждем {wait_time} секунд перед повторной попыткой...")
                time.sleep(wait_time)
            else:
                logger.error(f"❌ Не удалось загрузить RSS после {max_retries} попыток: {rss_url}")
                return None

    return None

def initialize_processed_links():
    """При первом запуске запоминает текущие новости без отправки"""
    global processed_links, first_run
    logger.info("🚀 Первый запуск - инициализация базы ссылок...")

    for rss_url in RSS_FEED_URLS:
        try:
            feed = safe_parse_feed(rss_url)
            if feed and feed.entries:
                # Берем только самую свежую новость и запоминаем ее
                latest_entry = feed.entries[0]
                processed_links.add(latest_entry.link)
                logger.info(f"📝 Запомнили ссылку: {latest_entry.title}")
            else:
                logger.warning(f"⚠️ Не удалось загрузить ленту для инициализации: {rss_url}")
        except Exception as e:
            logger.error(f"❌ Ошибка при инициализации ленты {rss_url}: {e}")

    first_run = False
    logger.info(f"✅ Инициализация завершена. Запомнено {len(processed_links)} ссылок")

def check_single_feed(rss_url):
    """Проверяет одну RSS ленту на новые записи"""
    global processed_links
    try:
        logger.info(f"🔍 Проверяем RSS ленту: {rss_url}")

        feed = safe_parse_feed(rss_url)
        if not feed:
            logger.warning(f"📭 Не удалось загрузить ленту: {rss_url}")
            return 0

        if not feed.entries:
            logger.warning(f"📭 Лента загрузилась, но записей нет: {rss_url}")
            return 0

        # Берем только САМУЮ СВЕЖУЮ запись
        latest_entry = feed.entries[0]
        latest_link = latest_entry.link

        logger.info(f"📖 Самая свежая запись: {latest_entry.title}")

        # Сравниваем ссылку с уже отправленными
        if latest_link not in processed_links:
            logger.info(f"🆕 Новая запись!")
            message = format_message(latest_entry, rss_url)

            if send_to_telegram(message):
                processed_links.add(latest_link)
                logger.info(f"✅ Запись добавлена в обработанные")
                time.sleep(10)
                return 1
            else:
                logger.error("❌ Не удалось отправить сообщение в Telegram")
                return 0
        else:
            logger.info("⏩ Нет новых записей")
            return 0

    except Exception as e:
        logger.error(f"❌ Неожиданная ошибка при проверке ленты {rss_url}: {e}")
        return 0

def rss_check_loop():
    """Бесконечный цикл проверки RSS лент"""
    global first_run
    logger.info("🔄 Запуск цикла проверки RSS...")

    while True:
        try:
            # Если первый запуск - инициализируем базу ссылок
            if first_run:
                initialize_processed_links()
                logger.info("⏰ Ожидание 15 минут до первой проверки новых новостей...")
                time.sleep(900)  # Ждем 15 минут перед первой проверкой
                continue  # Переходим к следующей итерации

            total_new = 0

            for rss_url in RSS_FEED_URLS:
                new_entries = check_single_feed(rss_url)
                total_new += new_entries

            if total_new > 0:
                logger.info(f"🎉 Проверка завершена. Найдено {total_new} новых записей!")
            else:
                logger.info("ℹ️ Проверка завершена. Новых записей нет.")

            logger.info("⏰ Ожидание 15 минут до следующей проверки...")
            time.sleep(900)  # 15 минут

        except Exception as e:
            logger.error(f"❌ Критическая ошибка в основном цикле: {e}")
            logger.info("⏰ Ожидание 1 минуту перед повторной попыткой...")
            time.sleep(60)

# Запускаем фоновый поток
logger.info("🚀 Запуск фонового потока для проверки RSS...")
thread = Thread(target=rss_check_loop)
thread.daemon = True
thread.start()

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
    logger.info("🤖 Бот запущен и готов к работе!")
    app.run(host='0.0.0.0', port=5000)
