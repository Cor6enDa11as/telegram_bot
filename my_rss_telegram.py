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

# Словарь для отслеживания последних 20 ссылок
last_links = {}

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
        logger.info(f"📡 Пробуем получить RSS напрямую: {rss_url}")
        response = requests.get(rss_url, timeout=25, headers=headers)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        if feed and hasattr(feed, 'entries') and feed.entries:
            logger.info(f"✅ Успешно через requests: {rss_url}")
            return feed
    except Exception as e:
        logger.warning(f"❌ Ошибка через requests: {e}")

    # 2. Если не сработало и доступен Selenium — пробуем его
    if HAS_SELENIUM:
        try:
            logger.info(f"🤖 Пробуем получить RSS через Selenium: {rss_url}")
            page_source = fetch_with_selenium(rss_url)
            if page_source:
                feed = feedparser.parse(page_source)
                if feed and hasattr(feed, 'entries') and feed.entries:
                    logger.info(f"✅ Успешно через Selenium: {rss_url}")
                    return feed
        except Exception as e:
            logger.error(f"❌ Ошибка через Selenium: {e}")

    logger.error(f"❌ Все методы парсинга провалились для: {rss_url}")
    return None

def fetch_with_selenium(url):
    """Использование headless браузера для обхода Cloudflare и антибот систем"""
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

        # Ждем, пока страница полностью загрузится
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

def format_message(entry, rss_url):
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
        return response.status_code == 200
    except Exception as e:
        logger.error(f"❌ Ошибка отправки в Telegram: {e}")
        return False

def rss_check_loop():
    """Главный цикл мониторинга"""
    global last_links

    logger.info("🚀 Запуск RSS бота")

    # Инициализация - запоминаем текущие статьи при запуске
    logger.info("🔄 Инициализация лент...")
    for url in RSS_FEED_URLS:
        try:
            feed = robust_parse_feed(url)
            if feed and feed.entries:
                # Запоминаем только ссылки из последних 20 статей
                links = []
                for entry in feed.entries[:20]:
                    link = get_first_link(entry)
                    if link:
                        links.append(link)

                # Сохраняем В ОБРАТНОМ ПОРЯДКЕ - от новых к старым
                last_links[url] = links[::-1] if links else []
                logger.info(f"✅ Запомнено {len(links)} статей из {urlparse(url).netloc}")
            else:
                logger.warning(f"⚠️ Пустая лента: {url}")
                last_links[url] = []
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации {url}: {e}")
            last_links[url] = []

    logger.info(f"✅ Отслеживается {len(last_links)} лент")

    # Ждем 1 минуту перед началом мониторинга
    logger.info("⏳ Ожидание 1 минуту перед началом мониторинга...")
    time.sleep(60)

    while True:
        logger.info("🔍 Начинаю цикл проверки...")

        for url in RSS_FEED_URLS:
            try:
                logger.info(f"📰 Проверяю: {urlparse(url).netloc}")
                feed = robust_parse_feed(url)

                if not feed or not feed.entries:
                    continue

                # Получаем текущие ссылки (новые идут первыми в RSS)
                current_links = []
                for entry in feed.entries:
                    link = get_first_link(entry)
                    if link:
                        current_links.append(link)

                # Получаем сохраненные ссылки
                saved_links = last_links.get(url, [])

                # ПРОСТАЯ ЛОГИКА: находим новые статьи
                # Идем по текущим статьям от новых к старым
                new_links_sent = 0
                for link in current_links:
                    # Если ссылка уже есть в сохраненных - пропускаем
                    if link in saved_links:
                        continue

                    # Находим entry для этой ссылки
                    entry = None
                    for e in feed.entries:
                        if get_first_link(e) == link:
                            entry = e
                            break

                    if not entry:
                        continue

                    # Отправляем сообщение
                    message = format_message(entry, url)
                    if message and send_to_telegram(message):
                        logger.info(f"✅ Отправлено: {getattr(entry, 'title', 'Без заголовка')[:50]}...")
                        new_links_sent += 1

                        # Ждем 5-10 секунд между отправками
                        delay = random.randint(5, 10)
                        time.sleep(delay)
                    else:
                        logger.error(f"❌ Не удалось отправить новость из {url}")

                # Обновляем сохраненные ссылки: новые + старые (максимум 20)
                if new_links_sent > 0:
                    # Добавляем новые ссылки в начало (они новые)
                    all_links = current_links[:20]  # Берем только последние 20
                    last_links[url] = all_links
                    logger.info(f"📨 Отправлено {new_links_sent} новых статей из {urlparse(url).netloc}")

                # Небольшая задержка между проверкой разных лент
                time.sleep(3)

            except Exception as e:
                logger.error(f"❌ Ошибка при обработке {url}: {e}")
                time.sleep(5)

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
