#!/usr/bin/env python3
import os
import json
import feedparser
import requests
import time
import threading
from datetime import datetime
from flask import Flask

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
    try:
        with open('dates.json', 'r') as f:
            data = json.load(f)
            return {url: datetime.fromisoformat(date_str) for url, date_str in data.items()}
    except:
        return {}

def save_dates(dates_dict):
    with open('dates.json', 'w') as f:
        json.dump({k: v.isoformat() for k, v in dates_dict.items()}, f)

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
        print(f"  ⚠️  Ошибка перевода: {e}")
        return text, False

def prepare_news_content(title):
    if not title:
        return title, False

    was_translated = False
    processed_title = title

    if not is_russian_text(title):
        translated_title, success = translate_text(title)
        if success:
            processed_title = translated_title
            was_translated = True

    return processed_title, was_translated

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

        return response.status_code == 200

    except Exception as e:
        print(f"  ❌ Ошибка отправки: {e}")
        return False

def check_feeds():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🔍 Проверка новостей...")

    dates = load_dates()
    sent_count = 0

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            if not feed.entries:
                continue

            last_date = dates.get(feed_url)

            new_entries = []
            for entry in feed.entries:
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    pub_date = datetime(*entry.published_parsed[:6])

                    if not last_date or pub_date > last_date:
                        new_entries.append(entry)
                    else:
                        break

            if new_entries:
                domain = feed_url.split('//')[1].split('/')[0]
                print(f"  📰 {domain}: {len(new_entries)} новых")

                for entry in reversed(new_entries):
                    final_title, was_translated = prepare_news_content(entry.title)

                    if send_to_telegram(final_title, entry.link):
                        sent_count += 1
                        time.sleep(10)

            if feed.entries and hasattr(feed.entries[0], 'published_parsed'):
                dates[feed_url] = datetime(*feed.entries[0].published_parsed[:6])

        except Exception as e:
            print(f"  ❌ Ошибка: {feed_url[:40]}...: {str(e)[:50]}")

    save_dates(dates)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 📊 Отправлено: {sent_count} новостей")
    return sent_count

def scheduler():
    print("⏰ Планировщик запущен. Проверка каждые 15 минут.")

    # Первая проверка
    check_feeds()

    while True:
        time.sleep(15 * 60)
        check_feeds()

@app.route('/')
def home():
    return "RSS to Telegram Bot работает! ✅"

@app.route('/health')
def health():
    return "OK"

@app.route('/check')
def manual_check():
    result = check_feeds()
    return f"Проверка завершена. Отправлено: {result} новостей"

if __name__ == '__main__':
    if not BOT_TOKEN or not CHANNEL_ID:
        print("❌ ОШИБКА: Установите BOT_TOKEN и CHANNEL_ID!")
        exit(1)

    print("=" * 50)
    print("🚀 RSS to Telegram Bot запущен")
    print(f"📰 Отслеживается лент: {len(RSS_FEEDS)}")
    print("⏱️  Проверка каждые 15 минут")
    print("=" * 50)

    # Запускаем планировщик в отдельном потоке
    thread = threading.Thread(target=scheduler, daemon=True)
    thread.start()

    # Запускаем Flask сервер
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
