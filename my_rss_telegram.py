#!/usr/bin/env python3
import os
import feedparser
import requests
from flask import Flask
from threading import Thread
import time
import logging
from dotenv import load_dotenv
from urllib.parse import urlparse
import random

# Попытка импорта Selenium
try:
    from selenium.webdriver import Chrome, ChromeOptions
    from selenium.webdriver.support.ui import WebDriverWait
    import undetected_chromedriver as uc
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False
    logging.warning("Selenium или undetected_chromedriver не установлены — обход Cloudflare недоступен")

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()
app = Flask(__name__)

# Конфигурация
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')
RSS_FEED_URLS = [url.strip() for url in os.getenv('RSS_FEED_URLS', '').split(',') if url.strip()]

if not all([BOT_TOKEN, CHANNEL_ID, RSS_FEED_URLS]):
    logger.error("❌ Отсутствуют необходимые переменные окружения!")
    exit(1)

# Словарь для отслеживания ВСЕХ виденных ссылок
seen_links = {}
MAX_SEEN_LINKS = 100  # Максимальное количество ссылок для хранения

def build_headers(rss_url):
    domain = urlparse(rss_url).netloc
    return {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
        'Accept': 'application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8',
        'Accept-Language': 'ru-RU,ru,en-US,en;q=0.9',
        'Referer': f'https://{domain}/',
        'Connection': 'keep-alive',
    }

def robust_parse_feed(rss_url):
    """Парсинг RSS: сначала requests, если ошибка — пробуем Selenium (если доступен)"""
    headers = build_headers(rss_url)

    # 1. Пробуем обычный requests
    try:
        response = requests.get(rss_url, timeout=25, headers=headers)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        if feed and hasattr(feed, 'entries') and feed.entries:
            logger.info(f"✅ Успешно получил RSS: {urlparse(rss_url).netloc}")
            return feed
    except Exception as e:
        logger.warning(f"❌ Ошибка через requests для {urlparse(rss_url).netloc}: {e}")

    # 2. Если не сработало и доступен Selenium — пробуем его
    if HAS_SELENIUM:
        try:
            logger.info(f"🤖 Пробуем через Selenium: {urlparse(rss_url).netloc}")
            page_source = fetch_with_selenium(rss_url)
            if page_source:
                feed = feedparser.parse(page_source)
                if feed and hasattr(feed, 'entries') and feed.entries:
                    logger.info(f"✅ Успешно через Selenium: {urlparse(rss_url).netloc}")
                    return feed
        except Exception as e:
            logger.error(f"❌ Ошибка через Selenium: {e}")

    logger.error(f"❌ Все методы провалились: {urlparse(rss_url).netloc}")
    return None

def fetch_with_selenium(url):
    """Использование headless браузера для обхода Cloudflare"""
    options = ChromeOptions()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    driver = uc.Chrome(options=options)

    try:
        driver.get(url)
        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        return driver.page_source
    finally:
        driver.quit()

def get_first_link(entry):
    """Извлекает первую валидную ссылку"""
    link = getattr(entry, 'link', None)
    if not link:
        return None
    if isinstance(link, list):
        for item in link:
            if item and str(item).startswith(('http://', 'https://')):
                return str(item).strip()
        return None
    elif str(link).startswith(('http://', 'https://')):
        return str(link).strip()
    return None

def format_message(entry):
    """Форматирует сообщение для Telegram"""
    link = get_first_link(entry)
    if not link:
        return None

    title = getattr(entry, 'title', 'Новая статья').strip() or 'Новая статья'
    return f'[{title}]({link})'

def send_to_telegram(message):
    """Отправляет сообщение в Telegram"""
    if not message:
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': CHANNEL_ID,
        'text': message,
        'parse_mode': 'Markdown',
        'disable_web_page_preview': False,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return True
        else:
            logger.error(f"❌ Telegram API error: {response.text}")
            return False
    except Exception as e:
        logger.error(f"❌ Ошибка отправки в Telegram: {e}")
        return False

def rss_check_loop():
    """Главный цикл мониторинга"""
    global seen_links

    logger.info("🚀 Запуск RSS бота")

    # Инициализация - запоминаем ВСЕ текущие статьи при запуске
    logger.info("🔄 Инициализация лент...")
    for url in RSS_FEED_URLS:
        domain = urlparse(url).netloc
        try:
            feed = robust_parse_feed(url)
            if feed and feed.entries:
                # Собираем ВСЕ ссылки из текущей ленты
                links_set = set()
                for entry in feed.entries:
                    link = get_first_link(entry)
                    if link:
                        links_set.add(link)

                seen_links[url] = list(links_set)  # Сохраняем как список для удобства
                logger.info(f"✅ Запомнено {len(links_set)} статей из {domain}")
            else:
                logger.warning(f"⚠️ Пустая лента при инициализации: {domain}")
                seen_links[url] = []
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации {domain}: {e}")
            seen_links[url] = []

    logger.info(f"✅ Отслеживается {len(seen_links)} лент")

    # Ждем 2 минуты перед началом мониторинга
    logger.info("⏳ Ожидание 2 минуты перед началом мониторинга...")
    time.sleep(120)

    while True:
        logger.info("🔍 Начинаю цикл проверки...")
        total_new = 0

        for url in RSS_FEED_URLS:
            domain = urlparse(url).netloc
            try:
                logger.info(f"📰 Проверяю: {domain}")
                feed = robust_parse_feed(url)

                if not feed or not feed.entries:
                    logger.warning(f"⚠️ Пустая лента или ошибка: {domain}")
                    continue

                # Получаем уже виденные ссылки для этой ленты
                already_seen = set(seen_links.get(url, []))
                new_entries = []

                # Проверяем статьи по порядку (обычно новые идут первыми)
                for entry in feed.entries:
                    link = get_first_link(entry)
                    if not link:
                        continue

                    # Если ссылка НОВАЯ (еще не виделась)
                    if link not in already_seen:
                        new_entries.append((entry, link))

                # Отправляем новые статьи
                sent_count = 0
                for entry, link in new_entries:
                    message = format_message(entry)
                    if message and send_to_telegram(message):
                        logger.info(f"✅ Отправлено из {domain}: {getattr(entry, 'title', 'Без заголовка')[:60]}")
                        sent_count += 1
                        total_new += 1

                        # Добавляем ссылку в уже виденные
                        already_seen.add(link)

                        # Задержка между отправками
                        delay = random.randint(8, 12)
                        time.sleep(delay)
                    else:
                        logger.error(f"❌ Не удалось отправить из {domain}")

                # Обновляем список виденных ссылок
                # Ограничиваем размер, чтобы не рос бесконечно
                seen_list = list(already_seen)
                if len(seen_list) > MAX_SEEN_LINKS:
                    seen_list = seen_list[-MAX_SEEN_LINKS:]  # Оставляем только последние

                seen_links[url] = seen_list

                if sent_count > 0:
                    logger.info(f"📨 Отправлено {sent_count} новых статей из {domain}")
                else:
                    logger.info(f"📭 Нет новых статей в {domain}")

                # Небольшая задержка между проверкой разных лент
                time.sleep(3)

            except Exception as e:
                logger.error(f"❌ Ошибка при обработке {domain}: {e}")
                time.sleep(5)

        logger.info(f"📊 Всего отправлено новых статей: {total_new}")
        logger.info("✅ Цикл проверки завершен")
        logger.info("⏳ Ожидание 15 минут до следующей проверки...")
        time.sleep(900)

@app.route('/')
def home():
    return '✅ RSS Bot is running!'

if __name__ == '__main__':
    logger.info(f"📡 Отслеживается {len(RSS_FEED_URLS)} RSS-лент")

    Thread(target=rss_check_loop, daemon=True).start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
