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

PROCESSED_LINKS_FILE = 'processed_links.txt'

def load_processed_links():
    try:
        with open(PROCESSED_LINKS_FILE, 'r') as f:
            links = set(line.strip() for line in f)
            logger.info(f"Загружено {len(links)} обработанных ссылок")
            return links
    except FileNotFoundError:
        logger.info("Файл с обработанными ссылками не найден, начинаем с чистого листа")
        return set()

def save_processed_links(links):
    links_list = list(links)
    recent_links = links_list[-100:] if len(links_list) > 100 else links_list
    with open(PROCESSED_LINKS_FILE, 'w') as f:
        for link in recent_links:
            f.write(link + '\n')
    logger.info(f"Сохранено {len(recent_links)} обработанных ссылок")

def clean_title(title):
    """Очищает заголовок от лишних символов и исправляет проблему с [Перевод]"""
    # Убираем квадратные скобки и их содержимое (например, [Перевод])
    cleaned = re.sub(r'\[.*?\]', '', title).strip()
    # Убираем лишние пробелы
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned

def translate_text(text):
    """Переводит текст на русский язык"""
    try:
        # Если текст уже содержит кириллицу - не переводим
        if re.search('[а-яА-Я]', text):
            return text

        url = "https://translate.googleapis.com/translate_a/single"
        params = {'client': 'gtx', 'sl': 'auto', 'tl': 'ru', 'dt': 't', 'q': text}
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            translated = ''.join([item[0] for item in data[0] if item[0]])
            logger.info(f"Перевод: '{text}' -> '{translated}'")
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
    # Очищаем заголовок от [Перевод] и других скобок
    clean_title_text = clean_title(entry.title)

    # Переводим заголовок если нужно
    translated_title = translate_text(clean_title_text)

    # Создаем кликабельный заголовок
    clickable_title = f"📰 [{translated_title}]({entry.link})"

    # Генерируем хэштег
    hashtag = get_hashtag(rss_url)

    # Собираем строку с автором и хэштегом
    if hasattr(entry, 'author') and entry.author:
        author_emoji = "👤"
        meta_line = f"{author_emoji} {entry.author} • 🏷️ {hashtag}"
    else:
        meta_line = f"🏷️ {hashtag}"

    # Собираем полное сообщение с пробелом между блоками
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
        logger.info(f"Отправка сообщения в Telegram...")
        response = requests.post(url, json=payload, timeout=10)
        logger.info(f"Ответ Telegram API: {response.status_code}")

        if response.status_code == 200:
            logger.info("✅ Сообщение успешно отправлено в Telegram")
            return True
        elif response.status_code == 429:
            # Обработка слишком частых запросов
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

def check_single_feed(rss_url, processed_links):
    """Проверяет одну RSS ленту на новые записи"""
    try:
        logger.info(f"🔍 Проверяем RSS ленту: {rss_url}")
        feed = feedparser.parse(rss_url)

        if not feed.entries:
            logger.info("📭 Лента пуста")
            return processed_links, 0

        # Берем только САМУЮ СВЕЖУЮ запись (первую в списке)
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
                time.sleep(10)  # Задержка 10 секунд между сообщениями
                return processed_links, 1
            else:
                logger.error("❌ Не удалось отправить сообщение в Telegram")
                return processed_links, 0
        else:
            logger.info("⏩ Нет новых записей")
            return processed_links, 0

    except Exception as e:
        logger.error(f"❌ Ошибка при проверке ленты {rss_url}: {e}")
        return processed_links, 0

def rss_check_loop():
    """Бесконечный цикл проверки RSS лент"""
    logger.info("🔄 Запуск цикла проверки RSS...")

    while True:
        try:
            processed_links = load_processed_links()
            total_new = 0

            for rss_url in RSS_FEED_URLS:
                processed_links, new_entries = check_single_feed(rss_url, processed_links)
                total_new += new_entries

            save_processed_links(processed_links)

            if total_new > 0:
                logger.info(f"🎉 Проверка завершена. Найдено {total_new} новых записей!")
            else:
                logger.info("ℹ️ Проверка завершена. Новых записей нет.")

            logger.info("⏰ Ожидание 15 минут до следующей проверки...")
            time.sleep(900)  # 15 минут

        except Exception as e:
            logger.error(f"❌ Ошибка в основном цикле: {e}")
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
