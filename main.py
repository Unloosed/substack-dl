import os
import feedparser
import requests
from readability import Document
from markdownify import markdownify as md

# === CONFIG ===
SUBSTACK_URL = "https://treeofwoe.substack.com"  # Change to any Substack
SAVE_AS = "markdown"  # "markdown" or "html"
SAVE_DIR = "substack_posts"

# === SETUP ===
FEED_URL = f"{SUBSTACK_URL}/feed"
os.makedirs(SAVE_DIR, exist_ok=True)

# === FETCH FEED ===
feed = feedparser.parse(FEED_URL)
print(f"Found {len(feed.entries)} posts...")

for entry in feed.entries:
    title = entry.title
    url = entry.link
    slug = title.replace(" ", "_").replace("/", "-").strip()[:100]

    try:
        print(f"Downloading: {title}")
        resp = requests.get(url)
        doc = Document(resp.text)
        content_html = doc.summary()

        if SAVE_AS == "markdown":
            content = md(content_html)
            ext = "md"
        else:
            content = content_html
            ext = "html"

        filepath = os.path.join(SAVE_DIR, f"{slug}.{ext}")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n{content}")
    except Exception as e:
        print(f"Failed to process {url}: {e}")

print("Done.")
