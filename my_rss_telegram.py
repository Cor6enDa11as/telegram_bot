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
    from selenium.webdriver.support import expected_conditions as EC
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

# Словарь для отслеживания последних N ссылок (например, 10)
last_links = {}
MAX_TRACKED = 10

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

        # Ждем, пока страница полностью загрузится (включая выполнение JS)
        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )

        # Возвращаем уже обработанный HTML/XML-код страницы (после выполнения JS)
        return driver.page_source

    finally:
        driver.quit()

def get_first_link(entry):
    """Извлекает первую валидную ссылку из entry.link (может быть строкой или списком)"""
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
    """Форматирует сообщение: заголовок статьи как ссылка + скрытый URL для превью"""
    link = get_first_link(entry)
    if not link:
        logger.warning(f"Пропущена новость без ссылки из {rss_url}")
        return None

    # Попробуем получить заголовок статьи
    title = getattr(entry, 'title', 'Новая статья').strip()
    if not title:
        title = 'Новая статья'

    # Форматируем как Markdown: [Заголовок](URL)
    # Telegram покажет "Заголовок" как кликабельную ссылку
    # и сгенерирует превью
    return f'[{title}]({link})'

def send_to_telegram(message):
    """Отправляет сообщение в Telegram"""
    if not message:
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        'chat_id': CHANNEL_ID,
        'text': message,
        'parse_mode': 'Markdown',  # 🔥 Обязательно для Markdown-ссылки
        'disable_web_page_preview': False,  # 🔥 Обязательно False, чтобы превью генерировалось
        'disable_notification': False
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            logger.error(f"Telegram API error: {response.text}")
        return response.status_code == 200
    except Exception as e:
        logger.exception("Ошибка отправки в Telegram")
        return False

def rss_check_loop():
    """Главный цикл мониторинга"""
    global last_links

    logger.info("🚀 Запуск RSS бота")

    # Инициализация
    for url in RSS_FEED_URLS:
        try:
            feed = robust_parse_feed(url)
            if feed and feed.entries:
                # Сохраняем первые N ссылок (или все, если их меньше)
                links = []
                for entry in feed.entries[:MAX_TRACKED]:
                    link = get_first_link(entry)
                    if link:
                        links.append(link)
                last_links[url] = links
                logger.info(f"✅ Инициализирована: {urlparse(url).netloc} | Сохранено {len(links)} статей")
            else:
                logger.warning(f"⚠️ Пустая лента: {url}")
        except Exception as e:
            logger.exception(f"Ошибка инициализации {url}")

    logger.info(f"✅ Отслеживается {len(last_links)} лент")
    time.sleep(900)

    while True:
        for url in RSS_FEED_URLS:
            try:
                feed = robust_parse_feed(url)
                if not feed or not feed.entries:
                    continue

                # Получаем список текущих ссылок (только первые MAX_TRACKED)
                current_links = []
                for entry in feed.entries[:MAX_TRACKED]:
                    link = get_first_link(entry)
                    if link:
                        current_links.append(link)

                # Получаем список "уже виденных" ссылок
                seen_links = last_links.get(url, [])

                # Находим новые статьи (которых не было в предыдущем списке)
                # Важно: порядок в current_links - от новых к старым
                new_links = []
                for link in current_links:
                    if link not in seen_links:
                        new_links.append(link)

                # Отправляем новые статьи в порядке от новой к старой
                for link in new_links:  # уже от новых к старым, как в current_links
                    logger.info(f"🎉 Новая новость: {urlparse(url).netloc} | {link}")

                    # Находим entry для форматирования
                    entry = next((e for e in feed.entries if get_first_link(e) == link), None)
                    if not entry:
                        continue

                    message = format_message(entry, url)
                    if message and send_to_telegram(message):
                        # Обновляем список "уже виденных" ссылок
                        # Вставляем новую ссылку в начало
                        seen_links.insert(0, link)
                        # Убираем дубликаты (если вдруг), оставляем только первые MAX_TRACKED
                        seen_links = list(dict.fromkeys(seen_links))[:MAX_TRACKED]
                        last_links[url] = seen_links
                        # ✅ Задержка 10-15 сек между отправками
                        delay = random.randint(10, 15)
                        logger.info(f"⏳ Задержка {delay} сек перед следующей отправкой...")
                        time.sleep(delay)
                    else:
                        logger.error(f"❌ Не удалось отправить новость из {url}")

            except Exception as e:
                logger.exception(f"Ошибка при обработке {url}")

        logger.info("✅ Цикл проверки завершён")
        time.sleep(900)

@app.route('/')
def home():
    return '✅ RSS Bot is running!'

if __name__ == '__main__':
    logger.info(f"📡 Отслеживается {len(RSS_FEED_URLS)} RSS-лент")

    Thread(target=rss_check_loop, daemon=True).start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
