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

# Валидация конфигурации
if not all([BOT_TOKEN, CHANNEL_ID, RSS_FEED_URLS]):
    logger.error("❌ Отсутствуют необходимые переменные окружения!")
    exit(1)

# Словарь для отслеживания последних новостей
last_links = {}

def should_translate_text(text):
    """Определяет, нужно ли переводить текст"""
    if not text or not text.strip():
        return False

    # Если текст уже содержит кириллицу - проверяем процент
    if re.search('[а-яА-Я]', text):
        # Подсчитываем процент кириллицы
        total_letters = len([c for c in text if c.isalpha()])
        if total_letters == 0:
            return False

        cyrillic_count = len([c for c in text if re.match('[а-яА-Я]', c)])
        cyrillic_ratio = cyrillic_count / total_letters

        # Если букв мало, не определяем язык
        if total_letters < 3:
            return False

        # Если более 30% символов - кириллица, считаем текст русским (не переводим)
        return cyrillic_ratio <= 0.3

    # Если нет кириллицы - переводим
    return True

def translate_text(text):
    """Переводит текст на русский язык и возвращает (текст, был_ли_перевод)"""
    try:
        # Сначала проверяем, нужно ли переводить
        if not should_translate_text(text):
            return text, False

        # Если нужно переводить - делаем перевод
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

def robust_parse_feed(rss_url):
    """Улучшенный парсинг RSS с обходом защиты"""
    methods = [
        # Метод 1: Стандартный парсинг
        lambda: feedparser.parse(rss_url),

        # Метод 2: Requests с реалистичными заголовками
        lambda: parse_with_realistic_headers(rss_url),

        # Метод 3: Requests с сессией
        lambda: parse_with_session(rss_url),
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

def parse_with_realistic_headers(rss_url):
    """Парсинг с реалистичными заголовками браузера"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/rss+xml, application/xml, text/xml, */*',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
    }

    response = requests.get(rss_url, timeout=20, headers=headers)
    response.raise_for_status()
    return feedparser.parse(response.content)

def parse_with_session(rss_url):
    """Парсинг с сессией и куками"""
    session = requests.Session()

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
    }

    # Сначала получаем главную страницу для куков
    try:
        domain = urlparse(rss_url).netloc
        main_page_url = f"https://{domain}"
        session.get(main_page_url, timeout=10, headers=headers)
        logger.info(f"🍪 Получили куки с главной страницы: {domain}")
    except:
        pass

    # Затем получаем RSS
    response = session.get(rss_url, timeout=15, headers=headers)
    response.raise_for_status()
    return feedparser.parse(response.content)

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

def rss_check_loop():
    """Главный цикл мониторинга с переводом"""
    global last_links

    logger.info("🚀 Запуск главного цикла мониторинга...")

    # Первая инициализация
    if not last_links:
        logger.info("📝 Первая проверка - инициализация последних ссылок...")
        for url in RSS_FEED_URLS:
            try:
                feed = robust_parse_feed(url)
                if feed and feed.entries:
                    latest = feed.entries[0]
                    last_links[url] = latest.link
                    logger.info(f"✅ Инициализирована лента: {urlparse(url).netloc}")
            except Exception as e:
                logger.error(f"❌ Ошибка инициализации {url}: {e}")

    logger.info(f"✅ Инициализация завершена. Отслеживается {len(last_links)} лент")
    logger.info("⏰ Ожидание 15 минут до первой проверки...")
    time.sleep(900)

    while True:
        try:
            for url in RSS_FEED_URLS:
                try:
                    # Парсим RSS-ленту
                    feed = robust_parse_feed(url)

                    # Проверяем что лента загрузилась
                    if not feed or not feed.entries:
                        logger.warning(f"⚠️ Нет новостей в ленте: {url}")
                        continue

                    latest = feed.entries[0]
                    link = latest.link

                    # Если это первая проверка - сохраняем ссылку
                    if not last_links:
                        last_links[url] = link
                        continue

                    # Проверяем есть ли новая новость
                    if url in last_links:
                        if last_links[url] != link:
                            domain = urlparse(url).netloc
                            logger.info(f"🎉 Новая новость из: {domain}")
                            logger.info(f"📰 Заголовок: {latest.title}")

                            # Отправляем сообщение
                            if send_to_telegram(format_message(latest, url)):
                                # Обновляем последнюю ссылку
                                last_links[url] = link
                                logger.info(f"✅ Новость отправлена и ссылка обновлена: {link}")

                                # Задержка между отправками
                                logger.info("⏸️ Задержка 10 секунд перед следующей отправкой...")
                                time.sleep(10)
                            else:
                                logger.error(f"❌ Не удалось отправить новость: {link}")
                    else:
                        # Добавляем новую ленту в отслеживание
                        last_links[url] = link
                        logger.info(f"📝 Добавлена новая лента в отслеживание: {url}")

                except Exception as e:
                    logger.error(f"💥 Ошибка при обработке {url}: {e}")
                    continue

            # Ждем 15 минут перед следующей проверкой
            logger.info(f"✅ Проверка завершена. Жду 15 минут... ({datetime.now().strftime('%H:%M:%S')})")
            time.sleep(900)  # 900 секунд = 15 минут

        except Exception as e:
            logger.error(f"💥 Критическая ошибка в главном цикле: {e}")
            time.sleep(60)

@app.route('/')
def home():
    return 'RSS Bot is running!'

if __name__ == '__main__':
    logger.info("🤖 Запуск RSS бота...")
    logger.info(f"📡 Отслеживается {len(RSS_FEED_URLS)} RSS лент")

    Thread(target=rss_check_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)
