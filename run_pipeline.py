import asyncio
import os

from scrape_shows import scrape_all
from find_emails import main as find_emails_main


SHOWS_FILE = "shows.txt"
EMAIL_FILE = "shows_with_email.txt"


async def run_pipeline(url: str):

    # Clear previous files
    for f in [SHOWS_FILE, EMAIL_FILE]:
        if os.path.exists(f):
            os.remove(f)

    print("\nStep 1: Scraping shows...\n")

    await scrape_all([url])

    print("\nStep 2: Finding emails...\n")

    await find_emails_main()

    print("\nPipeline finished successfully\n")


if __name__ == "__main__":
    asyncio.run(run_pipeline("https://www.millionpodcasts.com/witchcraft-podcasts/"))