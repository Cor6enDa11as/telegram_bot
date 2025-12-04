#!/usr/bin/env python3
import os
import time
import feedparser
import requests
import sqlite3
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify
import re
import random

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Флаг для отслеживания первой проверки
first_check_completed = False

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
    CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '15'))  # 15 минут по умолчанию

    # Файл базы данных
    DB_FILE = os.getenv('DB_FILE', 'processed_posts.db')

    # Максимальная длина заголовка
    MAX_TITLE_LENGTH = int(os.getenv('MAX_TITLE_LENGTH', '300'))

    # Лимиты для предотвращения спама
    MAX_POSTS_PER_CHECK = int(os.getenv('MAX_POSTS_PER_CHECK', '10'))  # Макс постов за одну проверку
    MAX_POSTS_PER_FEED = int(os.getenv('MAX_POSTS_PER_FEED', '2'))     # Макс постов из одной ленты
    MIN_DELAY_BETWEEN_POSTS = int(os.getenv('MIN_DELAY_BETWEEN_POSTS', '10'))  # Минимальная задержка (сек)
    MAX_DELAY_BETWEEN_POSTS = int(os.getenv('MAX_DELAY_BETWEEN_POSTS', '15'))  # Максимальная задержка (сек)

    # Флаг для пропуска первой проверки (чтобы не отправлять старые новости)
    SKIP_INITIAL_CHECK = os.getenv('SKIP_INITIAL_CHECK', 'true').lower() == 'true'

    # Максимальный возраст новости для отправки (в часах)
    MAX_POST_AGE_HOURS = int(os.getenv('MAX_POST_AGE_HOURS', '24'))  # Только новости за последние 24 часа

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
    logger.info(f"Лимиты: {Config.MAX_POSTS_PER_CHECK} постов за проверку, " +
                f"{Config.MAX_POSTS_PER_FEED} из одной ленты")
    logger.info(f"Задержка между постами: {Config.MIN_DELAY_BETWEEN_POSTS}-{Config.MAX_DELAY_BETWEEN_POSTS} сек")

    if Config.SKIP_INITIAL_CHECK:
        logger.info("⚠️  Первая проверка будет пропущена (не отправлять старые новости)")

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

        # Основная таблица обработанных постов
        c.execute('''CREATE TABLE IF NOT EXISTS processed_posts
                     (post_id TEXT PRIMARY KEY,
                      feed_url TEXT,
                      title TEXT,
                      published TIMESTAMP,
                      processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

        # Таблица для хранения истории проверок
        c.execute('''CREATE TABLE IF NOT EXISTS check_history
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      check_time TIMESTAMP,
                      posts_found INTEGER,
                      posts_sent INTEGER,
                      feeds_checked INTEGER)''')

        # Создаем индексы
        c.execute('''CREATE INDEX IF NOT EXISTS idx_feed_url
                     ON processed_posts(feed_url)''')
        c.execute('''CREATE INDEX IF NOT EXISTS idx_processed_at
                     ON processed_posts(processed_at)''')

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

# Логирование результатов проверки
def log_check_result(posts_found, posts_sent, feeds_checked):
    try:
        conn = sqlite3.connect(Config.DB_FILE)
        c = conn.cursor()
        c.execute("INSERT INTO check_history (check_time, posts_found, posts_sent, feeds_checked) VALUES (?, ?, ?, ?)",
                  (datetime.now(), posts_found, posts_sent, feeds_checked))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка логирования результатов: {e}")

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

# Получение времени публикации новости
def get_post_published_time(entry):
    """Извлекает время публикации из записи RSS"""
    try:
        # Пробуем разные поля с временем
        for time_field in ['published_parsed', 'updated_parsed', 'created_parsed']:
            if hasattr(entry, time_field) and getattr(entry, time_field):
                time_tuple = getattr(entry, time_field)
                return datetime(*time_tuple[:6])

        # Если нет времени в структуре, используем текущее время
        return datetime.now()
    except Exception:
        return datetime.now()

# Проверка возраста новости
def is_post_too_old(published_time):
    """Проверяет, не слишком ли старая новость"""
    if Config.MAX_POST_AGE_HOURS <= 0:
        return False  # Если 0 или отрицательное, возраст не проверяем

    age_hours = (datetime.now() - published_time).total_seconds() / 3600
    return age_hours > Config.MAX_POST_AGE_HOURS

# Отправка сообщения в Telegram с обработкой ограничений скорости
def send_to_telegram(title, link, retry_count=0):
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

    # Форматируем сообщение с кликабельной ссылкой (без источника)
    message = f'<a href="{link}">{escaped_title}</a>'

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
            error_msg = result.get('description', str(result))
            logger.error(f"❌ Ошибка Telegram API: {error_msg}")

            # Если это ошибка слишком частых запросов, ждем
            if 'Too Many Requests' in error_msg or response.status_code == 429:
                # Пытаемся получить время ожидания из заголовков
                retry_after = response.headers.get('Retry-After')
                if retry_after:
                    wait_time = int(retry_after)
                else:
                    # Экспоненциальная отсрочка
                    wait_time = min(30 * (2 ** retry_count), 300)  # до 5 минут

                logger.warning(f"⏳ Слишком много запросов. Ждем {wait_time} секунд...")
                time.sleep(wait_time)

                # Рекурсивный повтор
                if retry_count < 3:  # максимум 3 повтора
                    return send_to_telegram(title, link, retry_count + 1)

            return False

    except requests.exceptions.Timeout:
        logger.error(f"❌ Таймаут при отправке в Telegram")
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Ошибка сети при отправке в Telegram: {e}")
    except Exception as e:
        logger.error(f"❌ Неизвестная ошибка при отправке в Telegram: {e}")

    return False

# Обработка одной RSS ленты
def process_single_feed(feed_url, posts_sent_count):
    try:
        logger.info(f"📡 Проверяем ленту: {feed_url[:80]}...")

        # Очищаем URL
        clean_feed_url = clean_url(feed_url)

        # Парсим RSS с таймаутом
        feed = feedparser.parse(clean_feed_url)

        if feed.bozo:  # Проверяем на ошибки парсинга
            error_msg = str(feed.bozo_exception)
            logger.warning(f"⚠️  Проблемы с парсингом RSS: {error_msg}")

        if not feed.entries:
            logger.warning(f"⚠️  В ленте нет записей")
            return posts_sent_count, 0, 0

        logger.info(f"📰 Найдено записей: {len(feed.entries)}")

        new_posts_count = 0
        processed_count = 0
        entries_to_process = min(len(feed.entries), 15)  # Обрабатываем максимум 15 записей

        # Обрабатываем записи в обратном порядке (самые новые сначала)
        for entry in reversed(feed.entries[:entries_to_process]):
            try:
                # Проверяем общий лимит постов
                if posts_sent_count >= Config.MAX_POSTS_PER_CHECK:
                    logger.info(f"📊 Достигнут общий лимит постов ({Config.MAX_POSTS_PER_CHECK})")
                    return posts_sent_count, processed_count, new_posts_count

                # Проверяем лимит для этой ленты
                if new_posts_count >= Config.MAX_POSTS_PER_FEED:
                    logger.info(f"📊 Достигнут лимит постов для этой ленты ({Config.MAX_POSTS_PER_FEED})")
                    return posts_sent_count, processed_count, new_posts_count

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

                # Получаем время публикации
                published_time = get_post_published_time(entry)

                # Пропускаем слишком старые новости (если не первая проверка)
                if first_check_completed and Config.SKIP_INITIAL_CHECK and is_post_too_old(published_time):
                    logger.debug(f"⏳ Пропускаем старую новость: {title[:50]}...")
                    continue

                processed_count += 1

                # Проверяем, не обрабатывалась ли новость
                if not is_processed(post_id):
                    logger.info(f"🆕 Новая запись ({posts_sent_count + 1}/{Config.MAX_POSTS_PER_CHECK}): {title[:60]}...")

                    # Отправляем в Telegram
                    if send_to_telegram(title, link):
                        mark_as_processed(post_id, clean_feed_url, title)
                        posts_sent_count += 1
                        new_posts_count += 1

                        # Случайная задержка между постами 10-15 секунд
                        if posts_sent_count < Config.MAX_POSTS_PER_CHECK:
                            delay = random.uniform(
                                Config.MIN_DELAY_BETWEEN_POSTS,
                                Config.MAX_DELAY_BETWEEN_POSTS
                            )
                            logger.debug(f"⏳ Ждем {delay:.1f} сек перед следующим постом...")
                            time.sleep(delay)
                    else:
                        logger.error(f"❌ Не удалось отправить: {title[:50]}...")

            except Exception as e:
                logger.error(f"❌ Ошибка обработки записи: {e}")
                continue

        logger.info(f"✅ Проверено записей: {processed_count}, новых отправлено: {new_posts_count}")
        return posts_sent_count, processed_count, new_posts_count

    except Exception as e:
        logger.error(f"❌ Критическая ошибка обработки фида: {e}")
        return posts_sent_count, 0, 0

# Задача для планировщика
def check_all_feeds():
    global first_check_completed

    logger.info("=" * 60)
    logger.info("🔄 Начинаем проверку RSS лент...")
    start_time = time.time()

    if not Config.RSS_FEEDS:
        logger.error("❌ Нет RSS лент для проверки")
        return 0

    total_processed = 0
    total_sent = 0
    total_feeds_checked = 0

    # Пропускаем первую проверку если нужно (чтобы не отправлять старые новости)
    if not first_check_completed and Config.SKIP_INITIAL_CHECK:
        logger.info("⏭️  Первая проверка - только сканирование, отправка отключена")
        # Помечаем все существующие посты как обработанные
        for feed_url in Config.RSS_FEEDS:
            feed_url = feed_url.strip()
            if not feed_url:
                continue

            try:
                clean_feed_url = clean_url(feed_url)
                feed = feedparser.parse(clean_feed_url)

                if feed.entries:
                    for entry in reversed(feed.entries[:10]):  # Только первые 10
                        post_id = entry.get('id') or entry.get('link') or entry.get('title')
                        if post_id:
                            title = entry.get('title', 'Без заголовка').strip()
                            mark_as_processed(str(post_id).strip(), clean_feed_url, title)

                    logger.info(f"📝 Помечено как прочитано из {feed_url[:50]}...: {len(feed.entries[:10])} записей")
                    total_feeds_checked += 1

            except Exception as e:
                logger.error(f"❌ Ошибка при сканировании ленты: {e}")

        first_check_completed = True
        logger.info("✅ Первая проверка завершена. Теперь бот будет отправлять только новые новости.")
        logger.info("=" * 60)
        return 0

    posts_sent_count = 0

    # Перемешиваем ленты для разнообразия
    shuffled_feeds = Config.RSS_FEEDS.copy()
    random.shuffle(shuffled_feeds)

    for i, feed_url in enumerate(shuffled_feeds, 1):
        feed_url = feed_url.strip()
        if not feed_url:
            continue

        # Проверяем общий лимит постов
        if posts_sent_count >= Config.MAX_POSTS_PER_CHECK:
            logger.info(f"📊 Достигнут общий лимит постов ({Config.MAX_POSTS_PER_CHECK}). Завершаем проверку.")
            break

        logger.info(f"📋 Лента {i}/{len(shuffled_feeds)} ({posts_sent_count}/{Config.MAX_POSTS_PER_CHECK} постов отправлено)")

        posts_sent_count, processed, sent = process_single_feed(feed_url, posts_sent_count)
        total_processed += processed
        total_sent += sent
        total_feeds_checked += 1

    elapsed_time = time.time() - start_time

    # Логируем результаты проверки
    log_check_result(total_processed, total_sent, total_feeds_checked)

    logger.info(f"📊 Итоги проверки:")
    logger.info(f"   Проверено лент: {total_feeds_checked}")
    logger.info(f"   Найдено записей: {total_processed}")
    logger.info(f"   Отправлено новых: {total_sent}")
    logger.info(f"   ⏱️  Время выполнения: {elapsed_time:.1f} секунд")
    logger.info("=" * 60)

    return total_sent

# Очистка старых записей из базы данных
def cleanup_old_posts():
    try:
        conn = sqlite3.connect(Config.DB_FILE)
        c = conn.cursor()

        # Удаляем записи старше 30 дней
        cutoff_date = datetime.now() - timedelta(days=30)
        c.execute("DELETE FROM processed_posts WHERE processed_at < ?", (cutoff_date,))
        deleted_count = c.changes

        # Удаляем старые записи истории проверок
        c.execute("DELETE FROM check_history WHERE check_time < ?",
                  (datetime.now() - timedelta(days=7),))

        conn.commit()
        conn.close()

        if deleted_count > 0:
            logger.info(f"🧹 Очистка БД: удалено {deleted_count} старых постов")

    except Exception as e:
        logger.error(f"❌ Ошибка при очистке БД: {e}")

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
        'first_check_completed': first_check_completed,
        'limits': {
            'max_posts_per_check': Config.MAX_POSTS_PER_CHECK,
            'max_posts_per_feed': Config.MAX_POSTS_PER_FEED,
            'delay_between_posts': f"{Config.MIN_DELAY_BETWEEN_POSTS}-{Config.MAX_DELAY_BETWEEN_POSTS} сек"
        },
        'skip_initial_check': Config.SKIP_INITIAL_CHECK,
        'max_post_age_hours': Config.MAX_POST_AGE_HOURS
    })

@app.route('/check-now', methods=['POST', 'GET'])
def manual_check():
    result = check_all_feeds()
    return jsonify({
        'status': 'check_completed',
        'new_posts_sent': result,
        'first_check_completed': first_check_completed,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/force-check', methods=['POST'])
def force_check():
    """Принудительная проверка без ограничений"""
    global first_check_completed

    # Сохраняем старые значения лимитов
    old_max_posts = Config.MAX_POSTS_PER_CHECK
    old_skip_initial = Config.SKIP_INITIAL_CHECK

    try:
        # Временно убираем ограничения
        Config.MAX_POSTS_PER_CHECK = 999
        Config.SKIP_INITIAL_CHECK = False

        result = check_all_feeds()
        return jsonify({
            'status': 'force_check_completed',
            'new_posts_sent': result,
            'timestamp': datetime.now().isoformat()
        })
    finally:
        # Восстанавливаем старые значения
        Config.MAX_POSTS_PER_CHECK = old_max_posts
        Config.SKIP_INITIAL_CHECK = old_skip_initial

@app.route('/mark-all-read', methods=['POST'])
def mark_all_read():
    """Пометить все текущие новости как прочитанные"""
    try:
        conn = sqlite3.connect(Config.DB_FILE)
        c = conn.cursor()

        # Получаем количество записей до
        c.execute("SELECT COUNT(*) FROM processed_posts")
        count_before = c.fetchone()[0]

        # Сканируем все ленты и помечаем текущие новости как прочитанные
        total_marked = 0
        for feed_url in Config.RSS_FEEDS:
            feed_url = feed_url.strip()
            if not feed_url:
                continue

            try:
                clean_feed_url = clean_url(feed_url)
                feed = feedparser.parse(clean_feed_url)

                if feed.entries:
                    for entry in feed.entries[:20]:  # Только первые 20
                        post_id = entry.get('id') or entry.get('link') or entry.get('title')
                        if post_id:
                            title = entry.get('title', 'Без заголовка').strip()
                            post_id = str(post_id).strip()

                            # Добавляем в БД если еще нет
                            c.execute("INSERT OR IGNORE INTO processed_posts (post_id, feed_url, title, published) VALUES (?, ?, ?, ?)",
                                      (post_id, clean_feed_url, title, datetime.now()))
                            total_marked += 1

            except Exception as e:
                logger.error(f"❌ Ошибка при сканировании ленты: {e}")

        conn.commit()

        # Получаем количество записей после
        c.execute("SELECT COUNT(*) FROM processed_posts")
        count_after = c.fetchone()[0]

        conn.close()

        global first_check_completed
        first_check_completed = True

        return jsonify({
            'status': 'marked_all_read',
            'previously_processed': count_before,
            'newly_marked': count_after - count_before,
            'total_processed': count_after,
            'first_check_completed': True,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/stats')
def stats():
    try:
        conn = sqlite3.connect(Config.DB_FILE)
        c = conn.cursor()

        # Общая статистика
        c.execute("SELECT COUNT(*) FROM processed_posts")
        total_posts = c.fetchone()[0]

        # Статистика за последние 24 часа
        cutoff = datetime.now() - timedelta(days=1)
        c.execute("SELECT COUNT(*) FROM processed_posts WHERE processed_at > ?", (cutoff,))
        posts_last_24h = c.fetchone()[0]

        # Статистика проверок за последние 7 дней
        c.execute("""
            SELECT DATE(check_time) as date,
                   SUM(posts_found) as found,
                   SUM(posts_sent) as sent,
                   COUNT(*) as checks
            FROM check_history
            WHERE check_time > datetime('now', '-7 days')
            GROUP BY DATE(check_time)
            ORDER BY date DESC
        """)

        check_stats = []
        for row in c.fetchall():
            check_stats.append({
                'date': row[0],
                'posts_found': row[1] or 0,
                'posts_sent': row[2] or 0,
                'checks_count': row[3]
            })

        conn.close()

        return jsonify({
            'first_check_completed': first_check_completed,
            'total_posts_processed': total_posts,
            'posts_last_24h': posts_last_24h,
            'check_history_last_7_days': check_stats,
            'current_settings': {
                'check_interval_minutes': Config.CHECK_INTERVAL,
                'max_posts_per_check': Config.MAX_POSTS_PER_CHECK,
                'max_posts_per_feed': Config.MAX_POSTS_PER_FEED,
                'delay_between_posts': f"{Config.MIN_DELAY_BETWEEN_POSTS}-{Config.MAX_DELAY_BETWEEN_POSTS} сек",
                'skip_initial_check': Config.SKIP_INITIAL_CHECK,
                'max_post_age_hours': Config.MAX_POST_AGE_HOURS
            },
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/test-send')
def test_send():
    """Тестовая отправка сообщения"""
    test_title = "✅ RSS Bot работает корректно!"
    test_link = "https://github.com"

    success = send_to_telegram(test_title, test_link)
    return jsonify({
        'telegram_test': 'success' if success else 'failed',
        'message': test_title,
        'timestamp': datetime.now().isoformat()
    })

# ===================== Основной блок =====================

if __name__ == '__main__':
    logger.info("🚀 Запуск RSS to Telegram Bot")
    logger.info("=" * 60)

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

    # Первоначальная проверка (только сканирование)
    time.sleep(2)
    logger.info("🔍 Выполняем первоначальное сканирование...")
    initial_result = check_all_feeds()
    logger.info(f"📊 Первоначальное сканирование завершено. Помечено записей: {initial_result}")

    # Очистка старых записей
    cleanup_old_posts()

    # Запускаем Flask приложение
    port = int(os.getenv('PORT', 10000))
    logger.info(f"🌐 Flask сервер запускается на порту {port}")
    logger.info("=" * 60)
    logger.info("✅ Бот запущен и готов к работе. Новые новости будут отправляться автоматически.")

    app.run(host='0.0.0.0', port=port, debug=False)
