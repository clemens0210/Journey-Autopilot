import requests
from bs4 import BeautifulSoup
from datetime import datetime

SOURCES = [
    "https://www.bahn.de/service/informationen-buchung/fahrgastrechte",
    "https://www.bahn.de/faq/pk/service/buchung/fahrgastrechte",
    "https://www.bahn.de/service/informationen-buchung/fahrgastrechte/rechtliche-regelungen",
]

def crawl_all() -> list[dict]:
    documents = []
    for url in SOURCES:
        try:
            doc = _crawl_page(url)
            if doc:
                documents.append(doc)
                print(f"✓ Crawled: {url}")
        except Exception as e:
            print(f"✗ Error crawling {url}: {e}")
    return documents

def _crawl_page(url: str) -> dict | None:
    headers = {"User-Agent": "Mozilla/5.0 (Journey-Autopilot-PoC)"}
    response = requests.get(url, headers=headers, timeout=10)
    
    if response.status_code != 200:
        return None
    
    soup = BeautifulSoup(response.text, "html.parser")
    
    # Remove noise
    for tag in soup(["nav", "footer", "script", "style", "header"]):
        tag.decompose()
    
    text = soup.get_text(separator="\n", strip=True)
    
    # Reduce empty lines
    lines = [l for l in text.splitlines() if len(l.strip()) > 20]
    clean_text = "\n".join(lines)
    
    return {
        "source": url,
        "content": clean_text,
        "crawled_at": datetime.now().isoformat()
    }