#!/usr/bin/env python3
import os
import time
import feedparser
import requests
import sqlite3
import logging
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify
import re

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Сначала определяем функцию для парсинга RSS лент
def parse_rss_feeds_from_env(rss_url_env):
    """Парсит RSS ленты из переменной окружения"""
    if not rss_url_env:
        return []

    # Ищем все URL в строке с помощью regex
    url_pattern = r'https?://[^\s,]+'
    feeds = re.findall(url_pattern, rss_url_env)

    # Очищаем URL от лишних пробелов
    cleaned_feeds = []
    for feed in feeds:
        feed = feed.strip()
        # Убираем возможные пробелы в конце и кодируем пробелы внутри URL
        if ' ' in feed:
            feed = feed.replace(' ', '%20')
        cleaned_feeds.append(feed)

    return cleaned_feeds

# Конфигурация из переменных окружения
class Config:
    TELEGRAM_BOT_TOKEN = os.getenv('BOT_TOKEN')
    TELEGRAM_CHANNEL_ID = os.getenv('CHANNEL_ID')
    RSS_URL = os.getenv('RSS_URL')

    # Парсим RSS ленты
    RSS_FEEDS = parse_rss_feeds_from_env(RSS_URL)

    # Интервал проверки в минутах
    CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '10'))

    # Файл базы данных
    DB_FILE = os.getenv('DB_FILE', 'processed_posts.db')

    # Максимальная длина заголовка
    MAX_TITLE_LENGTH = int(os.getenv('MAX_TITLE_LENGTH', '300'))

# Проверка конфигурации
def validate_config():
    errors = []

    if not Config.TELEGRAM_BOT_TOKEN:
        errors.append("BOT_TOKEN не установлен")

    if not Config.TELEGRAM_CHANNEL_ID:
        errors.append("CHANNEL_ID не установлен")

    if not Config.RSS_URL or not Config.RSS_FEEDS:
        errors.append("RSS_URL не установлен или некорректен")

    if errors:
        logger.error("Ошибки конфигурации:")
        for error in errors:
            logger.error(f"  - {error}")
        return False

    logger.info(f"Конфигурация загружена. RSS лент: {len(Config.RSS_FEEDS)}")

    # Выводим первые 5 лент для проверки
    for i, feed in enumerate(Config.RSS_FEEDS[:5]):
        logger.info(f"  Лента {i+1}: {feed[:80]}...")

    if len(Config.RSS_FEEDS) > 5:
        logger.info(f"  ... и еще {len(Config.RSS_FEEDS) - 5} лент")

    return True

# Инициализация базы данных
def init_db():
    try:
        conn = sqlite3.connect(Config.DB_FILE)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS processed_posts
                     (post_id TEXT PRIMARY KEY,
                      feed_url TEXT,
                      title TEXT,
                      published TIMESTAMP)''')

        # Создаем индекс для быстрого поиска
        c.execute('''CREATE INDEX IF NOT EXISTS idx_feed_url
                     ON processed_posts(feed_url)''')

        conn.commit()
        logger.info(f"База данных инициализирована: {Config.DB_FILE}")

        # Проверяем количество записей
        c.execute("SELECT COUNT(*) FROM processed_posts")
        count = c.fetchone()[0]
        logger.info(f"В базе уже записей: {count}")

        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка инициализации базы данных: {e}")
        return False

# Проверка, обрабатывалась ли новость
def is_processed(post_id):
    try:
        conn = sqlite3.connect(Config.DB_FILE)
        c = conn.cursor()
        c.execute("SELECT 1 FROM processed_posts WHERE post_id = ?", (post_id,))
        result = c.fetchone()
        conn.close()
        return result is not None
    except Exception as e:
        logger.error(f"Ошибка проверки записи в БД: {e}")
        return False

# Сохранение ID обработанной новости
def mark_as_processed(post_id, feed_url, title):
    try:
        conn = sqlite3.connect(Config.DB_FILE)
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO processed_posts (post_id, feed_url, title, published) VALUES (?, ?, ?, ?)",
                  (post_id, feed_url, title, datetime.now()))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения в БД: {e}")
        return False

# Очистка URL от лишних символов
def clean_url(url):
    url = url.strip()
    # Убираем возможные пробелы в конце
    url = url.rstrip()
    # Заменяем пробелы на %20 если они есть внутри URL
    if ' ' in url:
        parts = url.split(' ')
        url = parts[0]
        for part in parts[1:]:
            if part.startswith('http'):
                break
            url += '%20' + part
    return url

# Отправка сообщения в Telegram
def send_to_telegram(title, link, feed_url=None):
    url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"

    # Экранируем специальные символы HTML
    def escape_html(text):
        if not text:
            return ""
        return (text
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;'))

    # Форматируем заголовок
    escaped_title = escape_html(title[:Config.MAX_TITLE_LENGTH])

    # Форматируем сообщение с кликабельной ссылкой
    message = f'<a href="{link}">{escaped_title}</a>'

    # Добавляем источник, если указан
    if feed_url:
        try:
            source_name = feed_url.split('//')[-1].split('/')[0]
            message += f"\n\n🔗 Источник: {source_name}"
        except:
            pass

    data = {
        'chat_id': Config.TELEGRAM_CHANNEL_ID,
        'text': message,
        'parse_mode': 'HTML',
        'disable_web_page_preview': False,
        'disable_notification': False
    }

    try:
        response = requests.post(url, data=data, timeout=30)
        response.raise_for_status()

        result = response.json()
        if result.get('ok'):
            logger.info(f"✅ Отправлено: {title[:50]}...")
            return True
        else:
            logger.error(f"❌ Ошибка Telegram API: {result}")
            return False

    except requests.exceptions.Timeout:
        logger.error(f"❌ Таймаут при отправке в Telegram")
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Ошибка сети при отправке в Telegram: {e}")
    except Exception as e:
        logger.error(f"❌ Неизвестная ошибка при отправке в Telegram: {e}")

    return False

# Обработка одной RSS ленты
def process_single_feed(feed_url):
    try:
        logger.info(f"📡 Проверяем ленту: {feed_url[:80]}...")

        # Очищаем URL
        clean_feed_url = clean_url(feed_url)

        # Парсим RSS с таймаутом
        feed = feedparser.parse(clean_feed_url)

        if feed.bozo:  # Проверяем на ошибки парсинга
            logger.warning(f"⚠️  Проблемы с парсингом RSS: {feed.bozo_exception}")

        if not feed.entries:
            logger.warning(f"⚠️  В ленте нет записей")
            return 0

        logger.info(f"📰 Найдено записей: {len(feed.entries)}")

        processed_count = 0
        # Обрабатываем записи в обратном порядке (самые новые сначала)
        for entry in reversed(feed.entries[:20]):  # Ограничиваем 20 записями за раз
            try:
                # Получаем или генерируем уникальный ID
                post_id = entry.get('id') or entry.get('link') or entry.get('title')
                if not post_id:
                    continue

                # Нормализуем ID
                post_id = str(post_id).strip()
                title = entry.get('title', 'Без заголовка').strip()
                link = entry.get('link', '').strip()

                if not link:
                    logger.warning(f"⚠️  У записи нет ссылки: {title[:50]}...")
                    continue

                # Проверяем, не обрабатывалась ли новость
                if not is_processed(post_id):
                    logger.info(f"🆕 Новая запись: {title[:60]}...")

                    # Отправляем в Telegram
                    if send_to_telegram(title, link, clean_feed_url):
                        mark_as_processed(post_id, clean_feed_url, title)
                        processed_count += 1

                        # Небольшая задержка между отправками
                        time.sleep(1)
                    else:
                        logger.error(f"❌ Не удалось отправить: {title[:50]}...")

            except Exception as e:
                logger.error(f"❌ Ошибка обработки записи: {e}")
                continue

        logger.info(f"✅ Обработано новых записей: {processed_count}")
        return processed_count

    except Exception as e:
        logger.error(f"❌ Критическая ошибка обработки фида: {e}")
        return 0

# Задача для планировщика
def check_all_feeds():
    logger.info("=" * 50)
    logger.info("🔄 Начинаем проверку всех RSS лент...")

    if not Config.RSS_FEEDS:
        logger.error("❌ Нет RSS лент для проверки")
        return 0

    total_processed = 0
    successful_feeds = 0

    for i, feed_url in enumerate(Config.RSS_FEEDS, 1):
        feed_url = feed_url.strip()
        if not feed_url:
            logger.warning(f"⚠️  Пустая строка на позиции {i}, пропускаем")
            continue

        logger.info(f"📋 Лента {i}/{len(Config.RSS_FEEDS)}")
        processed = process_single_feed(feed_url)
        total_processed += processed

        if processed > 0:
            successful_feeds += 1

        # Небольшая задержка между лентами
        if i < len(Config.RSS_FEEDS):
            time.sleep(2)

    logger.info(f"🎯 Итого: {total_processed} новых записей из {successful_feeds} лент")
    logger.info("=" * 50)
    return total_processed

# Инициализация планировщика
def init_scheduler():
    try:
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            func=check_all_feeds,
            trigger="interval",
            minutes=Config.CHECK_INTERVAL,
            id="check_feeds_job",
            replace_existing=True,
            max_instances=1
        )
        scheduler.start()
        logger.info(f"⏰ Планировщик запущен. Интервал: {Config.CHECK_INTERVAL} минут")
        return scheduler
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации планировщика: {e}")
        return None

# ===================== Flask маршруты =====================

@app.route('/')
def home():
    return jsonify({
        'status': 'running',
        'service': 'RSS to Telegram Bot',
        'channel': Config.TELEGRAM_CHANNEL_ID,
        'total_feeds': len(Config.RSS_FEEDS),
        'check_interval_minutes': Config.CHECK_INTERVAL,
        'database': Config.DB_FILE,
        'sample_feeds': Config.RSS_FEEDS[:3] if Config.RSS_FEEDS else []
    })

@app.route('/check-now', methods=['POST', 'GET'])
def manual_check():
    result = check_all_feeds()
    return jsonify({
        'status': 'check_completed',
        'new_posts_sent': result,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/health')
def health():
    try:
        # Проверяем подключение к БД
        conn = sqlite3.connect(Config.DB_FILE)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM processed_posts")
        count = c.fetchone()[0]
        conn.close()

        return jsonify({
            'status': 'healthy',
            'database': 'ok',
            'total_processed_posts': count,
            'feeds_configured': len(Config.RSS_FEEDS) > 0,
            'feeds_count': len(Config.RSS_FEEDS),
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/feeds')
def list_feeds():
    return jsonify({
        'total_feeds': len(Config.RSS_FEEDS),
        'feeds': Config.RSS_FEEDS,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/test-feed/<int:feed_index>')
def test_feed(feed_index):
    """Тест отдельной RSS ленты"""
    if feed_index < 0 or feed_index >= len(Config.RSS_FEEDS):
        return jsonify({'error': 'Invalid feed index'}), 400

    feed_url = Config.RSS_FEEDS[feed_index]
    logger.info(f"Тестируем ленту {feed_index}: {feed_url}")

    try:
        feed = feedparser.parse(feed_url)
        return jsonify({
            'feed_index': feed_index,
            'feed_url': feed_url,
            'feed_title': feed.feed.get('title', 'No title'),
            'entries_count': len(feed.entries) if feed.entries else 0,
            'sample_entries': [
                {
                    'title': entry.title[:100] if hasattr(entry, 'title') else 'No title',
                    'link': entry.link if hasattr(entry, 'link') else 'No link'
                }
                for entry in (feed.entries[:3] if feed.entries else [])
            ],
            'parse_error': str(feed.bozo_exception) if feed.bozo else None
        })
    except Exception as e:
        return jsonify({
            'feed_index': feed_index,
            'feed_url': feed_url,
            'error': str(e)
        }), 500

@app.route('/clear-db', methods=['POST'])
def clear_database():
    """Очистка базы данных (только для отладки)"""
    try:
        conn = sqlite3.connect(Config.DB_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM processed_posts")
        conn.commit()
        conn.close()
        return jsonify({
            'status': 'cleared',
            'message': 'Database cleared successfully',
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ===================== Основной блок =====================

if __name__ == '__main__':
    logger.info("🚀 Запуск RSS to Telegram Bot")
    logger.info("=" * 50)

    # Проверяем конфигурацию
    if not validate_config():
        logger.error("❌ Неверная конфигурация. Завершаем работу.")
        exit(1)

    # Инициализируем БД
    if not init_db():
        logger.error("❌ Не удалось инициализировать БД. Завершаем работу.")
        exit(1)

    # Инициализируем планировщик
    scheduler = init_scheduler()

    # Первоначальная проверка (с задержкой для полной инициализации)
    time.sleep(3)
    logger.info("🔍 Выполняем первоначальную проверку...")
    initial_result = check_all_feeds()
    logger.info(f"📊 Первоначальная проверка завершена. Отправлено: {initial_result} записей")

    # Запускаем Flask приложение
    port = int(os.getenv('PORT', 10000))
    logger.info(f"🌐 Flask сервер запускается на порту {port}")
    logger.info("=" * 50)

    app.run(host='0.0.0.0', port=port, debug=False)
