#!/usr/bin/env python3
import feedparser
import time
import requests
import re
import html
from datetime import datetime
import os
from flask import Flask
import threading
from urllib.parse import urlparse
from bs4 import BeautifulSoup

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
    print("❌ Ошибка: Не установлены TELEGRAM_BOT_TOKEN или TELEGRAM_CHANNEL_ID")
    exit(1)

RSS_SOURCES = [
    {"url": "https://habr.com/ru/rss/hubs/linux_dev/articles/?fl=ru", "hashtag": "#linux"},
    {"url": "https://habr.com/ru/rss/hubs/linux/articles/?fl=ru", "hashtag": "#linux"},
    {"url": "https://habr.com/ru/rss/hubs/popular_science/articles/?fl=ru", "hashtag": "#наука"},
    {"url": "https://habr.com/ru/rss/hubs/astronomy/articles/?fl=ru", "hashtag": "#астрономия"},
    {"url": "https://habr.com/ru/rss/flows/popsci/articles/?fl=ru", "hashtag": "#наука"},
    {"url": "https://4pda.to/feed/", "hashtag": "#мобильные"},
    {"url": "https://tech.onliner.by/feed", "hashtag": "#технологии"},
    {"url": "https://www.ixbt.com/export/news.rss", "hashtag": "#технологии"},
    {"url": "https://androidinsider.ru/feed", "hashtag": "#android"},
    {"url": "https://naked-science.ru/feed", "hashtag": "#наука"},
    {"url": "https://www.opennet.ru/opennews/opennews_full_utf.rss", "hashtag": "#linux"},
    {"url": "https://www.comss.ru/linux.php", "hashtag": "#linux"},
    {"url": "https://www.linux.org.ru/section-rss.jsp?section=1", "hashtag": "#linux"},
    {"url": "https://www.phoronix.com/rss.php", "hashtag": "#linux"},
    {"url": "https://linuxiac.com/feed/", "hashtag": "#linux"},
    {"url": "https://www.linuxinsider.com/rss-feed", "hashtag": "#linux"},
    {"url": "https://distrowatch.com/news/dw.xml", "hashtag": "#linux"},
    {"url": "https://9to5linux.com/feed/", "hashtag": "#linux"},
    {"url": "https://www.gamingonlinux.com/article_rss.php", "hashtag": "#linux"},
    {"url": "https://itsfoss.com/feed/", "hashtag": "#linux"},
    {"url": "https://www.omgubuntu.co.uk/feed/", "hashtag": "#linux"},
    {"url": "https://rozetked.me/rss.xml", "hashtag": "#технологии"},
    {"url": "https://mobile-review.com/all/news/feed/", "hashtag": "#android"},
    {"url": "https://droider.ru/feed", "hashtag": "#технологии"},
    {"url": "https://www.comss.ru/linux.php", "hashtag": "#linux"},
    {"url": "https://rss-bridge.org/bridge01/?action=display&bridge=YouTubeFeedExpanderBridge&channel=UCt75WMud0RMUivGBNzvBPXQ&embed=on&format=Mrss" , "hashtag": "#Польза NET"},
    {"url": "https://rss-bridge.org/bridge01/?action=display&bridge=TelegramBridge&username=%40prohitec&format=Mrss" , "hashtag": "#PRO Hi-Tech"},
]

def parse_feed(url):
    """Парсит RSS с улучшенной обработкой ошибок"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/rss+xml, application/xml, text/xml'
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        content = response.content

        if any(site in url for site in ['4pda.to', 'ixbt.com']):
            try:
                content = content.decode('windows-1251').encode('utf-8')
            except:
                pass

        feed = feedparser.parse(content)

        if feed.bozo and feed.entries:
            print(f"   ⚠️ Есть ошибки парсинга, но новости найдены: {feed.bozo_exception}")
            return feed
        elif feed.entries:
            return feed
        else:
            print(f"   ❌ Нет новостей в фиде")
            return feedparser.parse("")

    except Exception as e:
        print(f"💥 Ошибка парсинга {url}: {e}")
        return feedparser.parse("")

def is_russian_text(text):
    if not text:
        return False
    cyrillic_count = sum(1 for char in text if '\u0400' <= char <= '\u04FF')
    total_letters = sum(1 for char in text if char.isalpha())
    return (cyrillic_count / total_letters) > 0.3 if total_letters >= 3 else False

def translate_text(text):
    try:
        if not text or not text.strip():
            return text
        url = "https://translate.googleapis.com/translate_a/single"
        params = {'client': 'gtx', 'sl': 'auto', 'tl': 'ru', 'dt': 't', 'q': text}
        response = requests.get(url, params=params, timeout=10)
        return response.json()[0][0][0] if response.status_code == 200 else text
    except Exception:
        return text

def prepare_news_content(title, description):
    was_translated = False
    processed_title = title

    if not is_russian_text(title):
        translated_title = translate_text(title)
        if translated_title and translated_title != title:
            processed_title = translated_title
            was_translated = True

    processed_description = ""
    if description:
        clean_desc = re.sub('<[^<]+?>', '', description)
        clean_desc = html.unescape(clean_desc)
        clean_desc = re.sub(r'\s+', ' ', clean_desc).strip()
        clean_desc = re.sub(r'<[^>]*$', '', clean_desc)
        clean_desc = re.sub(r'^[^<]*>', '', clean_desc)

        if len(clean_desc) > 400:
            clean_desc = clean_desc[:400] + "..."
        if not is_russian_text(clean_desc) and clean_desc.strip():
            translated_desc = translate_text(clean_desc)
            if translated_desc and translated_desc != clean_desc:
                processed_description = translated_desc
                was_translated = True
            else:
                processed_description = clean_desc
        else:
            processed_description = clean_desc

    return processed_title, processed_description, was_translated

def extract_image_from_entry(entry):
    """Поиск картинок в RSS записи"""
    try:
        if hasattr(entry, 'links'):
            for link in entry.links:
                if 'image' in link.type:
                    return link.href
                if hasattr(link, 'rel') and 'enclosure' in link.rel:
                    if 'image' in getattr(link, 'type', ''):
                        return link.href

        if hasattr(entry, 'media_content'):
            for media in entry.media_content:
                if media.get('type', '').startswith('image/'):
                    return media['url']

        if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
            return entry.media_thumbnail[0]['url']

        content_fields = ['summary', 'content', 'description', 'content_encoded']
        for field in content_fields:
            if hasattr(entry, field):
                content = getattr(entry, field)
                if isinstance(content, list):
                    content = content[0].value if content else ""
                if content:
                    img_match = re.search(r'<img[^>]+src="([^">]+)"', content)
                    if img_match:
                        img_url = img_match.group(1)
                        if any(ext in img_url.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
                            return img_url

        if hasattr(entry, 'enclosures'):
            for enclosure in entry.enclosures:
                if 'image' in getattr(enclosure, 'type', ''):
                    return enclosure.href

    except Exception as e:
        print(f"💥 Ошибка поиска картинки в RSS: {e}")

    return None

def extract_image_from_page(link):
    """Парсит главную картинку со страницы новости"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml'
        }

        response = requests.get(link, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')

        # Сначала ищем в meta-тегах (самые надежные)
        meta_selectors = [
            'meta[property="og:image"]',
            'meta[name="twitter:image"]',
            'meta[property="og:image:url"]',
            'link[rel="image_src"]'
        ]

        for selector in meta_selectors:
            meta_tag = soup.select_one(selector)
            if meta_tag:
                image_url = meta_tag.get('content') or meta_tag.get('href')
                if image_url and image_url.startswith('http'):
                    # Проверяем что это действительно картинка
                    if any(ext in image_url.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
                        print(f"   🖼️ Найдена картинка в meta-тегах")
                        return image_url

        # Ищем главную картинку в контенте
        content_selectors = [
            'article img:first-of-type',
            '.content img:first-of-type',
            'main img:first-of-type',
            '.post-content img:first-of-type',
            '.article img:first-of-type',
            'img[class*="hero"]',
            'img[class*="main"]',
            'img[class*="featured"]',
            'img[class*="cover"]'
        ]

        for selector in content_selectors:
            img_tag = soup.select_one(selector)
            if img_tag:
                image_url = img_tag.get('src')
                if image_url and image_url.startswith('http'):
                    if any(ext in image_url.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
                        print(f"   🖼️ Найдена картинка в контенте: {selector}")
                        return image_url
                # Проверяем data-src для ленивой загрузки
                elif img_tag.get('data-src'):
                    image_url = img_tag.get('data-src')
                    if image_url.startswith('http'):
                        if any(ext in image_url.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
                            print(f"   🖼️ Найдена картинка в data-src: {selector}")
                            return image_url

        print(f"   ❌ Картинка не найдена на странице")
        return None

    except Exception as e:
        print(f"💥 Ошибка парсинга страницы {link}: {e}")
        return None

def get_news_image(entry, link):
    """Пытается получить картинку разными способами"""
    # 1. Сначала из RSS (быстрее)
    image_url = extract_image_from_entry(entry)
    if image_url:
        print(f"   🖼️ Картинка найдена в RSS")
        return image_url

    # 2. Если в RSS нет - парсим со страницы
    print(f"   🔍 Картинки в RSS нет, парсим страницу...")
    image_url = extract_image_from_page(link)
    if image_url:
        print(f"   🖼️ Картинка найдена на странице")
        return image_url

    # 3. Если всё равно нет - возвращаем None
    print(f"   ❌ Картинка не найдена ни в RSS, ни на странице")
    return None

def create_news_message(domain, title, description, link, pub_date, was_translated, hashtag):
    """Создает красивое сообщение с markdown разметкой"""
    message_parts = [
        f"🌐  {domain}",
        "",
        f"⚡  *{title}*",
    ]

    if description:
        message_parts.append("")
        message_parts.append(f"✨  _{description}_")

    message_parts.extend([
        "",
        f"🔗  [Читать]({link})",
        "",
        f"📅  {pub_date}",
    ])

    if hashtag:
        message_parts.append("")
        message_parts.append(f"🏷️ {hashtag}")

    if was_translated:
        message_parts.append("")
        message_parts.append("`🔤 [Переведено]`")

    return "\n".join(message_parts)

def send_news_message(title, description, link, pub_date, image_url=None, was_translated=False, hashtag=""):
    """Отправляет сообщение в Telegram"""
    try:
        domain = urlparse(link).netloc.replace('www.', '')
        message_text = create_news_message(domain, title, description, link, pub_date, was_translated, hashtag)

        # Всегда используем наш формат с картинкой если есть
        if image_url:
            # Пробуем отправить с картинкой
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            data = {
                'chat_id': TELEGRAM_CHANNEL_ID,
                'photo': image_url,
                'caption': message_text,
                'parse_mode': 'Markdown'
            }

            response = requests.post(url, data=data, timeout=10)

            # Если не получилось с картинкой, отправляем текстовое сообщение
            if response.status_code != 200:
                print(f"   ⚠️ Не удалось отправить с картинкой, пробую текстовое")
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                data = {
                    'chat_id': TELEGRAM_CHANNEL_ID,
                    'text': message_text,
                    'parse_mode': 'Markdown',
                    'disable_web_page_preview': True
                }
                response = requests.post(url, data=data, timeout=10)
        else:
            # Без картинки - текстовое сообщение
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data = {
                'chat_id': TELEGRAM_CHANNEL_ID,
                'text': message_text,
                'parse_mode': 'Markdown',
                'disable_web_page_preview': True
            }
            response = requests.post(url, data=data, timeout=10)

        if response.status_code == 200:
            print(f"✅ Отправлено: {title[:50]}... {hashtag}")
            return True
        else:
            print(f"❌ Ошибка отправки: {response.status_code}")
            return False

    except Exception as e:
        print(f"💥 Ошибка отправки: {e}")
        return False

def run_bot():
    last_links = {}
    print("🚀 Бот запущен...")

    for source in RSS_SOURCES:
        url, hashtag = source["url"], source["hashtag"]
        try:
            feed = parse_feed(url)
            if feed.entries:
                last_links[url] = feed.entries[0].link
                print(f"✅ {urlparse(url).netloc} {hashtag}")
            elif feed.bozo:
                print(f"⚠️  Ошибка парсинга {url}: {feed.bozo_exception}")
        except Exception as e:
            print(f"💥 Ошибка {url}: {e}")

    while True:
        try:
            print(f"\n🔍 Проверка... ({datetime.now().strftime('%H:%M:%S')})")
            found_new_news = False

            for source in RSS_SOURCES:
                url, hashtag = source["url"], source["hashtag"]

                try:
                    feed = parse_feed(url)
                    if not feed.entries:
                        continue

                    latest, link = feed.entries[0], feed.entries[0].link

                    if url not in last_links or last_links[url] != link:
                        if url in last_links:
                            print(f"🎉 НОВОСТЬ: {hashtag}")
                            found_new_news = True

                        pub_date = "Дата неизвестна"
                        if hasattr(latest, 'published_parsed') and latest.published_parsed:
                            pub_date = datetime(*latest.published_parsed[:6]).strftime("%d.%m.%Y %H:%M")

                        title, description, was_translated = prepare_news_content(
                            latest.title,
                            latest.description if hasattr(latest, 'description') else ""
                        )

                        # УЛУЧШЕННЫЙ ПОИСК КАРТИНКИ: сначала RSS, потом страница
                        image_url = get_news_image(latest, link)

                        if send_news_message(title, description, link, pub_date, image_url, was_translated, hashtag):
                            last_links[url] = link

                except Exception as e:
                    print(f"💥 Ошибка {url}: {e}")

            print(f"⏰ Ожидание 15 минут..." if not found_new_news else "✅ Новости отправлены")
            time.sleep(900)

        except Exception as e:
            print(f"💥 Критическая ошибка: {e}")
            time.sleep(60)

@app.route('/')
def home():
    return "🤖 Telegram RSS Bot работает!"

@app.route('/ping')
def ping():
    return "pong"

bot_thread = threading.Thread(target=run_bot)
bot_thread.daemon = True
bot_thread.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
