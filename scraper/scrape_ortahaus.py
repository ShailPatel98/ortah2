#!/usr/bin/env python3
import os, re, json, time
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

BASE_URL = os.getenv("BASE_URL", "https://ortahaus.com")
OUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
os.makedirs(OUT_DIR, exist_ok=True)
OUT_FILE = os.path.join(OUT_DIR, "products.json")

UA = "Mozilla/5.0 (compatible; OrtahausBot/1.0; +https://example.com)"
session = requests.Session()
session.headers.update({"User-Agent": UA})

def clean(s): return re.sub(r"\s+", " ", (s or "").strip())

def fetch(url):
    r = session.get(url, timeout=30, allow_redirects=True)
    r.raise_for_status()
    return r.text

def get_sitemap_products():
    urls = set()
    for path in ("/sitemap_products_1.xml", "/sitemap.xml"):
        try:
            xml = fetch(urljoin(BASE_URL, path))
            for m in re.finditer(r"<loc>(.*?)</loc>", xml, re.I):
                u = m.group(1).strip()
                if "/products/" in u:
                    urls.add(u.split("?")[0])
        except Exception:
            continue
    return urls

def crawl_collections():
    seeds = [
        "/", "/collections/all", "/collections", "/products", "/search?q=products"
    ]
    urls = set()
    for s in seeds:
        try:
            html = fetch(urljoin(BASE_URL, s))
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.select("a[href*='/products/']"):
                href = a.get("href", "")
                if "/products/" in href:
                    urls.add(urljoin(BASE_URL, href.split("?")[0]))
        except Exception:
            pass
    return urls

def parse_product(url):
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    title = soup.title.get_text(strip=True) if soup.title else url
    md = soup.find("meta", attrs={"name": "description"})
    desc = md["content"].strip() if md and md.has_attr("content") else ""

    # ld+json Product (description often better)
    for tag in soup.find_all("script", attrs={"type":"application/ld+json"}):
        try:
            data = json.loads(tag.string or "{}")
            if isinstance(data, dict) and data.get("@type") in ("Product","product"):
                desc = data.get("description") or desc
                title = data.get("name") or title
        except Exception:
            pass

    def section(label_words):
        for h in soup.find_all(re.compile("^h[1-6]$")):
            htxt = clean(h.get_text(" ", strip=True)).lower()
            if any(w in htxt for w in label_words):
                frag = []
                sib = h.find_next_sibling()
                for _ in range(5):
                    if not sib:
                        break
                    if sib.name in ("p","ul","ol","div","li"):
                        frag.append(clean(sib.get_text(" ", strip=True)))
                    sib = sib.find_next_sibling()
                s = " ".join(x for x in frag if x)
                if s:
                    return s[:500]
        return ""

    how_to = section(["how to use","how-to","usage","use"])
    ingredients = section(["ingredients","what's inside","what’s inside"])

    bullets = []
    for li in soup.select("li"):
        t = clean(li.get_text(" ", strip=True))
        if 10 <= len(t) <= 180 and any(k in t.lower() for k in ["hold","finish","texture","volume","frizz","shine","powder","spray","clay","cream"]):
            bullets.append(t)
    bullets = list(dict.fromkeys(bullets))[:10]

    rec = {
        "id": url, "url": url,
        "title": clean(title),
        "description": clean(desc),
        "how_to_use": clean(how_to),
        "ingredients": clean(ingredients),
        "bullets": bullets,
        "tags": [],
    }
    # normalize (no nulls)
    for k,v in list(rec.items()):
        if v is None: rec[k] = "" if k != "tags" else []
    return rec

def main():
    urls = set()
    urls |= get_sitemap_products()
    if not urls:
        print("Sitemap empty; crawling collections instead…")
        urls |= crawl_collections()

    urls = sorted(urls)
    if not urls:
        print("No product URLs found via sitemap or crawl.")
        return

    print(f"Found {len(urls)} product URLs")
    out = []
    for i,u in enumerate(urls,1):
        try:
            rec = parse_product(u)
            out.append(rec)
            print(f"[{i}/{len(urls)}] scraped: {rec['title']}")
            time.sleep(0.2)
        except Exception as e:
            print(f"[{i}/{len(urls)}] ERROR {u} -> {e}")

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("Wrote:", OUT_FILE)

if __name__ == "__main__":
    main()
