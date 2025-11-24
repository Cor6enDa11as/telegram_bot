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

app = Flask(__name__)

# =============================================================================
# НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ
# =============================================================================

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
    print("❌ Ошибка: Не установлены TELEGRAM_BOT_TOKEN или TELEGRAM_CHANNEL_ID")
    exit(1)

# =============================================================================
# RSS ЛЕНТЫ С ХЭШТЕГАМИ
# =============================================================================

RSS_SOURCES = [
    {"url": "https://habr.com/ru/rss/hubs/linux_dev/articles/?fl=ru", "hashtag": "#linux"},
    {"url": "https://habr.com/ru/rss/hubs/linux/articles/?fl=ru", "hashtag": "#linux"},
    {"url": "https://habr.com/ru/rss/hubs/popular_science/articles/?fl=ru", "hashtag": "#наука"},
    {"url": "https://habr.com/ru/rss/hubs/astronomy/articles/?fl=ru", "hashtag": "#астрономия"},
    {"url": "https://habr.com/ru/rss/hubs/futurenow/articles/?fl=ru", "hashtag": "#технологии"},
    {"url": "https://habr.com/ru/rss/flows/popsci/articles/?fl=ru", "hashtag": "#наука"},
    {"url": "https://4pda.to/feed/", "hashtag": "#мобильные", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    {"url": "https://tech.onliner.by/feed", "hashtag": "#технологии"},
    {"url": "https://www.ixbt.com/export/hardnews.rss", "hashtag": "#железо"},
    {"url": "https://www.ixbt.com/export/sec_mobile.rss", "hashtag": "#мобильные"},
    {"url": "https://www.ixbt.com/export/sec_cpu.rss", "hashtag": "#процессоры"},
    {"url": "https://www.ixbt.com/export/applenews.rss", "hashtag": "#apple"},
    {"url": "https://www.ixbt.com/export/softnews.rss", "hashtag": "#софт"},
    {"url": "https://www.ixbt.com/export/sec_peripheral.rss", "hashtag": "#периферия"},
    {"url": "https://androidinsider.ru/feed", "hashtag": "#android"}
]

# =============================================================================
# КЭШ ИКОНОК
# =============================================================================

favicon_cache = {}

def get_favicon_url(domain):
    """Получает URL favicon для домена с улучшенной проверкой"""
    if domain in favicon_cache:
        return favicon_cache[domain]

    favicon_urls = [
        f"https://{domain}/favicon.ico",
        f"https://www.{domain}/favicon.ico",
        f"https://{domain}/favicon.png",
        f"https://www.{domain}/favicon.png",
        f"https://{domain}/apple-touch-icon.png",
        f"https://www.{domain}/apple-touch-icon.png",
        f"https://{domain}/apple-touch-icon-precomposed.png",
        f"https://www.{domain}/apple-touch-icon-precomposed.png",
    ]

    for url in favicon_urls:
        try:
            response = requests.head(url, timeout=5)
            if response.status_code == 200:
                content_type = response.headers.get('content-type', '').lower()
                if any(img_type in content_type for img_type in ['image/png', 'image/jpeg', 'image/x-icon', 'image/vnd.microsoft.icon']):
                    print(f"✅ Найден favicon для {domain}: {url}")
                    favicon_cache[domain] = url
                    return url
        except Exception as e:
            continue

    print(f"❌ Favicon не найден для {domain}")
    favicon_cache[domain] = None
    return None

def download_and_validate_favicon(favicon_url):
    """Скачивает и проверяет favicon"""
    try:
        response = requests.get(favicon_url, timeout=10)
        if response.status_code == 200:
            # Проверяем размер файла (не должен быть слишком маленьким или большим)
            content_length = len(response.content)
            if 100 <= content_length <= 50000:  # от 100 байт до 50 КБ
                # Проверяем что это изображение
                if response.content[:4] in [b'\x89PNG', b'\xff\xd8\xff', b'GIF8', b'RIFF'] or response.content[:3] == b'\xff\xd8\xff':
                    return response.content
                # Для .ico файлов
                elif favicon_url.endswith('.ico') and content_length > 0:
                    return response.content
        return None
    except Exception as e:
        print(f"💥 Ошибка загрузки favicon: {e}")
        return None

def get_site_icon(source_name, url):
    """Возвращает эмодзи для сайта (fallback)"""
    domain_icons = {
        'habr.com': '🐧',
        '4pda.to': '📱',
        'ixbt.com': '💻',
        'onliner.by': '🏠',
        'androidinsider.ru': '🤖',
    }

    domain = urlparse(url).netloc
    for site_domain, icon in domain_icons.items():
        if site_domain in domain:
            return icon

    return '📰'

# =============================================================================
# ФУНКЦИИ
# =============================================================================

def parse_feed_with_retry(url, user_agent=None):
    """Парсит RSS с повторными попытками и кастомным User-Agent"""
    headers = {}
    if user_agent:
        headers['User-Agent'] = user_agent

    try:
        if headers:
            response = requests.get(url, headers=headers, timeout=10)
            content = response.content
            feed = feedparser.parse(content)
        else:
            feed = feedparser.parse(url)

        return feed
    except Exception as e:
        print(f"💥 Ошибка парсинга {url}: {e}")
        # Попробуем без кастомного User-Agent
        try:
            feed = feedparser.parse(url)
            return feed
        except Exception as e2:
            print(f"💥 Вторая ошибка парсинга {url}: {e2}")
            return feedparser.parse("")  # Возвращаем пустой фид

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
            return text
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
            return response.json()[0][0][0]
        return text
    except Exception as e:
        print(f"💥 Ошибка перевода: {e}")
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
        if len(clean_desc) > 300:
            clean_desc = clean_desc[:300] + "..."
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
    """Улучшенный поиск картинок в RSS записи"""
    try:
        # 1. Проверяем медиа-контент
        if hasattr(entry, 'links'):
            for link in entry.links:
                if 'image' in link.type:
                    return link.href
                if hasattr(link, 'rel') and 'icon' in link.rel:
                    return link.href

        # 2. Проверяем summary/content на наличие img тегов
        content_fields = ['summary', 'content', 'description']
        for field in content_fields:
            if hasattr(entry, field):
                content = getattr(entry, field)
                if isinstance(content, list):
                    content = content[0].value if content else ""

                img_match = re.search(r'<img[^>]+src="([^">]+)"', content)
                if img_match:
                    img_url = img_match.group(1)
                    # Проверяем что это действительно картинка
                    if any(ext in img_url.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
                        return img_url

        # 3. Проверяем медиа-thumbnail
        if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
            return entry.media_thumbnail[0]['url']

        # 4. Для Habr: пытаемся найти картинку в содержании
        if hasattr(entry, 'content') and entry.content:
            for content_item in entry.content:
                if hasattr(content_item, 'value'):
                    img_match = re.search(r'<img[^>]+src="([^">]+)"', content_item.value)
                    if img_match:
                        img_url = img_match.group(1)
                        if any(ext in img_url.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
                            return img_url

    except Exception as e:
        print(f"💥 Ошибка поиска картинки: {e}")

    return None

def send_split_news(title, description, link, source_name, pub_date, image_url=None, was_translated=False, hashtag=""):
    """Отправляет новость в двух сообщениях с настоящей иконкой сайта"""
    try:
        domain = urlparse(link).netloc
        favicon_url = get_favicon_url(domain)

        # 🔷 СООБЩЕНИЕ 1: Заголовок с иконкой сайта
        message1 = f"<b>{source_name}</b>\n\n<b>{title}</b>\n\n🔗 {link}"

        if favicon_url:
            # Пробуем отправить favicon
            try:
                # Скачиваем и проверяем favicon
                favicon_data = download_and_validate_favicon(favicon_url)
                if favicon_data:
                    # Отправляем как файл
                    files = {'photo': ('favicon.png', favicon_data, 'image/png')}
                    data = {
                        'chat_id': TELEGRAM_CHANNEL_ID,
                        'caption': message1,
                        'parse_mode': 'HTML'
                    }

                    url1 = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
                    response1 = requests.post(url1, files=files, data=data, timeout=10)

                    if response1.status_code == 200:
                        print(f"   ✅ Favicon отправлен успешно (как файл)")
                    else:
                        # Если не получилось, пробуем по URL
                        raise Exception(f"Favicon file upload failed: {response1.status_code}")
                else:
                    raise Exception("Favicon validation failed")

            except Exception as e:
                print(f"   ⚠️ Не удалось отправить favicon как файл: {e}")

                # Пробуем отправить по URL (оригинальный метод)
                try:
                    url1 = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
                    data1 = {
                        'chat_id': TELEGRAM_CHANNEL_ID,
                        'photo': favicon_url,
                        'caption': message1,
                        'parse_mode': 'HTML'
                    }

                    response1 = requests.post(url1, data=data1, timeout=10)
                    if response1.status_code == 200:
                        print(f"   ✅ Favicon отправлен успешно (по URL)")
                    else:
                        raise Exception(f"URL upload failed: {response1.status_code}")

                except Exception as e2:
                    print(f"   ⚠️ Не удалось отправить favicon по URL: {e2}")
                    # Fallback: текстовое сообщение с эмодзи
                    icon = get_site_icon(source_name, link)
                    message1_fallback = f"{icon} <b>{source_name}</b>\n\n<b>{title}</b>\n\n🔗 {link}"
                    url1 = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                    data1 = {
                        'chat_id': TELEGRAM_CHANNEL_ID,
                        'text': message1_fallback,
                        'parse_mode': 'HTML',
                        'disable_web_page_preview': True
                    }
                    response1 = requests.post(url1, data=data1, timeout=10)
        else:
            # Fallback: текстовое сообщение с эмодзи
            icon = get_site_icon(source_name, link)
            message1_fallback = f"{icon} <b>{source_name}</b>\n\n<b>{title}</b>\n\n🔗 {link}"
            url1 = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data1 = {
                'chat_id': TELEGRAM_CHANNEL_ID,
                'text': message1_fallback,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True
            }
            response1 = requests.post(url1, data=data1, timeout=10)

        if response1.status_code != 200:
            print(f"❌ Ошибка отправки заголовка: {response1.status_code} - {response1.text}")
            return False

        # Получаем ID первого сообщения для ответа
        message_id = response1.json()['result']['message_id']

        # 🔷 СООБЩЕНИЕ 2: Контент (картинка новости + описание + дата + хэштег)
        message2 = ""
        if was_translated:
            message2 += "🔤 <i>[Переведено]</i>\n\n"

        if description:
            message2 += f"<i>{description}</i>\n\n"

        message2 += f"📅 {pub_date}\n\n"

        # Добавляем хэштег
        if hashtag:
            message2 += f"<code>{hashtag}</code>"

        # Проверяем длину сообщения для Telegram API
        if len(message2) > 1024:
            message2 = message2[:1000] + "..."

        # Отправляем второе сообщение
        if image_url:
            # Проверяем валидность URL картинки
            try:
                # С картинкой новости
                url2 = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
                data2 = {
                    'chat_id': TELEGRAM_CHANNEL_ID,
                    'photo': image_url,
                    'caption': message2,
                    'parse_mode': 'HTML',
                    'reply_to_message_id': message_id
                }

                response2 = requests.post(url2, data=data2, timeout=10)
                if response2.status_code != 200:
                    print(f"   ⚠️ Не удалось отправить с картинкой: {response2.text}")
                    # Fallback: отправляем без картинки
                    raise Exception("Image send failed")

            except Exception as e:
                print(f"   ⚠️ Ошибка отправки картинки: {e}")
                # Fallback: отправляем текстовое сообщение
                url2 = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                data2 = {
                    'chat_id': TELEGRAM_CHANNEL_ID,
                    'text': message2,
                    'parse_mode': 'HTML',
                    'reply_to_message_id': message_id,
                    'disable_web_page_preview': True
                }
                response2 = requests.post(url2, data=data2, timeout=10)
        else:
            # Без картинки новости
            url2 = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data2 = {
                'chat_id': TELEGRAM_CHANNEL_ID,
                'text': message2,
                'parse_mode': 'HTML',
                'reply_to_message_id': message_id,
                'disable_web_page_preview': True
            }
            response2 = requests.post(url2, data=data2, timeout=10)

        # Небольшая задержка между сообщениями
        time.sleep(0.5)

        if response2.status_code == 200:
            print(f"✅ Отправлено раздельное сообщение: {title[:50]}... {hashtag}")
            return True
        else:
            print(f"❌ Ошибка отправки контента: {response2.status_code} - {response2.text}")
            return False

    except Exception as e:
        print(f"💥 Ошибка отправки раздельного сообщения: {e}")
        return False

# =============================================================================
# ОСНОВНОЙ ЦИКЛ БОТА
# =============================================================================

def run_bot():
    last_links = {}

    print("🚀 Бот запущен и начинает мониторинг...")
    print(f"📊 Источников: {len(RSS_SOURCES)}")

    # Первая инициализация
    for source in RSS_SOURCES:
        url = source["url"]
        hashtag = source["hashtag"]
        user_agent = source.get("user_agent")

        try:
            print(f"🔧 Инициализация: {url} {hashtag}")
            feed = parse_feed_with_retry(url, user_agent)

            if feed.entries:
                last_links[url] = feed.entries[0].link
                print(f"✅ Инициализирован: {url} {hashtag}")
                print(f"   Первая новость: {feed.entries[0].title[:50]}...")
                print(f"   Количество новостей: {len(feed.entries)}")
            else:
                print(f"⚠️  Нет новостей в ленте: {url} {hashtag}")
                print(f"   Статус фида: {feed.get('status', 'N/A')}")
                if feed.get('bozo'):
                    print(f"   Ошибка парсинга: {feed.bozo_exception}")
        except Exception as e:
            print(f"💥 Ошибка инициализации {url}: {e}")

    print(f"📝 Всего инициализировано лент: {len(last_links)}")

    # Бесконечный цикл проверки
    while True:
        try:
            print(f"\n🔍 Начинаю проверку всех источников... ({datetime.now().strftime('%H:%M:%S')})")

            found_new_news = False

            for source in RSS_SOURCES:
                url = source["url"]
                hashtag = source["hashtag"]
                user_agent = source.get("user_agent")

                try:
                    print(f"📡 Проверяю: {url} {hashtag}")
                    feed = parse_feed_with_retry(url, user_agent)

                    if not feed.entries:
                        print(f"   ⚠️ Нет новостей в фиде")
                        if feed.get('bozo'):
                            print(f"   Ошибка парсинга: {feed.bozo_exception}")
                        continue

                    latest = feed.entries[0]
                    link = latest.link

                    print(f"   Последняя новость: {latest.title[:50]}...")
                    print(f"   Всего новостей в фиде: {len(feed.entries)}")

                    # Проверяем есть ли уже эта ссылка
                    if url not in last_links:
                        print(f"   🆕 Первая проверка, сохраняем ссылку")
                        last_links[url] = link
                    elif last_links[url] != link:
                        print(f"   🎉 ОБНАРУЖЕНА НОВАЯ НОВОСТЬ! {hashtag}")
                        found_new_news = True

                        # Дата
                        if hasattr(latest, 'published_parsed') and latest.published_parsed:
                            pub_date = datetime(*latest.published_parsed[:6])
                            formatted_date = pub_date.strftime("%d.%m.%Y %H:%M")
                        elif hasattr(latest, 'updated_parsed') and latest.updated_parsed:
                            pub_date = datetime(*latest.updated_parsed[:6])
                            formatted_date = pub_date.strftime("%d.%m.%Y %H:%M")
                        else:
                            formatted_date = "Дата неизвестна"

                        # Контент с переводом
                        news_title = latest.title
                        news_description = latest.description if hasattr(latest, 'description') else ""

                        processed_title, processed_description, was_translated = prepare_news_content(
                            news_title, news_description
                        )

                        # Картинка новости (улучшенный поиск)
                        image_url = extract_image_from_entry(latest)
                        if image_url:
                            print(f"   🖼️ Найдена картинка новости: {image_url}")
                        else:
                            print(f"   📄 Картинка новости не найдена")

                        # Источник
                        source_name = feed.feed.title if hasattr(feed.feed, 'title') else urlparse(url).netloc

                        # Отправляем в Telegram (РАЗДЕЛЬНОЕ СООБЩЕНИЕ)
                        print(f"   📤 Отправляю раздельное сообщение с иконкой сайта...")
                        success = send_split_news(
                            processed_title,
                            processed_description,
                            link,
                            source_name,
                            formatted_date,
                            image_url,
                            was_translated,
                            hashtag
                        )

                        if success:
                            last_links[url] = link
                            print(f"   ✅ Успешно отправлено {hashtag}")
                        else:
                            print(f"   ❌ Ошибка отправки {hashtag}")
                    else:
                        print(f"   ✅ Новостей нет {hashtag}")

                except Exception as e:
                    print(f"💥 Ошибка при проверке {url}: {e}")

            if not found_new_news:
                print(f"📭 Новых новостей не найдено в этой проверке")

            print(f"⏰ Ожидание 15 минут... ({datetime.now().strftime('%H:%M:%S')})")
            time.sleep(900)  # 15 минут

        except Exception as e:
            print(f"💥 Критическая ошибка в основном цикле: {e}")
            print("🔄 Перезапуск через 60 секунд...")
            time.sleep(60)

# =============================================================================
# МИНИМАЛЬНЫЙ FLASK ДЛЯ ПОРТА
# =============================================================================

@app.route('/')
def home():
    return "🤖 Telegram RSS Bot работает!"

@app.route('/ping')
def ping():
    return "pong"

# Запускаем бот в отдельном потоке
bot_thread = threading.Thread(target=run_bot)
bot_thread.daemon = True
bot_thread.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
