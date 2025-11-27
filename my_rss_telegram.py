#!/usr/bin/env python3
import os
import feedparser
import requests
from flask import Flask
from threading import Thread
import time
import logging
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

app = Flask(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')
RSS_FEED_URLS = [url.strip() for url in os.getenv('RSS_FEED_URLS', '').split(',') if url.strip()]

PROCESSED_LINKS_FILE = 'processed_links.txt'

def load_processed_links():
    try:
        with open(PROCESSED_LINKS_FILE, 'r') as f:
            return set(line.strip() for line in f)
    except FileNotFoundError:
        return set()

def save_processed_links(links):
    links_list = list(links)
    recent_links = links_list[-100:] if len(links_list) > 100 else links_list
    with open(PROCESSED_LINKS_FILE, 'w') as f:
        for link in recent_links:
            f.write(link + '\n')

def translate_text(text):
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            'client': 'gtx',
            'sl': 'auto',
            'tl': 'ru',
            'dt': 't',
            'q': text
        }
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return ''.join([item[0] for item in data[0] if item[0]])
        return text
    except:
        return text

def get_hashtag(rss_url):
    try:
        from urllib.parse import urlparse
        domain = urlparse(rss_url).netloc.replace('www.', '').split('.')[0]
        return f"#{domain}"
    except:
        return "#news"

def format_message(entry, rss_url):
    translated_title = translate_text(entry.title)
    clickable_title = f"📰 [{translated_title}]({entry.link})"

    hashtag = get_hashtag(rss_url)
    if hasattr(entry, 'author') and entry.author:
        meta_line = f"👤 {entry.author} • {hashtag}"
    else:
        meta_line = f"🏷️ {hashtag}"

    return f"{clickable_title}\n{meta_line}"

def send_to_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': CHANNEL_ID,
        'text': message,
        'parse_mode': 'Markdown',
        'disable_web_page_preview': False
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except:
        return False

def check_single_feed(rss_url, processed_links):
    try:
        feed = feedparser.parse(rss_url)
        if not feed.entries:
            return processed_links, 0

        new_count = 0
        for entry in feed.entries:
            if entry.link not in processed_links:
                message = format_message(entry, rss_url)
                if send_to_telegram(message):
                    processed_links.add(entry.link)
                    new_count += 1
                    time.sleep(1)
            else:
                break

        return processed_links, new_count
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return processed_links, 0

def rss_check_loop():
    while True:
        try:
            processed_links = load_processed_links()
            total_new = 0

            for rss_url in RSS_FEED_URLS:
                processed_links, new_entries = check_single_feed(rss_url, processed_links)
                total_new += new_entries

            save_processed_links(processed_links)
            logger.info(f"Новых записей: {total_new}")
            time.sleep(600)

        except Exception as e:
            logger.error(f"Ошибка: {e}")
            time.sleep(60)

@app.route('/')
def home():
    return 'RSS Bot is running!'

@app.route('/health')
def health():
    return 'OK'

@app.before_first_request
def start_rss_checker():
    if not hasattr(app, 'rss_thread_started'):
        thread = Thread(target=rss_check_loop)
        thread.daemon = True
        thread.start()
        app.rss_thread_started = True

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
