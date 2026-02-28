"""
YouTube Channel Social Links Scraper
FastAPI + Async Playwright
Python 3.9 Compatible

Install:
    pip install fastapi uvicorn playwright
    playwright install chromium

Run:
    uvicorn youtube_scraper_service:app --host 0.0.0.0 --port 5050 --reload

Docs:
    http://localhost:5050/docs
"""

import re
import asyncio
from urllib.parse import urlparse, unquote, parse_qs
from contextlib import asynccontextmanager
from typing import Optional, List, Tuple, Set
import html
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    TimeoutError as PlaywrightTimeout,
)

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

SOCIAL_DOMAINS = [
    "instagram.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "linktr.ee",
    "tiktok.com",
    "threads.net",
    "snapchat.com",
    "pinterest.com",
    "discord.gg",
    "discord.com",
    "twitch.tv",
    "patreon.com",
    "beacons.ai",
    "bio.link",
    "bsky.app",
]

EMAIL_REGEX = re.compile(
    r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE
)
LINK_REGEX = re.compile(r"https?://[^\s\"'<>)\]]+")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Ordered from most-specific (current YouTube DOM) to broadest fallback.
# YouTube changed their layout in 2024 — links now live inside
# .ytChannelExternalLinkViewModelContainer, NOT #links-container.
LINK_SELECTORS = [
    ".ytChannelExternalLinkViewModelContainer a",   # ✅ current YouTube layout
    "yt-channel-external-link-view-model a",        # ✅ current custom element
    "ytd-channel-external-link-view-model a",       # older layout
    "#links-container a",                           # older layout
    "ytd-channel-about-metadata-renderer a",        # older layout
]

# ─────────────────────────────────────────────
# BROWSER LIFECYCLE
# ─────────────────────────────────────────────

browser: Optional[Browser] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global browser
    playwright_instance = await async_playwright().start()
    browser = await playwright_instance.chromium.launch(headless=True)
    print("✅ Chromium browser launched")
    yield
    if browser:
        await browser.close()
    await playwright_instance.stop()
    print("🛑 Chromium browser closed")


# ─────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────

app = FastAPI(
    title="YouTube Channel Scraper",
    version="2.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────


class ScrapeRequest(BaseModel):
    handle: str = Field(..., description="YouTube handle (e.g. '@rajshamani') or full URL")


class ScrapeResponse(BaseModel):
    handle: str
    url: str
    email: Optional[str]
    social_links: List[str]
    website_links: List[str]
    all_links: List[str]
    raw_description: str
    error: Optional[str]


class BulkScrapeRequest(BaseModel):
    handles: List[str]
    concurrency: int = Field(default=3, ge=1, le=10)


class BulkScrapeResponse(BaseModel):
    results: List[ScrapeResponse]
    total: int
    succeeded: int
    failed: int


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────


def normalize_url(handle_or_url: str) -> str:
    h = handle_or_url.strip()
    if h.startswith("http"):
        base = h.rstrip("/")
        return base if base.endswith("/about") else base + "/about"
    handle = h if h.startswith("@") else "@{}".format(h)
    return "https://www.youtube.com/{}/about".format(handle)


def is_social(url: str) -> bool:
    try:
        domain = urlparse(url).netloc.lower().lstrip("www.")
        return any(d in domain for d in SOCIAL_DOMAINS)
    except Exception:
        return False


def decode_youtube_redirect(url: str) -> str:
    """
    Fully decode YouTube redirect URLs safely.
    """
    if not url:
        return url

    # Properly unescape HTML entities
    url = html.unescape(url)

    if "youtube.com/redirect" in url:
        match = re.search(r"[?&]q=(https?://[^&]+)", url)
        if match:
            return unquote(match.group(1))

    return url

def extract_real_url(url: str) -> str:
    """
    Extract final destination from YouTube redirect URLs.
    """
    if not url:
        return url

    # Unescape HTML entities (&amp;)
    url = html.unescape(url)

    # If YouTube redirect wrapper
    if "youtube.com/redirect" in url:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)

        if "q" in query:
            return query["q"][0]

    return url

def classify_links(raw: Set[str]) -> Tuple[List[str], List[str]]:
    social, websites = [], []
    for link in raw:
        (social if is_social(link) else websites).append(link)
    return sorted(set(social)), sorted(set(websites))


# ─────────────────────────────────────────────
# CORE SCRAPER
# ─────────────────────────────────────────────


async def scrape_channel(handle_or_url: str) -> ScrapeResponse:
    url = normalize_url(handle_or_url)

    result = ScrapeResponse(
        handle=handle_or_url,
        url=url,
        email=None,
        social_links=[],
        website_links=[],
        all_links=[],
        raw_description="",
        error=None,
    )

    if browser is None:
        result.error = "Browser not initialized"
        return result

    context: BrowserContext = await browser.new_context(
        user_agent=USER_AGENT,
        locale="en-US",
        viewport={"width": 1280, "height": 800},
    )

    try:
        page = await context.new_page()

        # Block images/fonts — faster loading, no effect on link extraction
        await page.route(
            "**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf}",
            lambda route: route.abort(),
        )

        # networkidle ensures JS-rendered elements (like the links panel) are present
        await page.goto(url, wait_until="networkidle", timeout=30000)

        # Handle EU consent dialog
        try:
            await page.click('button:has-text("Accept all")', timeout=4000)
            await asyncio.sleep(1)
        except Exception:
            pass

        # Scroll to trigger lazy-loaded content (link panel often lazy loads)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.5)

        # Wait for the external links container to appear (current YouTube layout)
        try:
            await page.wait_for_selector(
                ".ytChannelExternalLinkViewModelContainer, yt-channel-external-link-view-model",
                timeout=6000,
            )
        except Exception:
            pass  # Not all channels have external links

        # ── 1. Extract description text ──
        description = ""
        for sel in ["#description-container", "#description", "yt-formatted-string#description"]:
            try:
                description = await page.inner_text(sel, timeout=4000)
                if description.strip():
                    break
            except Exception:
                continue
        result.raw_description = description.strip()

        # ── 2. Extract external links using ordered selectors ──
        # Tries specific selectors first, falls back to broader ones.
        # The key fix: target .ytChannelExternalLinkViewModelContainer which is
        # YouTube's CURRENT layout (2024+), not the old #links-container.
        raw_hrefs: List[str] = []

        for selector in LINK_SELECTORS:
            try:
                found: List[str] = await page.eval_on_selector_all(
                    selector,
                    "els => els.map(e => e.getAttribute('href') || e.href || '')"
                )
                found = [h for h in found if h]  # remove empty strings
                print("Found links: ",found)
                if found:
                    raw_hrefs.extend(found)
                    print(f"  ✅ [{selector}] found {len(found)} links")
                    break  # stop at first selector that returns results
            except Exception:
                continue

        # Final fallback: all <a> on the page (filters youtube.com links later)
        if not raw_hrefs:
            try:
                raw_hrefs = await page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(e => e.getAttribute('href') || e.href || '')"
                )
                print(f"  ⚠️  Fell back to all <a> tags: {len(raw_hrefs)} found")
            except Exception:
                pass

        # ── 3. Decode, clean, and deduplicate ──
        raw_links: Set[str] = set()

        for href in raw_hrefs:
            real_url = extract_real_url(href.strip())

            if not real_url.startswith("http"):
                continue

            # Remove trailing garbage
            real_url = real_url.rstrip(".,)")

            raw_links.add(real_url)
        # Regex fallback on description text (catches plaintext URLs)
        for link in LINK_REGEX.findall(description):
            clean = link.rstrip(".,)")
            if "youtube.com" not in clean:
                raw_links.add(clean)

        # ── 4. Email extraction ──
        full_text = description + " " + " ".join(raw_links)
        email_match = EMAIL_REGEX.search(full_text)
        result.email = email_match.group(0) if email_match else None

        # ── 5. Classify social vs website ──
        result.social_links, result.website_links = classify_links(raw_links)
        result.all_links = sorted(raw_links)

        print(f"  📦 Final: {len(result.social_links)} social, {len(result.website_links)} website links")

    except PlaywrightTimeout:
        result.error = "Timeout loading {}".format(url)
    except Exception as e:
        result.error = str(e)
    finally:
        await context.close()

    return result


# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "browser": "ready" if browser else "not initialized",
    }


@app.post("/scrape", response_model=ScrapeResponse)
async def scrape_single(body: ScrapeRequest):
    """Scrape a single YouTube channel About page for social/contact links."""
    return await scrape_channel(body.handle)


@app.post("/scrape/bulk", response_model=BulkScrapeResponse)
async def scrape_bulk(body: BulkScrapeRequest):
    """Scrape multiple YouTube channels concurrently."""
    semaphore = asyncio.Semaphore(body.concurrency)

    async def guarded(handle: str) -> ScrapeResponse:
        async with semaphore:
            return await scrape_channel(handle)

    results = await asyncio.gather(*[guarded(h) for h in body.handles])
    succeeded = sum(1 for r in results if r.error is None)

    return BulkScrapeResponse(
        results=list(results),
        total=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
    )


if __name__ == "__main__":
    import os
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)