# YouTube Scraper Service — FastAPI + Playwright

A headless browser microservice that scrapes social links, website links,
and contact emails from YouTube channel About pages.

---

## Install

```bash
pip install fastapi uvicorn playwright
playwright install chromium
```

---

## Run

```bash
uvicorn main:app --host 0.0.0.0 --port 5050 --reload
```

---

## Swagger Docs (free with FastAPI)

Open in your browser once the service is running:

```
http://localhost:5050/docs
```

You can test every endpoint directly from the UI — no curl needed.

---

## Endpoints

### `GET /health`
Check if the service and browser are alive.

### `POST /scrape`
Scrape a single channel.

```json
// Request
{ "handle": "@mkbhd" }

// Response
{
  "handle": "@mkbhd",
  "url": "https://www.youtube.com/@mkbhd/about",
  "email": "contact@mkbhd.com",
  "social_links": ["https://instagram.com/mkbhd", "https://twitter.com/mkbhd"],
  "website_links": ["https://mkbhd.com"],
  "all_links": ["https://instagram.com/mkbhd", "https://twitter.com/mkbhd", "https://mkbhd.com"],
  "raw_description": "...",
  "error": null
}
```

### `POST /scrape/bulk`
Scrape multiple channels in parallel.

```json
// Request
{
  "handles": ["@mkbhd", "@linustechtips", "@veritasium"],
  "concurrency": 3
}

// Response
{
  "results": [ ...ScrapeResponse array... ],
  "total": 3,
  "succeeded": 3,
  "failed": 0
}
```

---

## Node.js Integration

```js
const { scrapeYouTubeLinks, scrapeYouTubeLinksInBulk } = require("./youtube-scraper-client");

// Single
const result = await scrapeYouTubeLinks("@mkbhd");
console.log(result.email, result.social_links);

// Bulk
const bulk = await scrapeYouTubeLinksInBulk(["@mkbhd", "@linustechtips"], 3);
console.log(bulk.succeeded, bulk.results);
```

Set `SCRAPER_URL` env var if not running on localhost:

```bash
SCRAPER_URL=http://my-python-service:5050 node server.js
```

---

## Architecture Notes

| Thing | Detail |
|---|---|
| Browser | One shared Chromium instance, launched at startup |
| Isolation | Each request gets its own browser context (like incognito) |
| Concurrency | Fully async — multiple `/scrape` requests run in parallel |
| Speed | Images/fonts are blocked to reduce load time |
| Redirects | YouTube's `/redirect?q=` tracking links are decoded automatically |

---

## Production Deployment

**With PM2 (recommended alongside Node.js):**
```bash
pm2 start "uvicorn youtube_scraper_service:app --host 0.0.0.0 --port 5050" --name youtube-scraper
pm2 save
```

**With Docker:**
```dockerfile
FROM python:3.11-slim
RUN pip install fastapi uvicorn playwright && playwright install --with-deps chromium
COPY youtube_scraper_service.py .
CMD ["uvicorn", "youtube_scraper_service:app", "--host", "0.0.0.0", "--port", "5050"]
```
