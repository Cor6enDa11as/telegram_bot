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
import random

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

        # Метод 4: С мобильными заголовками
        lambda: parse_with_mobile_headers(rss_url),

        # Метод 5: С рандомными User-Agent
        lambda: parse_with_random_agents(rss_url),
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

def parse_with_mobile_headers(rss_url):
    """Парсинг с мобильными заголовками"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
        'Accept': 'application/rss+xml, application/xml, text/xml, */*',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'X-Requested-With': 'XMLHttpRequest',
    }

    response = requests.get(rss_url, timeout=20, headers=headers)
    response.raise_for_status()
    return feedparser.parse(response.content)

def parse_with_random_agents(rss_url):
    """Парсинг с рандомными User-Agent"""
    user_agents = [
        # Chrome Windows
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        # Firefox Windows
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
        # Safari Mac
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
        # Edge
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
        # Opera
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OPR/106.0.0.0',
    ]

    for ua in user_agents:
        try:
            headers = {
                'User-Agent': ua,
                'Accept': 'application/rss+xml, application/xml, text/xml, */*',
                'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            }
            # Добавляем небольшую задержку между попытками
            time.sleep(0.5)

            response = requests.get(rss_url, timeout=15, headers=headers)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            if feed and feed.entries:
                return feed
        except Exception as e:
            continue

    return None

def format_message(entry, rss_url):
    """Форматирует сообщение с точкой как видимым текстом"""
    try:
        # Проверяем что есть заголовок и ссылка
        if not entry.title or not entry.link:
            logger.error(f"❌ Отсутствует заголовок или ссылка в записи")
            return None

        translated_title, was_translated = translate_text(entry.title)

        # Fallback: если перевод не удался, используем оригинальный заголовок
        if not translated_title or translated_title.strip() == "":
            translated_title = entry.title
            was_translated = False

        # Используем точку как минимальный видимый текст
        invisible_link = f'<a href="{entry.link}">.</a>'

        # Для переведенных: невидимая ссылка + заголовок
        if was_translated:
            message = f"{invisible_link}\n{translated_title}\n{invisible_link}"
        else:
            # Для непереведенных - только невидимая ссылка
            message = f"{invisible_link}"

        # Проверяем что сообщение не пустое
        if not message or message.strip() == "":
            # Fallback: простой текст с ссылкой
            message = f"{translated_title}\n{entry.link}"
            logger.warning(f"⚠️ Используем fallback формат для: {entry.title}")

        return message

    except Exception as e:
        logger.error(f"❌ Ошибка форматирования сообщения: {e}")
        # Fallback: минимальное сообщение
        return f'<a href="{entry.link}">.</a>'

def send_to_telegram(message):
    """Отправляет сообщение в Telegram с HTML разметкой"""
    # Проверяем что сообщение не пустое
    if not message or message.strip() == "":
        logger.error("❌ Попытка отправить пустое сообщение")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': CHANNEL_ID,
        'text': message,
        'parse_mode': 'HTML',
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
                else:
                    logger.warning(f"⚠️ Не удалось инициализировать ленту: {url}")
            except Exception as e:
                logger.error(f"❌ Ошибка инициализации {url}: {e}")

    successful_feeds = len(last_links)
    logger.info(f"✅ Инициализация завершена. Успешно инициализировано {successful_feeds}/{len(RSS_FEED_URLS)} лент")

    if successful_feeds == 0:
        logger.error("❌ Ни одна лента не инициализирована! Проверьте RSS ссылки и подключение.")

    logger.info("⏰ Ожидание 15 минут до первой проверки...")
    time.sleep(900)

    while True:
        try:
            successful_checks = 0
            for url in RSS_FEED_URLS:
                try:
                    # Пропускаем ленты, которые не удалось инициализировать
                    if url not in last_links:
                        continue

                    # Парсим RSS-ленту
                    feed = robust_parse_feed(url)

                    # Проверяем что лента загрузилась
                    if not feed or not feed.entries:
                        logger.warning(f"⚠️ Нет новостей в ленте: {url}")
                        continue

                    successful_checks += 1
                    latest = feed.entries[0]
                    link = latest.link

                    # Проверяем есть ли новая новость
                    if last_links[url] != link:
                        domain = urlparse(url).netloc
                        logger.info(f"🎉 Новая новость из: {domain}")
                        logger.info(f"📰 Заголовок: {latest.title}")

                        # Форматируем сообщение
                        message = format_message(latest, url)
                        if not message:
                            logger.error(f"❌ Не удалось сформировать сообщение для: {latest.title}")
                            continue

                        # Отправляем сообщение
                        if send_to_telegram(message):
                            # Обновляем последнюю ссылку
                            last_links[url] = link
                            logger.info(f"✅ Новость отправлена и ссылка обновлена")

                            # Задержка между отправками
                            logger.info("⏸️ Задержка 10 секунд перед следующей отправкой...")
                            time.sleep(10)
                        else:
                            logger.error(f"❌ Не удалось отправить новость")
                    else:
                        logger.debug(f"⏩ Нет новых новостей в {domain}")

                except Exception as e:
                    logger.error(f"💥 Ошибка при обработке {url}: {e}")
                    continue

            logger.info(f"✅ Проверка завершена. Успешно проверено {successful_checks}/{len(RSS_FEED_URLS)} лент")
            logger.info(f"⏰ Жду 15 минут до следующей проверки... ({datetime.now().strftime('%H:%M:%S')})")
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
