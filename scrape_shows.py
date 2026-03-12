import asyncio
import json
import os
from urllib.parse import urljoin
from playwright.async_api import async_playwright

URLS = [
    "https://www.millionpodcasts.com/witchcraft-podcasts/",
]

CARD_SELECTORS = [
    "article",
    "li",
    "div[class*=podcast]",
    "div[class*=card]",
    "div[class*=post]",
    "div[class*=item]",
]

TITLE_SELECTORS = [
    "h1",
    "h2",
    "h3",
    "h4",
    ".title",
    "[class*=title]",
    "a"
]

DESC_SELECTORS = [
    "p",
    ".description",
    "[class*=desc]"
]

IMAGE_SELECTORS = ["img"]

OUTPUT_FILE = "shows.txt"


def abs_url(base, url):
    if not url:
        return None
    return urljoin(base, url)


async def extract_text(locator):
    try:
        if await locator.count():
            txt = (await locator.inner_text()).strip()
            if len(txt) > 2:
                return txt
    except:
        pass
    return None


async def extract_attr(locator, attr):
    try:
        if await locator.count():
            return await locator.get_attribute(attr)
    except:
        pass
    return None


# -------------------------------
# FILE STORAGE FUNCTIONS
# -------------------------------

def load_existing_titles():
    """Load titles already saved to avoid duplicates."""
    titles = set()

    if not os.path.exists(OUTPUT_FILE):
        return titles

    with open(OUTPUT_FILE, "r") as f:
        for line in f:
            try:
                data = json.loads(line)
                titles.add(data.get("title"))
            except:
                pass

    return titles


def save_show(show):
    """Append show to file."""
    with open(OUTPUT_FILE, "a") as f:
        f.write(json.dumps(show) + "\n")


# -------------------------------
# SCRAPER LOGIC (UNCHANGED)
# -------------------------------

async def extract_card(card, base):

    data = {
        "title": None,
        "description": None,
        "image": None,
        "website": None,
        "spotify": None,
        "apple": None,
        "youtube": None,
        "host": None,
        "producer": None,
        "location": None,
        "rating": None,
    }

    # title
    for sel in TITLE_SELECTORS:
        title = await extract_text(card.locator(sel).first)
        if title:
            data["title"] = title
            break

    # description
    for sel in DESC_SELECTORS:
        desc = await extract_text(card.locator(sel).first)
        if desc and len(desc) > 20:
            data["description"] = desc[:500]
            break

    # image
    for sel in IMAGE_SELECTORS:
        src = await extract_attr(card.locator(sel).first, "src")
        if src:
            data["image"] = abs_url(base, src)
            break

    # links
    links = await card.locator("a[href]").all()

    for l in links:

        href = await l.get_attribute("href")
        if not href:
            continue

        href = abs_url(base, href)

        if "spotify" in href:
            data["spotify"] = href
        elif "apple" in href:
            data["apple"] = href
        elif "youtube" in href:
            data["youtube"] = href
        else:
            data["website"] = href

    return data


async def scrape_page(page, url, existing_titles):

    print(f"\nOpening: {url}")

    await page.goto(url, wait_until="domcontentloaded")

    await page.wait_for_timeout(2000)

    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await page.wait_for_timeout(2000)

    results = []

    for selector in CARD_SELECTORS:

        cards = page.locator(selector)
        count = await cards.count()

        if count < 3:
            continue

        print(f"Detected {count} cards with {selector}")

        for i in range(count):

            card = cards.nth(i)

            data = await extract_card(card, url)

            if not data["title"]:
                continue

            if data["title"] in existing_titles:
                continue

            existing_titles.add(data["title"])

            save_show(data)

            print("Saved:", data["title"])

            results.append(data)

        if results:
            break

    return results


async def scrape_all(urls=None):

    if urls is None:
        urls = URLS

    existing_titles = load_existing_titles()

    print(f"Existing shows in file: {len(existing_titles)}")

    async with async_playwright() as p:

        browser = await p.chromium.launch(headless=True)

        context = await browser.new_context()

        page = await context.new_page()

        shows = []

        for url in urls:
            shows.extend(await scrape_page(page, url, existing_titles))

        await browser.close()

        return shows


if __name__ == "__main__":
    asyncio.run(scrape_all())