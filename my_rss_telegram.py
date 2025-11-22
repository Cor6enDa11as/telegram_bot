#!/usr/bin/env python3
import feedparser
import time
import requests
import re
import html
from datetime import datetime
import os

# =============================================================================
# НАСТРОЙКИ
# =============================================================================


TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')

RSS_SOURCES = [
    "https://habr.com/ru/rss/hubs/linux_dev/articles/?fl=ru",
    "https://habr.com/ru/rss/hubs/linux/articles/?fl=ru",
    "https://habr.com/ru/rss/hubs/popular_science/articles/?fl=ru",
    "https://habr.com/ru/rss/hubs/astronomy/articles/?fl=ru",
    "https://habr.com/ru/rss/hubs/futurenow/articles/?fl=ru",
    "https://habr.com/ru/rss/flows/popsci/articles/?fl=ru",
    "https://4pda.to/feed/",
    "https://tech.onliner.by/feed",
    "https://www.ixbt.com/export/hardnews.rss",
    "https://www.ixbt.com/export/sec_mobile.rss",
    "https://www.ixbt.com/export/sec_cpu.rss",
    "https://www.ixbt.com/export/applenews.rss",
    "https://www.ixbt.com/export/softnews.rss",
    "https://www.ixbt.com/export/sec_peripheral.rss",
    "http://androidinsider.ru/feed"
]

# =============================================================================
# ФУНКЦИИ ПЕРЕВОДА
# =============================================================================

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

    # Заголовок
    processed_title = title
    if not is_russian_text(title):
        translated_title = translate_text(title)
        if translated_title and translated_title != title:
            processed_title = translated_title
            was_translated = True

    # Описание
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

# =============================================================================
# ФУНКЦИИ ДЛЯ КАРТИНОК
# =============================================================================

def extract_image_from_entry(entry):
    try:
        if hasattr(entry, 'links'):
            for link in entry.links:
                if 'image' in link.type:
                    return link.href

        if hasattr(entry, 'summary'):
            img_match = re.search(r'<img[^>]+src="([^">]+)"', entry.summary)
            if img_match:
                return img_match.group(1)

        if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
            return entry.media_thumbnail[0]['url']

    except Exception as e:
        print(f"💥 Ошибка поиска картинки: {e}")

    return None

# =============================================================================
# ФУНКЦИЯ ОТПРАВКИ В TELEGRAM
# =============================================================================

def send_to_telegram(title, description, link, source_name, pub_date, image_url=None, was_translated=False):
    try:
        message = f"📰 **{source_name}**\n"
        message += f"📅 **{pub_date}**\n\n"

        if was_translated:
            message += "🔤 *[Переведено]*\n\n"

        message += f"**{title}**\n\n"

        if description:
            message += f"{description}\n\n"

        message += f"🔗 [Читать полностью]({link})"

        if image_url:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            data = {
                'chat_id': TELEGRAM_CHANNEL_ID,
                'photo': image_url,
                'caption': message,
                'parse_mode': 'Markdown'
            }
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data = {
                'chat_id': TELEGRAM_CHANNEL_ID,
                'text': message,
                'parse_mode': 'Markdown',
                'disable_web_page_preview': False
            }

        response = requests.post(url, data=data, timeout=10)

        if response.status_code == 200:
            print(f"✅ Отправлено: {title[:50]}...")
            return True
        else:
            print(f"❌ Ошибка: {response.status_code}")
            return False

    except Exception as e:
        print(f"💥 Ошибка отправки: {e}")
        return False

# =============================================================================
# ОСНОВНАЯ ФУНКЦИЯ (ОДНА ПРОВЕРКА)
# =============================================================================

def main():
    print("🚀 Запуск проверки новостей...")
    print(f"📊 Источников: {len(RSS_SOURCES)}")

    # Словарь для хранения последних ссылок (в памяти)
    last_links = {}

    try:
        # Читаем последние ссылки из файла (если есть)
        try:
            with open('last_links.txt', 'r') as f:
                for line in f:
                    if ':' in line:
                        url, link = line.strip().split(':', 1)
                        last_links[url] = link
        except FileNotFoundError:
            print("📝 Файл last_links.txt не найден, создаем новый...")

        new_links = {}

        for url in RSS_SOURCES:
            try:
                feed = feedparser.parse(url)
                if not feed.entries:
                    continue

                latest = feed.entries[0]
                link = latest.link

                # Проверяем есть ли новая новость
                if url in last_links and last_links[url] == link:
                    continue

                print(f"🎉 Новая новость: {feed.feed.title}")

                # Дата
                if hasattr(latest, 'published_parsed') and latest.published_parsed:
                    pub_date = datetime(*latest.published_parsed[:6])
                    formatted_date = pub_date.strftime("%d.%m.%Y %H:%M")
                else:
                    formatted_date = "Дата неизвестна"

                # Контент с переводом
                news_title = latest.title
                news_description = latest.description if hasattr(latest, 'description') else ""

                processed_title, processed_description, was_translated = prepare_news_content(
                    news_title, news_description
                )

                # Картинка
                image_url = extract_image_from_entry(latest)

                # Источник
                source_name = feed.feed.title if hasattr(feed.feed, 'title') else url

                # Отправляем в Telegram
                send_to_telegram(
                    processed_title,
                    processed_description,
                    link,
                    source_name,
                    formatted_date,
                    image_url,
                    was_translated
                )

                new_links[url] = link
                time.sleep(2)  # Пауза между отправками

            except Exception as e:
                print(f"💥 Ошибка: {url} - {e}")

        # Сохраняем новые ссылки
        if new_links:
            with open('last_links.txt', 'w') as f:
                for url, link in new_links.items():
                    f.write(f"{url}:{link}\n")
            print(f"💾 Сохранено {len(new_links)} новых ссылок")
        else:
            print("📭 Новых новостей нет")

    except Exception as e:
        print(f"💥 Критическая ошибка: {e}")

if __name__ == "__main__":
    main()
