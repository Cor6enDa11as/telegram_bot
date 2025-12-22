#!/usr/bin/env python3
"""
🚀 RSS to Telegram Bot (GitHub Actions) - ИСПРАВЛЕННАЯ ВЕРСИЯ
✅ ФИКС дублей: первый запуск=24ч, остальные=только новые после last_date
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
    'MAX_HOURS_BACK': int(os.getenv('MAX_HOURS_BACK', '24'))  # Только для справки
}

RSS_FEEDS = []
HASHTAGS = {}

# ==================== ЛОГИРОВАНИЕ ====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==================== УТИЛИТЫ ====================
def get_entry_image(entry):
    """🖼️ Ищет картинку в RSS: enclosures → media → thumbnail → image"""
    candidates = [
        getattr(entry, 'enclosures', [{}])[0].get('href') if entry.enclosures else None,
        getattr(entry, 'media_content', [{}])[0].get('url') if hasattr(entry, 'media_content') and entry.media_content else None,
        getattr(entry, 'media_thumbnail', [{}])[0].get('url') if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail else None,
        getattr(entry, 'image', {}).get('href') if hasattr(entry, 'image') else None,
    ]
    for img_url in candidates:
        if img_url and (img_url.startswith('http') or img_url.startswith('//')):
            if img_url.startswith('//'):  # Фикс относительных URL
                base_url = getattr(entry, 'base', 'https://example.com')
                if base_url.startswith('http'):
                    parsed = urlparse(base_url)
                    img_url = f"{parsed.scheme}:{img_url}"
            return img_url
    return None

def clean_description(description):
    """🧹 Убирает HTML, обрезает до 300 символов"""
    if not description:
        return ''
    description = re.sub(r'<[^>]+>', '', description.strip())
    description = description.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return description[:300] + '...' if len(description) > 300 else description

def format_publication_date(pub_date):
    """📅 Формат даты: 25.12.2025 14:30"""
    return pub_date.strftime('%d.%m.%Y %H:%M')

# ==================== ОТПРАВКА В TELEGRAM ====================
def send_to_telegram(title, link, feed_url, hashtags_dict, entry, pub_date):
    """📤 Отправляет пост с картинкой (если есть) или текстом"""
    try:
        clean_title = title.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        hashtag = hashtags_dict.get(feed_url, '#новости')
        author = getattr(entry, 'author', 'Неизвестный').replace(" ", "")

        description = clean_description(getattr(entry, 'summary', '') or getattr(entry, 'description', ''))
        image_url = get_entry_image(entry)  # 🔍 Универсальный поиск картинки

        message_text = f'<a href="{link}">{clean_title}</a>'
        if description:
            message_text += f'\n\n<i>{description}</i>'
        message_text += f'\n\n📌 {hashtag} 👤 #{author}'

        # 🎨 ПРИОРИТЕТ 1: Картинка (если найдена)
        if image_url:
            try:
                if image_url.startswith('//'):
                    image_url = 'https:' + image_url

                img_response = requests.get(image_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
                if img_response.status_code == 200:
                    files = {'photo': ('image.jpg', img_response.content, img_response.headers.get('Content-Type', 'image/jpeg'))}
                    response = requests.post(
                        f'https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto',
                        files=files,
                        data={'chat_id': CHANNEL_ID, 'caption': message_text, 'parse_mode': 'HTML'},
                        timeout=20
                    )
                    if response.status_code == 200:
                        logger.info("✅ Пост с картинкой отправлен")
                        time.sleep(random.uniform(1, 3))
                        return True
            except Exception as e:
                logger.warning(f"⚠️ Картинка не сработала: {str(e)[:50]}")

        # 📝 ПРИОРИТЕТ 2: Текст без превью
        data_text = {
            'chat_id': CHANNEL_ID,
            'text': message_text,
            'parse_mode': 'HTML',
            'disable_web_page_preview': 'true'
        }
        response = requests.post(f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage', data=data_text, timeout=10)

        if response.status_code == 200:
            logger.info("✅ Текстовый пост отправлен")
            time.sleep(random.uniform(15, 25))
            return True
        else:
            logger.error(f"❌ Ошибка отправки: {response.status_code}")
            return False

    except Exception as e:
        logger.error(f"🤖 Критическая ошибка отправки: {e}")
        return False

# ==================== РАБОТА С ФАЙЛАМИ ====================
def load_rss_feeds():
    """📁 Читает feeds.txt: URL#хэштег или URL → #новости"""
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
    """📅 Читает dates.json, конвертирует строки в datetime"""
    try:
        with open('dates.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            for url, info in data.items():
                if 'last_date' in info:
                    data[url]['last_date'] = datetime.fromisoformat(info['last_date'])
            return data
    except FileNotFoundError:
        return {}  # ✅ ПЕРВЫЙ ЗАПУСК

def save_dates(dates_dict):
    """💾 Сохраняет dates.json (только last_date как ISO строку)"""
    data_to_save = {url: {'last_date': info['last_date'].isoformat()}
                   for url, info in dates_dict.items()
                   if isinstance(info, dict) and 'last_date' in info}
    with open('dates.json', 'w', encoding='utf-8') as f:
        json.dump(data_to_save, f, indent=2, ensure_ascii=False)

# ==================== RSS ПАРСИНГ ====================
def parse_feed(url):
    """🌐 Скачивает и парсит RSS"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/rss+xml'}
        response = requests.get(url, headers=headers, timeout=10)
        feed = feedparser.parse(response.content)
        return feed if hasattr(feed, 'entries') and feed.entries else None
    except Exception as e:
        logger.error(f"❌ Парсинг {url[:40]}...: {e}")
        return None

def get_entry_date(entry):
    """📅 Извлекает дату публикации (UTC)"""
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)  # Fallback

# ==================== ОСНОВНАЯ ЛОГИКА ✅ ИСПРАВЛЕНА ====================
def check_feeds():
    """🔍 ГЛАВНАЯ ФУНКЦИЯ - проверяет все ленты"""
    logger.info("=" * 60)
    logger.info(f"🤖 [{len(RSS_FEEDS)} лент] {datetime.now().strftime('%H:%M')}")
    start_time = time.time()

    dates = load_dates()
    sent_count = 0

    for feed_url in RSS_FEEDS:
        logger.info(f"📰 {feed_url[:50]}...")

        # ✅ КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ - ФИКС ДУБЛЕЙ!
        last_date = dates.get(feed_url, {}).get('last_date')
        if last_date is None:
            # 🎯 ПЕРВЫЙ ЗАПУСК: только свежие за 24ч
            threshold_date = datetime.now(timezone.utc) - timedelta(hours=24)
            logger.info("  🔄 ПЕРВЫЙ запуск: ищем за 24ч")
        else:
            # 🎯 ПОСЛЕДУЮЩИЕ: только новые после last_date
            threshold_date = last_date
            logger.info(f"  ⏰ С last_date: {last_date.strftime('%H:%M')}")

        feed = parse_feed(feed_url)
        if not feed:
            time.sleep(random.uniform(CONFIG['REQUEST_DELAY_MIN'], CONFIG['REQUEST_DELAY_MAX']))
            continue

        new_entries = []
        for entry in feed.entries:
            entry_date = get_entry_date(entry)
            if entry_date > threshold_date:  # ✅ Только новые!
                new_entries.append((entry, entry_date))

        if new_entries:
            logger.info(f"  📦 Новых: {len(new_entries)}")
            new_entries.sort(key=lambda x: x[1])  # По дате (старые → новые)

            for entry, pub_date in new_entries:
                title = getattr(entry, 'title', 'Без названия')
                link = getattr(entry, 'link', '')
                if not link:
                    continue

                logger.info(f"  📤 [{pub_date.strftime('%H:%M')}] {title[:60]}...")

                if send_to_telegram(title, link, feed_url, HASHTAGS, entry, pub_date):
                    sent_count += 1
                    dates[feed_url] = {'last_date': pub_date}  # ✅ Обновляем дату
                    save_dates(dates)  # Сохраняем ПОСЛЕ каждой отправки
                else:
                    logger.error("  ❌ Ошибка отправки")
                    break
        else:
            logger.info("  ✅ Нет новых")

        time.sleep(random.uniform(CONFIG['REQUEST_DELAY_MIN'], CONFIG['REQUEST_DELAY_MAX']))

    save_dates(dates)  # Финальное сохранение
    logger.info(f"📊 Завершено. Отправлено: {sent_count}")
    logger.info(f"⏱️ {time.time() - start_time:.1f} сек")
    logger.info("=" * 60)
    return sent_count

# ==================== ЗАПУСК ====================
if __name__ == '__main__':
    logger.info("=" * 60)
    load_rss_feeds()
    logger.info(f"⏰ Задержки: {CONFIG['REQUEST_DELAY_MIN']}-{CONFIG['REQUEST_DELAY_MAX']}с")
    logger.info(f"🆕 Логика: первый запуск=24ч, далее=только новые")
    logger.info("=" * 60)

    sent_count = check_feeds()
    logger.info(f"✅ ГОТОВО! Отправлено: {sent_count} постов 🚀")
