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

# Храним только последнюю проверенную ссылку для каждой ленты
last_checked_links = {}

def build_headers(rss_url):
    """Создает заголовки для запроса"""
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
    logger.info("🚀 Запуск RSS бота")

    # Инициализация
    for url in RSS_FEED_URLS:
        domain = urlparse(url).netloc
        logger.info(f"🔄 Инициализация {domain}...")

        # Просто запоминаем, что ничего еще не проверяли
        last_checked_links[url] = None
        time.sleep(2)

    logger.info(f"✅ Отслеживается {len(RSS_FEED_URLS)} лент")

    # ПЕРВЫЙ ЦИКЛ: НИЧЕГО НЕ ОТПРАВЛЯЕМ
    logger.info("🔄 Первый цикл проверки (ничего не отправляем)...")
    for url in RSS_FEED_URLS:
        domain = urlparse(url).netloc
        try:
            feed = robust_parse_feed(url)
            if feed and feed.entries:
                # Берем самую последнюю (первую в списке) ссылку
                for entry in feed.entries:
                    link = get_first_link(entry)
                    if link:
                        last_checked_links[url] = link
                        logger.info(f"✅ Установлена стартовая ссылка для {domain}: {link[:60]}...")
                        break
            else:
                logger.warning(f"⚠️ Пустая лента при инициализации: {domain}")
        except Exception as e:
            logger.error(f"❌ Ошибка при инициализации {domain}: {e}")

        time.sleep(random.randint(2, 5))

    logger.info("⏳ Ожидание 1 минуту перед началом отправки...")
    time.sleep(60)

    # ОСНОВНОЙ ЦИКЛ
    while True:
        logger.info("🔍 Начинаю цикл проверки...")

        for url in RSS_FEED_URLS:
            domain = urlparse(url).netloc
            try:
                feed = robust_parse_feed(url)
                if not feed or not feed.entries:
                    logger.warning(f"⚠️ Пустая лента или ошибка парсинга: {domain}")
                    continue

                last_link = last_checked_links.get(url)
                new_links_to_send = []

                # Идем по статьям от НОВЫХ к СТАРЫМ
                for entry in feed.entries:
                    link = get_first_link(entry)
                    if not link:
                        continue

                    # Если это наша последняя проверенная ссылка - останавливаемся
                    if link == last_link:
                        break

                    # Иначе это новая статья - добавляем в список для отправки
                    new_links_to_send.append((entry, link))

                # Отправляем новые статьи в правильном порядке (от старых к новым)
                # потому что мы шли от новых к старым, нужно развернуть
                sent_count = 0
                for entry, link in reversed(new_links_to_send):
                    message = format_message(entry, url)
                    if message and send_to_telegram(message):
                        logger.info(f"✅ Отправлено из {domain}: {getattr(entry, 'title', 'Без заголовка')[:60]}...")
                        sent_count += 1

                        # Обновляем последнюю проверенную ссылку
                        last_checked_links[url] = link

                        # Задержка между отправками
                        delay = random.randint(10, 15)
                        logger.info(f"⏳ Задержка {delay} сек перед следующей отправкой...")
                        time.sleep(delay)
                    else:
                        logger.error(f"❌ Не удалось отправить новость из {domain}")

                if sent_count > 0:
                    logger.info(f"📨 Отправлено {sent_count} новых статей из {domain}")
                else:
                    logger.info(f"📭 Нет новых статей в {domain}")

                time.sleep(random.randint(3, 7))

            except Exception as e:
                logger.error(f"❌ Ошибка при обработке {domain}: {e}")
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
