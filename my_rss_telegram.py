#!/usr/bin/env python3
import os
import json
import feedparser
import requests
import time
import logging
import threading
from datetime import datetime, timedelta, timezone
from flask import Flask

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

# Глобальные переменные
last_check_time = None
is_checking = False

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

    except requests.exceptions.Timeout:
        logger.warning("⏱️ Таймаут при переводе")
        return text, False
    except requests.exceptions.RequestException as e:
        logger.warning(f"🌐 Ошибка сети при переводе: {e}")
        return text, False
    except (IndexError, KeyError) as e:
        logger.warning(f"📊 Ошибка парсинга ответа перевода: {e}")
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

    except requests.exceptions.Timeout:
        logger.error("⏱️ Таймаут Telegram API")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"🌐 Ошибка сети Telegram API: {e}")
        return False
    except Exception as e:
        logger.error(f"🤖 Ошибка отправки в Telegram: {e}")
        return False

def initialize_first_run():
    """Инициализация при первом запуске"""
    logger.info("🔄 Первый запуск - инициализация лент")
    dates = {}

    for feed_url in RSS_FEEDS:
        try:
            logger.info(f"  Инициализация: {feed_url[:50]}...")

            feed = feedparser.parse(feed_url)

            # Проверка 1: Лента не пустая
            if not feed.entries:
                logger.error(f"    ❌ Пустая лента, пропускаем")
                continue

            # Проверка 2: Есть даты у новостей
            if not hasattr(feed.entries[0], 'published_parsed'):
                logger.error(f"    ❌ Лента без дат, пропускаем")
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
                # Сохраняем дату этой новости
                pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                dates[feed_url] = {
                    'last_date': pub_date,
                    'error_count': 0
                }
                save_dates(dates)
                logger.info(f"    ✅ Успешно, дата: {pub_date.strftime('%H:%M')}")

                time.sleep(10)  # Задержка между лентами
            else:
                logger.error(f"    ❌ Ошибка отправки")

        except requests.exceptions.Timeout:
            logger.error(f"    ⏱️ Таймаут при инициализации")
        except requests.exceptions.ConnectionError:
            logger.error(f"    🔌 Ошибка подключения")
        except Exception as e:
            logger.error(f"    ❌ Ошибка инициализации: {str(e)[:50]}")

    logger.info(f"✅ Инициализация завершена. Успешно: {len(dates)}/{len(RSS_FEEDS)} лент")
    return dates

def check_feeds():
    """Проверяем все RSS ленты"""
    global last_check_time, is_checking

    if is_checking:
        logger.info("⚠️ Проверка уже выполняется, пропускаем")
        return 0

    is_checking = True
    try:
        logger.info("=" * 50)
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
                    last_date = dates[feed_url]['last_date']
                    error_count = dates[feed_url].get('error_count', 0)
                else:
                    last_date = None  # Лента новая или была удалена
                    error_count = 0

                # Получаем ленту
                feed = feedparser.parse(feed_url)

                # Проверка 1: Лента не пустая
                if not feed.entries:
                    logger.error(f"  ❌ Пустая лента")
                    if feed_url in dates:
                        del dates[feed_url]
                        save_dates(dates)
                    continue

                # Проверка 2: Есть даты у новостей
                if not hasattr(feed.entries[0], 'published_parsed'):
                    logger.error(f"  ❌ Лента без дат")
                    if feed_url in dates:
                        del dates[feed_url]
                        save_dates(dates)
                    continue

                # Определяем самую свежую дату в ленте
                latest_entry = feed.entries[0]
                latest_date = datetime(*latest_entry.published_parsed[:6], tzinfo=timezone.utc)

                # ЛОГИКА ОТПРАВКИ НОВОСТЕЙ
                if last_date is None:
                    # СИТУАЦИЯ: Лента новая или была удалена
                    # Берём САМУЮ СВЕЖУЮ новость
                    entry = latest_entry
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
                        time.sleep(10)

                else:
                    # СИТУАЦИЯ: Лента уже отслеживается
                    # Ищем ВСЕ новости новее last_date
                    new_entries = []
                    for entry in feed.entries:
                        if hasattr(entry, 'published_parsed'):
                            pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                            if pub_date > last_date:
                                new_entries.append(entry)

                    # Если есть новые новости
                    if new_entries:
                        logger.info(f"  📦 Найдено новых: {len(new_entries)}")

                        # СОРТИРУЕМ от СТАРОЙ к НОВОЙ
                        new_entries.sort(key=lambda x: datetime(*x.published_parsed[:6], tzinfo=timezone.utc))

                        # Отправляем каждую в правильном порядке
                        for entry in new_entries:
                            title = entry.title
                            pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

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
                                time.sleep(10)  # Задержка между новостями

                    else:
                        logger.info(f"  ✅ Нет новых новостей (последняя: {last_date.strftime('%H:%M')})")

                # Сбрасываем счётчик ошибок при успешной обработке
                if feed_url in dates:
                    dates[feed_url]['error_count'] = 0
                    save_dates(dates)

            except requests.exceptions.Timeout:
                logger.error(f"  ⏱️ Таймаут при получении ленты")
                handle_feed_error(feed_url, dates, error_count)
            except requests.exceptions.ConnectionError:
                logger.error(f"  🔌 Ошибка подключения")
                handle_feed_error(feed_url, dates, error_count)
            except requests.exceptions.HTTPError as e:
                logger.error(f"  🌐 HTTP ошибка: {e.response.status_code if e.response else 'нет ответа'}")
                handle_feed_error(feed_url, dates, error_count)
            except Exception as e:
                logger.error(f"  ❌ Ошибка обработки ленты: {str(e)[:50]}")
                handle_feed_error(feed_url, dates, error_count)

        # Сохраняем обновленные даты
        save_dates(dates)
        last_check_time = datetime.now(timezone.utc)
        logger.info(f"📊 Проверка завершена. Отправлено: {sent_count} новостей")
        logger.info("=" * 50)
        return sent_count

    finally:
        is_checking = False

def handle_feed_error(feed_url, dates, error_count):
    """Обработка ошибок ленты"""
    if feed_url in dates:
        dates[feed_url]['error_count'] = error_count + 1

        # Если 3 ошибки подряд - удаляем ленту
        if dates[feed_url]['error_count'] >= 3:
            del dates[feed_url]
            logger.info(f"  🗑️ Лента удалена после 3 ошибок")
        else:
            save_dates(dates)
    else:
        # Лента ещё не отслеживалась, просто не добавляем
        pass

def auto_check_scheduler():
    """Фоновая задача: проверка каждые 15 минут"""
    logger.info("⏰ Автоматический планировщик запущен")

    # Первая проверка сразу
    check_feeds()

    # Затем каждые 15 минут
    while True:
        time.sleep(15 * 60)  # 15 минут
        logger.info("⏰ Автопроверка по расписанию")
        check_feeds()

@app.route('/')
def home():
    global last_check_time
    status = "🔄 Проверка выполняется" if is_checking else "✅ Готов"

    if last_check_time:
        next_check = last_check_time + timedelta(minutes=15)
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
            <p>⏰ Проверка каждые: <strong>15 минут</strong></p>
            <p>⏳ Задержка между новостями: <strong>10 секунд</strong></p>
            <hr>
            <p>Последняя проверка: <strong>{last_str}</strong></p>
            <p>Следующая проверка: <strong>{next_str}</strong></p>
        </div>

        <p><small>Бот автоматически удаляет проблемные ленты (пустые, без дат, с ошибками)</small></p>
    </body>
    </html>
    """

@app.route('/health')
def health():
    return "OK"

if __name__ == '__main__':
    if not BOT_TOKEN or not CHANNEL_ID:
        logger.error("❌ Установите BOT_TOKEN и CHANNEL_ID!")
        exit(1)

    logger.info("=" * 50)
    logger.info("🚀 RSS to Telegram Bot запущен")
    logger.info(f"📰 Отслеживается лент: {len(RSS_FEEDS)}")
    logger.info("⏳ Задержка между новостями: 10 секунд")
    logger.info("⏰ Автопроверка каждые: 15 минут")
    logger.info("=" * 50)

    # Запускаем планировщик в отдельном потоке
    scheduler_thread = threading.Thread(target=auto_check_scheduler, daemon=True)
    scheduler_thread.start()
    logger.info("✅ Планировщик запущен в фоновом режиме")

    # Запускаем Flask
    port = int(os.getenv('PORT', 10000))
    logger.info(f"🌐 Flask сервер запускается на порту {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
