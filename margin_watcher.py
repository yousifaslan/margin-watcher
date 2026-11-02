"""
Margin Watcher - COE203 Project 2 (Web Monitoring)

Watches supplier product pages (web scraping) and today's exchange rate
(JSON API), calculates the profit margin of each product in Turkish lira,
and alerts only when something important changes. Run it from cli.py.
"""

import json
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# ---------- Settings ----------
FOLDER = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_FILE = os.path.join(FOLDER, "data", "products.json")
STATE_FILE = os.path.join(FOLDER, "data", "state.json")
ALERTS_FILE = os.path.join(FOLDER, "data", "alerts.log")

RATE_API = "https://open.er-api.com/v6/latest/"
ALLOWED_HOSTS = {"books.toscrape.com", "localhost", "127.0.0.1"}
CURRENCY_SYMBOLS = {"£": "GBP", "$": "USD", "€": "EUR"}
HEADERS = {"User-Agent": "MarginWatcher/1.0 (COE203 student project)"}
TIMEOUT = 10
ATTEMPTS = 3
MAX_PRICE = 10_000_000


# ---------- Checking input ----------
def parse_id(text):
    """A product id: 1-30 lowercase letters, digits or '-'."""
    product_id = str(text).strip().lower()
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789-"
    if len(product_id) < 1 or len(product_id) > 30:
        raise ValueError("ID must be 1-30 letters, digits or '-'.")
    for char in product_id:
        if char not in allowed:
            raise ValueError("ID must be 1-30 letters, digits or '-'.")
    return product_id


def parse_url(text):
    """A supplier address: http(s) only, and only allowed hosts."""
    url = str(text).strip()
    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("URL must start with http:// or https://")
    if parts.hostname not in ALLOWED_HOSTS:
        allowed = ", ".join(sorted(ALLOWED_HOSTS))
        raise ValueError(f"Supplier not allowed. Allowed: {allowed}")
    return url


def parse_money(text):
    """A selling price in TL: a number above 0 and at most MAX_PRICE."""
    try:
        value = float(str(text).strip())
    except ValueError:
        raise ValueError("Price must be a number, e.g. 3500")
    if not (value > 0 and value <= MAX_PRICE):
        raise ValueError(f"Price must be above 0 and at most {MAX_PRICE:,}.")
    return round(value, 2)


def parse_percent(text):
    """A minimum margin: a number from 0 to 100."""
    try:
        value = float(str(text).strip())
    except ValueError:
        raise ValueError("Margin must be a number, e.g. 20")
    if not (value >= 0 and value <= 100):
        raise ValueError("Margin must be between 0 and 100.")
    return value


# ---------- Saving and loading (JSON files) ----------
def load_json(path, default):
    """Read a JSON file. Missing gives the default; broken is an error."""
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, UnicodeDecodeError):
        name = os.path.basename(path)
        raise ValueError(f"{name} is damaged. Fix or delete it.")


def save_json(path, data):
    """Write to a temporary file first, then replace the real one."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
    os.replace(temp, path)


def load_products():
    products = load_json(PRODUCTS_FILE, {})
    if not isinstance(products, dict):
        raise ValueError("products.json must contain a dictionary.")
    return products


def save_products(products):
    save_json(PRODUCTS_FILE, products)


def load_state():
    """The memory of the last check. Anything unusable starts fresh."""
    state = load_json(STATE_FILE, {})
    if not isinstance(state, dict):
        return {}
    return state


def check_product(product):
    """Re-check a saved product, in case products.json was edited."""
    if not isinstance(product, dict):
        raise ValueError("entry in products.json is not a dictionary")
    for key in ("url", "sell_try", "min_margin"):
        if key not in product:
            raise ValueError(f"entry in products.json has no '{key}'")
    parse_url(product["url"])
    parse_money(product["sell_try"])
    parse_percent(product["min_margin"])


# ---------- Talking to the web ----------
def fetch(url):
    """GET with a timeout. Retry temporary problems up to 3 times."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if response.status_code == 429 or response.status_code >= 500:
                raise requests.ConnectionError("server busy")
            response.raise_for_status()
            return response
        except (requests.ConnectionError, requests.Timeout):
            if attempt < ATTEMPTS:
                time.sleep(attempt)
    raise RuntimeError(f"no answer from the server after {ATTEMPTS} tries")


def get_rate(currency):
    """API: how many Turkish lira one unit of the currency costs."""
    response = fetch(RATE_API + currency)
    data = json.loads(response.text)
    rates = data.get("rates", {})
    if data.get("result") != "success" or "TRY" not in rates:
        raise RuntimeError(f"rate API gave unexpected data for {currency}")
    rate = float(rates["TRY"])
    if not (rate > 0 and rate < 1000):
        raise RuntimeError(f"rate API gave an impossible rate: {rate}")
    return rate


def read_price(price_text):
    """'£51.77' -> ('GBP', 51.77). Anything else: the page changed."""
    price_text = price_text.strip()
    currency = CURRENCY_SYMBOLS.get(price_text[:1])
    if currency is None:
        raise RuntimeError(f"unknown currency in price '{price_text}'")
    try:
        amount = float(price_text[1:].replace(",", ""))
    except ValueError:
        raise RuntimeError(f"price is not a number: '{price_text}'")
    if not (amount > 0 and amount <= MAX_PRICE):
        raise RuntimeError(f"impossible price '{price_text}'")
    return currency, amount


def read_stock(stock_text):
    """'In stock (22 available)' -> 22, 'Out of stock' -> 0, else None."""
    words = " ".join(stock_text.split())
    if words.lower().startswith("out of stock"):
        return 0
    if "(" in words:
        number = words.split("(")[1].split()[0]
        if number.isdigit():
            return int(number)
    return None


def scrape_product(url):
    """Scraping: read the supplier page's title, price and stock."""
    response = fetch(url)
    soup = BeautifulSoup(response.content, "html.parser")
    title = soup.select_one(".product_main h1")
    price = soup.select_one(".product_main .price_color")
    stock = soup.select_one(".product_main .availability")
    if not title or not price:
        raise RuntimeError("page structure changed: title or price missing")
    currency, amount = read_price(price.get_text())
    stock_count = None
    if stock:
        stock_count = read_stock(stock.get_text())
    return {
        "title": " ".join(title.get_text().split()),
        "currency": currency,
        "price": amount,
        "stock": stock_count,
    }


# ---------- Business logic ----------
def margin_percent(sell_try, cost_try):
    """Profit margin: how much of the selling price is profit."""
    return (sell_try - cost_try) / sell_try * 100


def observe(product, rates):
    """One fresh look: scrape, convert to TL, calculate the margin."""
    check_product(product)
    page = scrape_product(product["url"])
    currency = page["currency"]
    if currency not in rates:
        rates[currency] = get_rate(currency)
    cost_try = round(page["price"] * rates[currency], 2)
    page["rate"] = rates[currency]
    page["cost_try"] = cost_try
    page["margin"] = round(margin_percent(product["sell_try"], cost_try), 1)
    page["time"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return page


def compare(pid, product, old, new):
    """Decide which changes deserve an alert. Repeats are not news."""
    limit = product["min_margin"]
    if not old:
        message = f"{pid}: now watching, margin {new['margin']}%"
        if new["margin"] < limit:
            message += " - BELOW your limit!"
        return [message]
    alerts = []
    if new["price"] != old["price"]:
        alerts.append(f"{pid}: price {old['price']} -> {new['price']}")
    if old["margin"] >= limit and new["margin"] < limit:
        alerts.append(f"{pid}: LOW MARGIN {new['margin']}% (limit {limit}%)")
    if old["margin"] < limit and new["margin"] >= limit:
        alerts.append(f"{pid}: margin back to {new['margin']}%")
    if old.get("stock") != 0 and new["stock"] == 0:
        alerts.append(f"{pid}: OUT OF STOCK at the supplier")
    if old.get("stock") == 0 and new["stock"]:
        alerts.append(f"{pid}: back in stock ({new['stock']} available)")
    return alerts


def notify(message):
    """Show the alert, log it, and push it to a phone if NTFY_TOPIC."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    line = f"[{stamp}] {message}"
    print("ALERT", line)
    with open(ALERTS_FILE, "a", encoding="utf-8") as file:
        file.write(line + "\n")
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            response = requests.post(f"https://ntfy.sh/{topic}",
                                     data=message.encode("utf-8"),
                                     headers={"Title": "Margin Watcher"},
                                     timeout=TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as error:
            print(f"(phone notification failed: {error})")


def run_check():
    """One full scan: observe, compare with saved state, alert, save."""
    products = load_products()
    state = load_state()
    rates = {}
    failed = 0
    alert_count = 0
    for pid, product in products.items():
        try:
            new = observe(product, rates)
        except (RuntimeError, ValueError, requests.RequestException) as error:
            failed += 1
            print(f"{pid}: check failed, keeping last data ({error})")
            continue
        old = state.get(pid)
        if not isinstance(old, dict):
            old = None  # no usable memory of this product: treat it as new
        elif "price" not in old or "margin" not in old:
            old = None
        for message in compare(pid, product, old, new):
            notify(message)
            alert_count += 1
        state[pid] = new
    save_json(STATE_FILE, state)
    print(f"Checked {len(products) - failed}/{len(products)} products, "
          f"{alert_count} alert(s), {failed} failed.")
    return failed
