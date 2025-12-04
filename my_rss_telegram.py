#!/usr/bin/env python3
import os
import json
import feedparser
import requests
import time
import logging
from datetime import datetime
from flask import Flask

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Получаем настройки из переменных окружения Render
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')

# RSS ленты
RSS_FEEDS = [
    "https://habr.com/ru/rss/hubs/linux_dev/articles/?fl=ru",
    "https://habr.com/ru/rss/hubs/popular_science/articles/?fl=ru",
    "https://4pda.to/articles/feed/",
    "https://naked-science.ru/feed/",
    "https://rozetked.me/rss.xml",
    "https://droider.ru/feed",
    "https://www.comss.ru/linux.php",
    "https://rss-bridge.org/bridge01/?action=display&bridge=YouTubeFeedExpanderBridge&channel=UCt75WMud0RMUivGBNzvBPXQ&embed=on&format=Mrss",
    "https://rss-bridge.org/bridge01/?action=display&bridge=TelegramBridge&username=%40prohitec&format=Mrss",
    "https://androidinsider.ru/feed",
    "https://www.opennet.ru/opennews/opennews_full_utf.rss",
    "https://mobile-review.com/all/news/feed/",
    "https://www.linux.org.ru/section-rss.jsp?section=1",
    "https://www.phoronix.com/rss.php",
    "https://www.gamingonlinux.com/article_rss.php",
    "https://www.gsmarena.com/rss-news-reviews.php3",
    "https://www.ixbt.com/live/rss/blog/mobile/",
    "https://www.ixbt.com/export/sec_pda.rss",
    "https://www.ixbt.com/live/rss/blog/games/",
    "https://www.ixbt.com/live/rss/blog/gadgets/",
    "https://overclockers.ru/rss/hardnews.rss",
    "https://overclockers.ru/rss/softnews.rss",
]

def load_dates():
    """Загружаем даты последних новостей"""
    try:
        with open('dates.json', 'r') as f:
            data = json.load(f)
            return {url: datetime.fromisoformat(date_str) for url, date_str in data.items()}
    except:
        return {}

def save_dates(dates_dict):
    """Сохраняем даты в файл"""
    with open('dates.json', 'w') as f:
        json.dump({k: v.isoformat() for k, v in dates_dict.items()}, f)

def is_russian_text(text):
    """Определяет, является ли текст русским"""
    if not text:
        return False
    cyrillic_count = sum(1 for char in text if '\u0400' <= char <= '\u04FF')
    total_letters = sum(1 for char in text if char.isalpha())
    if total_letters < 3:
        return False
    return (cyrillic_count / total_letters) > 0.3

def translate_text(text):
    """Переводит текст на русский язык через Google Translate"""
    try:
        if not text or not text.strip():
            return text, False

        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            'client': 'gtx',
            'sl': 'auto',
            'tl': 'ru',
            'dt': 't',
            'q': text[:490]
        }

        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            translated = response.json()[0][0][0]
            if translated and translated.strip() and translated != text:
                return translated, True

        return text, False

    except Exception as e:
        logger.warning(f"Ошибка перевода: {e}")
        return text, False

def send_to_telegram(title, link):
    """Отправляет новость в Telegram"""
    try:
        # Экранируем HTML
        clean_title = (title
                      .replace('&', '&amp;')
                      .replace('<', '&lt;')
                      .replace('>', '&gt;')
                      .replace('"', '&quot;'))

        # Формируем сообщение
        message = f'<a href="{link}">{clean_title}</a>'

        response = requests.post(
            f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
            data={
                'chat_id': CHANNEL_ID,
                'text': message,
                'parse_mode': 'HTML',
                'disable_web_page_preview': False
            },
            timeout=10
        )

        return response.status_code == 200

    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")
        return False

def check_feeds():
    """Проверяем все RSS ленты"""
    logger.info(f"🔍 Проверка новостей начата")

    # Загружаем сохраненные даты
    dates = load_dates()

    # Если первый запуск - инициализируем
    if not dates:
        logger.info("🔄 Первый запуск - инициализация")
        for feed_url in RSS_FEEDS:
            try:
                feed = feedparser.parse(feed_url)
                if feed.entries and hasattr(feed.entries[0], 'published_parsed'):
                    dates[feed_url] = datetime(*feed.entries[0].published_parsed[:6])
                    logger.info(f"  Инициализирована: {feed_url[:50]}...")
            except:
                pass
        save_dates(dates)
        logger.info("✅ Инициализация завершена")
        return 0

    sent_count = 0

    # Проверяем каждую ленту
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            if not feed.entries:
                continue

            last_date = dates.get(feed_url)

            # Собираем новые новости
            new_entries = []
            for entry in feed.entries:
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    pub_date = datetime(*entry.published_parsed[:6])

                    if not last_date or pub_date > last_date:
                        new_entries.append(entry)
                    else:
                        break

            # Отправляем новые новости
            if new_entries:
                logger.info(f"  {feed_url[:40]}...: {len(new_entries)} новых")

                # Отправляем в обратном порядке (старые → новые)
                for entry in reversed(new_entries):
                    # Определяем язык и переводим если нужно
                    title = entry.title
                    if not is_russian_text(title):
                        translated, success = translate_text(title)
                        if success:
                            title = translated

                    # Отправляем в Telegram
                    if send_to_telegram(title, entry.link):
                        sent_count += 1
                        logger.info(f"    ✅ Отправлено: {title[:50]}...")

                        # ЗАДЕРЖКА МЕЖДУ НОВОСТЯМИ
                        time.sleep(10)

            # Обновляем дату для этой ленты
            if feed.entries and hasattr(feed.entries[0], 'published_parsed'):
                dates[feed_url] = datetime(*feed.entries[0].published_parsed[:6])

        except Exception as e:
            logger.error(f"  Ошибка ленты: {str(e)[:50]}")

    # Сохраняем обновленные даты
    save_dates(dates)
    logger.info(f"📊 Проверка завершена. Отправлено: {sent_count} новостей")
    return sent_count

@app.route('/')
def home():
    return """
    <h1>RSS to Telegram Bot ✅</h1>
    <p>Бот работает. Задержка между новостями: 10 секунд.</p>
    <p><a href="/check">Проверить сейчас</a></p>
    """

@app.route('/check')
def check():
    """Эндпоинт для UptimeRobot"""
    result = check_feeds()
    return f"✅ Проверка завершена. Отправлено: {result} новостей"

@app.route('/health')
def health():
    return "OK"

@app.route('/ping')
def ping():
    return "pong"

if __name__ == '__main__':
    if not BOT_TOKEN or not CHANNEL_ID:
        logger.error("❌ Установите BOT_TOKEN и CHANNEL_ID!")
        exit(1)

    logger.info("=" * 50)
    logger.info("🚀 RSS to Telegram Bot запущен")
    logger.info(f"📰 Отслеживается лент: {len(RSS_FEEDS)}")
    logger.info("⏳ Задержка между новостями: 10 секунд")
    logger.info("=" * 50)

    # Первая проверка при запуске
    check_feeds()

    # Запускаем Flask
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
