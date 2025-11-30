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

# Храним историю отправленных записей
sent_entries = {}  # Формат: {'rss_url': set(['link1', 'link2', ...])}
first_run = True

def parse_date(date_string):
    """Парсит дату из RSS в timestamp"""
    if not date_string:
        return None

    try:
        time_tuple = feedparser._parse_date(date_string)
        if time_tuple:
            return calendar.timegm(time_tuple)
    except:
        pass

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
    response = requests.get(rss_url, timeout=15, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; RSS-Bot/1.0)'
    })
    response.raise_for_status()
    return feedparser.parse(response.text)

def parse_with_requests_bytes(rss_url):
    response = requests.get(rss_url, timeout=15, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; RSS-Bot/1.0)'
    })
    response.raise_for_status()
    return feedparser.parse(response.content)

def parse_with_custom_headers(rss_url):
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
            logger.error(f"❌ Текст ошибки: {response.text}")
            return False
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        return False

def initialize_sent_entries():
    """Инициализация при первом запуске - запоминаем текущие записи"""
    global sent_entries, first_run

    logger.info("🚀 Первый запуск - инициализация истории записей...")

    for rss_url in RSS_FEED_URLS:
        feed = robust_parse_feed(rss_url)
        if feed and feed.entries:
            # Запоминаем все текущие записи как уже отправленные
            sent_entries[rss_url] = set()
            for entry in feed.entries[:10]:  # Запоминаем последние 10 записей
                sent_entries[rss_url].add(entry.link)
                logger.info(f"📝 Запомнили запись: {entry.link}")

    first_run = False
    logger.info(f"✅ Инициализация завершена. Запомнено записей: {sum(len(links) for links in sent_entries.values())}")

def check_feed(rss_url):
    """Проверяет RSS ленту на новые записи"""
    global sent_entries

    feed = robust_parse_feed(rss_url)
    if not feed or not feed.entries:
        return 0

    # Инициализируем множество для этой RSS если его нет
    if rss_url not in sent_entries:
        sent_entries[rss_url] = set()

    new_entries_count = 0

    # Проверяем записи в обратном порядке (от старых к новым)
    for entry in reversed(feed.entries):
        if entry.link not in sent_entries[rss_url]:
            logger.info(f"🆕 Новая запись в {rss_url}: {entry.title}")

            if send_to_telegram(format_message(entry, rss_url)):
                # Добавляем в отправленные
                sent_entries[rss_url].add(entry.link)
                new_entries_count += 1
                logger.info(f"✅ Отправлено и запомнено: {entry.link}")

                # Задержка между отправками сообщений
                logger.info("⏸️ Задержка 10 секунд перед следующим сообщением...")
                time.sleep(10)
            else:
                logger.error(f"❌ Не удалось отправить: {entry.link}")
        else:
            logger.info(f"⏩ Запись уже отправлена: {entry.link}")

    # Очищаем старые записи чтобы не накапливать слишком много
    if len(sent_entries[rss_url]) > 50:
        # Оставляем только последние 30 записей
        all_links = list(sent_entries[rss_url])
        sent_entries[rss_url] = set(all_links[-30:])
        logger.info(f"🧹 Очищены старые записи, осталось: {len(sent_entries[rss_url])}")

    return new_entries_count

def rss_check_loop():
    """Основной цикл проверки"""
    global first_run

    if first_run:
        initialize_sent_entries()
        logger.info("⏰ Ожидание 15 минут до первой проверки...")
        time.sleep(900)

    while True:
        try:
            total_new = 0

            for rss_url in RSS_FEED_URLS:
                logger.info(f"🔍 Проверяем ленту: {rss_url}")
                new_entries = check_feed(rss_url)
                total_new += new_entries

                # Задержка между проверками разных RSS лент
                if rss_url != RSS_FEED_URLS[-1]:  # Не ждем после последней ленты
                    logger.info("⏸️ Задержка 5 секунд перед следующей лентой...")
                    time.sleep(5)

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
        'tracked_entries': sum(len(links) for links in sent_entries.values()),
        'sent_entries_per_feed': {url: len(links) for url, links in sent_entries.items()}
    }
    return status_info

if __name__ == '__main__':
    logger.info("🤖 Запуск RSS бота...")
    logger.info(f"📡 Отслеживается {len(RSS_FEED_URLS)} RSS лент")

    Thread(target=rss_check_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)
