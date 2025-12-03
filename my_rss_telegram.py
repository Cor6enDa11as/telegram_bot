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

# Храним последние проверенные ссылки
last_checked_links = {}
# Храним историю отправленных статей для каждой ленты
sent_articles_history = {}
MAX_HISTORY = 50  # Храним историю последних 50 статей

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
    """Парсинг RSS с учетом особенностей сайтов"""
    domain = urlparse(rss_url).netloc

    # Для 4pda.to сначала пробуем Selenium
    if '4pda.to' in domain and HAS_SELENIUM:
        try:
            logger.info(f"🤖 Для 4pda.to используем Selenium")
            page_source = fetch_with_selenium(rss_url)
            if page_source:
                feed = feedparser.parse(page_source)
                if feed and hasattr(feed, 'entries') and feed.entries:
                    logger.info(f"✅ Успешно через Selenium: {domain}")
                    return feed
        except Exception as e:
            logger.error(f"❌ Ошибка Selenium для {domain}: {e}")

    # Для остальных или если Selenium не сработал
    headers = build_headers(rss_url)
    try:
        response = requests.get(rss_url, timeout=25, headers=headers)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        if feed and hasattr(feed, 'entries') and feed.entries:
            logger.info(f"✅ Успешно через requests: {domain}")
            return feed
    except Exception as e:
        logger.warning(f"❌ Ошибка requests для {domain}: {e}")
        return None

    return None

def fetch_with_selenium(url):
    """Использование headless браузера для обхода защиты"""
    options = ChromeOptions()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    driver = uc.Chrome(options=options)

    try:
        driver.get(url)
        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        time.sleep(3)  # Дополнительная задержка для загрузки
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

def get_entry_id(entry):
    """Создает уникальный идентификатор для статьи"""
    link = get_first_link(entry)
    if link:
        return link  # Используем ссылку как идентификатор

    # Если нет ссылки, используем комбинацию заголовка и даты
    title = getattr(entry, 'title', '')
    published = getattr(entry, 'published', '')
    guid = getattr(entry, 'guid', '')
    return f"{title}_{published}_{guid}"

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
        'disable_notification': False
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
    logger.info("🚀 Запуск RSS бота")

    # Инициализация
    for url in RSS_FEED_URLS:
        domain = urlparse(url).netloc
        last_checked_links[url] = None
        sent_articles_history[url] = set()  # Множество для быстрой проверки
        logger.info(f"🔄 Инициализирована лента: {domain}")

    logger.info(f"✅ Отслеживается {len(RSS_FEED_URLS)} лент")

    # ПЕРВЫЙ ЦИКЛ: запоминаем текущие статьи БЕЗ отправки
    logger.info("🔄 Первый цикл проверки (запоминаем статьи без отправки)...")
    for url in RSS_FEED_URLS:
        domain = urlparse(url).netloc
        try:
            feed = robust_parse_feed(url)
            if feed and feed.entries:
                # Запоминаем все текущие статьи
                for entry in feed.entries:
                    entry_id = get_entry_id(entry)
                    if entry_id:
                        sent_articles_history[url].add(entry_id)

                # Запоминаем последнюю статью как проверенную
                for entry in feed.entries:
                    link = get_first_link(entry)
                    if link:
                        last_checked_links[url] = link
                        break

                logger.info(f"✅ Запомнено {len(sent_articles_history[url])} статей из {domain}")
            else:
                logger.warning(f"⚠️ Пустая лента при инициализации: {domain}")
        except Exception as e:
            logger.error(f"❌ Ошибка при инициализации {domain}: {e}")

        time.sleep(random.randint(3, 7))

    logger.info("⏳ Ожидание 2 минуты перед началом отправки...")
    time.sleep(120)

    # ОСНОВНОЙ ЦИКЛ
    while True:
        logger.info("🔍 Начинаю цикл проверки...")

        for url in RSS_FEED_URLS:
            domain = urlparse(url).netloc
            try:
                logger.info(f"📰 Проверяю ленту: {domain}")
                feed = robust_parse_feed(url)

                if not feed or not feed.entries:
                    logger.warning(f"⚠️ Пустая лента или ошибка парсинга: {domain}")
                    continue

                sent_in_this_check = 0

                # Проверяем статьи от НОВЫХ к СТАРЫМ
                for entry in feed.entries:
                    entry_id = get_entry_id(entry)
                    if not entry_id:
                        continue

                    # Если статья уже была отправлена - пропускаем
                    if entry_id in sent_articles_history[url]:
                        continue

                    # Новая статья - отправляем
                    message = format_message(entry)
                    if message and send_to_telegram(message):
                        logger.info(f"✅ Отправлено из {domain}: {getattr(entry, 'title', 'Без заголовка')[:60]}...")
                        sent_in_this_check += 1

                        # Добавляем в историю отправленных
                        sent_articles_history[url].add(entry_id)

                        # Обновляем последнюю проверенную ссылку
                        link = get_first_link(entry)
                        if link:
                            last_checked_links[url] = link

                        # Задержка между отправками
                        delay = random.randint(10, 15)
                        logger.info(f"⏳ Задержка {delay} сек...")
                        time.sleep(delay)
                    else:
                        logger.error(f"❌ Не удалось отправить из {domain}")

                # Ограничиваем размер истории
                if len(sent_articles_history[url]) > MAX_HISTORY:
                    # Преобразуем в список, обрезаем и обратно в множество
                    history_list = list(sent_articles_history[url])
                    sent_articles_history[url] = set(history_list[-MAX_HISTORY:])

                if sent_in_this_check > 0:
                    logger.info(f"📨 Отправлено {sent_in_this_check} новых статей из {domain}")
                else:
                    logger.info(f"📭 Нет новых статей в {domain}")

                time.sleep(random.randint(5, 10))

            except Exception as e:
                logger.error(f"❌ Ошибка при обработке {domain}: {e}")
                time.sleep(10)

        logger.info("✅ Цикл проверки завершен")
        logger.info("⏳ Ожидание 15 минут до следующей проверки...")
        time.sleep(900)

@app.route('/')
def home():
    return '✅ RSS Bot is running!'

if __name__ == '__main__':
    logger.info(f"📡 Отслеживается {len(RSS_FEED_URLS)} RSS-лент")

    # Проверяем доступность Selenium
    if not HAS_SELENIUM:
        logger.warning("⚠️ Selenium не доступен, 4pda.to может не работать")

    Thread(target=rss_check_loop, daemon=True).start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
