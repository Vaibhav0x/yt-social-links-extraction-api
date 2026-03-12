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
from typing import Optional, List, Tuple, Set, Dict
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
from run_pipeline import run_pipeline
import json
import os


EMAIL_FILE = "shows_with_email.txt"
SHOWS_FILE = "shows.txt"

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

class PodcastListingScrapeRequest(BaseModel):
    website_url: str = Field(
        ...,
        description="URL of podcast listing website (e.g. millionpodcasts.com)"
    )

class PodcastListingItem(BaseModel):
    title: str
    url: Optional[str] = None
    description: Optional[str] = None
    host: Optional[str] = None
    image: Optional[str] = None
    website: Optional[str] = None
    spotify: Optional[str] = None
    apple: Optional[str] = None
    youtube: Optional[str] = None
    email: Optional[str] = None

class PodcastListingScrapeResponse(BaseModel):
    podcasts: List[PodcastListingItem]
    total_found: int
    error: Optional[str] = None
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
# PODCAST LISTING SCRAPER
# ─────────────────────────────────────────────


def load_email_results():
    """Load podcast results with emails from file."""
    podcasts = []

    if not os.path.exists(EMAIL_FILE):
        return podcasts

    with open(EMAIL_FILE) as f:
        for line in f:
            try:
                podcasts.append(json.loads(line))
            except:
                pass

    return podcasts


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


# ─────────────────────────────────────────────
# PODCAST LISTING SCRAPER
# ─────────────────────────────────────────────


async def scrape_podcast_listings(website_url: str) -> PodcastListingScrapeResponse:
    """
    Scrape podcast listings from websites like millionpodcasts.com using Playwright.
    Handles JavaScript-rendered content.
    """
    result = PodcastListingScrapeResponse(
        website_url=website_url,
        podcasts=[],
        total_found=0,
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

        # Block images/fonts for faster loading
        await page.route(
            "**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf}",
            lambda route: route.abort(),
        )

        print(f"🌐 Fetching podcast listings from: {website_url}")
        
        # Navigate to website - use domcontentloaded for speed (faster than networkidle)
        await page.goto(website_url, wait_until="domcontentloaded", timeout=25000)
        
        # Wait a bit for JavaScript to render (shorter than networkidle)
        await asyncio.sleep(1)

        # Handle consent dialogs (common on listing sites)
        try:
            await page.click('button:has-text("Accept"), button:has-text("Accept all"), [aria-label*="Accept"]', timeout=3000)
            await asyncio.sleep(1)
        except Exception:
            pass

        # Scroll to trigger lazy loading
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1)  # Reduced from 2 seconds for speed

        podcasts: List[PodcastListingItem] = []
        seen_urls = set()

        # Strategy 1: Look for podcast/show cards (most common on listing sites)
        card_selectors = [
            'article',
            'div[class*="podcast"]',
            'div[class*="show"]',
            'div[class*="card"]',
            'div[class*="item"]',
            'li[class*="podcast"]',
            'li[class*="show"]',
            '[class*="listing"]',
        ]

        print(f"  🔍 Searching for podcast cards...")
        
        # Try each selector
        for selector in card_selectors:
            try:
                # Count matching elements
                count = await page.locator(selector).count()
                
                if count > 2:  # Only use if we found multiple cards
                    print(f"  ✅ Found {count} items using: {selector}")
                    
                    # Extract all podcast data in one JavaScript batch (much faster)
                    max_cards = min(count, 50)  # Limit to 50 for speed
                    
                    extracted_data = await page.evaluate(f"""
                    (async () => {{
                        const selector = "{selector}";
                        const cards = document.querySelectorAll(selector);
                        const results = [];
                        
                        for (let i = 0; i < Math.min(cards.length, {max_cards}); i++) {{
                            const card = cards[i];
                            
                            // Extract title
                            const titleElem = card.querySelector('a, h1, h2, h3, h4, .title, [class*="title"]');
                            let title = titleElem?.textContent?.trim() || '';
                            title = title.slice(0, 200);
                            
                            if (!title || title.length < 2) continue;
                            
                            // Extract URL
                            const linkElem = card.querySelector('a[href]');
                            let url = linkElem?.href || '';
                            
                            if (!url) continue;
                            
                            // Extract description
                            const descElem = card.querySelector('p, [class*="description"], [class*="desc"]');
                            let description = descElem?.textContent?.trim() || null;
                            if (description) description = description.slice(0, 500);
                            
                            // Extract host
                            const hostElem = card.querySelector('[class*="host"], [class*="author"], [class*="creator"]');
                            let host = hostElem?.textContent?.trim() || null;
                            
                            // Extract image
                            const imgElem = card.querySelector('img');
                            let image_url = imgElem?.src || null;
                            
                            results.push({{ title, url, description, host, image_url }});
                        }}
                        
                        return results;
                    }})()
                    """)
                    
                    # Process results
                    for item in extracted_data:
                        try:
                            title = item.get('title', '').strip()
                            url = item.get('url', '').strip()
                            description = item.get('description')
                            host = item.get('host')
                            image_url = item.get('image_url')
                            
                            if not title or not url:
                                continue
                            
                            # Make URL absolute if relative
                            if url.startswith('/'):
                                base_url = urlparse(website_url).scheme + '://' + urlparse(website_url).netloc
                                url = base_url + url
                            elif not url.startswith('http'):
                                url = website_url.rstrip('/') + '/' + url
                            
                            # Avoid duplicates
                            if url in seen_urls:
                                continue
                            seen_urls.add(url)
                            
                            # Make image URL absolute if needed
                            if image_url and image_url.startswith('/'):
                                base_url = urlparse(website_url).scheme + '://' + urlparse(website_url).netloc
                                image_url = base_url + image_url
                            
                            podcast = PodcastListingItem(
                                title=title,
                                url=url,
                                description=description,
                                host=host,
                                image_url=image_url,
                            )
                            podcasts.append(podcast)
                            
                        except Exception as e:
                            print(f"    ⚠️  Error processing item: {str(e)[:50]}")
                            continue
                    
                    if podcasts:
                        break  # Found valid podcasts with this selector
                        
            except Exception as e:
                print(f"    ⚠️  Selector error ({selector}): {str(e)[:50]}")
                continue

        # Strategy 2: Fallback to extracting all links on page
        if not podcasts:
            print(f"  ⚠️  No cards found, falling back to link extraction...")
            try:
                # Use JavaScript to extract all links at once (faster)
                links_data = await page.evaluate("""
                () => {
                    const links = document.querySelectorAll('a[href]');
                    const results = [];
                    
                    for (let i = 0; i < Math.min(links.length, 50); i++) {
                        const link = links[i];
                        let title = link.textContent?.trim() || '';
                        let url = link.href || '';
                        
                        if (!title || title.length < 2 || title.length > 200) continue;
                        if (!url || url.startsWith('#')) continue;
                        
                        // Skip navigation
                        if (['search', 'filter', 'category', 'tag', 'page='].some(s => url.toLowerCase().includes(s))) continue;
                        
                        results.push({ title, url });
                    }
                    
                    return results;
                }
                """)
                
                for item in links_data:
                    try:
                        url = item.get('url', '').strip()
                        title = item.get('title', '').strip()
                        
                        # Avoid duplicates
                        if url in seen_urls:
                            continue
                        seen_urls.add(url)
                        
                        podcast = PodcastListingItem(
                            title=title,
                            url=url,
                        )
                        podcasts.append(podcast)
                    except Exception:
                        continue
                        
            except Exception as e:
                print(f"    ⚠️  Fallback extraction error: {str(e)[:50]}")

        result.podcasts = podcasts[:100]  # Limit to 100 podcasts
        result.total_found = len(podcasts)
        print(f"  ✅ Extracted {len(podcasts)} podcasts")

    except PlaywrightTimeout:
        result.error = f"Timeout loading {website_url}"
        print(f"  ❌ Timeout: {result.error}")
    except Exception as e:
        result.error = f"Scraping error: {str(e)}"
        print(f"  ❌ Error: {result.error}")
    finally:
        await context.close()

    return result


@app.post("/scrape/podcast-listings")
async def scrape_podcast_listings_endpoint(body: PodcastListingScrapeRequest):
    """Scrape podcast listings - run pipeline completely, then return all results."""
    website_url = body.website_url
    
    try:
        print(f"\n🚀 Starting pipeline for {website_url}")
        
        # Run the COMPLETE pipeline (synchronously, blocking)
        await run_pipeline(website_url)
        
        print(f"✅ Pipeline complete! Reading results...")
        
        # Load ALL results from email file
        results = load_email_results()
        
        # Ensure all items are valid PodcastListingItem objects
        valid_podcasts = []
        for item in results:
            try:
                podcast = PodcastListingItem(**item) if isinstance(item, dict) else item
                valid_podcasts.append(podcast)
            except Exception as e:
                print(f"  ⚠️  Skipping invalid item: {str(e)[:50]}")
                continue
        
        print(f"📊 Total shows with emails: {len(valid_podcasts)}")
        
        return PodcastListingScrapeResponse(
            podcasts=valid_podcasts,
            total_found=len(valid_podcasts),
            error=None
        )
        
    except Exception as e:
        print(f"❌ Error in scraping pipeline: {str(e)}")
        return PodcastListingScrapeResponse(
            podcasts=[],
            total_found=0,
            error=str(e)
        )

if __name__ == "__main__":
    import os
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)