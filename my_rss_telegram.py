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

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Конфигурация из переменных окружения
class Config:
    TELEGRAM_BOT_TOKEN = os.getenv('BOT_TOKEN')
    TELEGRAM_CHANNEL_ID = os.getenv('CHANNEL_ID')
    RSS_URL = os.getenv('RSS_URL')

    # Несколько RSS лент можно передать через разделитель
    RSS_FEEDS = RSS_URL.split(';') if RSS_URL else []

    # Интервал проверки в минутах
    CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '10'))

    # Файл базы данных
    DB_FILE = os.getenv('DB_FILE', 'processed_posts.db')

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

# Отправка сообщения в Telegram
def send_to_telegram(title, link, feed_url=None):
    url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"

    # Экранируем специальные символы HTML
    def escape_html(text):
        return (text
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;'))

    # Форматируем заголовок
    escaped_title = escape_html(title[:300])  # Ограничиваем длину заголовка

    # Форматируем сообщение с кликабельной ссылкой
    message = f'<a href="{link}">{escaped_title}</a>'

    # Добавляем источник, если указан
    if feed_url:
        source_name = feed_url.split('//')[-1].split('/')[0]
        message += f"\n\n🔗 Источник: {source_name}"

    data = {
        'chat_id': Config.TELEGRAM_CHANNEL_ID,
        'text': message,
        'parse_mode': 'HTML',
        'disable_web_page_preview': False
    }

    try:
        response = requests.post(url, data=data, timeout=10)
        response.raise_for_status()

        result = response.json()
        if result.get('ok'):
            logger.info(f"✅ Отправлено: {title[:50]}...")
            return True
        else:
            logger.error(f"❌ Ошибка Telegram API: {result}")
            return False

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Ошибка сети при отправке в Telegram: {e}")
    except Exception as e:
        logger.error(f"❌ Неизвестная ошибка при отправке в Telegram: {e}")

    return False

# Обработка одной RSS ленты
def process_single_feed(feed_url):
    try:
        logger.info(f"📡 Проверяем ленту: {feed_url}")

        # Парсим RSS с таймаутом
        feed = feedparser.parse(feed_url)

        if feed.bozo:  # Проверяем на ошибки парсинга
            logger.warning(f"⚠️  Проблемы с парсингом RSS {feed_url}: {feed.bozo_exception}")

        if not feed.entries:
            logger.warning(f"⚠️  В ленте {feed_url} нет записей")
            return 0

        logger.info(f"📰 Найдено записей: {len(feed.entries)}")

        processed_count = 0
        # Обрабатываем записи в обратном порядке (самые новые сначала)
        for entry in reversed(feed.entries):
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
                    continue

                # Проверяем, не обрабатывалась ли новость
                if not is_processed(post_id):
                    logger.info(f"🆕 Новая запись: {title[:60]}...")

                    # Отправляем в Telegram
                    if send_to_telegram(title, link, feed_url):
                        mark_as_processed(post_id, feed_url, title)
                        processed_count += 1

                        # Небольшая задержка между отправками
                        time.sleep(0.5)
                    else:
                        logger.error(f"❌ Не удалось отправить: {title[:50]}...")

            except Exception as e:
                logger.error(f"❌ Ошибка обработки записи из {feed_url}: {e}")
                continue

        logger.info(f"✅ Обработано новых записей из {feed_url}: {processed_count}")
        return processed_count

    except Exception as e:
        logger.error(f"❌ Критическая ошибка обработки фида {feed_url}: {e}")
        return 0

# Задача для планировщика
def check_all_feeds():
    logger.info("=" * 50)
    logger.info("🔄 Начинаем проверку всех RSS лент...")

    total_processed = 0
    for i, feed_url in enumerate(Config.RSS_FEEDS, 1):
        feed_url = feed_url.strip()
        if not feed_url:
            continue

        logger.info(f"📋 Лента {i}/{len(Config.RSS_FEEDS)}: {feed_url}")
        processed = process_single_feed(feed_url)
        total_processed += processed

        # Небольшая задержка между лентами
        if i < len(Config.RSS_FEEDS):
            time.sleep(1)

    logger.info(f"🎯 Итого отправлено новых записей: {total_processed}")
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
            replace_existing=True
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
        'feeds_count': len(Config.RSS_FEEDS),
        'check_interval_minutes': Config.CHECK_INTERVAL,
        'database': Config.DB_FILE
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
        conn.close()

        # Проверяем количество RSS лент
        feeds_ok = len(Config.RSS_FEEDS) > 0

        return jsonify({
            'status': 'healthy',
            'database': 'ok',
            'feeds_configured': feeds_ok,
            'feeds_count': len(Config.RSS_FEEDS),
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/stats')
def stats():
    try:
        conn = sqlite3.connect(Config.DB_FILE)
        c = conn.cursor()

        # Общая статистика
        c.execute("SELECT COUNT(*) FROM processed_posts")
        total_posts = c.fetchone()[0]

        # Статистика по источникам
        c.execute("""
            SELECT feed_url, COUNT(*) as count
            FROM processed_posts
            GROUP BY feed_url
            ORDER BY count DESC
        """)
        by_source = [{"feed": row[0], "count": row[1]} for row in c.fetchall()]

        # Последние 10 записей
        c.execute("""
            SELECT title, feed_url, published
            FROM processed_posts
            ORDER BY published DESC
            LIMIT 10
        """)
        recent = [{"title": row[0][:50] + "...",
                   "feed": row[1],
                   "published": row[2]} for row in c.fetchall()]

        conn.close()

        return jsonify({
            'total_posts_processed': total_posts,
            'posts_by_source': by_source,
            'recent_posts': recent,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/test-telegram')
def test_telegram():
    """Тестовая отправка сообщения в Telegram"""
    test_title = "✅ RSS Bot работает!"
    test_link = "https://github.com"

    success = send_to_telegram(test_title, test_link, "test")
    return jsonify({
        'telegram_test': 'success' if success else 'failed',
        'message_sent': test_title,
        'timestamp': datetime.now().isoformat()
    })

# ===================== Основной блок =====================

if __name__ == '__main__':
    logger.info("🚀 Запуск RSS to Telegram Bot")

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
    time.sleep(2)
    logger.info("🔍 Выполняем первоначальную проверку...")
    check_all_feeds()

    # Запускаем Flask приложение
    port = int(os.getenv('PORT', 10000))
    logger.info(f"🌐 Flask сервер запускается на порту {port}")

    app.run(host='0.0.0.0', port=port, debug=False)
