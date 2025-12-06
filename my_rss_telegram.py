#!/usr/bin/env python3
import os
import json
import feedparser
import requests
import time
import logging
import threading
import random
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from flask import Flask

# ==================== НАСТРОЙКИ ====================
# Настройка логирования
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

# Получаем настройки из переменных окружения Render
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')

# ==================== КОНСТАНТЫ ====================
# Новые настройки
CHECK_INTERVAL = 20 * 60  # 20 минут между проверками (было 15)
REQUEST_DELAY = (3, 7)    # Случайная задержка 3-7 сек между лентами

# User-Agent для обхода блокировок
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
]

# Кодировки для разных сайтов
SITE_ENCODINGS = {
    '4pda.to': 'windows-1251',
    '4pda.ru': 'windows-1251',
}

# RSS ленты (оставляем твой список)
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

# Глобальные переменные
last_check_time = None
is_checking = False

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def load_dates():
    """Загружаем даты последних новостей"""
    try:
        with open('dates.json', 'r') as f:
            data = json.load(f)
            # Конвертируем строки в datetime
            for url, info in data.items():
                if isinstance(info, dict) and 'last_date' in info:
                    info['last_date'] = datetime.fromisoformat(info['last_date'])
            return data
    except FileNotFoundError:
        logger.info("📁 Файл dates.json не найден, создаём новый")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"❌ Ошибка чтения dates.json: {e}")
        return {}
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки dates.json: {e}")
        return {}

def save_dates(dates_dict):
    """Сохраняем даты в файл"""
    try:
        # Конвертируем datetime в строки
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
    """Возвращает случайный User-Agent"""
    return random.choice(USER_AGENTS)

def get_feed_headers():
    """Возвращает headers для запроса RSS"""
    return {
        'User-Agent': get_random_user_agent(),
        'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
    }

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
        logger.warning(f"⚠️ Ошибка перевода: {e}")
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

        if response.status_code == 200:
            return True
        else:
            logger.error(f"🤖 Telegram API ошибка {response.status_code}: {response.text[:100]}")
            return False

    except Exception as e:
        logger.error(f"🤖 Ошибка отправки в Telegram: {e}")
        return False

def has_valid_date(entry):
    """Проверяет есть ли у новости валидная дата (в любом формате)"""
    # Способ 1: Структурированные даты
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        # Проверяем что дата реалистичная
        year = entry.published_parsed[0]
        if 2000 <= year <= 2030:  # Реалистичный диапазон
            return True

    if hasattr(entry, 'updated_parsed') and entry.updated_parsed:
        year = entry.updated_parsed[0]
        if 2000 <= year <= 2030:
            return True

    # Способ 2: Строковые поля даты
    for field in ['published', 'updated', 'date']:
        if field in entry and entry[field]:
            date_str = entry[field]

            # Проверяем что строка похожа на дату
            # Должен содержать год (4 цифры) и месяц
            import re
            if re.search(r'\d{4}', date_str):  # Есть 4 цифры (год)
                # Проверяем месяцы (английские или русские)
                months_en = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                           'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
                months_ru = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн',
                           'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек']

                if any(month in date_str for month in months_en + months_ru):
                    return True

    return False

def fix_4pda_feed(feed):
    """Исправляет feedparser результат для 4pda - создаёт published_parsed из строки"""
    if not feed.entries:
        return feed

    fixed_count = 0
    for entry in feed.entries:
        # Если есть published строка, но нет published_parsed
        if 'published' in entry and not hasattr(entry, 'published_parsed'):
            try:
                date_str = entry['published']
                dt = parsedate_to_datetime(date_str)

                # Добавляем published_parsed вручную
                entry.published_parsed = dt.utctimetuple()
                fixed_count += 1

            except Exception as e:
                logger.debug(f"⚠️ Не удалось распарсить дату 4pda: {date_str} - {e}")

    if fixed_count > 0:
        logger.info(f"✅ Исправлено дат для 4pda: {fixed_count}")

    return feed

def parse_feed_with_fallback(url):
    """Парсит RSS с обработкой кодировок и User-Agent"""
    try:
        # Пробуем с случайным User-Agent
        headers = get_feed_headers()
        feed = feedparser.parse(url, request_headers=headers)

        # Если нет записей, пробуем определить кодировку для специфичных сайтов
        if not feed.entries:
            for site, encoding in SITE_ENCODINGS.items():
                if site in url:
                    logger.info(f"🔄 Пробую кодировку {encoding} для {site}")
                    try:
                        response = requests.get(url, headers=headers, timeout=10)
                        decoded = response.content.decode(encoding, errors='ignore')
                        feed = feedparser.parse(decoded)
                        if feed.entries:
                            logger.info(f"✅ Кодировка {encoding} сработала")
                            break
                    except Exception as e:
                        logger.warning(f"⚠️ Ошибка перекодировки {encoding}: {e}")
                        continue

        return feed

    except Exception as e:
        logger.error(f"❌ Ошибка парсинга {url[:50]}...: {e}")
        return None

def handle_request_error(feed_url, error, dates, error_count):
    """Обрабатывает ошибки запросов"""
    error_str = str(error).lower()

    # 429 или Too Many Requests
    if "429" in error_str or "too many" in error_str:
        logger.warning(f"⏳ Лимит запросов для {feed_url[:40]}..., пропускаю в этой проверке")
        # Даём сайту передышку
        time.sleep(30)
        return "skip"  # Пропускаем сейчас

    # 5xx Server Errors
    if any(code in error_str for code in ["500", "502", "503", "504"]):
        logger.warning(f"🔄 Ошибка сервера для {feed_url[:40]}..., пропускаю")
        return "skip"  # Пропускаем, пробуем позже

    # Network/Connection errors
    logger.error(f"❌ Ошибка сети для {feed_url[:40]}...: {error_str[:50]}")
    return "error"  # Увеличиваем error_count

def initialize_first_run():
    """Инициализация при первом запуске"""
    logger.info("🔄 Первый запуск - инициализация лент")
    dates = {}

    for feed_url in RSS_FEEDS:
        try:
            logger.info(f"  Инициализация: {feed_url[:50]}...")

            feed = parse_feed_with_fallback(feed_url)
            if feed is None:
                continue

            # Проверка 1: Лента не пустая
            if not feed.entries:
                logger.error(f"    ❌ Пустая лента, пропускаем")
                continue

            # Специальная обработка для 4pda
            if '4pda' in feed_url:
                feed = fix_4pda_feed(feed)

            # Проверка 2: Есть валидные даты
            if not has_valid_date(feed.entries[0]):
                logger.error(f"    ❌ Лента без валидных дат, пропускаем")
                continue

            # Берём самую свежую новость
            entry = feed.entries[0]
            title = entry.title

            # Перевод если нужно
            if not is_russian_text(title):
                translated, success = translate_text(title)
                if success:
                    title = translated
                    logger.debug(f"    🌐 Переведено: {title[:50]}...")

            # Отправляем в Telegram
            logger.info(f"    📤 Отправка: {title[:60]}...")
            if send_to_telegram(title, entry.link):
                # Определяем дату для сохранения
                if hasattr(entry, 'published_parsed'):
                    pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                elif hasattr(entry, 'updated_parsed'):
                    pub_date = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
                else:
                    # Используем текущее время для инициализации
                    pub_date = datetime.now(timezone.utc)

                dates[feed_url] = {
                    'last_date': pub_date,
                    'error_count': 0
                }
                save_dates(dates)
                logger.info(f"    ✅ Успешно, дата: {pub_date.strftime('%Y-%m-%d %H:%M')}")

                # Задержка между лентами при инициализации
                time.sleep(10)
            else:
                logger.error(f"    ❌ Ошибка отправки")

        except Exception as e:
            logger.error(f"    ❌ Ошибка инициализации: {str(e)[:50]}")

    logger.info(f"✅ Инициализация завершена. Успешно: {len(dates)}/{len(RSS_FEEDS)} лент")
    return dates

# ==================== ОСНОВНАЯ ЛОГИКА ====================
def check_feeds():
    """Проверяем все RSS ленты"""
    global last_check_time, is_checking

    if is_checking:
        logger.info("⚠️ Проверка уже выполняется, пропускаем")
        return 0

    is_checking = True
    try:
        logger.info("=" * 60)
        logger.info("🔍 Начало проверки новостей")

        # Загружаем сохраненные даты
        dates = load_dates()

        # Если первый запуск - инициализируем
        if not dates:
            dates = initialize_first_run()
            last_check_time = datetime.now(timezone.utc)
            return len(dates)

        sent_count = 0

        # Проверяем каждую ленту
        for feed_url in RSS_FEEDS:
            try:
                logger.info(f"📰 Проверка: {feed_url[:50]}...")

                # Загружаем состояние ленты
                if feed_url in dates:
                    last_date_info = dates[feed_url]
                    last_date = last_date_info['last_date']
                    error_count = last_date_info.get('error_count', 0)
                else:
                    last_date = None  # Лента новая или была удалена
                    error_count = 0

                # ПАРСИМ ЛЕНТУ С ИСПРАВЛЕНИЯМИ
                feed = parse_feed_with_fallback(feed_url)
                if feed is None:
                    # Увеличиваем счётчик ошибок
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

                # Специальная обработка для 4pda
                if '4pda' in feed_url:
                    feed = fix_4pda_feed(feed)

                # ПРОВЕРКА: Лента не пустая
                if not feed.entries:
                    logger.error(f"  ❌ Пустая лента")
                    if feed_url in dates:
                        del dates[feed_url]
                        save_dates(dates)

                    # Задержка перед следующей лентой
                    time.sleep(random.uniform(*REQUEST_DELAY))
                    continue

                # ПРОВЕРКА: Есть валидные даты
                if not has_valid_date(feed.entries[0]):
                    logger.error(f"  ❌ Лента без валидных дат")
                    logger.debug(f"     Пример новости: {feed.entries[0].get('title', 'Без названия')[:80]}...")

                    # Логируем какие поля есть для отладки
                    entry = feed.entries[0]
                    logger.debug(f"     Поля новости: {list(entry.keys())}")
                    for field in ['published', 'updated', 'date']:
                        if field in entry:
                            logger.debug(f"     {field}: {entry[field]}")

                    if feed_url in dates:
                        del dates[feed_url]
                        save_dates(dates)
                        logger.info(f"  🗑️ Лента удалена из отслеживания")

                    # Задержка перед следующей лентой
                    time.sleep(random.uniform(*REQUEST_DELAY))
                    continue

                # Определяем дату самой свежей новости
                entry = feed.entries[0]
                if hasattr(entry, 'published_parsed'):
                    latest_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                elif hasattr(entry, 'updated_parsed'):
                    latest_date = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
                else:
                    # Должно не случиться благодаря has_valid_date()
                    latest_date = datetime.now(timezone.utc)

                # ЛОГИКА ОТПРАВКИ НОВОСТЕЙ
                if last_date is None:
                    # СИТУАЦИЯ: Лента новая или была удалена
                    # Берём САМУЮ СВЕЖУЮ новость
                    title = entry.title

                    # Перевод если нужно
                    if not is_russian_text(title):
                        translated, success = translate_text(title)
                        if success:
                            title = translated

                    # Отправляем
                    logger.info(f"  📤 Отправка (новая лента): {title[:60]}...")
                    if send_to_telegram(title, entry.link):
                        sent_count += 1
                        dates[feed_url] = {
                            'last_date': latest_date,
                            'error_count': 0
                        }
                        save_dates(dates)

                else:
                    # СИТУАЦИЯ: Лента уже отслеживается
                    # Ищем ВСЕ новости новее last_date
                    new_entries = []
                    for entry in feed.entries:
                        if has_valid_date(entry):
                            # Определяем дату новости
                            if hasattr(entry, 'published_parsed'):
                                pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                            elif hasattr(entry, 'updated_parsed'):
                                pub_date = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
                            else:
                                continue  # Пропускаем если нет даты (не должно случиться)

                            if pub_date > last_date:
                                new_entries.append((entry, pub_date))

                    # Если есть новые новости
                    if new_entries:
                        logger.info(f"  📦 Найдено новых: {len(new_entries)}")

                        # СОРТИРУЕМ от СТАРОЙ к НОВОЙ
                        new_entries.sort(key=lambda x: x[1])

                        # Отправляем каждую в правильном порядке
                        for entry, pub_date in new_entries:
                            title = entry.title

                            # Перевод если нужно
                            if not is_russian_text(title):
                                translated, success = translate_text(title)
                                if success:
                                    title = translated

                            # Отправляем
                            logger.info(f"  📤 Отправка [{pub_date.strftime('%H:%M')}]: {title[:60]}...")
                            if send_to_telegram(title, entry.link):
                                sent_count += 1
                                dates[feed_url] = {
                                    'last_date': pub_date,
                                    'error_count': 0
                                }
                                save_dates(dates)  # Атомарно сохраняем
                                time.sleep(10)  # Задержка между новостями одной ленты

                    else:
                        logger.info(f"  ✅ Нет новых новостей (последняя: {last_date.strftime('%Y-%m-%d %H:%M')})")

                # Сбрасываем счётчик ошибок при успешной обработке
                if feed_url in dates:
                    dates[feed_url]['error_count'] = 0
                    save_dates(dates)

                # Задержка перед следующей лентой (после успешной обработки)
                time.sleep(random.uniform(*REQUEST_DELAY))

            except Exception as e:
                error_result = handle_request_error(feed_url, e, dates, error_count)

                if error_result == "skip":
                    # Пропускаем ленту в этой проверке
                    time.sleep(random.uniform(*REQUEST_DELAY))
                    continue
                elif error_result == "error":
                    # Увеличиваем счётчик ошибок
                    error_count += 1

                    if feed_url in dates:
                        dates[feed_url]['error_count'] = error_count
                    else:
                        dates[feed_url] = {
                            'last_date': datetime.now(timezone.utc),
                            'error_count': error_count
                        }

                    # Если 3 ошибки подряд - удаляем ленту
                    if error_count >= 3:
                        del dates[feed_url]
                        logger.info(f"  🗑️ Лента удалена после 3 ошибок: {feed_url[:50]}...")

                    save_dates(dates)

                # Задержка перед следующей лентой (после ошибки)
                time.sleep(random.uniform(*REQUEST_DELAY))

        # Сохраняем обновленные даты
        save_dates(dates)
        last_check_time = datetime.now(timezone.utc)
        logger.info(f"📊 Проверка завершена. Отправлено: {sent_count} новостей")
        logger.info("=" * 60)
        return sent_count

    finally:
        is_checking = False

def auto_check_scheduler():
    """Фоновая задача: проверка каждые 20 минут"""
    logger.info(f"⏰ Автоматический планировщик запущен (интервал: {CHECK_INTERVAL//60} мин)")

    # Первая проверка сразу
    check_feeds()

    # Затем каждые 20 минут
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
            .fixes {{ background: #d4edda; padding: 15px; border-radius: 5px; margin: 15px 0; }}
        </style>
    </head>
    <body>
        <h1>📰 RSS to Telegram Bot (Исправленная версия)</h1>

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

        <div class="fixes">
            <h3>🛠 Исправления в этой версии:</h3>
            <ul>
                <li>User-Agent браузера для всех запросов</li>
                <li>Ротация User-Agent для обхода блокировок</li>
                <li>Исправление дат для 4pda (windows-1251 кодировка)</li>
                <li>Обработка 429 ошибок (Too Many Requests)</li>
                <li>Задержки между лентами 3-7 секунд</li>
                <li>Автоматическое удаление лент без валидных дат</li>
            </ul>
        </div>

        <p><small>Бот автоматически удаляет проблемные ленты (пустые, без дат, с ошибками)</small></p>
    </body>
    </html>
    """

@app.route('/health')
def health():
    return "OK"

# ==================== ЗАПУСК ====================
if __name__ == '__main__':
    if not BOT_TOKEN or not CHANNEL_ID:
        logger.error("❌ Установите BOT_TOKEN и CHANNEL_ID!")
        exit(1)

    logger.info("=" * 60)
    logger.info("🚀 RSS to Telegram Bot (Исправленная версия) запущен")
    logger.info(f"📰 Отслеживается лент: {len(RSS_FEEDS)}")
    logger.info(f"⏰ Проверка каждые: {CHECK_INTERVAL//60} минут")
    logger.info(f"⏳ Задержка между лентами: {REQUEST_DELAY[0]}-{REQUEST_DELAY[1]} секунд")
    logger.info("🛠 Исправления: User-Agent, 4pda даты, 429 обработка, кодировки")
    logger.info("=" * 60)

    # Запускаем планировщик в отдельном потоке
    scheduler_thread = threading.Thread(target=auto_check_scheduler, daemon=True)
    scheduler_thread.start()
    logger.info("✅ Планировщик запущен в фоновом режиме")

    # Запускаем Flask
    port = int(os.getenv('PORT', 10000))
    logger.info(f"🌐 Flask сервер запускается на порту {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
