#!/usr/bin/env python3
"""
🚀 RSS to Telegram Bot (GitHub Actions) - ПОЛНАЯ ВЕРСИЯ С ПОИСКОМ В ОПИСАНИИ
✅ ФИКС дублей + ✅ ПОЛНЫЙ поиск картинок (5 этапов)
"""

import os
import json
import feedparser
import requests
import time
import logging
import random
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')

if not BOT_TOKEN or not CHANNEL_ID:
    print("❌ Установите BOT_TOKEN и CHANNEL_ID в GitHub Secrets!")
    exit(1)

CONFIG = {
    'REQUEST_DELAY_MIN': int(os.getenv('REQUEST_DELAY_MIN', '5')),
    'REQUEST_DELAY_MAX': int(os.getenv('REQUEST_DELAY_MAX', '10')),
    'MAX_HOURS_BACK': int(os.getenv('MAX_HOURS_BACK', '24'))
}

RSS_FEEDS = []
HASHTAGS = {}

# ==================== ЛОГИРОВАНИЕ ====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_entry_image(entry):
    """🖼️ Этапы 1-4: enclosures → media → thumbnail → image"""
    candidates = [
        getattr(entry, 'enclosures', [{}])[0].get('href') if entry.enclosures else None,
        getattr(entry, 'media_content', [{}])[0].get('url') if hasattr(entry, 'media_content') and entry.media_content else None,
        getattr(entry, 'media_thumbnail', [{}])[0].get('url') if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail else None,
        getattr(entry, 'image', {}).get('href') if hasattr(entry, 'image') else None,
    ]
    for img_url in candidates:
        if img_url and (img_url.startswith('http') or img_url.startswith('//')):
            if img_url.startswith('//'):
                base_url = getattr(entry, 'base', 'https://example.com')
                if base_url.startswith('http'):
                    parsed = urlparse(base_url)
                    img_url = f"{parsed.scheme}:{img_url}"
            return img_url
    return None

def find_image_in_html(description):
    """🖼️ Этап 5: поиск <img src=...> в HTML описании"""
    if not description:
        return None

    img_patterns = [
        r'<img[^>]+src="([^">]+)"',
        r"<img[^>]+src='([^'>]+)'",
        r'<img[^>]+src=([^\s>]+)'
    ]

    for pattern in img_patterns:
        match = re.search(pattern, description, re.IGNORECASE)
        if match:
            return match.group(1)
    return None

def clean_description(description):
    """🧹 Очищает HTML, обрезает до 300 символов"""
    if not description:
        return ''
    description = re.sub(r'<[^>]+>', '', description.strip())
    description = description.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return description[:300] + '...' if len(description) > 300 else description

def format_publication_date(pub_date):
    """📅 Формат: 25.12.2025 14:30"""
    return pub_date.strftime('%d.%m.%Y %H:%M')

def send_to_telegram(title, link, feed_url, hashtags_dict, entry, pub_date):
    """📤 Отправляет пост С ПОЛНЫМ ПОИСКОМ КАРТИНОК (5 этапов)"""
    try:
        clean_title = title.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        hashtag = hashtags_dict.get(feed_url, '#новости')
        author = getattr(entry, 'author', 'Неизвестный автор').strip().replace(" ", "")

        original_description = getattr(entry, 'summary', '') or getattr(entry, 'description', '')
        description = clean_description(original_description)

        logger.info(f"  📝 Подготовка: {title[:50]}...")

        # 🎯 ПОЛНЫЙ ПОИСК КАРТИНОК (5 этапов)
        image_url = None

        # Этап 1-4: RSS теги
        image_url = get_entry_image(entry)
        if image_url:
            logger.info(f"  🖼️ Найдено в RSS: {image_url[:60]}...")

        # ✅ Этап 5: HTML описание (ВОЗВРАЩЁН!)
        if not image_url and original_description:
            image_url = find_image_in_html(original_description)
            if image_url:
                logger.info(f"  🖼️ Найдено в HTML-описании: {image_url[:60]}...")

        if not image_url:
            logger.info("  ⚠️ Картинка не найдена")

        message_text = f'<a href="{link}">{clean_title}</a>'
        if description:
            message_text += f'\n\n<i>{description}</i>'
        message_text += f'\n\n📌 {hashtag} 👤 #{author}'

        # 📸 ОТПРАВКА С КАРТИНКОЙ (приоритет 1)
        if image_url:
            try:
                if image_url.startswith('//'):
                    image_url = 'https:' + image_url
                    logger.info(f"  🔗 Фикс URL: {image_url[:60]}...")

                logger.info(f"  📤 Отправка с картинкой...")
                img_response = requests.get(image_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})

                if img_response.status_code == 200:
                    photo_data = {
                        'chat_id': CHANNEL_ID,
                        'caption': message_text,
                        'parse_mode': 'HTML'
                    }
                    files = {'photo': ('image.jpg', img_response.content, img_response.headers.get('Content-Type', 'image/jpeg'))}

                    response = requests.post(
                        f'https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto',
                        files=files,
                        data=photo_data,
                        timeout=20
                    )

                    if response.status_code == 200:
                        logger.info("  ✅ ✅ Пост с картинкой отправлен!")
                        time.sleep(random.uniform(1, 3))
                        return True
                    else:
                        logger.warning(f"  ⚠️ Фото не отправлено: {response.status_code}")

            except Exception as e:
                logger.warning(f"  ⚠️ Ошибка картинки: {str(e)[:80]}")

        # 📝 ОТПРАВКА ТЕКСТОМ (приоритет 2)
        logger.info("  📤 Отправка текстом")
        data_text = {
            'chat_id': CHANNEL_ID,
            'text': message_text,
            'parse_mode': 'HTML',
            'disable_web_page_preview': 'true'
        }

        response = requests.post(
            f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
            data=data_text,
            timeout=10
        )

        if response.status_code == 200:
            logger.info("  ✅ ✅ Текстовый пост отправлен!")
            time.sleep(random.uniform(15, 25))
            return True
        else:
            logger.error(f"  ❌ Ошибка отправки: {response.status_code}")
            return False

    except Exception as e:
        logger.error(f"🤖 Критическая ошибка: {e}")
        return False

# ==================== ФАЙЛЫ ====================
def load_rss_feeds():
    """📁 feeds.txt: URL#хэштег или URL → #новости"""
    global RSS_FEEDS, HASHTAGS
    try:
        with open('feeds.txt', 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '#' in line:
                    url, tag = line.split('#', 1)
                    RSS_FEEDS.append(url.strip())
                    HASHTAGS[url.strip()] = '#' + tag.strip()
                else:
                    RSS_FEEDS.append(line)
                    HASHTAGS[line] = '#новости'
    except FileNotFoundError:
        logger.error("❌ feeds.txt не найден!")
        exit(1)

    if not RSS_FEEDS:
        logger.error("❌ Нет RSS-лент!")
        exit(1)

    logger.info(f"📰 Загружено {len(RSS_FEEDS)} лент")
    return RSS_FEEDS, HASHTAGS

def load_dates():
    """📅 dates.json → datetime объекты"""
    try:
        with open('dates.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            for url, info in data.items():
                if 'last_date' in info:
                    data[url]['last_date'] = datetime.fromisoformat(info['last_date'])
            return data
    except FileNotFoundError:
        return {}

def save_dates(dates_dict):
    """💾 Сохраняет только last_date как ISO строки"""
    data_to_save = {}
    for url, info in dates_dict.items():
        if isinstance(info, dict) and 'last_date' in info:
            data_to_save[url] = {'last_date': info['last_date'].isoformat()}

    with open('dates.json', 'w', encoding='utf-8') as f:
        json.dump(data_to_save, f, indent=2, ensure_ascii=False)

# ==================== RSS ====================
def parse_feed(url):
    """🌐 Скачивает RSS"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/rss+xml'}
        response = requests.get(url, headers=headers, timeout=10)
        feed = feedparser.parse(response.content)
        return feed if hasattr(feed, 'entries') and feed.entries else None
    except Exception as e:
        logger.error(f"❌ Парсинг {url[:40]}...: {e}")
        return None

def get_entry_date(entry):
    """📅 Дата публикации UTC"""
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)

# ==================== ✅ ОСНОВНАЯ ЛОГИКА (ФИКС ДУБЛЕЙ) ====================
def check_feeds():
    """🔍 ПРОВЕРКА ВСЕХ ЛЕНТ"""
    logger.info("=" * 60)
    logger.info(f"🤖 [{len(RSS_FEEDS)} лент] {datetime.now().strftime('%H:%M')}")
    start_time = time.time()

    dates = load_dates()
    sent_count = 0

    for feed_url in RSS_FEEDS:
        try:
            logger.info(f"📰 {feed_url[:50]}...")

            # ✅ КРИТИЧЕСКИЙ ФИКС ДУБЛЕЙ!
            last_date = dates.get(feed_url, {}).get('last_date')
            if last_date is None:
                # 🎯 ПЕРВЫЙ ЗАПУСК: только 24ч назад
                threshold_date = datetime.now(timezone.utc) - timedelta(hours=24)
                logger.info("  🔄 ПЕРВЫЙ запуск: за 24ч")
            else:
                # 🎯 ДАЛЬШЕ: только новые после last_date
                threshold_date = last_date
                logger.info(f"  ⏰ С last_date: {last_date.strftime('%H:%M')}")

            feed = parse_feed(feed_url)
            if not feed:
                time.sleep(random.uniform(CONFIG['REQUEST_DELAY_MIN'], CONFIG['REQUEST_DELAY_MAX']))
                continue

            new_entries = []
            for entry in feed.entries:
                entry_date = get_entry_date(entry)
                if entry_date > threshold_date:
                    new_entries.append((entry, entry_date))

            if new_entries:
                logger.info(f"  📦 Новых: {len(new_entries)}")
                new_entries.sort(key=lambda x: x[1])  # Старые → новые

                for entry, pub_date in new_entries:
                    title = getattr(entry, 'title', 'Без названия')
                    link = getattr(entry, 'link', '')
                    if not link:
                        continue

                    logger.info(f"  📤 [{pub_date.strftime('%H:%M')}] {title[:60]}...")

                    if send_to_telegram(title, link, feed_url, HASHTAGS, entry, pub_date):
                        sent_count += 1
                        dates[feed_url] = {'last_date': pub_date}
                        save_dates(dates)  # Сохраняем после каждой!
                    else:
                        logger.error("  ❌ Ошибка отправки")
                        break
            else:
                logger.info("  ✅ Нет новых")

            time.sleep(random.uniform(CONFIG['REQUEST_DELAY_MIN'], CONFIG['REQUEST_DELAY_MAX']))

        except Exception as e:
            logger.error(f"  ❌ Ошибка: {e}")
            continue

    save_dates(dates)  # Финальное сохранение
    logger.info(f"📊 ГОТОВО! Отправлено: {sent_count}")
    logger.info(f"⏱️ {time.time() - start_time:.1f}с")
    logger.info("=" * 60)
    return sent_count

# ==================== ЗАПУСК ====================
if __name__ == '__main__':
    logger.info("=" * 60)
    load_rss_feeds()
    logger.info(f"⏰ Задержки: {CONFIG['REQUEST_DELAY_MIN']}-{CONFIG['REQUEST_DELAY_MAX']}с")
    logger.info("🆕 Логика: 1й запуск=24ч, далее=только новые")
    logger.info("🖼️ Поиск картинок: 5 этапов (RSS + HTML)")
    logger.info("=" * 60)

    sent_count = check_feeds()
    logger.info(f"✅ ПЕРФЕКТ! Отправлено: {sent_count} 🚀")
