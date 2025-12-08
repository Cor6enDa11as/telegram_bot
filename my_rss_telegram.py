#!/usr/bin/env python3

import os
import json
import feedparser
import requests
import time
import logging
import threading
import random
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from flask import Flask

# ==================== ЗАГРУЗКА .env ====================
from dotenv import load_dotenv
load_dotenv()  # Просто загружаем .env файл

# ==================== НАСТРОЙКИ ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Получаем настройки из переменных окружения (теперь из .env тоже)
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')

if not BOT_TOKEN or not CHANNEL_ID:
    logger.error("❌ Установите BOT_TOKEN и CHANNEL_ID в .env файле!")
    logger.error("Пример .env файла:")
    logger.error("BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz")
    logger.error("CHANNEL_ID=@your_channel или -1001234567890")
    exit(1)

# ==================== КОНСТАНТЫ ====================
CHECK_INTERVAL = 20 * 60  # 20 минут
REQUEST_DELAY = (3, 7)

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
]

# Автоматическое определение кодировки для всех лент
RUSSIAN_ENCODINGS = ['utf-8', 'windows-1251', 'cp1251', 'koi8-r', 'iso-8859-5']

RSS_FEEDS = [
    "https://habr.com/ru/rss/hubs/linux_dev/articles/?fl=ru",
    "https://habr.com/ru/rss/hubs/popular_science/articles/?fl=ru",
    "https://4pda.to/feed/",
    "https://naked-science.ru/feed/",
    "https://rozetked.me/rss.xml",
    "https://droider.ru/feed",
    "https://www.comss.ru/linux.php",
    "https://rss-bridge.org/bridge01/?action=display&bridge=YouTubeFeedExpanderBridge&channel=UCt75WMud0RMUivGBNzvBPXQ&embed=on&format=Mrss",
    "https://androidinsider.ru/feed",
    "https://www.opennet.ru/opennews/opennews_full_utf.rss",
    "https://mobile-review.com/all/news/feed/",
    "https://www.linux.org.ru/section-rss.jsp?section=1",
    "https://www.phoronix.com/rss.php",
    "https://www.gsmarena.com/rss-news-reviews.php3",
    "https://www.ixbt.com/live/rss/blog/mobile/",
    "https://www.ixbt.com/export/sec_pda.rss",
    "https://www.ixbt.com/live/rss/blog/games/",
    "https://www.ixbt.com/live/rss/blog/gadgets/",
    "https://ololbu.ru/rss-bridge/?action=display&bridge=TelegramBridge&username=%40kde_ru_news&format=Atom",
    "https://ololbu.ru/rss-bridge/?action=display&bridge=TelegramBridge&username=%40prohitec&format=Html",
    "https://ololbu.ru/rss-bridge/?action=display&bridge=TelegramBridge&username=%40real4pda&format=Html",
    "https://ololbu.ru/rss-bridge/?action=display&bridge=TelegramBridge&username=%40droidergram&format=Html",
    "https://archlinux.org/feeds/news/"
]

last_check_time = None
is_checking = False

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def load_dates():
    try:
        with open('dates.json', 'r') as f:
            data = json.load(f)
            for url, info in data.items():
                if isinstance(info, dict) and 'last_date' in info:
                    info['last_date'] = datetime.fromisoformat(info['last_date'])
            return data
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки dates.json: {e}")
        return {}

def save_dates(dates_dict):
    try:
        data_to_save = {}
        for url, info in dates_dict.items():
            if isinstance(info, dict) and 'last_date' in info and isinstance(info['last_date'], datetime):
                data_to_save[url] = {
                    'last_date': info['last_date'].isoformat(),
                    'error_count': info.get('error_count', 0)
                }
            else:
                data_to_save[url] = info

        with open('dates.json', 'w') as f:
            json.dump(data_to_save, f, indent=2)
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения dates.json: {e}")

def get_random_user_agent():
    return random.choice(USER_AGENTS)

def get_feed_headers():
    return {
        'User-Agent': get_random_user_agent(),
        'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml',
    }

def is_russian_text(text):
    if not text:
        return False
    cyrillic_count = sum(1 for char in text if '\u0400' <= char <= '\u04FF')
    total_letters = sum(1 for char in text if char.isalpha())
    if total_letters < 3:
        return False
    return (cyrillic_count / total_letters) > 0.3

def translate_text(text):
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
        logger.warning(f"⚠️ Ошибка перевода: {e}")
        return text, False

def send_to_telegram(title, link):
    try:
        clean_title = (title
                      .replace('&', '&amp;')
                      .replace('<', '&lt;')
                      .replace('>', '&gt;')
                      .replace('"', '&quot;'))

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

        if response.status_code == 200:
            return True
        else:
            logger.error(f"🤖 Telegram API ошибка {response.status_code}: {response.text[:100]}")
            return False

    except Exception as e:
        logger.error(f"🤖 Ошибка отправки в Telegram: {e}")
        return False

def has_valid_date(entry):
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        year = entry.published_parsed[0]
        if 2000 <= year <= 2030:
            return True

    if hasattr(entry, 'updated_parsed') and entry.updated_parsed:
        year = entry.updated_parsed[0]
        if 2000 <= year <= 2030:
            return True

    for field in ['published', 'updated', 'date']:
        if field in entry and entry[field]:
            date_str = entry[field]
            if re.search(r'\d{4}', date_str):
                months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                         'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
                if any(month in date_str for month in months):
                    return True

    return False

def parse_feed_with_auto_encoding(url):
    """Парсит RSS с автоматическим определением кодировки"""
    try:
        headers = get_feed_headers()
        response = requests.get(url, headers=headers, timeout=10)

        # Автоматически определяем лучшую кодировку
        best_feed = None
        best_encoding = 'utf-8'
        max_entries = 0

        for encoding in RUSSIAN_ENCODINGS:
            try:
                content = response.content.decode(encoding, errors='ignore')
                feed = feedparser.parse(content)

                if feed.entries and len(feed.entries) > max_entries:
                    max_entries = len(feed.entries)
                    best_feed = feed
                    best_encoding = encoding
            except:
                continue

        # Если нашли подходящую кодировку
        if best_feed:
            if best_encoding != 'utf-8':
                logger.debug(f"🔄 Использована кодировка {best_encoding} для {url[:40]}...")
            return best_feed

        # Если ничего не нашли - пробуем стандартный способ
        return feedparser.parse(url, request_headers=headers)

    except Exception as e:
        logger.error(f"❌ Ошибка парсинга {url[:50]}...: {e}")
        return None

def handle_request_error(feed_url, error, dates, error_count):
    error_str = str(error).lower()

    if "429" in error_str or "too many" in error_str:
        logger.warning(f"⏳ Лимит запросов для {feed_url[:40]}..., пропускаю")
        time.sleep(30)
        return "skip"

    if any(code in error_str for code in ["500", "502", "503", "504"]):
        logger.warning(f"🔄 Ошибка сервера для {feed_url[:40]}..., пропускаю")
        return "skip"

    logger.error(f"❌ Ошибка сети для {feed_url[:40]}...: {error_str[:50]}")
    return "error"

def initialize_first_run():
    logger.info("🔄 Первый запуск - инициализация лент")
    dates = {}

    for feed_url in RSS_FEEDS:
        try:
            logger.info(f"  Инициализация: {feed_url[:50]}...")

            feed = parse_feed_with_auto_encoding(feed_url)
            if feed is None:
                continue

            if not feed.entries:
                logger.error(f"    ❌ Пустая лента, пропускаем")
                continue

            if not has_valid_date(feed.entries[0]):
                logger.error(f"    ❌ Лента без валидных дат, пропускаем")
                continue

            entry = feed.entries[0]
            title = entry.title

            if not is_russian_text(title):
                translated, success = translate_text(title)
                if success:
                    title = translated
                    logger.debug(f"    🌐 Переведено: {title[:50]}...")

            logger.info(f"    📤 Отправка: {title[:60]}...")
            if send_to_telegram(title, entry.link):
                if hasattr(entry, 'published_parsed'):
                    pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                elif hasattr(entry, 'updated_parsed'):
                    pub_date = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
                else:
                    pub_date = datetime.now(timezone.utc)

                dates[feed_url] = {
                    'last_date': pub_date,
                    'error_count': 0
                }
                save_dates(dates)
                logger.info(f"    ✅ Успешно, дата: {pub_date.strftime('%Y-%m-%d %H:%M')}")

                time.sleep(10)
            else:
                logger.error(f"    ❌ Ошибка отправки")

        except Exception as e:
            logger.error(f"    ❌ Ошибка инициализации: {str(e)[:50]}")

    logger.info(f"✅ Инициализация завершена. Успешно: {len(dates)}/{len(RSS_FEEDS)} лент")
    return dates

# ==================== ОСНОВНАЯ ЛОГИКА ====================
def check_feeds():
    global last_check_time, is_checking

    if is_checking:
        logger.info("⚠️ Проверка уже выполняется, пропускаем")
        return 0

    is_checking = True
    try:
        logger.info("=" * 60)
        logger.info("🔍 Начало проверки новостей")

        dates = load_dates()

        if not dates:
            dates = initialize_first_run()
            last_check_time = datetime.now(timezone.utc)
            return len(dates)

        sent_count = 0

        for feed_url in RSS_FEEDS:
            try:
                logger.info(f"📰 Проверка: {feed_url[:50]}...")

                if feed_url in dates:
                    last_date_info = dates[feed_url]
                    last_date = last_date_info['last_date']
                    error_count = last_date_info.get('error_count', 0)
                else:
                    last_date = None
                    error_count = 0

                feed = parse_feed_with_auto_encoding(feed_url)
                if feed is None:
                    error_count += 1
                    dates[feed_url] = {
                        'last_date': last_date if last_date else datetime.now(timezone.utc),
                        'error_count': error_count
                    }

                    if error_count >= 3:
                        del dates[feed_url]
                        logger.info(f"🗑️ Лента удалена после 3 ошибок: {feed_url[:50]}...")

                    save_dates(dates)
                    continue

                if not feed.entries:
                    logger.error(f"  ❌ Пустая лента")
                    if feed_url in dates:
                        del dates[feed_url]
                        save_dates(dates)

                    time.sleep(random.uniform(*REQUEST_DELAY))
                    continue

                if not has_valid_date(feed.entries[0]):
                    logger.error(f"  ❌ Лента без валидных дат")
                    if feed_url in dates:
                        del dates[feed_url]
                        save_dates(dates)
                        logger.info(f"  🗑️ Лента удалена из отслеживания")

                    time.sleep(random.uniform(*REQUEST_DELAY))
                    continue

                entry = feed.entries[0]
                if hasattr(entry, 'published_parsed'):
                    latest_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                elif hasattr(entry, 'updated_parsed'):
                    latest_date = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
                else:
                    latest_date = datetime.now(timezone.utc)

                if last_date is None:
                    title = entry.title

                    if not is_russian_text(title):
                        translated, success = translate_text(title)
                        if success:
                            title = translated

                    logger.info(f"  📤 Отправка (новая лента): {title[:60]}...")
                    if send_to_telegram(title, entry.link):
                        sent_count += 1
                        dates[feed_url] = {
                            'last_date': latest_date,
                            'error_count': 0
                        }
                        save_dates(dates)

                else:
                    new_entries = []
                    for entry in feed.entries:
                        if has_valid_date(entry):
                            if hasattr(entry, 'published_parsed'):
                                pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                            elif hasattr(entry, 'updated_parsed'):
                                pub_date = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
                            else:
                                continue

                            if pub_date > last_date:
                                new_entries.append((entry, pub_date))

                    if new_entries:
                        logger.info(f"  📦 Найдено новых: {len(new_entries)}")

                        new_entries.sort(key=lambda x: x[1])

                        for entry, pub_date in new_entries:
                            title = entry.title

                            if not is_russian_text(title):
                                translated, success = translate_text(title)
                                if success:
                                    title = translated

                            logger.info(f"  📤 Отправка [{pub_date.strftime('%H:%M')}]: {title[:60]}...")
                            if send_to_telegram(title, entry.link):
                                sent_count += 1
                                dates[feed_url] = {
                                    'last_date': pub_date,
                                    'error_count': 0
                                }
                                save_dates(dates)
                                time.sleep(10)

                    else:
                        logger.info(f"  ✅ Нет новых новостей (последняя: {last_date.strftime('%Y-%m-%d %H:%M')})")

                if feed_url in dates:
                    dates[feed_url]['error_count'] = 0
                    save_dates(dates)

                time.sleep(random.uniform(*REQUEST_DELAY))

            except Exception as e:
                error_result = handle_request_error(feed_url, e, dates, error_count)

                if error_result == "skip":
                    time.sleep(random.uniform(*REQUEST_DELAY))
                    continue
                elif error_result == "error":
                    error_count += 1

                    if feed_url in dates:
                        dates[feed_url]['error_count'] = error_count
                    else:
                        dates[feed_url] = {
                            'last_date': datetime.now(timezone.utc),
                            'error_count': error_count
                        }

                    if error_count >= 3:
                        del dates[feed_url]
                        logger.info(f"  🗑️ Лента удалена после 3 ошибок: {feed_url[:50]}...")

                    save_dates(dates)

                time.sleep(random.uniform(*REQUEST_DELAY))

        save_dates(dates)
        last_check_time = datetime.now(timezone.utc)
        logger.info(f"📊 Проверка завершена. Отправлено: {sent_count} новостей")
        logger.info("=" * 60)
        return sent_count

    finally:
        is_checking = False

def auto_check_scheduler():
    logger.info(f"⏰ Автоматический планировщик запущен (интервал: {CHECK_INTERVAL//60} мин)")
    check_feeds()

    while True:
        time.sleep(CHECK_INTERVAL)
        logger.info("⏰ Автопроверка по расписанию")
        check_feeds()

# ==================== WEB ИНТЕРФЕЙС ====================
@app.route('/')
def home():
    global last_check_time
    status = "🔄 Проверка выполняется" if is_checking else "✅ Готов"

    if last_check_time:
        next_check = last_check_time + timedelta(seconds=CHECK_INTERVAL)
        next_str = next_check.strftime("%H:%M")
        last_str = last_check_time.strftime("%H:%M:%S")
    else:
        next_str = "скоро"
        last_str = "никогда"

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>RSS to Telegram Bot</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }}
            h1 {{ color: #333; }}
            .status {{ padding: 10px; border-radius: 5px; margin: 10px 0; }}
            .checking {{ background: #fff3cd; border: 1px solid #ffeaa7; }}
            .ready {{ background: #d1ecf1; border: 1px solid #bee5eb; }}
            .info {{ background: #f8f9fa; padding: 15px; border-radius: 5px; }}
        </style>
    </head>
    <body>
        <h1>📰 RSS to Telegram Bot</h1>

        <div class="status {'checking' if is_checking else 'ready'}">
            <strong>Статус:</strong> {status}
        </div>

        <div class="info">
            <p>✅ Бот работает в автоматическом режиме</p>
            <p>📰 Лент отслеживается: <strong>{len(RSS_FEEDS)}</strong></p>
            <p>⏰ Проверка каждые: <strong>{CHECK_INTERVAL//60} минут</strong></p>
            <p>⏳ Задержка между лентами: <strong>{REQUEST_DELAY[0]}-{REQUEST_DELAY[1]} секунд</strong></p>
            <hr>
            <p>Последняя проверка: <strong>{last_str}</strong></p>
            <p>Следующая проверка: <strong>{next_str}</strong></p>
        </div>

        <p><small>Настройки загружаются из .env файла</small></p>
    </body>
    </html>
    """

@app.route('/health')
def health():
    return "OK"

# ==================== ЗАПУСК ====================
if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("🚀 RSS to Telegram Bot запущен")
    logger.info(f"📰 Отслеживается лент: {len(RSS_FEEDS)}")
    logger.info(f"⏰ Проверка каждые: {CHECK_INTERVAL//60} минут")
    logger.info("🔤 Автоматическое определение кодировки для всех лент")
    logger.info("=" * 60)

    scheduler_thread = threading.Thread(target=auto_check_scheduler, daemon=True)
    scheduler_thread.start()
    logger.info("✅ Планировщик запущен в фоновом режиме")

    port = int(os.getenv('PORT', 10000))
    logger.info(f"🌐 Flask сервер запускается на порту {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
