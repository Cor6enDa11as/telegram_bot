##!/usr/bin/env python3
"""
🚀 RSS to Telegram Bot для Termux (без Flask)
"""

import os
import json
import feedparser
import requests
import time
import logging
import random
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import hashlib
import base64

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s',
                   handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()])

def load_rss_feeds():
    feeds = []; hashtags = {}
    try:
        with open('feeds.txt', 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'): continue
                if '#' in line:
                    url, tag = line.split('#', 1)
                    feeds.append(url.strip()); hashtags[url.strip()] = '#' + tag.strip()
                else:
                    feeds.append(line); hashtags[line] = '#новости'
    except FileNotFoundError:
        logging.error("❌ feeds.txt не найден"); exit(1)
    return feeds, hashtags

def load_dates():
    try:
        with open('dates.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            for url, info in data.items():
                if 'last_date' in info: info['last_date'] = datetime.fromisoformat(info['last_date'])
            return data
    except: return {}

def save_dates(dates_dict):
    data_to_save = {k: {'last_date': v['last_date'].isoformat()}
                   for k, v in dates_dict.items() if 'last_date' in v}
    with open('dates.json', 'w', encoding='utf-8') as f:
        json.dump(data_to_save, f, indent=2, ensure_ascii=False)

def get_og_data(url):
    """Получает OG или заглушку"""
    try:
        resp = requests.get(url, headers={'User-Agent': 'TelegramBot'}, timeout=10)
        if resp.status_code != 200: return None

        soup = BeautifulSoup(resp.text, 'html.parser')
        title = (soup.find('meta', property='og:title') or
                soup.find('meta', property='twitter:title') or {}).get('content', 'Новость')[:100]
        desc = (soup.find('meta', property='og:description') or
               soup.find('meta', property='twitter:description') or {}).get('content', 'Подробнее...')[:200]
        img = (soup.find('meta', property='og:image') or
              soup.find('meta', property='twitter:image') or {}).get('content')

        return {
            'title': title, 'desc': desc,
            'image': img or 'https://via.placeholder.com/600x315/4285F4/FFFFFF?text=📰',
            'url': url
        }
    except: return None

def send_to_telegram(title, link, feed_url, hashtags_dict):
    """Отправка с превью"""
    clean_title = title.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    hashtag = hashtags_dict.get(feed_url, '#новости')

    # Пробуем кастомное превью, fallback на оригинал
    preview_url = link
    og_data = get_og_data(link)
    if og_data:
        # Генерируем мини-OG HTML в памяти (base64)
        og_html = f'''<!DOCTYPE html><html><head>
<meta property="og:title" content="{og_data['title']}">
<meta property="og:description" content="{og_data['desc']}">
<meta property="og:image" content="{og_data['image']}">
<meta property="og:url" content="{og_data['url']}">
<meta name="twitter:card" content="summary_large_image">
</head></html>'''
        preview_id = hashlib.md5(og_html.encode()).hexdigest()[:8]
        preview_url = f"https://t.me/iv?url={base64.urlsafe_b64encode(og_html.encode()).decode()}"

    message = f'''📢 <a href="{link}"><b>{clean_title}</b></a>
<h4>👆 Посмотреть</h4>
||{hashtag}||'''

    data = {
        'chat_id': CHANNEL_ID,
        'text': message,
        'parse_mode': 'HTML',
        'link_preview_options': json.dumps({
            'is_disabled': False, 'url': preview_url, 'show_above_text': True
        })
    }

    resp = requests.post(f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage', data=data, timeout=10)
    time.sleep(10)  # задержка
    return resp.status_code == 200

def parse_feed(url):
    resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
    feed = feedparser.parse(resp.content)
    return feed if hasattr(feed, 'entries') and feed.entries else None

def get_entry_date(entry):
    return (datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            if hasattr(entry, 'published_parsed') and entry.published_parsed
            else datetime.now(timezone.utc))

def check_feeds():
    logging.info("🔍 Проверка новостей")
    RSS_FEEDS, HASHTAGS = load_rss_feeds()
    dates = load_dates(); sent_count = 0

    for feed_url in RSS_FEEDS:
        last_date = dates.get(feed_url, {}).get('last_date')
        threshold = (datetime.now(timezone.utc) - timedelta(hours=24) if last_date is None else last_date)

        feed = parse_feed(feed_url)
        if not feed: continue

        new_entries = [(entry, get_entry_date(entry)) for entry in feed.entries
                      if get_entry_date(entry) > threshold]

        if new_entries:
            new_entries.sort(key=lambda x: x[1])
            for entry, pub_date in new_entries:
                title = getattr(entry, 'title', 'Без названия')
                link = getattr(entry, 'link', '')
                if link and send_to_telegram(title, link, feed_url, HASHTAGS):
                    sent_count += 1
                    dates[feed_url] = {'last_date': pub_date}
                    save_dates(dates)
        else:
            dates[feed_url] = {'last_date': datetime.now(timezone.utc)}
            save_dates(dates)
        time.sleep(random.uniform(3, 7))

    return sent_count

if __name__ == '__main__':
    if not BOT_TOKEN or not CHANNEL_ID:
        logging.error("❌ Установите BOT_TOKEN и CHANNEL_ID в .env"); exit(1)

    logging.info("🚀 Termux RSS Bot запущен")
    check_feeds()
