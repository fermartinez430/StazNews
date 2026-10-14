"""StazGames static site generator. Usage: python build.py [--offline]"""
import json, os, re, sys, html, datetime as dt
from zoneinfo import ZoneInfo
from urllib.parse import quote, urlparse
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

def get(url, timeout=20):
    try:
        r = requests.get(url, headers=UA, timeout=timeout); r.raise_for_status(); return r
    except Exception as e:
        FAILURES.append(f"{url}: {e}"); return None

def esc(s): return html.escape(str(s or ""), quote=True)
def strip(s): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(s or ""))).strip()

def one_line(title, summary):
    """Our own short summary: first sentence of the feed teaser, trimmed. Never full text."""
    s = strip(summary)
    s = re.split(r"(?<=[.!?])\s", s)[0] if s else ""
    if not s or s.lower().startswith(title.lower()[:30].lower()):
        s = "Quick take: " + title.rstrip(".") + "."
    return (s[:157] + "…") if len(s) > 160 else s

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
                        "ends": end.isoformat()})
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
                "ends": "" if not end or end == "N/A" else end.replace(" ", "T")})
    except Exception as e: FAILURES.append(f"gamerpower parse: {e}")
    return out

def fetch_deals(offline=False):
    if offline: return []
    r = get("https://www.cheapshark.com/api/1.0/deals?storeID=1,7,11,25&sortBy=Deal%20Rating&pageSize=40&onSale=1")
    stores = {}
    s = get("https://www.cheapshark.com/api/1.0/stores")
    if s:
        try: stores = {x["storeID"]: x["storeName"] for x in s.json()}
        except Exception: pass
    out, seen = [], set()
    if not r: return out
    try:
        for d in r.json():
            if d["title"] in seen: continue
            seen.add(d["title"])
            out.append({"title": d["title"], "store": stores.get(d["storeID"], "Store " + d["storeID"]),
                "price": d["salePrice"], "normal": d["normalPrice"], "savings": round(float(d["savings"])),
                "metacritic": d.get("metacriticScore"),
                "url": "https://www.cheapshark.com/redirect?dealID=" + d["dealID"]})  # redirect kept intact
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
                          "summary": one_line(title, e.get("summary", ""))})
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
.cards li{background:var(--card);border-radius:10px;padding:12px 14px}.cards h3{margin:0 0 4px;font-size:1.05rem}
.meta{color:var(--mut);font-size:.88rem}.price{color:var(--acc2);font-weight:700}s{color:var(--mut)}
.badge{background:var(--acc);color:#062;border-radius:6px;padding:0 6px;font-weight:700;font-size:.8rem}
.signup{background:var(--card);border:1px dashed var(--acc);border-radius:10px;padding:16px;margin:24px 0}
.signup input{padding:8px;border-radius:6px;border:0;max-width:100%}.signup button{padding:8px 14px;border:0;border-radius:6px;background:var(--acc);font-weight:700}
footer{color:var(--mut);font-size:.85rem;padding:24px 16px;border-top:1px solid var(--card);margin-top:32px}.empty{color:var(--mut);font-style:italic}"""

LOGO_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#18221d"/><ellipse cx="32" cy="38" rx="20" ry="16" fill="#a0703f"/><circle cx="25" cy="34" r="3" fill="#222"/><circle cx="39" cy="34" r="3" fill="#222"/><ellipse cx="32" cy="43" rx="7" ry="4" fill="#6b4423"/><path d="M12 32a20 20 0 0 1 40 0" stroke="#4ade80" stroke-width="5" fill="none"/><rect x="8" y="30" width="7" height="12" rx="3" fill="#4ade80"/><rect x="49" y="30" width="7" height="12" rx="3" fill="#4ade80"/></svg>'

NAV = [("/", "Home"), ("/free-games/", "Free games"), ("/deals/", "Deals"), ("/news/", "News"), ("/newsletter/", "Newsletter"), ("/about/", "About")]

def href(path):
    """Root-relative URL under the GitHub Pages project subpath.

    BASE_URL is https://fermartinez430.github.io/StazNews, so a logical path
    of /free-games/ must be served as /StazNews/free-games/. Files in OUT_DIR
    stay unprefixed; Pages mounts that directory at the project path.
    """
    prefix = urlparse(C.BASE_URL).path.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return prefix + "/" if path == "/" else prefix + path

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

def free_list(items, n=None):
    items = items[:n] if n else items
    if not items: return '<p class="empty">No free game giveaways found right now. Check back soon, or follow us on X for drops.</p>'
    li = []
    for g in items:
        ends = f' · ends <time datetime="{esc(g["ends"])}">{ct(g["ends"]) if "+" in g["ends"] or "Z" in g["ends"] else esc(g["ends"][:10])}</time>' if g.get("ends") else ""
        worth = f' · <s>{esc(g["worth"])}</s> <span class="price">Free</span>' if g.get("worth") and g["worth"] not in ("N/A", "0", "$0.00") else ' · <span class="price">Free</span>'
        li.append(f'<li><article><h3><a href="{esc(g["url"])}" rel="nofollow noopener">{esc(g["title"])}</a></h3><p class="meta">{esc(g["platform"])}{worth}{ends} · via {esc(g["source"])}</p></article></li>')
    return '<ul class="cards">' + "".join(li) + "</ul>"

def deal_list(items, n=None):
    items = items[:n] if n else items
    if not items: return '<p class="empty">No deals available right now. The list refreshes several times a day.</p>'
    return '<ul class="cards">' + "".join(
        f'<li><article><h3><a href="{esc(d["url"])}" rel="sponsored nofollow noopener">{esc(d["title"])}</a></h3><p class="meta"><span class="badge">-{d["savings"]}%</span> <span class="price">${esc(d["price"])}</span> <s>${esc(d["normal"])}</s> at {esc(d["store"])}{" · Metacritic " + esc(d["metacritic"]) if d.get("metacritic") not in (None, "0") else ""}</p></article></li>'
        for d in items) + "</ul>"

def news_list(items, n=None):
    items = items[:n] if n else items
    if not items: return '<p class="empty">News feeds are temporarily unavailable. Please check back shortly.</p>'
    return '<ul class="cards">' + "".join(
        f'<li><article><h3><a href="{esc(a["url"])}" rel="noopener">{esc(a["title"])}</a></h3><p>{esc(a["summary"])}</p><p class="meta">Source: {esc(a["source"])} · <time datetime="{esc(a["published"])}">{ct(a["published"])}</time></p></article></li>'
        for a in items) + "</ul>"

def itemlist(name, rows):
    return {"@context": "https://schema.org", "@type": "ItemList", "name": name,
            "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": r["title"], "url": r["url"]} for i, r in enumerate(rows)]}

SIGNUP = """<section class="signup" id="newsletter" aria-labelledby="nl"><h2 id="nl">Get the daily drop at 7 AM CT</h2>
<p>Free games, top deals and the day's biggest headlines in one short email. <strong>Coming soon.</strong></p>
<form action="#" onsubmit="return false"><label for="em">Email</label> <input id="em" type="email" placeholder="you@example.com" disabled> <button type="submit" disabled>Notify me</button></form></section>"""

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
    pages = []
    def P(path, *a, **k): write(path + "index.html", page(path, *a, now, **k)); pages.append(path)
    P("/", f"{C.SITE_NAME}: Free Games, Game Deals & Gaming News Today",
      f"Today's free PC games, the best game deals and the latest gaming news, updated several times a day. {len(free)} free games right now.",
      f'<h1>Free games, deals and gaming news, updated daily</h1><p>{esc(C.TAGLINE)}</p><h2>Free right now</h2>{free_list(free, 6)}<p><a href="{href("/free-games/")}">All free games →</a></p><h2>Top deals</h2>{deal_list(deals, 6)}<p><a href="{href("/deals/")}">All deals →</a></p><h2>Latest news</h2>{news_list(news, 6)}<p><a href="{href("/news/")}">All news →</a></p>{SIGNUP}',
      jsonld=[itemlist("Free games right now", free[:6])] if free else [])
    P("/free-games/", f"Free Games Right Now ({now:%B %Y}) | {C.SITE_NAME}",
      "Every free-to-keep game giveaway we can find on Epic Games Store, Steam, GOG and more, with end dates in Central Time.",
      f'<h1>Free games right now</h1><p>Games you can claim and keep for free. Data: Epic Games Store and <a href="https://www.gamerpower.com/">GamerPower</a>. Machine-readable: <a href="{href("/data/free-games.json")}">free-games.json</a>.</p>{free_list(free)}{SIGNUP}',
      jsonld=[itemlist("Free games right now", free)] if free else [])
    P("/deals/", f"Best PC Game Deals Today | {C.SITE_NAME}",
      "The highest-rated PC game discounts on Steam, GOG, Humble and Fanatical, with sale prices and savings, refreshed several times a day.",
      f'<h1>Top PC game deals</h1><p>Deal data from <a href="https://www.cheapshark.com/">CheapShark</a>. Prices can change at any time. Machine-readable: <a href="{href("/data/deals.json")}">deals.json</a>.</p>{deal_list(deals)}{SIGNUP}',
      jsonld=[itemlist("Top game deals", deals)] if deals else [])
    arts = [{"@context": "https://schema.org", "@type": "NewsArticle", "headline": a["title"][:110], "url": a["url"], "datePublished": a["published"],
             "publisher": {"@type": "Organization", "name": a["source"]}} for a in news[:20]]
    P("/news/", f"Latest Gaming News Headlines | {C.SITE_NAME}",
      "A fast roundup of today's gaming headlines from IGN, PC Gamer, VGC, Eurogamer and Gematsu, each with a one-line summary and a link to the source.",
      f'<h1>Latest gaming news</h1><p>Headlines with our one-line summaries. Read the full stories at the original publishers.</p>{news_list(news)}', jsonld=arts)
    P("/newsletter/", f"Daily Gaming Newsletter | {C.SITE_NAME}",
      "Sign up for the StazGames daily email at 7 AM Central: free games, top deals and gaming headlines. Launching soon.",
      f"<h1>The StazGames daily newsletter</h1>{SIGNUP}")
    P("/about/", f"About {C.SITE_NAME} | Who Runs This Site",
      f"{C.SITE_NAME} is run by {C.OPERATOR}. Learn how our automated free-game and deal alerts work, plus our affiliate disclosure.",
      f"""<h1>About {C.SITE_NAME}</h1><p>{esc(C.TAGLINE)}</p>
<h2>Who runs it</h2><p>{C.SITE_NAME} and <a href="https://x.com/{C.X_HANDLE}">@{C.X_HANDLE}</a> are operated by {C.OPERATOR} (<a href="https://x.com/{C.OPERATOR_X}" rel="me">@{C.OPERATOR_X}</a>), based in US Central Time.</p>
<h2>Automated account disclosure</h2><p>This site and the @{C.X_HANDLE} X account are updated automatically by software that checks public feeds (Epic Games Store, GamerPower, CheapShark and publisher RSS feeds). A human operator, {C.OPERATOR}, oversees it. Report mistakes to <a href="https://x.com/{C.OPERATOR_X}">@{C.OPERATOR_X}</a>.</p>
<h2>News policy</h2><p>We publish headlines, our own one-line summaries and a credited link to the original publisher. We do not republish full articles or images.</p>
<h2>Affiliate disclosure</h2><p><em>[Placeholder: final wording to be added once affiliate programs are approved.]</em> Some links may be affiliate or referral links. If you buy through them we may earn a small commission at no extra cost to you. Deal links currently go through CheapShark redirects.</p>""")
    write("/style.css", CSS); write("/logo.svg", LOGO_SVG)
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
- [News]({C.BASE_URL}/news/): headlines with one-line summaries, linked to original publishers
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
