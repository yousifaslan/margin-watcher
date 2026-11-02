# Margin Watcher

**COE203 Advanced Programming with Python · Project 2 (Web Monitoring) · Group Pypeople**

A command-line tool for small shops and importers. It watches **supplier prices** (web scraping) and **today's exchange rate** (JSON API),
calculates the **profit margin in Turkish lira** for every product, and **alerts only when something important changes**.

## The problem it solves
A shop in Turkey buys products from a supplier abroad (priced in $, € or £) and sells them in lira.
The supplier changes prices, and the exchange rate changes every day. So a product that was profitable last month can be **losing money today**,
and nobody notices until the end of the month.

Margin Watcher checks the supplier and the rate on a schedule and tells the owner, for example:
> `mouse: LOW MARGIN 16.8% (limit 20%)`

## How it works
```
supplier page ──(scraping)──┐
                            ├──> cost in TL ──> margin % ──> compare with last check ──> alert only on changes
exchange rate ──(JSON API)──┘                                   (saved in data/state.json)
```
1. **Scrape** each supplier page with `requests` + `BeautifulSoup`: title, price (e.g. `£51.77`) and stock (`In stock (22 available)`).
2. **Get the rate** from the free JSON API `open.er-api.com` (e.g. 1 GBP = 66.89 TL). Each currency is asked only once per scan.
3. **Calculate** cost in TL = supplier price × rate, and margin = (selling price − cost) ÷ selling price × 100.
4. **Compare** with the saved result of the last check (`data/state.json`) and alert only on **changes**:
   - first time a product is seen (and whether it is already below your limit),
   - supplier price changed,
   - margin dropped **below** your limit, or came **back above** it,
   - supplier went **out of stock**, or came **back in stock**.
   Seeing the same thing again is not news, so there are no repeated alerts.
5. **Alert:** print it, save it to `data/alerts.log`, and optionally push it to a phone with [ntfy](https://ntfy.sh).
6. **Schedule:** `watch` runs the check automatically every N minutes with APScheduler.

## Project structure
```
margin-watcher/
├── cli.py                 # Command-line interface: one command per feature (argparse) + the scheduler
├── margin_watcher.py      # Core logic: input checks, scraping, rate API, retries, margin, change detection, alerts
├── requirements.txt       # The three libraries: requests, beautifulsoup4, APScheduler
├── data/
│   └── products.json      # Watched products: supplier URL, your selling price in TL, your minimum margin %
├── demo/
│   └── product.html       # A local demo supplier page (same HTML format as the real shop) for the live demo
├── contributions/         # One file per team member describing their contribution
└── README.md              # This file
```
Created while running (not in the repo): `data/state.json` (last result per product, the program's memory)
and `data/alerts.log` (every alert with its time in UTC).

## Setup
Python 3.9 or newer.
```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Usage
```bash
python cli.py --help               # all commands
python cli.py <command> --help     # help for one command
```

| Command | What it does | Example |
|---|---|---|
| `add` | Start watching a product | `python cli.py add --id attic --url https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html --sell 4500 --min-margin 20` |
| `remove` | Stop watching a product | `python cli.py remove --id attic` |
| `list` | Table of products with their last supplier price, cost in TL, selling price, margin and status | `python cli.py list` |
| `check` | Check every product once, now | `python cli.py check` |
| `watch` | Check automatically every N minutes (1–1440, default 60). Ctrl+C stops it | `python cli.py watch --every 60` |
| `history` | Show the latest alerts | `python cli.py history --last 10` |

Exit codes: `0` = success, `1` = error (for example a product could not be checked), `2` = wrong arguments.

**Phone alerts (optional):** install the ntfy app, subscribe to a topic name only you know, then:
```bash
export NTFY_TOPIC=your-secret-topic-name
python cli.py check
```
The topic is read from an environment variable, so it never ends up in the code or on GitHub.

## Live demo (2 minutes)
The real supplier in `data/products.json` is [books.toscrape.com](https://books.toscrape.com), a practice shop made for scraping.
Its prices don't change, so the demo uses a local copy of a supplier page that we can edit:
```bash
cd demo && python -m http.server 8765        # terminal 1: start the demo supplier
python cli.py add --id mouse --url http://localhost:8765/product.html --sell 1800 --min-margin 20
python cli.py check                           # "mouse: now watching, margin 25.1%"
# edit demo/product.html: change $27.00 to $30.00 and save
python cli.py check                           # "mouse: price 27.0 -> 30.0" and "LOW MARGIN 16.8%"
python cli.py check                           # nothing new -> no alerts
python cli.py add --id x --url file:///etc/passwd --sell 100   # rejected
```

## Robustness: input rules and failure handling
| Input | Rule |
|---|---|
| `--id` | 1–30 characters: lowercase letters, digits and `-` (no spaces, no `../`) |
| `--url` | Must be `http://` or `https://`, and the host must be an allowed supplier (`books.toscrape.com`, `localhost`, `127.0.0.1`) |
| `--sell` | A normal number above 0 and at most 10,000,000 (rejects `abc`, `-5`, `inf`, `nan`) |
| `--min-margin` | A number from 0 to 100 |
| `--every` / `--last` | Whole numbers in a fixed range |

| Problem | What the program does |
|---|---|
| Supplier server busy (HTTP 429/5xx), timeout or connection lost | Retries up to 3 times, waiting 1 s then 2 s, then reports the product as failed |
| Page not found or access refused (4xx) | Stops immediately, no retry (repeating would fail the same way) |
| Page redesigned (price or title missing) or price text not a number | Reported as "page structure changed", **last good data is kept**, no false alert |
| Rate API returns wrong data or an impossible rate | The product is reported as failed, nothing is saved |
| `products.json` edited by hand with a bad URL or missing fields | That product is skipped with a message; the others are still checked |
| `products.json` or `state.json` damaged | Clear error message, no crash, nothing is overwritten |
| A check fails | It is **never** recorded as "0 products" or "price 0": the old state stays until a good check replaces it |

Files are saved through a temporary file that then replaces the real one, so they are never half-written.
`watch` never starts a check while the previous one is still running (`max_instances=1`), and after the computer sleeps,
missed checks are combined into one fresh check (`coalesce=True`).

## Data source and fair use
- Supplier pages: books.toscrape.com, a public site built for scraping practice. Each check reads one page per product, with a clear `User-Agent`.
- Exchange rates: `open.er-api.com` (free, no key, updated daily).
- ## Troubleshooting
| Message | What to do |
|---|---|
| `No module named 'requests'` | Activate the virtual environment and run `pip install -r requirements.txt` |
| `Supplier not allowed` | The website is not in the allowed list (`ALLOWED_HOSTS` in `margin_watcher.py`) |
| `no answer from the server after 3 tries` | Check your internet connection and run `python cli.py check` again |
| `page structure changed` | The supplier changed its website design; the last good data is kept |
| `... is damaged. Fix or delete it.` | A data file was edited by hand and is no longer valid JSON |

## Team
See the `contributions/` folder.
