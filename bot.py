#!/usr/bin/env python3
"""
🚀 RSS to Telegram Bot (Termux Optimized)
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
#from dotenv import load_dotenv
from urllib.parse import urlparse

# ==================== Загрузка настроек ====================
#load_dotenv()
#BOT_TOKEN = os.getenv('BOT_TOKEN')
#CHANNEL_ID = os.getenv('CHANNEL_ID')

#if not BOT_TOKEN or not CHANNEL_ID:
#    logging.error("❌ Установите BOT_TOKEN и CHANNEL_ID в .env файле!")
#    exit(1)

# ✅ ТЕРМИНАЛЬНЫЕ НАСТРОЙКИ TERMUX
CONFIG = {
    'REQUEST_DELAY_MIN': int(os.getenv('REQUEST_DELAY_MIN', '8')),
    'REQUEST_DELAY_MAX': int(os.getenv('REQUEST_DELAY_MAX', '20')),
    'MAX_HOURS_BACK': int(os.getenv('MAX_HOURS_BACK', '4'))
}

# ==================== Настройка логирования ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ==================== Функции ====================

def get_entry_image(entry):
    """🖼️ Извлекает картинку из RSS записи"""
    image_candidates = [
        getattr(entry, 'enclosures', [{}])[0].get('href') if entry.enclosures else None,
        getattr(entry, 'media_content', [{}])[0].get('url') if hasattr(entry, 'media_content') and entry.media_content else None,
        getattr(entry, 'media_thumbnail', [{}])[0].get('url') if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail else None,
        getattr(entry, 'image', {}).get('href') if hasattr(entry, 'image') else None,
    ]

    for img_url in image_candidates:
        if img_url and (img_url.startswith('http') or img_url.startswith('//')):
            if img_url.startswith('//'):
                base_url = getattr(entry, 'base', '')
                if base_url.startswith('http'):
                    parsed_base = urlparse(base_url)
                    img_url = f"{parsed_base.scheme}:{img_url}"
                else:
                    continue
            return img_url
    return None

def clean_description(description):
    """🧹 Очищает HTML теги и спецсимволы"""
    if not description:
        return ''
    
    description = description.strip()
    description = re.sub(r'<[^>]+>', '', description)
    description = description.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    
    if len(description) > 300:
        description = description[:300] + '...'
    
    return description

def format_publication_date(pub_date):
    """📅 Форматирует дату публикации"""
    return pub_date.strftime('%d.%m.%Y %H:%M')

def send_to_telegram(title, link, feed_url, hashtags_dict, entry, pub_date):
    """📨 Отправляет сообщение с новым форматом"""
    try:
        clean_title = title.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        hashtag = hashtags_dict.get(feed_url, '#новости')
        author = getattr(entry, 'author', 'Неизвестный автор').strip()
        
        original_description = getattr(entry, 'summary', '') or getattr(entry, 'description', '')
        description = clean_description(original_description)
        
        logger.info(f"  📝 Подготовка сообщения: {title[:50]}...")
        
        image_url = None
        
        if hasattr(entry, 'enclosures') and entry.enclosures:
            for enc in entry.enclosures:
                url = enc.get('href', '') or enc.get('url', '')
                if url and (url.endswith('.jpg') or url.endswith('.jpeg') or 
                           url.endswith('.png') or url.endswith('.gif') or
                           'image' in enc.get('type', '').lower()):
                    image_url = url
                    logger.info(f"  🖼️ Найдено в enclosures: {url[:60]}...")
                    break
        
        if not image_url and hasattr(entry, 'media_content'):
            for media in entry.media_content:
                url = media.get('url', '')
                if url and ('image' in media.get('type', '').lower() or
                           url.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp'))):
                    image_url = url
                    logger.info(f"  🖼️ Найдено в media_content: {url[:60]}...")
                    break
        
        if not image_url and hasattr(entry, 'media_thumbnail'):
            if entry.media_thumbnail:
                url = entry.media_thumbnail[0].get('url', '')
                if url:
                    image_url = url
                    logger.info(f"  🖼️ Найдено в media_thumbnail: {url[:60]}...")
        
        if not image_url and hasattr(entry, 'image'):
            url = entry.image.get('href', '') or entry.image.get('url', '')
            if url:
                image_url = url
                logger.info(f"  🖼️ Найдено в image: {url[:60]}...")
        
        if not image_url and original_description:
            import re
            img_patterns = [
                r'<img[^>]+src="([^">]+)"',
                r"<img[^>]+src='([^'>]+)'",
                r'<img[^>]+src=([^\s>]+)'
            ]
            
            for pattern in img_patterns:
                match = re.search(pattern, original_description, re.IGNORECASE)
                if match:
                    image_url = match.group(1)
                    logger.info(f"  🖼️ Найдено в HTML-описании: {image_url[:60]}...")
                    break
        
        if not image_url:
            logger.info("  ⚠️ Картинка не найдена в RSS")
        
        message_text = f'<a href="{link}">{clean_title}</a>'
        
        if description:
            message_text += f'\n\n<i>{description}</i>'
        
        author_hashtag = author.replace(" ", "")
        message_text += f'\n\n📌  {hashtag} 👤  #{author_hashtag}'
        
        if image_url:
            try:
                if image_url.startswith('//'):
                    image_url = 'https:' + image_url
                    logger.info(f"  🔗 Исправлен URL: {image_url[:60]}...")
                
                logger.info(f"  📤 Пытаемся отправить с картинкой...")
                
                img_response = requests.get(image_url, timeout=10, headers={
                    'User-Agent': 'Mozilla/5.0'
                })
                
                if img_response.status_code == 200:
                    photo_data = {
                        'chat_id': CHANNEL_ID,
                        'caption': message_text,
                        'parse_mode': 'HTML'
                    }
                    
                    content_type = img_response.headers.get('Content-Type', 'image/jpeg')
                    
                    files = {'photo': ('image.jpg', img_response.content, content_type)}
                    
                    response = requests.post(
                        f'https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto',
                        files=files,
                        data=photo_data,
                        timeout=20
                    )
                    
                    if response.status_code == 200:
                        logger.info("  ✅ Сообщение с картинкой отправлено")
                        time.sleep(random.uniform(15, 25))
                        return True
                    else:
                        logger.warning(f"  ⚠️ Не удалось отправить фото: {response.status_code}")
                
            except Exception as e:
                logger.warning(f"  ⚠️ Ошибка с картинкой: {str(e)[:80]}")
        
        logger.info("  📤 Отправляем текстовое сообщение")
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
            logger.info("  ✅ Текстовое сообщение отправлено")
            time.sleep(random.uniform(15, 25))
            return True
        else:
            logger.error(f"  ❌ Ошибка отправки: {response.status_code}")
            return False
        
    except Exception as e:
        logger.error(f"🤖 Критическая ошибка: {e}")
        return False

def load_rss_feeds():
    """📰 Загружает RSS-ленты и хэштеги"""
    feeds = []
    hashtags = {}
    
    try:
        with open('feeds.txt', 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                if '#' in line:
                    url, tag = line.split('#', 1)
                    feeds.append(url.strip())
                    hashtags[url.strip()] = '#' + tag.strip()
                else:
                    feeds.append(line)
                    hashtags[line] = '#новости'
    
    except FileNotFoundError:
        logger.error("❌ Файл feeds.txt не найден")
        exit(1)
    
    if not feeds:
        logger.error("❌ Нет RSS-лент")
        exit(1)
    
    logger.info(f"📰 Загружено: {len(feeds)} лент")
    return feeds, hashtags

def load_dates():
    """📁 Загружает историю отправленных постов"""
    try:
        with open('dates.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            for url, info in data.items():
                if 'last_date' in info:
                    info['last_date'] = datetime.fromisoformat(info['last_date'])
            return data
    except FileNotFoundError:
        return {}

def save_dates(dates_dict):
    """💾 Сохраняет историю отправленных постов"""
    data_to_save = {}
    for url, info in dates_dict.items():
        if isinstance(info, dict) and 'last_date' in info:
            data_to_save[url] = {'last_date': info['last_date'].isoformat()}
    
    with open('dates.json', 'w', encoding='utf-8') as f:
        json.dump(data_to_save, f, indent=2, ensure_ascii=False)

def parse_feed(url):
    """📰 Парсит RSS-ленту"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/rss+xml'}
        response = requests.get(url, headers=headers, timeout=10)
        feed = feedparser.parse(response.content)
        return feed if hasattr(feed, 'entries') and feed.entries else None
    except Exception as e:
        logger.error(f"❌ Ошибка парсинга {url[:40]}...: {e}")
        return None

def get_entry_date(entry):
    """📅 Получает дату записи"""
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)

# ==================== Основная логика ====================

def check_feeds():
    """🔍 Основная функция проверки лент"""
    logger.info("=" * 60)
    logger.info(f"🤖 [{len(RSS_FEEDS)} лент] {datetime.now().strftime('%H:%M')}")
    start_time = time.time()

    dates = load_dates()
    sent_count = 0

    for feed_url in RSS_FEEDS:
        try:
            logger.info(f"📰 Проверка: {feed_url[:50]}...")
            
            last_date = dates.get(feed_url, {}).get('last_date')
            threshold_date = (datetime.now(timezone.utc) - timedelta(hours=CONFIG['MAX_HOURS_BACK']) 
                            if last_date is None else last_date)

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
                logger.info(f"  📦 Найдено новых: {len(new_entries)}")
                new_entries.sort(key=lambda x: x[1])

                for entry, pub_date in new_entries:
                    title = getattr(entry, 'title', 'Без названия')
                    link = getattr(entry, 'link', '')
                    
                    if not link:
                        continue

                    logger.info(f"  📤 Отправка [{pub_date.strftime('%H:%M')}]: {title[:60]}...")
                    
                    if send_to_telegram(title, link, feed_url, HASHTAGS, entry, pub_date):
                        sent_count += 1
                        dates[feed_url] = {'last_date': pub_date}
                        save_dates(dates)
                    else:
                        logger.error("  ❌ Ошибка отправки")
                        break
            else:
                logger.info(f"  ✅ Нет новых новостей")

            time.sleep(random.uniform(CONFIG['REQUEST_DELAY_MIN'], CONFIG['REQUEST_DELAY_MAX']))

        except Exception as e:
            logger.error(f"  ❌ Ошибка: {e}")
            time.sleep(random.uniform(CONFIG['REQUEST_DELAY_MIN'], CONFIG['REQUEST_DELAY_MAX']))
            continue

    save_dates(dates)
    logger.info(f"📊 Проверка завершена. Отправлено: {sent_count} новостей")
    logger.info(f"⏱ Время выполнения: {time.time() - start_time:.1f} сек")
    logger.info("=" * 60)
    return sent_count

# ==================== Запуск ====================

if __name__ == '__main__':
    logger.info("=" * 60)
    RSS_FEEDS, HASHTAGS = load_rss_feeds()
    logger.info(f"⏰ Задержка между запросами: {CONFIG['REQUEST_DELAY_MIN']}-{CONFIG['REQUEST_DELAY_MAX']} сек")
    logger.info(f"⏳ Проверяем новости за: {CONFIG['MAX_HOURS_BACK']} часов")
    logger.info("=" * 60)
    
    sent_count = check_feeds()
    logger.info(f"✅ Бот завершил работу. Отправлено: {sent_count} постов")
