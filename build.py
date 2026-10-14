"""StazGames static site generator. Usage: python build.py [--offline]"""
import json, os, re, sys, html, datetime as dt
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, urlsplit, urlunsplit
import requests, feedparser
import config as C

CT = ZoneInfo("America/Chicago")
UA = {"User-Agent": "StazGamesBot/1.0 (+%s)" % C.BASE_URL}
FEEDS = {
 "IGN": "https://feeds.feedburner.com/ign/all",
 "PC Gamer": "https://www.pcgamer.com/rss/",
 "VGC": "https://www.videogameschronicle.com/feed/",
 "Eurogamer": "https://www.eurogamer.net/feed",
 "Gematsu": "https://www.gematsu.com/feed",
}
FAILURES = []
SUMMARY_LIMIT = 220
SUMMARY_MIN = 40
DANGLING = {
    "a", "an", "the", "and", "or", "but", "of", "to", "in", "for", "with",
    "on", "at", "by", "from", "as", "if", "that", "this", "its", "via",
    "into", "over", "after", "before", "about", "than",
}
LABELS = {
    "Epic Games Store": "Epic",
    "Epic Games": "Epic",
    "Humble Store": "Humble",
    "GamerPower": "GamerPower",
    "Steam": "Steam",
    "GOG": "GOG",
    "Fanatical": "Fanatical",
    "PC Gamer": "PC Gamer",
    "Eurogamer": "Eurogamer",
    "Gematsu": "Gematsu",
    "IGN": "IGN",
    "VGC": "VGC",
}
COPY = "Get the best free games and deals in your inbox every morning. Free, unsubscribe anytime."

def get(url, timeout=20):
    try:
        r = requests.get(url, headers=UA, timeout=timeout); r.raise_for_status(); return r
    except Exception as e:
        FAILURES.append(f"{url}: {e}"); return None

def esc(s): return html.escape(str(s or ""), quote=True)
def strip(s): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(s or ""))).strip()

def strip_boilerplate(text, source=""):
    text = re.sub(r"\s*the post\s+.{0,220}?\s+appeared first on\s+[^.…]+[.…]?", " ", text, flags=re.I)
    text = re.sub(r"\s*this (?:article|post) (?:originally )?appeared (?:on|at|in)\s+[^.…]+[.…]?", " ", text, flags=re.I)
    text = re.sub(r"\s*\b(?:continue reading|read more|read the full article|read the full story|click here)(?:\s+at\s+[\w .'-]+)?[.…]?", " ", text, flags=re.I)
    text = re.sub(r"\s+\bSource\b\s*$", "", text, flags=re.I)
    if source:
        text = re.sub(rf"\s*[-–—|]\s*{re.escape(source)}\s*$", "", text, flags=re.I)
        text = re.sub(rf"^\s*{re.escape(source)}\s*[:\-–—|]\s*", "", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip(" |-–—")

def split_sentences(text):
    parts, start = [], 0
    for m in re.finditer(r"[.!?](?:[\"'”’)\]]+)?(?=\s+|$)", text):
        token = text[max(0, m.start() - 10):m.start()]
        if m.group(0).startswith(".") and (re.search(r"\d$", token) or re.search(r"(?:Mr|Mrs|Ms|Dr|St|vs|Inc|Ltd|U\.S|U\.K|No)$", token)):
            continue
        chunk = text[start:m.end()].strip()
        if chunk:
            parts.append(chunk)
        start = m.end()
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts

def is_thin(text, title="", source=""):
    t = re.sub(r"\s+", " ", text or "").strip()
    if len(t) < SUMMARY_MIN:
        return True
    bare = t.rstrip(".!?…").strip()
    if not bare:
        return True
    if source and bare.lower() == source.strip().lower():
        return True
    title_key = re.sub(r"\s+", " ", title or "").strip().rstrip(".!?").lower()
    if title_key and bare.lower() == title_key:
        return True
    if re.fullmatch(r"[\w.&'’+-]+(?:\s+[\w.&'’+-]+){0,3}", bare) and re.search(r"\b(bros|inc|ltd|llc|gmbh|studio|studios)\b", bare, re.I):
        return True
    return False

def close_sentence(text):
    words = text.split()
    while words and words[-1].lower().strip(".,;:!?\"'”’") in DANGLING:
        words.pop()
    text = " ".join(words).rstrip(" ,;:-–—")
    if not text:
        return ""
    if text[-1] not in ".!?":
        text += "."
    return text

def salvage_ellipsis(text):
    """Publisher feeds often cut a sentence with an ellipsis. Keep whole sentences."""
    if not re.search(r"(?:…|\.\.\.)\s*$", text):
        return text
    text = re.sub(r"\s*(?:…|\.\.\.)\s*$", "", text).strip()
    parts = split_sentences(text)
    complete = [p for p in parts if p[-1:] in ".!?"]
    if parts and parts[-1][-1:] not in ".!?":
        if complete:
            return " ".join(complete)
        fragment = parts[-1]
        if "," in fragment:
            head, tail = fragment.rsplit(",", 1)
            if 0 < len(tail.strip().split()) <= 3 and len(tail.strip()) < 32 and len(head.strip()) >= SUMMARY_MIN:
                fragment = head.strip()
        return close_sentence(fragment)
    return " ".join(complete) if complete else close_sentence(text)

def clip_words(text, limit=SUMMARY_LIMIT):
    text = text.strip()
    if not text:
        return ""
    if len(text) <= limit and text[-1] in ".!?":
        return text
    if len(text) <= limit:
        return close_sentence(text)
    chunk = text[:limit + 1]
    sp = chunk.rfind(" ")
    if sp >= SUMMARY_MIN:
        chunk = chunk[:sp]
    chunk = chunk.rstrip(" ,;:-–—\"'”’")
    words = chunk.split()
    while words and words[-1].lower().strip(".,;:!?\"'”’") in DANGLING:
        words.pop()
    chunk = " ".join(words).rstrip(" ,;:-–—\"'”’")
    if len(chunk) < SUMMARY_MIN:
        return ""
    if chunk[-1] not in ".!?":
        chunk += "…"
    return chunk

def summarize(title, summary, source=""):
    """Complete sentences within about 220 characters, or nothing if the teaser is too thin."""
    text = strip_boilerplate(strip(summary), source)
    if is_thin(text, title, source):
        return ""
    text = salvage_ellipsis(text)
    text = re.sub(r"\s+", " ", text).strip()
    if is_thin(text, title, source):
        return ""
    kept, total = [], 0
    for part in split_sentences(text):
        part = part.strip()
        if not part:
            continue
        gap = 1 if kept else 0
        if part[-1:] in ".!?" and total + gap + len(part) <= SUMMARY_LIMIT:
            kept.append(part)
            total += gap + len(part)
            continue
        if not kept:
            clipped = clip_words(part)
            return "" if is_thin(clipped, title, source) else clipped
        break
    out = " ".join(kept).strip() or clip_words(text)
    return "" if is_thin(out, title, source) else out

def shrink_image(url):
    parts = urlsplit(url)
    path, query = parts.path, parts.query
    host = parts.netloc.lower()
    if "futurecdn.net" in host:
        path = re.sub(r"-\d{3,4}-\d{2}(\.(?:jpe?g|png|webp))$", r"-480-80\1", path)
    if "ignimgs.com" in host and not re.search(r"(?:^|&)width=", query):
        query = "width=480" + (("&" + query) if query else "")
    if "gnwcdn.com" in host and query:
        query = re.sub(r"(^|&)width=\d+", r"\1width=480", query)
    if host.endswith("epicgames.com") and "resize=" not in query:
        query = "resize=1&w=320&h=180" + (("&" + query) if query else "")
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))

def safe_img(url):
    url = html.unescape(str(url or "")).strip()
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    if not url or len(url) > 2000:
        return ""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    low = url.lower()
    if any(b in low for b in ("doubleclick", "scorecardresearch", "facebook.com/tr", "1x1", "pixel.gif", "spacer.gif")):
        return ""
    return shrink_image(url)

def looks_image(url):
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"))

def entry_image(entry):
    """Thumbnail already carried by the feed. No per-article page fetch."""
    for thumb in entry.get("media_thumbnail") or []:
        image = safe_img(thumb.get("url"))
        if image:
            return image
    for content in entry.get("media_content") or []:
        url = content.get("url") or ""
        mime = content.get("type") or ""
        medium = content.get("medium") or ""
        if not (mime.startswith("image") or medium == "image" or looks_image(url)):
            continue
        try:
            width, height = int(content.get("width") or 0), int(content.get("height") or 0)
        except (TypeError, ValueError):
            width = height = 0
        if (0 < width < 40) or (0 < height < 40):
            continue
        image = safe_img(url)
        if image:
            return image
    for link in list(entry.get("links") or []) + list(entry.get("enclosures") or []):
        if (link.get("type") or "").startswith("image") or link.get("rel") == "enclosure":
            if link.get("rel") == "enclosure" and not (link.get("type") or "").startswith("image") and not looks_image(link.get("href") or link.get("url") or ""):
                continue
            image = safe_img(link.get("href") or link.get("url"))
            if image:
                return image
    blob = entry.get("summary") or ""
    content = entry.get("content") or []
    if content and isinstance(content, list):
        blob += " " + str((content[0] or {}).get("value") or "")
    match = re.search(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", blob, re.I)
    if match:
        image = safe_img(match.group(1))
        if image:
            return image
    for pattern in (
        r"property=[\"']og:image[\"'][^>]*content=[\"']([^\"']+)[\"']",
        r"content=[\"']([^\"']+)[\"'][^>]*property=[\"']og:image[\"']",
    ):
        match = re.search(pattern, blob, re.I)
        if match:
            image = safe_img(match.group(1))
            if image:
                return image
    return ""

def short_label(name):
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    if name in LABELS:
        return LABELS[name]
    bit = re.split(r"[,/|]", name)[0].strip()
    return LABELS.get(bit, (bit or "Store")[:14])

def epic_image(game):
    images = game.get("keyImages") or []
    by_type = {}
    for image in images:
        if image.get("type") and image.get("url") and image["type"] not in by_type:
            by_type[image["type"]] = image["url"]
    for kind in ("Thumbnail", "OfferImageWide", "DieselStoreFrontWide", "OfferImageTall"):
        if by_type.get(kind):
            return safe_img(by_type[kind])
    for image in images:
        if image.get("url"):
            return safe_img(image["url"])
    return ""

def fetch_epic(offline=False):
    if offline: return []
    r = get("https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=en-US&country=US")
    out = []
    if not r: return out
    try:
        now = dt.datetime.now(dt.timezone.utc)
        for g in r.json()["data"]["Catalog"]["searchStore"]["elements"]:
            promos = (g.get("promotions") or {}).get("promotionalOffers") or []
            for p in promos:
                for o in p.get("promotionalOffers", []):
                    if (o.get("discountSetting") or {}).get("discountPercentage") != 0: continue
                    end = dt.datetime.fromisoformat(o["endDate"].replace("Z", "+00:00"))
                    start = dt.datetime.fromisoformat(o["startDate"].replace("Z", "+00:00"))
                    if not (start <= now <= end): continue
                    slug = (g.get("productSlug") or "") or next((m.get("pageSlug") for m in (g.get("catalogNs") or {}).get("mappings") or [] if m.get("pageSlug")), "") or (g.get("offerMappings") or [{}])[0].get("pageSlug", "")
                    out.append({"title": g["title"], "platform": "Epic Games Store", "source": "Epic Games",
                        "url": f"https://store.epicgames.com/en-US/p/{slug}" if slug else "https://store.epicgames.com/en-US/free-games",
                        "worth": (g.get("price") or {}).get("totalPrice", {}).get("fmtPrice", {}).get("originalPrice", ""),
                        "ends": end.isoformat(), "image": epic_image(g)})
    except Exception as e: FAILURES.append(f"epic parse: {e}")
    return out

def fetch_gamerpower(offline=False):
    if offline: return []
    r = get("https://www.gamerpower.com/api/giveaways?type=game")
    out = []
    if not r: return out
    try:
        for g in r.json()[:30]:
            end = g.get("end_date")
            out.append({"title": g["title"].replace(" Giveaway", ""), "platform": g.get("platforms", ""), "source": "GamerPower",
                "url": g.get("open_giveaway_url") or g.get("gamerpower_url"), "worth": g.get("worth", ""),
                "ends": "" if not end or end == "N/A" else end.replace(" ", "T"),
                "image": safe_img(g.get("thumbnail") or g.get("image"))})
    except Exception as e: FAILURES.append(f"gamerpower parse: {e}")
    return out

def fetch_deals(offline=False):
    if offline: return []
    r = get("https://www.cheapshark.com/api/1.0/deals?storeID=1,7,11,25&sortBy=Deal%20Rating&pageSize=40&onSale=1")
    stores = {}
    s = get("https://www.cheapshark.com/api/1.0/stores")
    if s:
        try: stores = {x["storeID"]: x for x in s.json()}
        except Exception: pass
    out, seen = [], set()
    if not r: return out
    try:
        for d in r.json():
            if d["title"] in seen: continue
            seen.add(d["title"])
            info = stores.get(d["storeID"]) or {}
            icon = ""
            if (info.get("images") or {}).get("icon"):
                icon = safe_img("https://www.cheapshark.com" + info["images"]["icon"])
            out.append({"title": d["title"], "store": info.get("storeName") or ("Store " + d["storeID"]),
                "price": d["salePrice"], "normal": d["normalPrice"], "savings": round(float(d["savings"])),
                "metacritic": d.get("metacriticScore"),
                "url": "https://www.cheapshark.com/redirect?dealID=" + d["dealID"],
                "image": safe_img(d.get("thumb")), "icon": icon})
    except Exception as e: FAILURES.append(f"cheapshark parse: {e}")
    return out[:30]

def fetch_news(offline=False):
    items = []
    if offline: return items
    for name, url in FEEDS.items():
        r = get(url)
        if not r: continue
        f = feedparser.parse(r.content)
        if not f.entries: FAILURES.append(f"{name}: feed had no entries"); continue
        for e in f.entries[:10]:
            t = e.get("published_parsed") or e.get("updated_parsed")
            when = dt.datetime(*t[:6], tzinfo=dt.timezone.utc) if t else dt.datetime.now(dt.timezone.utc)
            title = strip(e.get("title"))
            items.append({"title": title, "source": name, "url": e.get("link"), "published": when.isoformat(),
                          "summary": summarize(title, e.get("summary", ""), name), "image": entry_image(e)})
    items.sort(key=lambda x: x["published"], reverse=True)
    return items[:50]

def ct(iso, fmt="%b %d, %I:%M %p CT"):
    try: return dt.datetime.fromisoformat(iso).astimezone(CT).strftime(fmt)
    except Exception: return ""

# ---------- rendering ----------
CSS = """:root{--bg:#0f1512;--card:#18221d;--fg:#e9f2ec;--mut:#9fb3a7;--acc:#4ade80;--acc2:#a3e635}
*{box-sizing:border-box}body{margin:0;font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg)}
a{color:var(--acc)}header,main,footer{max-width:1000px;margin:auto;padding:0 16px}
header{display:flex;flex-wrap:wrap;align-items:center;gap:12px;padding-top:12px}
.logo{font-weight:800;font-size:1.4rem;color:var(--fg);text-decoration:none}.logo span{color:var(--acc)}
nav{display:flex;flex-wrap:wrap;gap:14px}nav a{text-decoration:none;color:var(--mut)}nav a[aria-current]{color:var(--acc)}
.upd{color:var(--mut);font-size:.85rem;margin:4px 0 16px}h1{font-size:1.7rem;margin:.4em 0}h2{border-bottom:2px solid var(--card);padding-bottom:4px}
ul.cards{list-style:none;padding:0;display:grid;gap:10px;grid-template-columns:1fr}
@media(min-width:700px){ul.cards{grid-template-columns:1fr 1fr}}
.cards li{background:var(--card);border-radius:10px;padding:12px 14px}
.card{display:flex;gap:12px;align-items:flex-start}.card-body{min-width:0;flex:1}
.cards h3{margin:0 0 4px;font-size:1.05rem}.cards h3 a{overflow-wrap:anywhere}
.thumb,.icon,.mark{width:160px;height:90px;flex:none;border-radius:8px;background:#102018}
.thumb{object-fit:cover;display:block}.icon{object-fit:contain;display:block;padding:10px;box-sizing:border-box}
.mark{box-sizing:border-box;display:flex;align-items:center;justify-content:center;text-align:center;color:var(--acc);font-weight:800;font-size:.78rem;line-height:1.15;padding:6px;border:1px solid #2c4a38}
.meta{color:var(--mut);font-size:.88rem}.price{color:var(--acc2);font-weight:700}s{color:var(--mut)}
.badge{background:var(--acc);color:#062;border-radius:6px;padding:0 6px;font-weight:700;font-size:.8rem}
.signup{background:var(--card);border:1px dashed var(--acc);border-radius:10px;padding:16px;margin:24px 0}
.signup form{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.signup input[type=email]{padding:8px;border-radius:6px;border:0;max-width:100%;min-width:0}
.signup button{padding:8px 14px;border:0;border-radius:6px;background:var(--acc);font-weight:700;cursor:pointer}
.form-msg{flex-basis:100%;margin:4px 0 0}.form-msg.bad{color:#fca5a5}
.hp{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:699px){.thumb,.icon,.mark{width:96px;height:54px}.signup input[type=email],.signup button{flex:1 1 100%}}
footer{color:var(--mut);font-size:.85rem;padding:24px 16px;border-top:1px solid var(--card);margin-top:32px}.empty{color:var(--mut);font-style:italic}"""

LOGO_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#18221d"/><ellipse cx="32" cy="38" rx="20" ry="16" fill="#a0703f"/><circle cx="25" cy="34" r="3" fill="#222"/><circle cx="39" cy="34" r="3" fill="#222"/><ellipse cx="32" cy="43" rx="7" ry="4" fill="#6b4423"/><path d="M12 32a20 20 0 0 1 40 0" stroke="#4ade80" stroke-width="5" fill="none"/><rect x="8" y="30" width="7" height="12" rx="3" fill="#4ade80"/><rect x="49" y="30" width="7" height="12" rx="3" fill="#4ade80"/></svg>'

NAV = [("/", "Home"), ("/free-games/", "Free games"), ("/deals/", "Deals"), ("/news/", "News"), ("/newsletter/", "Newsletter"), ("/about/", "About")]

def href(path):
    """Root-relative URL, prefixed when BASE_URL has a project path (GitHub Pages).

    Files in OUT_DIR stay unprefixed. Pages mounts that directory at the project path.
    """
    prefix = urlparse(C.BASE_URL).path.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return prefix + "/" if path == "/" else prefix + path

def form_action():
    """GitHub Pages has no function, so the form posts to the Vercel site."""
    host = urlparse(C.BASE_URL).netloc.lower()
    if host.endswith("github.io"):
        return "https://stazgames.com/api/subscribe"
    return href("/api/subscribe")

def page(path, title, desc, body, updated, jsonld=()):
    url = C.BASE_URL + path
    org = {"@context": "https://schema.org", "@type": "Organization", "name": C.SITE_NAME, "url": C.BASE_URL + "/",
           "logo": C.BASE_URL + "/logo.svg", "sameAs": [f"https://x.com/{C.X_HANDLE}"],
           "founder": {"@type": "Person", "name": C.OPERATOR, "sameAs": f"https://x.com/{C.OPERATOR_X}"}}
    site = {"@context": "https://schema.org", "@type": "WebSite", "name": C.SITE_NAME, "url": C.BASE_URL + "/", "description": C.TAGLINE}
    lds = "".join(f'<script type="application/ld+json">{json.dumps(j, ensure_ascii=False).replace("</", "<\\/")}</script>' for j in (org, site, *jsonld))
    nav = "".join(f'<a href="{href(h)}"{" aria-current=page" if h == path else ""}>{n}</a>' for h, n in NAV)
    iso = updated.isoformat()
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><meta name="description" content="{esc(desc)}"><link rel="canonical" href="{url}">
<meta property="og:type" content="website"><meta property="og:site_name" content="{C.SITE_NAME}"><meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}"><meta property="og:url" content="{url}"><meta property="og:image" content="{C.BASE_URL}/og.png">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:site" content="@{C.X_HANDLE}"><meta name="twitter:title" content="{esc(title)}"><meta name="twitter:description" content="{esc(desc)}"><meta name="twitter:image" content="{C.BASE_URL}/og.png">
<link rel="icon" href="{href("/logo.svg")}" type="image/svg+xml"><link rel="alternate" type="text/plain" href="{href("/llms.txt")}" title="LLM summary">
<link rel="stylesheet" href="{href("/style.css")}">{lds}</head><body>
<header><a class="logo" href="{href("/")}">Staz<span>Games</span></a><nav aria-label="Main">{nav}</nav></header>
<main><p class="upd">Updated <time datetime="{iso}">{updated.strftime("%b %d, %Y %I:%M %p")} CT</time></p>{body}</main>
<footer><p>{C.SITE_NAME} is run by {C.OPERATOR} and updated automatically. Follow <a href="https://x.com/{C.X_HANDLE}">@{C.X_HANDLE}</a>. Some links may be affiliate links; see <a href="{href("/about/")}">About</a>. News headlines link to and credit their original publishers.</p></footer>
</body></html>"""

def media_html(image, alt, badge, icon=""):
    if image:
        return f'<img class="thumb" src="{esc(image)}" alt="{esc(alt)}" width="160" height="90" loading="lazy">'
    if icon:
        return f'<img class="icon" src="{esc(icon)}" alt="{esc(badge + " icon")}" width="160" height="90" loading="lazy">'
    return f'<span class="mark">{esc(badge)}</span>'

def card(media, inner):
    return f'<li><article class="card">{media}<div class="card-body">{inner}</div></article></li>'

def free_list(items, n=None):
    items = items[:n] if n else items
    if not items: return '<p class="empty">No free game giveaways found right now. Check back soon, or follow us on X for drops.</p>'
    li = []
    for g in items:
        ends = f' · ends <time datetime="{esc(g["ends"])}">{ct(g["ends"]) if "+" in g["ends"] or "Z" in g["ends"] else esc(g["ends"][:10])}</time>' if g.get("ends") else ""
        worth = f' · <s>{esc(g["worth"])}</s> <span class="price">Free</span>' if g.get("worth") and g["worth"] not in ("N/A", "0", "$0.00") else ' · <span class="price">Free</span>'
        media = media_html(g.get("image"), g["title"], short_label(g.get("source") or g.get("platform")))
        inner = f'<h3><a href="{esc(g["url"])}" rel="nofollow noopener">{esc(g["title"])}</a></h3><p class="meta">{esc(g["platform"])}{worth}{ends} · via {esc(g["source"])}</p>'
        li.append(card(media, inner))
    return '<ul class="cards">' + "".join(li) + "</ul>"

def deal_list(items, n=None):
    items = items[:n] if n else items
    if not items: return '<p class="empty">No deals available right now. The list refreshes several times a day.</p>'
    li = []
    for d in items:
        meta = f'<span class="badge">-{d["savings"]}%</span> <span class="price">${esc(d["price"])}</span> <s>${esc(d["normal"])}</s> at {esc(d["store"])}'
        if d.get("metacritic") not in (None, "0"):
            meta += " · Metacritic " + esc(d["metacritic"])
        media = media_html(d.get("image"), d["title"], short_label(d.get("store")), d.get("icon"))
        inner = f'<h3><a href="{esc(d["url"])}" rel="sponsored nofollow noopener">{esc(d["title"])}</a></h3><p class="meta">{meta}</p>'
        li.append(card(media, inner))
    return '<ul class="cards">' + "".join(li) + "</ul>"

def news_list(items, n=None):
    items = items[:n] if n else items
    if not items: return '<p class="empty">News feeds are temporarily unavailable. Please check back shortly.</p>'
    li = []
    for a in items:
        summary = f'<p>{esc(a["summary"])}</p>' if a.get("summary") else ""
        media = media_html(a.get("image"), a["title"], short_label(a.get("source")))
        inner = f'<h3><a href="{esc(a["url"])}" rel="noopener">{esc(a["title"])}</a></h3>{summary}<p class="meta">Source: {esc(a["source"])} · <time datetime="{esc(a["published"])}">{ct(a["published"])}</time></p>'
        li.append(card(media, inner))
    return '<ul class="cards">' + "".join(li) + "</ul>"

def itemlist(name, rows):
    return {"@context": "https://schema.org", "@type": "ItemList", "name": name,
            "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": r["title"], "url": r["url"]} for i, r in enumerate(rows)]}

def signup():
    action = esc(form_action())
    privacy = esc(href("/newsletter/#privacy"))
    return f"""<section class="signup" id="newsletter" aria-labelledby="nl"><h2 id="nl">Get the daily drop at 7 AM CT</h2>
<p>{COPY} <a href="{privacy}">Privacy</a></p>
<form class="signup-form" method="post" action="{action}">
<label for="signup-email">Email</label>
<input id="signup-email" name="email" type="email" required autocomplete="email" placeholder="you@example.com" maxlength="254">
<div class="hp" aria-hidden="true"><label for="hp">Leave this field empty</label><input id="hp" name="hp" type="text" tabindex="-1" autocomplete="off" value=""></div>
<button type="submit">Subscribe</button>
<p class="form-msg" role="status" aria-live="polite" hidden></p>
</form>
<script>
document.querySelectorAll("form.signup-form").forEach(function(form){{
  form.addEventListener("submit", function(ev){{
    ev.preventDefault();
    var msg = form.querySelector(".form-msg");
    var btn = form.querySelector("button");
    var data = new FormData(form);
    btn.disabled = true;
    msg.hidden = false;
    msg.className = "form-msg";
    msg.textContent = "Sending…";
    fetch(form.action, {{
      method: "POST",
      headers: {{"Content-Type": "application/json", "Accept": "application/json"}},
      body: JSON.stringify({{email: data.get("email") || "", hp: data.get("hp") || ""}})
    }}).then(function(r){{
      return r.json().then(function(j){{ return {{ok: r.ok, body: j}}; }}, function(){{ return {{ok: false, body: {{}}}}; }});
    }}).then(function(res){{
      var body = res.body || {{}};
      if (res.ok && body.ok) {{
        msg.className = "form-msg";
        msg.textContent = body.message || "You're in. Watch your inbox.";
        form.reset();
      }} else {{
        msg.className = "form-msg bad";
        msg.textContent = body.error || body.message || "Something went wrong. Please try again.";
      }}
    }}).catch(function(){{
      msg.className = "form-msg bad";
      msg.textContent = "Something went wrong. Please try again.";
    }}).then(function(){{ btn.disabled = false; }});
  }});
}});
</script>
</section>"""

PRIVACY = """<section id="privacy"><h2>Privacy</h2><p>We use your email only to send the daily StazGames note about free games and deals. Addresses are stored with Resend so we can mail you. We don't sell or share them. Unsubscribe anytime from the link in each email.</p></section>"""

def load_font(size, bold):
    from PIL import ImageFont
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for base in (
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/dejavu",
        "/usr/share/fonts/TTF",
        "/usr/share/fonts/truetype/dejavu-sans-fonts",
    ):
        path = os.path.join(base, name)
        if os.path.isfile(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)

def fit_font(draw, text, size, bold, max_w):
    while size >= 22:
        font = load_font(size, bold)
        if draw.textlength(text, font=font) <= max_w:
            return font
        size -= 2
    return load_font(22, bold)

def write_og():
    from PIL import Image, ImageDraw
    width, height = 1200, 630
    bg, green, fg, mut, card = (15, 21, 18), (74, 222, 128), (233, 242, 236), (159, 179, 167), (24, 34, 29)
    image = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 22, height), fill=green)
    draw.rectangle((0, height - 16, width, height), fill=green)
    draw.rounded_rectangle((72, 72, 210, 210), radius=32, fill=card)
    draw.ellipse((100, 112, 182, 190), fill=(160, 112, 63))
    draw.ellipse((118, 136, 132, 150), fill=(28, 28, 28))
    draw.ellipse((150, 136, 164, 150), fill=(28, 28, 28))
    draw.ellipse((126, 158, 156, 174), fill=(107, 68, 35))
    draw.arc((92, 96, 190, 176), 200, 340, fill=green, width=10)
    draw.rounded_rectangle((84, 138, 104, 182), radius=7, fill=green)
    draw.rounded_rectangle((178, 138, 198, 182), radius=7, fill=green)
    word = fit_font(draw, "StazGames", 84, True, 860)
    draw.text((228, 96), "Staz", font=word, fill=fg)
    draw.text((228 + draw.textlength("Staz", font=word), 96), "Games", font=word, fill=green)
    line = fit_font(draw, "Daily gaming news, free games, and the best deals,", 40, False, 1040)
    draw.text((80, 280), "Daily gaming news, free games, and the best deals,", font=line, fill=fg)
    draw.text((80, 340), "so you never miss a drop.", font=line, fill=mut)
    small = fit_font(draw, "stazgames.com", 30, True, 600)
    draw.text((80, 470), "stazgames.com", font=small, fill=green)
    os.makedirs(C.OUT_DIR, exist_ok=True)
    image.save(os.path.join(C.OUT_DIR, "og.png"), "PNG", optimize=True)

def write(rel, text):
    p = os.path.join(C.OUT_DIR, rel.lstrip("/")); os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    open(p, "w", encoding="utf-8").write(text)

def build(offline=False):
    FAILURES.clear()
    now = dt.datetime.now(CT).replace(microsecond=0)
    free = fetch_epic(offline)
    norm = lambda t: re.sub(r"\s*\([^)]*\)\s*$", "", t).strip().lower()
    titles = {norm(g["title"]) for g in free}
    free += [g for g in fetch_gamerpower(offline) if norm(g["title"]) not in titles]
    deals, news = fetch_deals(offline), fetch_news(offline)
    form = signup()
    pages = []
    def P(path, *a, **k): write(path + "index.html", page(path, *a, now, **k)); pages.append(path)
    P("/", f"{C.SITE_NAME}: Free Games, Game Deals & Gaming News Today",
      f"Today's free PC games, the best game deals and the latest gaming news, updated several times a day. {len(free)} free games right now.",
      f'<h1>Free games, deals and gaming news, updated daily</h1><p>{esc(C.TAGLINE)}</p><h2>Free right now</h2>{free_list(free, 6)}<p><a href="{href("/free-games/")}">All free games →</a></p><h2>Top deals</h2>{deal_list(deals, 6)}<p><a href="{href("/deals/")}">All deals →</a></p><h2>Latest news</h2>{news_list(news, 6)}<p><a href="{href("/news/")}">All news →</a></p>{form}',
      jsonld=[itemlist("Free games right now", free[:6])] if free else [])
    P("/free-games/", f"Free Games Right Now ({now:%B %Y}) | {C.SITE_NAME}",
      "Every free-to-keep game giveaway we can find on Epic Games Store, Steam, GOG and more, with end dates in Central Time.",
      f'<h1>Free games right now</h1><p>Games you can claim and keep for free. Data: Epic Games Store and <a href="https://www.gamerpower.com/">GamerPower</a>. Machine-readable: <a href="{href("/data/free-games.json")}">free-games.json</a>.</p>{free_list(free)}{form}',
      jsonld=[itemlist("Free games right now", free)] if free else [])
    P("/deals/", f"Best PC Game Deals Today | {C.SITE_NAME}",
      "The highest-rated PC game discounts on Steam, GOG, Humble and Fanatical, with sale prices and savings, refreshed several times a day.",
      f'<h1>Top PC game deals</h1><p>Deal data from <a href="https://www.cheapshark.com/">CheapShark</a>. Prices can change at any time. Machine-readable: <a href="{href("/data/deals.json")}">deals.json</a>.</p>{deal_list(deals)}{form}',
      jsonld=[itemlist("Top game deals", deals)] if deals else [])
    arts = [{"@context": "https://schema.org", "@type": "NewsArticle", "headline": a["title"][:110], "url": a["url"], "datePublished": a["published"],
             "publisher": {"@type": "Organization", "name": a["source"]}} for a in news[:20]]
    P("/news/", f"Latest Gaming News Headlines | {C.SITE_NAME}",
      "A fast roundup of today's gaming headlines from IGN, PC Gamer, VGC, Eurogamer and Gematsu, each with a short summary and a link to the source.",
      f'<h1>Latest gaming news</h1><p>Headlines with a short summary. Read the full stories at the original publishers.</p>{news_list(news)}', jsonld=arts)
    P("/newsletter/", f"Daily Gaming Newsletter | {C.SITE_NAME}",
      "The StazGames morning email covers free games and the best deals. Joining is free, and you can unsubscribe anytime.",
      f"<h1>The StazGames daily newsletter</h1>{form}{PRIVACY}")
    P("/about/", f"About {C.SITE_NAME} | Who Runs This Site",
      f"{C.SITE_NAME} is run by {C.OPERATOR}. Learn how our automated free-game and deal alerts work, plus our affiliate disclosure.",
      f"""<h1>About {C.SITE_NAME}</h1><p>{esc(C.TAGLINE)}</p>
<h2>Who runs it</h2><p>{C.SITE_NAME} and <a href="https://x.com/{C.X_HANDLE}">@{C.X_HANDLE}</a> are operated by {C.OPERATOR} (<a href="https://x.com/{C.OPERATOR_X}" rel="me">@{C.OPERATOR_X}</a>), based in US Central Time.</p>
<h2>Automated account disclosure</h2><p>This site and the @{C.X_HANDLE} X account are updated automatically by software that checks public feeds (Epic Games Store, GamerPower, CheapShark and publisher RSS feeds). A human operator, {C.OPERATOR}, oversees it. Report mistakes to <a href="https://x.com/{C.OPERATOR_X}">@{C.OPERATOR_X}</a>.</p>
<h2>News policy</h2><p>We publish headlines, a short summary, a thumbnail when the source provides one, and a credited link to the original publisher. We do not republish full articles.</p>
<h2>Affiliate disclosure</h2><p><em>[Placeholder: final wording to be added once affiliate programs are approved.]</em> Some links may be affiliate or referral links. If you buy through them we may earn a small commission at no extra cost to you. Deal links currently go through CheapShark redirects.</p>""")
    write("/404.html", page("/404.html", f"Page not found | {C.SITE_NAME}",
      "That page isn't on StazGames. Head back for free games, deals, and gaming news.",
      f'<h1>Page not found</h1><p>That page isn\'t on {C.SITE_NAME}. The free games, deals, and news are on the homepage.</p><p><a href="{href("/")}">Back to the homepage</a></p>',
      now))
    write("/style.css", CSS); write("/logo.svg", LOGO_SVG); write_og()
    write("/data/deals.json", json.dumps({"updated": now.isoformat(), "source": "CheapShark", "count": len(deals), "deals": deals}, indent=1, ensure_ascii=False))
    write("/data/free-games.json", json.dumps({"updated": now.isoformat(), "sources": ["Epic Games Store", "GamerPower"], "count": len(free), "games": free}, indent=1, ensure_ascii=False))
    write("/robots.txt", f"User-agent: *\nAllow: {href("/")}\n\nSitemap: {C.BASE_URL}/sitemap.xml\n")
    write("/sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
          "".join(f"<url><loc>{C.BASE_URL}{p}</loc><lastmod>{now.isoformat()}</lastmod><changefreq>hourly</changefreq></url>\n" for p in pages) + "</urlset>\n")
    top = "\n".join(f"- {g['title']} ({g['platform']}): {g['url']}" for g in free[:10]) or "- None right now"
    write("/llms.txt", f"""# {C.SITE_NAME}

> {C.TAGLINE} Run by {C.OPERATOR} (https://x.com/{C.OPERATOR_X}). Updated automatically several times a day; last update {now:%Y-%m-%d %H:%M} CT.

## Pages
- [Free games]({C.BASE_URL}/free-games/): current free-to-keep game giveaways with end dates
- [Deals]({C.BASE_URL}/deals/): top-rated PC game discounts (CheapShark data)
- [News]({C.BASE_URL}/news/): headlines with short summaries, linked to original publishers
- [About]({C.BASE_URL}/about/): operator, automation and affiliate disclosures

## Data
- [free-games.json]({C.BASE_URL}/data/free-games.json): JSON list of current free games
- [deals.json]({C.BASE_URL}/data/deals.json): JSON list of current deals

## Free right now
{top}
""")
    write("/build-report.json", json.dumps({"built": now.isoformat(), "free": len(free), "deals": len(deals), "news": len(news), "failures": FAILURES}, indent=1))
    return {"free": len(free), "deals": len(deals), "news": len(news), "failures": list(FAILURES)}

if __name__ == "__main__":
    print(json.dumps(build("--offline" in sys.argv), indent=1))
