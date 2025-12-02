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
import json

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

class TelegraphPoster:
    """Класс для работы с Telegraph API"""

    def __init__(self):
        self.access_token = None
        self.author_name = "RSS Bot"
        self.author_url = "https://t.me/rss_bot"
        self.setup_telegraph()

    def setup_telegraph(self):
        """Создаем аккаунт в Telegraph если нужно"""
        try:
            # Создаем новый аккаунт
            response = requests.post(
                "https://api.telegra.ph/createAccount",
                data={
                    'short_name': 'RSS Bot',
                    'author_name': self.author_name,
                    'author_url': self.author_url
                },
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    self.access_token = data['result']['access_token']
                    logger.info("✅ Telegraph аккаунт создан")
                else:
                    logger.error("❌ Не удалось создать Telegraph аккаунт")
            else:
                logger.error(f"❌ Ошибка Telegraph API: {response.status_code}")
        except Exception as e:
            logger.error(f"❌ Ошибка настройки Telegraph: {e}")

    def create_page(self, original_title, translated_title, was_translated, source_url):
        """Создает страницу в Telegraph с переводом"""
        try:
            if not self.access_token:
                self.setup_telegraph()

            # Определяем заголовок для страницы
            if was_translated:
                page_title = translated_title
                show_original = True
            else:
                page_title = original_title
                show_original = False

            # Форматируем контент для Telegraph
            telegraph_content = []

            # Заголовок
            telegraph_content.append({
                "tag": "h3",
                "children": [page_title]
            })

            # Показываем оригинальный заголовок если был перевод
            if show_original:
                telegraph_content.append({
                    "tag": "p",
                    "attrs": {"style": "color: #666; font-style: italic;"},
                    "children": [f"Оригинал: {original_title}"]
                })

            # Кнопка читать оригинал
            telegraph_content.append({
                "tag": "p",
                "children": [
                    {
                        "tag": "a",
                        "attrs": {"href": source_url, "style": "color: #0088cc; text-decoration: none; font-weight: bold;"},
                        "children": ["📖 Читать оригинал статьи"]
                    }
                ]
            })

            telegraph_content.append({"tag": "hr"})

            # Источник
            telegraph_content.append({
                "tag": "p",
                "attrs": {"style": "color: #888; font-size: 0.9em;"},
                "children": [
                    "📰 Источник: ",
                    {
                        "tag": "a",
                        "attrs": {"href": source_url, "style": "color: #666;"},
                        "children": [urlparse(source_url).netloc]
                    }
                ]
            })

            response = requests.post(
                "https://api.telegra.ph/createPage",
                data={
                    'access_token': self.access_token,
                    'title': page_title[:256],
                    'author_name': self.author_name,
                    'author_url': self.author_url,
                    'content': json.dumps(telegraph_content),
                    'return_content': False
                },
                timeout=15
            )

            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    page_url = data['result']['url']
                    logger.info(f"✅ Telegraph страница создана: {page_url}")
                    return page_url
                else:
                    logger.error(f"❌ Telegraph ошибка: {data.get('error')}")
            else:
                logger.error(f"❌ Ошибка Telegraph API: {response.status_code}")

            return None

        except Exception as e:
            logger.error(f"❌ Ошибка создания Telegraph страницы: {e}")
            return None

# Создаем экземпляр TelegraphPoster
telegraph_poster = TelegraphPoster()

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

def create_telegraph_preview(entry, rss_url):
    """Создает Telegraph страницу и возвращает ссылку"""
    try:
        if not entry.title or not entry.link:
            return None

        # Переводим заголовок
        translated_title, was_translated = translate_text(entry.title)

        # Создаем страницу в Telegraph
        telegraph_url = telegraph_poster.create_page(
            original_title=entry.title,
            translated_title=translated_title,
            was_translated=was_translated,
            source_url=entry.link
        )

        if telegraph_url:
            # Генерируем хэштег
            domain = urlparse(rss_url).netloc.replace('www.', '').split('.')[0]
            hashtag = f"#{domain}"

            # Форматируем сообщение с ссылкой на Telegraph
            message = f"{telegraph_url}\n\n{hashtag}"
            return message

        return None

    except Exception as e:
        logger.error(f"❌ Ошибка создания превью: {e}")
        return None

def send_to_telegram(message):
    """Отправляет сообщение в Telegram"""
    if not message:
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        'chat_id': CHANNEL_ID,
        'text': message,
        'disable_web_page_preview': False,
        'disable_notification': False
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"❌ Ошибка отправки в Telegram: {e}")
        return False

def rss_check_loop():
    """Главный цикл мониторинга"""
    global last_links

    logger.info("🚀 Запуск RSS бота с Telegraph превью и переводом")

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

                        # Создаем Telegraph превью с переводом
                        message = create_telegraph_preview(latest, url)

                        if message and send_to_telegram(message):
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
    return 'RSS Bot with Telegraph is running!'

if __name__ == '__main__':
    logger.info(f"📡 Отслеживается {len(RSS_FEED_URLS)} RSS лент")
    logger.info(f"🌐 Используется Telegraph с переводом")

    Thread(target=rss_check_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)
