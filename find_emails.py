import asyncio
import json
import re
import aiohttp
import os


INPUT_FILE = "shows.txt"
OUTPUT_FILE = "shows_with_email.txt"

EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
)


def load_shows():

    shows = []

    if not os.path.exists(INPUT_FILE):
        print("shows.txt not found")
        return shows

    with open(INPUT_FILE) as f:
        for line in f:
            try:
                shows.append(json.loads(line))
            except:
                pass

    return shows


def save_email_show(show):

    with open(OUTPUT_FILE, "a") as f:
        f.write(json.dumps(show) + "\n")


async def fetch(session, url):

    try:
        async with session.get(url, timeout=10) as r:

            if r.status != 200:
                return ""

            return await r.text()

    except:
        return ""


def extract_email(text):

    emails = EMAIL_REGEX.findall(text)

    if emails:
        return emails[0]

    return None


async def scan_show(session, show):

    links = [
        show.get("website"),
        show.get("spotify"),
        show.get("apple"),
        show.get("youtube"),
    ]

    for link in links:

        if not link:
            continue

        html = await fetch(session, link)

        if not html:
            continue

        email = extract_email(html)

        if email:

            show["email"] = email
            show["email_source"] = link

            save_email_show(show)

            print("EMAIL FOUND:", email)

            return show

    return None


async def main():

    shows = load_shows()

    print("TOTAL SHOWS LOADED:", len(shows))

    async with aiohttp.ClientSession() as session:

        tasks = [scan_show(session, s) for s in shows]

        results = await asyncio.gather(*tasks)

    valid = [r for r in results if r]

    print("\nEMAILS FOUND:", len(valid))
    print("Saved to shows_with_email.txt")
    print("\nAll shows processed successfully")


# asyncio.run(main())
if __name__ == "__main__":
    asyncio.run(scrape_all())