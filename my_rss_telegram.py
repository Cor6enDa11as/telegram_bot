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
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Вывод в консоль
        logging.FileHandler('bot.log')  # Запись в файл
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Получаем настройки из переменных окружения Render
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')

logger.info(f"BOT_TOKEN установлен: {'Да' if BOT_TOKEN else 'Нет'}")
logger.info(f"CHANNEL_ID установлен: {'Да' if CHANNEL_ID else 'Нет'}")

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

logger.info(f"Загружено RSS лент: {len(RSS_FEEDS)}")

def load_dates():
    try:
        with open('dates.json', 'r') as f:
            data = json.load(f)
            logger.info(f"Загружены даты для {len(data)} лент")
            return {url: datetime.fromisoformat(date_str) for url, date_str in data.items()}
    except Exception as e:
        logger.warning(f"Не удалось загрузить dates.json: {e}")
        return {}

def save_dates(dates_dict):
    try:
        with open('dates.json', 'w') as f:
            json.dump({k: v.isoformat() for k, v in dates_dict.items()}, f)
        logger.info(f"Сохранены даты для {len(dates_dict)} лент")
    except Exception as e:
        logger.error(f"Ошибка сохранения dates.json: {e}")

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

        logger.debug(f"Перевод текста: {text[:50]}...")
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
                logger.debug(f"Успешно переведено")
                return translated, True

        return text, False

    except Exception as e:
        logger.warning(f"Ошибка перевода: {e}")
        return text, False

def prepare_news_content(title):
    if not title:
        return title, False

    was_translated = False
    processed_title = title

    if not is_russian_text(title):
        logger.debug(f"Текст не русский, пробуем перевести: {title[:50]}...")
        translated_title, success = translate_text(title)
        if success:
            processed_title = translated_title
            was_translated = True
            logger.info(f"Заголовок переведен: {title[:30]}... → {translated_title[:30]}...")

    return processed_title, was_translated

def send_to_telegram(title, link):
    try:
        clean_title = (title
                      .replace('&', '&amp;')
                      .replace('<', '&lt;')
                      .replace('>', '&gt;')
                      .replace('"', '&quot;'))

        message = f'<a href="{link}">{clean_title}</a>'

        logger.debug(f"Отправка в Telegram: {title[:50]}...")
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
            logger.info(f"✅ Отправлено: {title[:50]}...")
            return True
        else:
            logger.error(f"❌ Telegram API error {response.status_code}: {response.text}")
            return False

    except Exception as e:
        logger.error(f"❌ Ошибка отправки в Telegram: {e}")
        return False

def check_feeds():
    logger.info(f"🔍 Начало проверки новостей")

    dates = load_dates()
    sent_count = 0

    for i, feed_url in enumerate(RSS_FEEDS, 1):
        try:
            logger.info(f"[{i}/{len(RSS_FEEDS)}] Проверяем: {feed_url}")
            feed = feedparser.parse(feed_url)

            if not feed.entries:
                logger.warning(f"  📭 Нет записей в ленте")
                continue

            last_date = dates.get(feed_url)
            logger.debug(f"  Последняя дата для ленты: {last_date}")

            new_entries = []
            for entry in feed.entries:
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    pub_date = datetime(*entry.published_parsed[:6])

                    if not last_date or pub_date > last_date:
                        new_entries.append(entry)
                    else:
                        break

            if new_entries:
                logger.info(f"  🆕 Найдено новых: {len(new_entries)}")

                for j, entry in enumerate(reversed(new_entries), 1):
                    logger.info(f"  [{j}/{len(new_entries)}] Обработка: {entry.title[:50]}...")
                    final_title, was_translated = prepare_news_content(entry.title)

                    if send_to_telegram(final_title, entry.link):
                        sent_count += 1
                        logger.info(f"  ⏳ Задержка 10 секунд...")
                        time.sleep(10)
            else:
                logger.info(f"  📭 Нет новых новостей")

            if feed.entries and hasattr(feed.entries[0], 'published_parsed'):
                dates[feed_url] = datetime(*feed.entries[0].published_parsed[:6])
                logger.debug(f"  Обновлена дата: {dates[feed_url]}")

        except Exception as e:
            logger.error(f"  ❌ Ошибка ленты {feed_url[:40]}...: {str(e)}")

    save_dates(dates)
    logger.info(f"📊 Проверка завершена. Отправлено: {sent_count} новостей")
    return sent_count

@app.route('/')
def home():
    logger.info("Запрос на главную страницу")
    return """
    <h1>RSS to Telegram Bot ✅</h1>
    <p>Бот работает! Проверьте логи в Render Dashboard.</p>
    <p><a href="/check">Проверить сейчас</a></p>
    <p><a href="/log">Посмотреть последние логи</a></p>
    """

@app.route('/check')
def check():
    """Этот эндпоинт пингует UptimeRobot каждые 15 минут"""
    logger.info("=" * 50)
    logger.info("📞 ВЫЗВАН /check эндпоинт (UptimeRobot)")
    logger.info("=" * 50)

    result = check_feeds()

    logger.info("=" * 50)
    logger.info(f"✅ /check завершен. Результат: {result}")
    logger.info("=" * 50)

    return f"✅ Проверка завершена. Отправлено: {result} новостей"

@app.route('/log')
def show_log():
    """Показать последние логи"""
    try:
        with open('bot.log', 'r') as f:
            lines = f.readlines()[-100:]  # Последние 100 строк
        return "<pre>" + "".join(lines) + "</pre>"
    except:
        return "Лог файл не найден"

@app.route('/health')
def health():
    return "OK"

@app.route('/ping')
def ping():
    logger.info("Пинг от UptimeRobot")
    return "pong"

@app.route('/test-telegram')
def test_telegram():
    """Тест отправки в Telegram"""
    test_title = "✅ Тест: RSS Bot работает!"
    test_link = "https://github.com"

    if send_to_telegram(test_title, test_link):
        return "Тестовое сообщение отправлено"
    else:
        return "Ошибка отправки тестового сообщения"

if __name__ == '__main__':
    if not BOT_TOKEN:
        logger.error("❌ КРИТИЧЕСКАЯ ОШИБКА: BOT_TOKEN не установлен!")
        exit(1)

    if not CHANNEL_ID:
        logger.error("❌ КРИТИЧЕСКАЯ ОШИБКА: CHANNEL_ID не установлен!")
        exit(1)

    logger.info("=" * 50)
    logger.info("🚀 RSS to Telegram Bot ЗАПУЩЕН")
    logger.info(f"📰 Отслеживается лент: {len(RSS_FEEDS)}")
    logger.info("⏰ UptimeRobot будет пинговать /check каждые 15 минут")
    logger.info("=" * 50)

    # Первая проверка при запуске
    check_feeds()

    # Запускаем Flask
    port = int(os.getenv('PORT', 10000))
    logger.info(f"🌐 Flask запускается на порту {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
