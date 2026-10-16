"""Validate the built site. Usage: python test_site.py [--external]"""
import os, re, json, sys, shutil, html5lib, xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urlparse
import requests, config as C, build

def project_prefix():
    return urlparse(C.BASE_URL).path.rstrip("/")

def local_file(out, href):
    """Map a root-relative href onto the build directory.

    Published links include the GitHub Pages project prefix (for example
    /StazNews/style.css). The files themselves live at the output root.
    """
    prefix = project_prefix()
    bare = href.split("#", 1)[0]
    path = bare
    if prefix and (path == prefix or path.startswith(prefix + "/")):
        path = path[len(prefix):] or "/"
    elif prefix and path.startswith("/"):
        return None
    target = os.path.join(out, path.lstrip("/"))
    if bare.endswith("/"):
        target = os.path.join(target, "index.html")
    return target

ERR = []
def check(cond, msg):
    if not cond: ERR.append(msg)

class P(HTMLParser):
    def __init__(s): super().__init__(); s.links=[]; s.ld=[]; s._ld=False; s.tags={}; s.title=""; s._t=False
    def handle_starttag(s, t, a):
        a=dict(a); s.tags.setdefault(t,[]).append(a)
        if t=="a" and a.get("href"): s.links.append(a["href"])
        if t=="script" and a.get("type")=="application/ld+json": s._ld=True; s.ld.append("")
        if t=="title": s._t=True
    def handle_endtag(s,t): s._ld = False if t=="script" else s._ld; s._t = False if t=="title" else s._t
    def handle_data(s,d):
        if s._ld: s.ld[-1]+=d
        if s._t: s.title+=d

def html_pages(out):
    pages = {}
    for root, _, fs in os.walk(out):
        for f in fs:
            if not f.endswith(".html"):
                continue
            full = os.path.join(root, f)
            rel_dir = os.path.relpath(root, out)
            if f == "404.html" and rel_dir == ".":
                pages["/404.html"] = open(full, encoding="utf-8").read()
                continue
            if f != "index.html":
                continue
            path = "/" if rel_dir == "." else "/" + rel_dir.replace(os.sep, "/").strip("/") + "/"
            pages[path] = open(full, encoding="utf-8").read()
    return pages

def validate(out, external=False):
    pages = html_pages(out)
    for req in ["/", "/free-games/", "/deals/", "/news/", "/about/", "/newsletter/", "/disclosure/", "/privacy/", "/contact/", "/terms/", "/404.html"]: check(req in pages, f"missing page {req}")
    titles, descs, ext = set(), set(), set()
    for path, src in pages.items():
        try: html5lib.HTMLParser(strict=True).parse(src)
        except Exception as e: ERR.append(f"{path}: HTML parse error {e}")
        p = P(); p.feed(src)
        d = [m.get("content") for m in p.tags.get("meta",[]) if m.get("name")=="description"]
        canon = [l.get("href") for l in p.tags.get("link",[]) if l.get("rel")=="canonical"]
        check(p.title and p.title not in titles, f"{path}: title missing/duplicate"); titles.add(p.title)
        check(d and d[0] not in descs, f"{path}: description missing/duplicate"); descs.update(d[:1])
        check(canon == [C.BASE_URL + path], f"{path}: bad canonical {canon}")
        for k in ["og:title","og:description","og:url"]: check(any(m.get("property")==k for m in p.tags.get("meta",[])), f"{path}: missing {k}")
        og = [m.get("content") for m in p.tags.get("meta",[]) if m.get("property")=="og:image"]
        tw = [m.get("content") for m in p.tags.get("meta",[]) if m.get("name")=="twitter:image"]
        check(og == [C.BASE_URL + "/og.png"], f"{path}: og:image {og}")
        check(tw == [C.BASE_URL + "/og.png"], f"{path}: twitter:image {tw}")
        check(any(m.get("name")=="twitter:card" for m in p.tags.get("meta",[])), f"{path}: missing twitter:card")
        check(re.search(r"Updated <time[^>]*>[^<]+ CT</time>", src), f"{path}: missing Updated CT")
        for t in ["header","nav","main","footer","h1"]: check(t in p.tags, f"{path}: missing <{t}>")
        check(len(p.tags.get("h1",[]))==1, f"{path}: needs exactly one h1")
        for img in p.tags.get("img", []):
            check(img.get("alt"), f"{path}: img missing alt")
            check(str(img.get("width") or "").isdigit() and str(img.get("height") or "").isdigit(), f"{path}: img missing width/height")
            check(img.get("loading") == "lazy", f"{path}: img missing loading=lazy")
        if path in ("/", "/newsletter/"):
            check(build.COPY in src, f"{path}: missing newsletter copy")
            check('class="signup-form"' in src and 'method="post"' in src, f"{path}: signup form missing")
            check(f'action="{build.form_action()}"' in src, f"{path}: signup action {build.form_action()}")
            check('name="hp"' in src and 'name="email"' in src, f"{path}: signup fields missing")
            check("Coming soon" not in src and "Notify me" not in src, f"{path}: old signup copy remains")
            check('type="submit" disabled' not in src, f"{path}: submit still disabled")
            check(build.href("/privacy/") in src, f"{path}: privacy link missing")
            check("fetch(form.action" in src, f"{path}: signup script missing")
        for legal, dest in (("Disclosure", "/disclosure/"), ("Privacy", "/privacy/"), ("Contact", "/contact/"), ("Terms", "/terms/")):
            check(f'href="{build.href(dest)}"' in src, f"{path}: footer missing {legal}")
        check(build.AFFILIATE_NOTICE in src, f"{path}: footer affiliate notice missing")
        check('aria-label="Legal"' in src, f"{path}: legal nav missing")
        for a in p.tags.get("a", []):
            if "out" in (a.get("class") or "").split():
                check(a.get("rel") == "sponsored nofollow noopener", f"{path}: deal link rel {a.get('rel')} {a.get('href')}")
        if path == "/disclosure/":
            check(build.AMAZON_DISCLOSURE in src, f"{path}: Amazon sentence missing")
            check("commission" in src and "no extra cost to the reader" in src, f"{path}: commission sentence missing")
            check("Fernando Martinez" in src and "automation" in src, f"{path}: operator or automation missing")
        if path == "/privacy/":
            low = src.lower()
            for phrase in ("resend", "unsubscribe anytime", "do not sell", "tracking cookies", "vercel", "hosting logs"):
                check(phrase in low, f"{path}: missing {phrase}")
            check("https://x.com/StazNews" in src, f"{path}: contact link missing")
            check("mailto:" not in low and not re.search(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", src, re.I), f"{path}: email address published")
        if path == "/contact/":
            check("https://x.com/StazNews" in src and "@StazNews" in src, f"{path}: X contact missing")
            check("Fernando Martinez" in src and "automation" in src, f"{path}: operator missing")
            check("mailto:" not in src.lower() and not re.search(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", src, re.I), f"{path}: email address published")
        if path == "/terms/":
            check("By using StazGames you agree to these terms." in src, f"{path}: terms intro missing")
            check(build.href("/disclosure/") in src, f"{path}: disclosure link missing")
        if path == "/404.html":
            check("Page not found" in src and "Back to the homepage" in src, f"{path}: 404 copy missing")
            check(f'href="{build.href("/")}"' in src, f"{path}: 404 missing home link")
        types=set()
        docs=[]
        for raw in p.ld:
            try:
                j=json.loads(raw)
                docs.append(j)
                check("@context" in j and "@type" in j, f"{path}: JSON-LD missing @context/@type")
                types.add(j["@type"])
            except Exception as e: ERR.append(f"{path}: invalid JSON-LD {e}")
        check({"Organization","WebSite"} <= types, f"{path}: missing Org/WebSite JSON-LD")
        if path in ("/deals/", "/free-games/") and 'class="cards"' in src:
            lists = [j for j in docs if j.get("@type") == "ItemList"]
            check(len(lists) == 1, f"{path}: expected one ItemList, got {len(lists)}")
            elements = (lists[0].get("itemListElement") if lists else None) or []
            check(elements and lists and lists[0].get("numberOfItems") == len(elements), f"{path}: ItemList count")
            check(src.count("<h3>") == len(elements), f"{path}: ItemList size {len(elements)} vs cards {src.count('<h3>')}")
            for el in elements:
                item = el.get("item") or {}
                offer = item.get("offers") or {}
                check(isinstance(el.get("position"), int) and item.get("@type") == "Product" and item.get("name") and item.get("url"), f"{path}: ItemList entry {item.get('name')}")
                if path == "/deals/":
                    check(offer.get("priceCurrency") == "USD" and re.fullmatch(r"\d+\.\d{2}", str(offer.get("price") or "")), f"{path}: deal price {offer} for {item.get('name')}")
                else:
                    check(offer.get("price") == "0.00" and offer.get("priceCurrency") == "USD", f"{path}: free price {offer} for {item.get('name')}")
        for h in p.links:
            if h.startswith("/"):
                tgt = local_file(out, h)
                check(tgt is not None and os.path.exists(tgt), f"{path}: broken internal link {h}")
                if project_prefix():
                    check(h == project_prefix() or h.startswith(project_prefix() + "/"), f"{path}: link missing project subpath {h}")
            elif h.startswith("http"): ext.add(h)
            elif not h.startswith("#"): ERR.append(f"{path}: odd link {h}")
        for a in p.tags.get("link", []):
            h = a.get("href") or ""
            if a.get("rel") in ("stylesheet", "icon", "alternate") and h.startswith("/"):
                tgt = local_file(out, h)
                check(tgt is not None and os.path.exists(tgt), f"{path}: broken asset {h}")
        for h in p.links:
            if "cheapshark" in h and "redirect" in h: check(re.fullmatch(r"https://www\.cheapshark\.com/redirect\?dealID=[^&\s]+", h), f"{path}: altered CheapShark link {h}")
    sm = ET.parse(os.path.join(out,"sitemap.xml")).getroot()
    locs = {e.text for e in sm.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")}
    indexed = {p for p in pages if p != "/404.html"}
    check(locs == {C.BASE_URL+p for p in indexed}, f"sitemap mismatch: {locs ^ {C.BASE_URL+p for p in indexed}}")
    check(C.BASE_URL + "/404.html" not in locs, "404 should not be in the sitemap")
    rb = open(os.path.join(out,"robots.txt")).read()
    check(f"Sitemap: {C.BASE_URL}/sitemap.xml" in rb, "robots.txt missing sitemap")
    check(f"Allow: {project_prefix() + '/' if project_prefix() else '/'}" in rb, "robots.txt allow path missing project subpath")
    for f,k in [("deals.json","deals"),("free-games.json","games"),("news.json","news")]:
        j=json.load(open(os.path.join(out,"data",f))); check(isinstance(j.get(k),list) and j["count"]==len(j[k]), f"{f} malformed")
        check(isinstance(j.get("updated"), str) and j["updated"], f"{f} missing updated")
    news_doc = json.load(open(os.path.join(out, "data", "news.json"), encoding="utf-8"))
    news_html = pages.get("/news/", "")
    pos = 0
    for item in news_doc["news"]:
        for key in ("title", "source", "url", "summary"):
            check(key in item, f"news.json item missing {key}")
        if "published" in item:
            check(isinstance(item["published"], str) and item["published"], "news.json published empty")
        needle = build.esc(item.get("title") or "")
        found = news_html.find(needle, pos) if needle else -1
        check(bool(needle) and found >= 0, f"news.json title missing from news page: {item.get('title')}")
        if found >= 0:
            pos = found + len(needle)
        summary = item.get("summary") or ""
        if summary:
            check(build.esc(summary) in news_html, f"paraphrased summary missing from news page: {summary[:80]}")
            check(build._norm_text(summary) != build._norm_text(item.get("title") or ""), f"news summary pastes the headline: {summary}")
    llms = open(os.path.join(out,"llms.txt")).read()
    check(llms.startswith("# "), "llms.txt malformed")
    for frag in ("/free-games/", "/deals/", "/news/", "/about/", "/disclosure/", "/privacy/", "/contact/", "/terms/", "/data/free-games.json", "/data/deals.json", "/data/news.json"):
        check(C.BASE_URL + frag in llms, f"llms.txt missing {C.BASE_URL}{frag}")
    css = open(os.path.join(out, "style.css"), encoding="utf-8").read()
    check("object-fit:cover" in css, "stylesheet missing object-fit:cover")
    check("max-width:699px" in css, "stylesheet missing the under-700px rule")
    check_og(out)
    bad = 0
    if external:
        for u in sorted(ext):
            try:
                r = requests.head(u, allow_redirects=True, timeout=10, headers=build.UA)
                if r.status_code >= 400 and r.status_code not in (403, 405, 429):
                    r = requests.get(u, timeout=10, headers=build.UA, stream=True)
                if r.status_code >= 400 and r.status_code not in (403, 429, 520): ERR.append(f"external {r.status_code}: {u}"); bad += 1
            except Exception as e: ERR.append(f"external error {u}: {e}"); bad += 1
    return len(pages), len(ext)


def check_og(out):
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from urllib.request import urlopen
    from PIL import Image
    og = os.path.join(out, "og.png")
    check(os.path.isfile(og), "og.png missing")
    if not os.path.isfile(og):
        return
    with open(og, "rb") as fh:
        check(fh.read(8).startswith(b"\x89PNG"), "og.png is not a PNG")
    with Image.open(og) as im:
        check(im.size == (1200, 630), f"og.png size {im.size}")
        pixel = im.convert("RGB").getpixel((4, 24))
        check(pixel[1] > 180 and pixel[0] < 140, f"og.png missing green accent {pixel}")
    published = urlparse(C.BASE_URL + "/og.png")
    path = published.path
    prefix = project_prefix()
    if prefix and path.startswith(prefix + "/"):
        path = path[len(prefix):]
    check(path == "/og.png", f"og.png public path {published.path}")

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=out, **kwargs)
        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_address[1]}/og.png", timeout=5) as res:
            check(res.status == 200, f"og.png HTTP {res.status}")
            check(res.read(8).startswith(b"\x89PNG"), "og.png HTTP body is not a PNG")
    finally:
        server.shutdown()

def test_summaries():
    assert_eq = lambda cond, msg: check(cond, msg)
    assert_eq(build.summarize("T", "Slow Bros.", "IGN") == "", "publisher name summary kept")
    assert_eq(build.summarize("T", "Going up.", "PC Gamer") == "", "thin summary kept")
    assert_eq(build.summarize("T", "PC Gamer", "PC Gamer") == "", "source-name summary kept")
    assert_eq(build.summarize("T", "", "IGN") == "", "empty summary kept")
    raw = "Cool Game is out now and it is genuinely worth a look today. The post Cool Game appeared first on PC Gamer."
    cleaned = build.summarize("Cool Game", raw, "PC Gamer")
    assert_eq("appeared first" not in cleaned.lower() and "worth a look" in cleaned, f"boilerplate kept: {cleaned}")
    assert_eq(cleaned.endswith("."), f"boilerplate summary did not end on a sentence: {cleaned}")
    truncated = "The Switch 2 version of 007 First Light will launch on March 18, 2027, developer …"
    salvaged = build.summarize("007 First Light", truncated, "Gematsu")
    assert_eq("developer" not in salvaged.lower() and "2027" in salvaged and salvaged.endswith("."), f"dangling fragment kept: {salvaged}")
    source_tail = "Horizon Forbidden West is also returning, while Dino Crisis 2 joins the Classic Catalog Source"
    no_source = build.summarize("PS Plus", source_tail, "VGC")
    assert_eq("Source" not in no_source and no_source.endswith(".") and len(no_source) >= 40, f"source boilerplate kept: {no_source}")
    alpha = ("Alpha " * 12).strip() + "."
    beta = ("Beta " * 12).strip() + "."
    gamma = ("Gamma " * 20).strip() + "."
    joined = build.summarize("T", f"{alpha} {beta} {gamma}", "IGN")
    assert_eq(joined.startswith("Alpha") and "Gamma" not in joined and joined.endswith(".") and "…" not in joined, f"cut mid-sentence: {joined}")
    assert_eq(len(joined) <= 221, f"summary longer than about 220: {len(joined)}")
    long = ("Players discover new lands " * 12) + "and the ancient gate"
    clipped = build.summarize("T", long, "IGN")
    assert_eq(clipped.endswith("…") and not re.search(r"\s(and|the|of|to)…$", clipped), f"dangling word-boundary cut: {clipped}")
    assert_eq(len(clipped) <= 221, f"word clip too long: {len(clipped)}")
    words = build.summarize("T", ("Word " * 80).strip(), "IGN")
    assert_eq(words.endswith("…") and set(words[:-1].split()) == {"Word"}, f"mid-word clip: {words}")

def test_cards():
    free = build.free_list([{
        "title": "Halo", "platform": "Epic Games Store", "source": "Epic Games",
        "url": "https://example.com/halo", "worth": "$19.99", "ends": "",
        "image": "https://cdn.example/halo.jpg",
    }])
    check('class="thumb"' in free and 'loading="lazy"' in free and 'width="160"' in free and 'height="90"' in free, "free card thumb attributes")
    check('alt="Halo"' in free, "free card alt")
    check('rel="sponsored nofollow noopener"' in free and 'class="ad"' not in free, "free card outbound rel")
    badge = build.free_list([{
        "title": "Halo", "platform": "Epic Games Store", "source": "Epic Games",
        "url": "https://example.com/halo", "worth": "", "ends": "", "image": "",
    }])
    check('class="mark"' in badge and ">Epic<" in badge and "<img" not in badge, "free card store badge")
    deal = build.deal_list([{
        "title": "Hades", "store": "Steam", "price": "4.99", "normal": "24.99", "savings": 80,
        "metacritic": "93", "url": "https://www.cheapshark.com/redirect?dealID=abc",
        "image": "", "icon": "https://www.cheapshark.com/img/stores/icons/0.png",
    }])
    check('class="icon"' in deal and 'alt="Steam icon"' in deal and "loading=\"lazy\"" in deal, "deal card favicon")
    check('rel="sponsored nofollow noopener"' in deal and 'class="ad"' not in deal, "untagged deal should not be labeled")
    news = build.news_list([{
        "title": "A headline", "url": "https://example.com/story", "summary": "",
        "source": "IGN", "published": "2026-10-14T12:00:00+00:00", "image": "",
    }])
    check("<p></p>" not in news and 'class="mark"' in news and ">IGN<" in news, "thin news summary still rendered a paragraph")
    check('rel="noopener"' in news and "sponsored" not in news, "news link marked as a deal")
    raw = "Nintendo has shown off Dark Link in The Legend of Zelda: Ocarina of Time remake for the first time."
    voiced = build.news_list([{
        "title": "Here's Our First Look at Dark Link",
        "url": "https://example.com/story",
        "summary": raw,
        "source": "IGN",
        "published": "2026-10-14T12:00:00+00:00",
        "image": "",
    }])
    check("has shown off Dark Link" not in voiced and "first look" in voiced.lower() and "Nintendo" in voiced, f"news card not paraphrased: {voiced}")
    check('href="https://example.com/story"' in voiced and "Source: IGN" in voiced and 'rel="noopener"' in voiced, "news card lost the link or source")
    epic = build.safe_img("https://cdn1.epicgames.com/offer/abc/file.png")
    check("resize=1" in epic and "w=320" in epic and "h=180" in epic, f"epic image not resized: {epic}")
    future = build.safe_img("https://cdn.mos.cms.futurecdn.net/abc-1920-80.jpg")
    check(future.endswith("abc-480-80.jpg"), f"futurecdn image not resized: {future}")
    check(build.safe_img("javascript:alert(1)") == "", "javascript image url accepted")
    check(build.safe_img("") == "", "empty image url accepted")

def test_affiliates():
    check(C.AFFILIATES == {"humble": {}, "fanatical": {}, "gmg": {}, "amazon": {}}, f"default affiliate config {C.AFFILIATES}")
    samples = {
        "humble": ("https://www.humblebundle.com/store/hades", "Humble Store", "partner", "staz-humble"),
        "fanatical": ("https://www.fanatical.com/en/game/hades", "Fanatical", "affiliate", "staz-fan"),
        "gmg": ("https://www.greenmangaming.com/games/hades/", "Green Man Gaming", "tap_a", "staz-gmg"),
        "amazon": ("https://www.amazon.com/dp/B00TEST", "Amazon", "tag", "staz-20"),
    }
    for key, (url, store, param, value) in samples.items():
        plain, active = build.with_affiliate(url, store)
        check(plain == url and active is False, f"empty {key} config changed {plain}")
    saved = {k: dict(v) for k, v in C.AFFILIATES.items()}
    try:
        for key, (url, store, param, value) in samples.items():
            C.AFFILIATES[key] = {param: value}
            tagged, active = build.with_affiliate(url, store)
            check(active and f"{param}={value}" in tagged, f"{key} tag missing from {tagged}")
            C.AFFILIATES[key] = {}
        C.AFFILIATES["amazon"] = {"tag": "staz-20"}
        card = build.deal_list([{
            "title": "Widget", "store": "Amazon", "price": "9.99", "normal": "19.99", "savings": 50,
            "metacritic": None, "url": "https://www.amazon.com/dp/B00TEST?ref=sr",
            "image": "", "icon": "",
        }])
        check("tag=staz-20" in card and 'class="ad"' in card and ">Affiliate<" in card, f"amazon card not labeled: {card}")
        check('rel="sponsored nofollow noopener"' in card, "amazon card rel")
        C.AFFILIATES["humble"] = {"partner": "staz-humble"}
        redirect = "https://www.cheapshark.com/redirect?dealID=abc"
        kept, active = build.with_affiliate(redirect, "Humble Store")
        check(active and kept == redirect, f"CheapShark redirect rewritten: {kept}")
        humble = build.deal_list([{
            "title": "Widget", "store": "Humble Store", "price": "1.00", "normal": "2.00", "savings": 50,
            "metacritic": None, "url": redirect, "image": "", "icon": "",
        }])
        check("dealID=abc" in humble and "partner=" not in humble and ">Affiliate<" in humble, f"humble redirect card {humble}")
    finally:
        C.AFFILIATES.clear()
        C.AFFILIATES.update(saved)

def test_subscribe():
    import importlib.util
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api", "subscribe.py")
    spec = importlib.util.spec_from_file_location("staz_subscribe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls = []

    def poster(url, payload, api_key, key):
        calls.append((url, payload, api_key, key))
        return 201, {"id": "contact_1", "object": "contact"}

    mod.reset_limits()
    env = {"RESEND_API_KEY": "re_test", "RESEND_SEGMENT_ID": "seg_abc123"}
    status, body = mod.process_subscribe(b'{"email":"not-an-email","hp":""}', "application/json", env=env, now=1000, ip="10.0.0.1", poster=poster)
    check(status == 400 and body["ok"] is False and not calls, f"invalid email {status} {body}")
    status, body = mod.process_subscribe(b'{"email":"ada@example.com","hp":"filled"}', "application/json", env=env, now=1000, ip="10.0.0.2", poster=poster)
    check(status == 200 and body["ok"] is True and not calls, "honeypot reached Resend")
    status, body = mod.process_subscribe(b'{"email":"ada@example.com"}', "application/json", env={"RESEND_API_KEY": ""}, now=1000, ip="10.0.0.3", poster=poster)
    check(status == 503 and not calls, "missing API key still called Resend")
    status, body = mod.process_subscribe(b'{"email":"Ada@Example.com"}', "application/json", origin="https://fermartinez430.github.io", env=env, now=1000, ip="10.0.0.4", poster=poster)
    check(status == 200 and body["ok"] is True and len(calls) == 1, f"signup failed {status} {body}")
    url, payload, api_key, key = calls[0]
    check(url == "https://api.resend.com/contacts", f"contacts url {url}")
    check(payload["email"] == "ada@example.com" and payload["unsubscribed"] is False, f"payload {payload}")
    check(payload["segments"] == [{"id": "seg_abc123"}], f"segment payload {payload}")
    check(api_key == "re_test" and key.startswith("subscribe/"), "idempotency key missing")
    calls.clear()
    status, _ = mod.process_subscribe(b'{"email":"ada@example.com"}', "application/json", origin="https://evil.example", env=env, now=1000, ip="10.0.0.5", poster=poster)
    check(status == 403 and not calls, "disallowed origin was accepted")
    calls.clear()

    def already(url, payload, api_key, key):
        calls.append((url, payload))
        if payload is None:
            return 200, {"id": "seg_abc123"}
        return 409, {"name": "validation_error", "message": "Contact already exists"}

    status, body = mod.process_subscribe(b"email=ada%40example.com", "application/x-www-form-urlencoded", env=env, now=2000, ip="10.0.0.6", poster=already)
    check(status == 200 and body["message"].startswith("You're already"), f"duplicate contact {status} {body}")
    check(len(calls) == 2 and calls[1][0].endswith("/segments/seg_abc123") and "ada%40example.com" in calls[1][0], f"segment add {calls}")
    calls.clear()
    status, body = mod.process_subscribe(b'{"email":"bea@example.com"}', "application/json", env={"RESEND_API_KEY": "re_test"}, now=3000, ip="10.0.0.7", poster=poster)
    check(status == 200 and "segments" not in calls[0][1], f"global contact payload {calls}")
    mod.reset_limits()
    blocked = None
    for n in range(6):
        blocked = mod.process_subscribe(b'{"email":"nope"}', "application/json", env=env, now=4000 + n, ip="10.1.0.8", poster=poster)
    check(blocked[0] == 429, f"rate limit missing {blocked}")
    check(mod.cors_headers("https://fermartinez430.github.io")["Access-Control-Allow-Origin"] == "https://fermartinez430.github.io", "CORS origin missing")
    check(mod.cors_headers("http://127.0.0.1:8080")["Access-Control-Allow-Origin"] == "http://127.0.0.1:8080", "localhost CORS missing")
    check("Access-Control-Allow-Origin" not in mod.cors_headers("https://evil.example"), "CORS reflected an unknown origin")
    local_status, _ = mod.process_subscribe(b'{"email":"local@example.com"}', "application/json", origin="http://localhost:3000", env=env, now=5000, ip="10.9.0.1", poster=poster)
    check(local_status == 200, f"localhost origin rejected {local_status}")

    os.environ["RESEND_API_KEY"] = "re_test"
    os.environ.pop("RESEND_SEGMENT_ID", None)
    mod.reset_limits()
    mod.post_resend = poster
    calls.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), mod.handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        req = Request(base, data=b'{"email":"cara@example.com","hp":""}', headers={"Content-Type": "application/json", "Origin": "https://stazgames.com"}, method="POST")
        with urlopen(req, timeout=5) as res:
            payload = json.loads(res.read().decode())
            check(res.status == 200 and payload["ok"] is True, f"HTTP signup {payload}")
            check(res.headers.get("Access-Control-Allow-Origin") == "https://stazgames.com", "HTTP CORS header missing")
        req = Request(base, headers={"Origin": "https://fermartinez430.github.io"}, method="OPTIONS")
        with urlopen(req, timeout=5) as res:
            check(res.status == 204, f"OPTIONS {res.status}")
            check(res.headers.get("Access-Control-Allow-Origin") == "https://fermartinez430.github.io", "OPTIONS CORS missing")
    except HTTPError as exc:
        check(False, f"subscribe HTTP error {exc.code}")
    finally:
        server.shutdown()
        os.environ.pop("RESEND_API_KEY", None)

def test_cheapshark_retry():
    calls, sleeps = [], []

    class Response:
        def __init__(self, url):
            self.url = url
        def raise_for_status(self):
            return None
        def json(self):
            if self.url.endswith("/stores"):
                return [{"storeID": "1", "storeName": "Steam", "images": {}}]
            return [{
                "title": "Hades", "storeID": "1", "salePrice": "4.99", "normalPrice": "24.99",
                "savings": "80", "metacriticScore": "93", "dealID": "abc", "thumb": "",
            }, {
                "title": "Hades", "storeID": "1", "salePrice": "1.00", "normalPrice": "2.00",
                "savings": "50", "metacriticScore": "93", "dealID": "dup", "thumb": "",
            }]

    def flaky(url, headers=None, timeout=None):
        calls.append((url, timeout))
        seen = len([c for c in calls if c[0] == url])
        if seen == 1:
            raise requests.Timeout("read timed out")
        return Response(url)

    def down(url, headers=None, timeout=None):
        calls.append((url, timeout))
        raise requests.Timeout("read timed out")

    old_get, old_sleep = build.requests.get, build.time.sleep
    try:
        build.requests.get = flaky
        build.time.sleep = lambda seconds: sleeps.append(seconds)
        build.FAILURES.clear()
        calls.clear(); sleeps.clear()
        one = build.get("https://example.com/feed")
        check(one is None and len(calls) == 1 and calls[0][1] == 20 and sleeps == [], f"default get retried {calls} {sleeps}")
        check(build.FAILURES == ["https://example.com/feed: read timed out"], f"default failure {build.FAILURES}")
        build.FAILURES.clear()
        calls.clear(); sleeps.clear()
        deals = build.fetch_deals()
        check(len(deals) == 1 and deals[0]["url"] == "https://www.cheapshark.com/redirect?dealID=abc", f"retry deals {deals}")
        check(deals[0]["savings"] == 80 and deals[0]["store"] == "Steam", f"deal selection changed {deals}")
        check(build.FAILURES == [], f"recovered CheapShark recorded {build.FAILURES}")
        deal_calls = [c for c in calls if "/deals?" in c[0]]
        store_calls = [c for c in calls if c[0].endswith("/stores")]
        check(len(deal_calls) == 2 and len(store_calls) == 2, f"attempt counts {len(deal_calls)} {len(store_calls)}")
        check(all(timeout == 45 for _, timeout in deal_calls + store_calls), f"CheapShark timeout {calls}")
        check(sleeps == [2, 2], f"backoff {sleeps}")
        build.requests.get = down
        build.FAILURES.clear()
        calls.clear(); sleeps.clear()
        empty = build.fetch_deals()
        check(empty == [], f"final failure returned deals {empty}")
        check(len(build.FAILURES) == 2 and all("read timed out" in item and "cheapshark.com" in item for item in build.FAILURES), f"final failures {build.FAILURES}")
        check(len(calls) == 6 and sleeps == [2, 2, 2, 2], f"final attempts {len(calls)} sleeps {sleeps}")
    finally:
        build.requests.get = old_get
        build.time.sleep = old_sleep
        build.FAILURES.clear()

def test_voice_and_itemlist():
    blurb = build.voice_blurb(
        "The Switch 2 version of 007 First Light",
        "The Switch 2 version of 007 First Light will launch on March 18, 2027.",
        "Gematsu",
    )
    check("March 18, 2027" in blurb and "007" in blurb and blurb.endswith("."), f"launch paraphrase {blurb}")
    check("Gematsu" in blurb and "will launch on March 18, 2027" not in blurb, f"feed sentence pasted {blurb}")
    allowed = set(re.findall(r"\d+(?:\.\d+)?", "The Switch 2 version of 007 First Light will launch on March 18, 2027."))
    found = set(re.findall(r"\d+(?:\.\d+)?", blurb))
    check(found <= allowed, f"invented number {found - allowed} in {blurb}")
    titled = build.voice_blurb("Modern Warfare 4 release times and early access explained", "", "PC Gamer")
    check(titled.startswith("PC Gamer explains") and "early access" in titled, f"title paraphrase {titled}")
    check("Modern Warfare 4 release times and early access explained" not in titled, f"headline pasted {titled}")
    check(build.voice_blurb("T", "", "IGN") == "", f"thin headline kept {build.voice_blurb('T', '', 'IGN')}")
    quoted = build.voice_blurb("A quote headline here", '"He talks about Gabe\'s yachts obsessively, so this is not an accident."', "PC Gamer")
    check("Gabe's yachts" in quoted and "PC Gamer" in quoted and "He talks about Gabe" in quoted, f"quote dropped {quoted}")
    deals = build.itemlist("Top game deals", [{
        "title": "Hades", "url": "https://www.cheapshark.com/redirect?dealID=abc",
        "price": "4.99", "normal": "24.99", "store": "Steam",
    }])
    item = deals["itemListElement"][0]["item"]
    check(deals["@type"] == "ItemList" and deals["numberOfItems"] == 1, f"deal list {deals}")
    check(item["name"] == "Hades" and item["url"].endswith("dealID=abc"), f"deal item {item}")
    check(item["offers"]["price"] == "4.99" and item["offers"]["priceCurrency"] == "USD", f"deal offer {item}")
    free = build.itemlist("Free games right now", [{
        "title": "Halo", "url": "https://example.com/halo", "platform": "Epic Games Store", "worth": "$19.99",
    }])
    offer = free["itemListElement"][0]["item"]["offers"]
    check(offer["price"] == "0.00" and offer["priceCurrency"] == "USD" and "19.99" not in str(offer), f"free offer {offer}")
    plain = build.itemlist("Misc", [{"title": "X", "url": "https://example.com/x"}])
    check("offers" not in plain["itemListElement"][0]["item"], f"unknown price got an offer {plain}")
    check(build.schema_price({"price": "nope"}) is None, "unparsed price accepted")

def test_static_config():
    import subprocess
    vercel = json.load(open("vercel.json", encoding="utf-8"))
    check(vercel.get("framework") is None, "vercel framework")
    check(vercel.get("installCommand") == "pip install --break-system-packages -r requirements.txt", "vercel installCommand")
    check(vercel.get("buildCommand") == "python3 build.py && python3 test_site.py", "vercel buildCommand")
    check(vercel.get("outputDirectory") == "public", "vercel outputDirectory")
    check("api/subscribe.py" in vercel.get("functions", {}), "vercel function config missing")
    workflow = open(".github/workflows/pages.yml", encoding="utf-8").read()
    check(workflow.count("BASE_URL: https://fermartinez430.github.io/StazNews") >= 2, "pages workflow BASE_URL")
    env = os.environ.copy()
    env["BASE_URL"] = "https://example.com/"
    ran = subprocess.run([sys.executable, "-c", "import config; print(config.BASE_URL)"], capture_output=True, text=True, env=env)
    check(ran.stdout.strip() == "https://example.com", f"BASE_URL rstrip {ran.stdout!r} {ran.stderr!r}")
    check("pillow==" in open("requirements.txt", encoding="utf-8").read(), "pillow missing from requirements")

if __name__ == "__main__":
    test_summaries()
    test_cards()
    test_voice_and_itemlist()
    test_affiliates()
    test_subscribe()
    test_cheapshark_retry()
    test_static_config()
    n, e = validate(C.OUT_DIR, external="--external" in sys.argv)
    print(f"live build: {n} pages, {e} external links {'checked' if '--external' in sys.argv else 'found'}")
    # empty-feed fallback: build to temp dir with all feeds disabled
    live_out = C.OUT_DIR; C.OUT_DIR = "/tmp/staz_empty"; shutil.rmtree(C.OUT_DIR, ignore_errors=True)
    r = build.build(offline=True); validate(C.OUT_DIR)
    home = open(C.OUT_DIR+"/index.html").read()
    check(home.count('class="empty"')==3, "empty-feed fallbacks not shown on home")
    check(json.load(open(C.OUT_DIR+"/data/deals.json"))["count"]==0, "empty deals.json wrong")
    check(json.load(open(C.OUT_DIR+"/data/news.json"))["count"]==0, "empty news.json wrong")
    print("empty-feed build:", r)
    C.OUT_DIR = live_out
    print("FAIL\n- " + "\n- ".join(ERR) if ERR else "ALL TESTS PASSED"); sys.exit(1 if ERR else 0)
