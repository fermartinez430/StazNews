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

ATOM_LINK = "{http://www.w3.org/2005/Atom}link"

def check_rss(path, self_url, channel_link, expected, html):
    check(os.path.isfile(path), f"missing feed {path}")
    if not os.path.isfile(path):
        return
    try:
        root = ET.parse(path).getroot()
    except Exception as exc:
        check(False, f"{path} XML parse error {exc}")
        return
    check(root.tag == "rss" and root.get("version") == "2.0", f"{path} not RSS 2.0")
    channel = root.find("channel")
    check(channel is not None, f"{path} missing channel")
    if channel is None:
        return
    title, link, description = channel.findtext("title"), channel.findtext("link"), channel.findtext("description")
    check(bool(title) and C.SITE_NAME in title, f"{path} channel title {title}")
    check(link == channel_link, f"{path} channel link {link}")
    check(bool(description), f"{path} missing channel description")
    built = channel.findtext("lastBuildDate")
    try:
        from email.utils import parsedate_to_datetime
        parsedate_to_datetime(built)
    except Exception as exc:
        check(False, f"{path} lastBuildDate {built} {exc}")
    self_links = [el for el in channel.findall(ATOM_LINK) if el.get("rel") == "self"]
    check(len(self_links) == 1, f"{path} atom:link self count {len(self_links)}")
    if self_links:
        check(self_links[0].get("href") == self_url, f"{path} self href {self_links[0].get('href')}")
        check(self_links[0].get("type") == "application/rss+xml", f"{path} self type")
    items = channel.findall("item")
    check(len(items) == len(expected), f"{path} item count {len(items)} vs {len(expected)}")
    if expected:
        check(bool(items), f"{path} has no items")
    for el, want in zip(items, expected):
        check(el.findtext("title") == want["title"], f"{path} title {el.findtext('title')!r}")
        check(el.findtext("link") == want["link"], f"{path} link changed {el.findtext('link')!r}")
        if want["link"]:
            check(f'href="{build.esc(want["link"])}"' in html, f"{path} feed link missing on the page {want['link']}")
            guid = el.find("guid")
            check(guid is not None and guid.text == want["link"] and guid.get("isPermaLink") == "true", f"{path} guid {want['link']}")
        if want.get("description"):
            check(el.findtext("description") == want["description"], f"{path} description mismatch for {want['title']}")
        else:
            check(not el.findtext("description"), f"{path} unexpected description for {want['title']}")
        if want.get("pubDate"):
            check(el.findtext("pubDate") == want["pubDate"], f"{path} pubDate {el.findtext('pubDate')}")
        else:
            check(el.findtext("pubDate") is None, f"{path} unexpected pubDate for {want['title']}")

def check_feeds(out, pages, free_doc, news_doc):
    news_expected = []
    for item in news_doc.get("news") or []:
        row = {"title": item.get("title") or "", "link": item.get("url") or ""}
        if item.get("summary"):
            row["description"] = item["summary"]
        if item.get("published"):
            try:
                row["pubDate"] = build.rss_datetime(item["published"])
            except Exception:
                pass
        news_expected.append(row)
    free_expected = []
    for game in free_doc.get("games") or []:
        link, _active = build.with_affiliate(game.get("url"), game.get("platform") or game.get("source") or "")
        row = {"title": game.get("title") or "", "link": link}
        label = build.end_label(game.get("ends"))
        if label:
            row["description"] = f"Ends {label}."
        free_expected.append(row)
    check_rss(os.path.join(out, "feed.xml"), C.BASE_URL + "/feed.xml", C.BASE_URL + "/news/", news_expected, pages.get("/news/", ""))
    check_rss(
        os.path.join(out, "free-games", "feed.xml"),
        C.BASE_URL + "/free-games/feed.xml",
        C.BASE_URL + "/free-games/",
        free_expected,
        pages.get("/free-games/", ""),
    )

def check_epic_page(src, parser, docs, out):
    """The Epic landing page: dates in plain text, FAQ, JSON-LD, and no empty sections."""
    path = "/free-games/epic/"
    desc = [m.get("content") for m in parser.tags.get("meta", []) if m.get("name") == "description"]
    check(parser.title.startswith("Epic Games Free Games This Week"), f"{path}: title {parser.title}")
    check(desc and "US Central Time and UTC" in desc[0], f"{path}: description {desc}")
    check(f'href="{build.href("/free-games/")}"' in src, f"{path}: missing free-games link")
    check(f'href="{build.href("/")}"' in src, f"{path}: missing homepage link")
    check("Central Time" in src and "UTC" in src, f"{path}: clocks missing")
    fg = json.load(open(os.path.join(out, "data", "free-games.json"), encoding="utf-8"))
    epic_now = [g for g in fg.get("games") or [] if g.get("source") == "Epic Games"]
    upcoming = fg.get("upcoming") or []
    for game in epic_now:
        check(game["title"] in parser.title, f"{path}: title missed {game['title']}")
        check(game["title"] in (desc[0] if desc else ""), f"{path}: description missed {game['title']}")
    if not epic_now:
        check("No Epic Games Store game is free" in (desc[0] if desc else ""), f"{path}: empty description {desc}")
    now_m = re.search(r'<section id="epic-now"[^>]*>(.*?)</section>', src, re.S)
    next_m = re.search(r'<section id="epic-next"[^>]*>(.*?)</section>', src, re.S)
    if epic_now:
        check(now_m is not None, f"{path}: missing current section")
        block = now_m.group(0) if now_m else ""
        for game in epic_now:
            check(build.esc(game["title"]) in block, f"{path}: current card missing {game['title']}")
            when = build.plain_when(game.get("ends"))
            clocks = build.zone_clocks(game.get("ends"))
            if when:
                check(when in src, f"{path}: plain end missing {when}")
            if clocks:
                check(clocks[0] in block and clocks[1] in block, f"{path}: card clocks missing {game['title']}")
    else:
        check(now_m is None, f"{path}: empty current section rendered")
        check("No Epic game is free to keep right now." in src, f"{path}: missing empty-current sentence")
    if upcoming:
        check(next_m is not None, f"{path}: missing upcoming section")
        block = next_m.group(0) if next_m else ""
        for game in upcoming:
            check(build.esc(game["title"]) in block, f"{path}: upcoming card missing {game['title']}")
            for key in ("starts", "ends"):
                when = build.plain_when(game.get(key))
                clocks = build.zone_clocks(game.get(key))
                if when:
                    check(when in src, f"{path}: plain {key} missing {when}")
                if clocks:
                    check(clocks[0] in block and clocks[1] in block, f"{path}: upcoming {key} clocks {game['title']}")
    else:
        check(next_m is None, f"{path}: empty upcoming section rendered")
        check("Epic has not posted the next free game yet." in src, f"{path}: missing empty-upcoming sentence")
    faqs = [j for j in docs if j.get("@type") == "FAQPage"]
    check(len(faqs) == 1, f"{path}: expected one FAQPage, got {len(faqs)}")
    entities = (faqs[0].get("mainEntity") if faqs else None) or []
    check(faqs and faqs[0].get("@context") == "https://schema.org", f"{path}: FAQPage context")
    names = [q.get("name") for q in entities]
    check(names == [
        "When do Epic free games change?",
        "Do you keep Epic free games forever?",
        "Do you need an Epic Games Store account?",
    ], f"{path}: FAQ questions {names}")
    for question in entities:
        answer = question.get("acceptedAnswer") or {}
        check(question.get("@type") == "Question" and answer.get("@type") == "Answer" and answer.get("text"), f"{path}: FAQ entry {question.get('name')}")
        check(f"<h3>{build.esc(question.get('name'))}</h3>" in src, f"{path}: FAQ question not visible {question.get('name')}")
        check(f"<p>{build.esc(answer.get('text'))}</p>" in src, f"{path}: FAQ answer not visible {question.get('name')}")
        try:
            json.loads(json.dumps(question))
        except Exception as exc:
            check(False, f"{path}: FAQ JSON-LD parse {exc}")
    lists = [j for j in docs if j.get("@type") == "ItemList"]
    shown = epic_now + upcoming
    h3 = 0
    if now_m:
        h3 += now_m.group(1).count("<h3>")
    if next_m:
        h3 += next_m.group(1).count("<h3>")
    if shown:
        check(len(lists) == 1, f"{path}: expected one ItemList, got {len(lists)}")
        elements = (lists[0].get("itemListElement") if lists else None) or []
        check(lists and lists[0].get("numberOfItems") == len(elements) == len(shown) == h3, f"{path}: ItemList size {len(elements)} vs {len(shown)} cards {h3}")
        check(lists and lists[0].get("@context") == "https://schema.org", f"{path}: ItemList context")
        for game, element in zip(shown, elements):
            item = element.get("item") or {}
            offer = item.get("offers") or {}
            check(item.get("name") == game.get("title") and item.get("url") == game.get("url"), f"{path}: ItemList entry {item}")
            check(offer.get("price") == "0.00" and offer.get("priceCurrency") == "USD", f"{path}: ItemList price {offer}")
        try:
            json.loads(json.dumps(lists[0]))
        except Exception as exc:
            check(False, f"{path}: ItemList JSON-LD parse {exc}")
    else:
        check(not lists, f"{path}: ItemList without Epic games")
        check(h3 == 0, f"{path}: cards rendered with no games")

def validate(out, external=False):
    pages = html_pages(out)
    for req in ["/", "/free-games/", "/free-games/epic/", "/deals/", "/news/", "/about/", "/newsletter/", "/disclosure/", "/privacy/", "/contact/", "/terms/", "/404.html"]: check(req in pages, f"missing page {req}")
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
        check('name="impact-site-verification" value="16c69e26-fc4d-424f-a478-069c40c6c520"' in src, f"{path}: impact verification missing")
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
        rss = [a.get("href") for a in p.tags.get("link", []) if a.get("rel") == "alternate" and a.get("type") == "application/rss+xml"]
        check(rss.count(build.href("/feed.xml")) == 1, f"{path}: news feed link {rss}")
        if path == "/free-games/":
            check(rss.count(build.href("/free-games/feed.xml")) == 1, f"{path}: free-games feed link {rss}")
        else:
            check(build.href("/free-games/feed.xml") not in rss, f"{path}: free-games feed link {rss}")
        crumbs = [j for j in docs if j.get("@type") == "BreadcrumbList"]
        if path == "/":
            check(not crumbs, f"{path}: homepage BreadcrumbList")
        else:
            check(len(crumbs) == 1, f"{path}: expected one BreadcrumbList, got {len(crumbs)}")
            elements = (crumbs[0].get("itemListElement") if crumbs else None) or []
            check(bool(crumbs) and crumbs[0].get("@context") == "https://schema.org", f"{path}: breadcrumb context")
            crumb_len = 3 if path == "/free-games/epic/" else 2
            check(len(elements) == crumb_len, f"{path}: breadcrumb length {len(elements)}")
            home_crumb = elements[0] if elements else {}
            page_crumb = elements[-1] if elements else {}
            check(home_crumb.get("@type") == "ListItem" and home_crumb.get("position") == 1, f"{path}: breadcrumb home position")
            check(home_crumb.get("name") == "Home" and home_crumb.get("item") == C.BASE_URL + "/", f"{path}: breadcrumb home {home_crumb}")
            check(page_crumb.get("@type") == "ListItem" and page_crumb.get("position") == crumb_len, f"{path}: breadcrumb page position")
            check(page_crumb.get("name") == build.CRUMBS.get(path) and page_crumb.get("item") == C.BASE_URL + path, f"{path}: breadcrumb page {page_crumb}")
            if path == "/free-games/epic/" and len(elements) == 3:
                parent = elements[1]
                check(parent.get("@type") == "ListItem" and parent.get("position") == 2, f"{path}: breadcrumb parent position")
                check(parent.get("name") == "Free games" and parent.get("item") == C.BASE_URL + "/free-games/", f"{path}: breadcrumb parent {parent}")
        updated_m = re.search(r'Updated <time datetime="([^"]+)">([^<]+) CT</time>', src)
        if path in build.FRESHNESS_PATHS:
            check(updated_m is not None, f"{path}: Updated line")
            stamp = updated_m.group(1) if updated_m else ""
            check(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}", stamp), f"{path}: Updated datetime {stamp}")
            webs = [j for j in docs if j.get("@type") == "WebPage"]
            check(len(webs) == 1 and webs[0].get("dateModified") == stamp, f"{path}: WebPage dateModified {webs}")
            check(webs and webs[0].get("url") == C.BASE_URL + path, f"{path}: WebPage url")
            for item_list in [j for j in docs if j.get("@type") == "ItemList"]:
                check(item_list.get("dateModified") == stamp, f"{path}: ItemList dateModified {item_list.get('name')}")
        else:
            check(not any(j.get("@type") == "WebPage" for j in docs), f"{path}: unexpected WebPage")
        if path in ("/", "/free-games/"):
            check(f'href="{build.href("/free-games/epic/")}"' in src, f"{path}: missing Epic page link")
        def check_itemlist(card_h3, price_kind):
            lists = [j for j in docs if j.get("@type") == "ItemList"]
            check(len(lists) == 1, f"{path}: expected one ItemList, got {len(lists)}")
            elements = (lists[0].get("itemListElement") if lists else None) or []
            check(elements and lists and lists[0].get("numberOfItems") == len(elements), f"{path}: ItemList count")
            check(card_h3 == len(elements), f"{path}: ItemList size {len(elements)} vs cards {card_h3}")
            for el in elements:
                item = el.get("item") or {}
                offer = item.get("offers") or {}
                check(isinstance(el.get("position"), int) and item.get("@type") == "Product" and item.get("name") and item.get("url"), f"{path}: ItemList entry {item.get('name')}")
                if price_kind == "deal":
                    check(offer.get("priceCurrency") == "USD" and re.fullmatch(r"\d+\.\d{2}", str(offer.get("price") or "")), f"{path}: deal price {offer} for {item.get('name')}")
                else:
                    check(offer.get("price") == "0.00" and offer.get("priceCurrency") == "USD", f"{path}: free price {offer} for {item.get('name')}")
        if path == "/deals/" and 'class="cards"' in src:
            check_itemlist(src.count("<h3>"), "deal")
        if path == "/free-games/":
            now_m = re.search(r'<section id="free-now"[^>]*>(.*?)</section>', src, re.S)
            check(now_m is not None, f"{path}: missing free-now section")
            now_h3 = now_m.group(1).count("<h3>") if now_m else 0
            if now_h3:
                check_itemlist(now_h3, "free")
            else:
                lists = [j for j in docs if j.get("@type") == "ItemList"]
                check(not lists, f"{path}: ItemList without current free games")
            faqs = [j for j in docs if j.get("@type") == "FAQPage"]
            check(len(faqs) == 1, f"{path}: expected one FAQPage, got {len(faqs)}")
            entities = (faqs[0].get("mainEntity") if faqs else None) or []
            check(faqs and faqs[0].get("@context") == "https://schema.org", f"{path}: FAQPage context")
            names = [q.get("name") for q in entities]
            check(names == [
                "What games are free on Epic right now?",
                "When is the next free Epic game?",
                "How long do I have to claim a free game?",
                "What do these Epic games normally cost?",
            ], f"{path}: FAQ questions {names}")
            answers = {}
            for q in entities:
                ans = q.get("acceptedAnswer") or {}
                check(q.get("@type") == "Question" and ans.get("@type") == "Answer" and ans.get("text"), f"{path}: FAQ entry {q.get('name')}")
                check(f"<h3>{build.esc(q.get('name'))}</h3>" in src, f"{path}: FAQ question not visible {q.get('name')}")
                check(f"<p>{build.esc(ans.get('text'))}</p>" in src, f"{path}: FAQ answer not visible {q.get('name')}")
                answers[q.get("name")] = ans.get("text") or ""
            fg = json.load(open(os.path.join(out, "data", "free-games.json"), encoding="utf-8"))
            epic_now = [g for g in fg.get("games") or [] if g.get("source") == "Epic Games"]
            current_a = answers.get("What games are free on Epic right now?", "")
            next_a = answers.get("When is the next free Epic game?", "")
            claim_a = answers.get("How long do I have to claim a free game?", "")
            cost_a = answers.get("What do these Epic games normally cost?", "")
            if epic_now:
                for g in epic_now:
                    check(g["title"] in current_a, f"FAQ missed current Epic game {g['title']}")
                    when = build.ct(g.get("ends") or "")
                    if when:
                        check(when in claim_a, f"FAQ claim answer missed {when}")
                    price = build.listed_price(g.get("worth"))
                    if price:
                        check(g["title"] in cost_a and price in cost_a, f"FAQ cost answer missed {g['title']}")
            else:
                check("No Epic Games Store game is free" in current_a, "empty Epic FAQ")
            coming_m = re.search(r'<section id="coming-next"[^>]*>(.*?)</section>', src, re.S)
            upcoming = fg.get("upcoming") or []
            if upcoming:
                check(coming_m is not None, "upcoming games missing Coming next section")
                block = coming_m.group(0) if coming_m else ""
                check('<h2 id="coming-next-title">Coming next on Epic</h2>' in block, "coming next heading")
                now_html = now_m.group(0) if now_m else ""
                for g in upcoming:
                    check(build.esc(g["title"]) in block, f"upcoming card missing {g['title']}")
                    check(build.esc(g["title"]) not in now_html, f"upcoming game listed as current {g['title']}")
                    check(g["title"] in next_a, f"FAQ next answer missed {g['title']}")
                    for iso_key in ("starts", "ends"):
                        label = build.ct(g.get(iso_key) or "")
                        check(label and label in block, f"upcoming {iso_key} missing for {g['title']}")
                        check(label in next_a, f"FAQ next answer missed {label}")
                    price = build.listed_price(g.get("worth"))
                    if price:
                        check(build.esc(price) in block, f"upcoming price missing {price}")
                        check(g["title"] in cost_a and price in cost_a, f"FAQ cost answer missed upcoming {g['title']}")
            else:
                check(coming_m is None and "Coming next on Epic" not in src, "empty upcoming still rendered")
                check("not announced" in next_a, "empty next FAQ")
            home = pages.get("/", "")
            if upcoming:
                check("Coming next on Epic" in home, "homepage missing coming next mention")
                check(build.href("/free-games/") + "#coming-next" in home, "homepage missing coming next link")
                for g in upcoming[:4]:
                    check(build.esc(g["title"]) in home, f"homepage mention missed {g['title']}")
            else:
                check("Coming next on Epic" not in home, "homepage mention rendered with no upcoming games")
        if path == "/free-games/epic/":
            check_epic_page(src, p, docs, out)
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
    check(not any("feed.xml" in (loc or "") for loc in locs), f"sitemap includes a feed: {sorted(locs)}")
    check(C.BASE_URL + "/404.html" not in locs, "404 should not be in the sitemap")
    check(C.BASE_URL + "/free-games/epic/" in locs, "sitemap missing Epic page")
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    lastmods = []
    for url_el in sm.findall(ns + "url"):
        loc = url_el.findtext(ns + "loc")
        lastmod = url_el.findtext(ns + "lastmod") or ""
        lastmods.append(lastmod)
        check(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}", lastmod), f"sitemap lastmod {loc} {lastmod}")
    home_stamp = re.search(r'Updated <time datetime="([^"]+)">', pages.get("/", ""))
    check(home_stamp is not None and lastmods and set(lastmods) == {home_stamp.group(1)}, f"sitemap lastmod drifted from Updated {lastmods[:1]}")
    rb = open(os.path.join(out,"robots.txt")).read()
    check(f"Sitemap: {C.BASE_URL}/sitemap.xml" in rb, "robots.txt missing sitemap")
    check(f"Allow: {project_prefix() + '/' if project_prefix() else '/'}" in rb, "robots.txt allow path missing project subpath")
    for f,k in [("deals.json","deals"),("free-games.json","games"),("news.json","news")]:
        j=json.load(open(os.path.join(out,"data",f))); check(isinstance(j.get(k),list) and j["count"]==len(j[k]), f"{f} malformed")
        check(isinstance(j.get("updated"), str) and j["updated"], f"{f} missing updated")
    free_doc = json.load(open(os.path.join(out, "data", "free-games.json"), encoding="utf-8"))
    check(isinstance(free_doc.get("upcoming"), list), "free-games.json upcoming missing")
    game_keys = {"title", "platform", "source", "url", "worth", "ends", "image"}
    built_at = build.dt.datetime.fromisoformat(free_doc["updated"])
    for g in free_doc["games"]:
        check(set(g) == game_keys, f"free game shape changed {sorted(set(g) ^ game_keys)} on {g.get('title')}")
        if g.get("source") == "GamerPower" and g.get("ends"):
            check(not build.gamerpower_ended(g["ends"], built_at), f"expired GamerPower giveaway {g.get('title')} ends {g.get('ends')}")
    for g in free_doc["upcoming"]:
        check(set(g) == game_keys | {"starts"}, f"upcoming shape {sorted(set(g))} on {g.get('title')}")
        check(g.get("source") == "Epic Games" and g.get("platform") == "Epic Games Store", f"upcoming source {g.get('title')}")
        try:
            start = build.dt.datetime.fromisoformat(g["starts"])
            end = build.dt.datetime.fromisoformat(g["ends"])
            check(start < end, f"upcoming window {g.get('title')}")
        except Exception as exc:
            check(False, f"upcoming dates {g.get('title')} {exc}")
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
    for frag in ("/free-games/", "/free-games/epic/", "/deals/", "/news/", "/about/", "/disclosure/", "/privacy/", "/contact/", "/terms/", "/data/free-games.json", "/data/deals.json", "/data/news.json", "/feed.xml", "/free-games/feed.xml"):
        check(C.BASE_URL + frag in llms, f"llms.txt missing {C.BASE_URL}{frag}")
    data = llms.split("## Data", 1)[-1].split("\n## ", 1)[0]
    check(C.BASE_URL + "/feed.xml" in data and C.BASE_URL + "/free-games/feed.xml" in data, "llms.txt Data section missing feeds")
    check_feeds(out, pages, free_doc, news_doc)
    check("## Coming next" in llms, "llms.txt missing Coming next")
    coming_txt = llms.split("## Coming next", 1)[-1]
    if free_doc.get("upcoming"):
        for g in free_doc["upcoming"]:
            check(g["title"] in coming_txt, f"llms.txt missing upcoming {g['title']}")
    else:
        check("None scheduled" in coming_txt, "llms.txt missing empty coming next")
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

def test_epic_upcoming_and_faq():
    import datetime as dt
    now = dt.datetime(2026, 10, 18, 18, 0, tzinfo=dt.timezone.utc)
    elements = [
        {
            "title": "Bad Cheese",
            "productSlug": "bad-cheese",
            "price": {"totalPrice": {"fmtPrice": {"originalPrice": "$12.99"}}},
            "keyImages": [],
            "promotions": {
                "promotionalOffers": [{"promotionalOffers": [{
                    "startDate": "2026-10-15T15:00:00.000Z",
                    "endDate": "2026-10-22T15:00:00.000Z",
                    "discountSetting": {"discountType": "PERCENTAGE", "discountPercentage": 0},
                }]}],
                "upcomingPromotionalOffers": [{"promotionalOffers": [{
                    "startDate": "2026-11-05T15:00:00.000Z",
                    "endDate": "2026-11-12T15:00:00.000Z",
                    "discountSetting": {"discountType": "PERCENTAGE", "discountPercentage": 0},
                }]}],
            },
        },
        {
            "title": "Sker Ritual",
            "productSlug": "sker-ritual",
            "price": {"totalPrice": {"fmtPrice": {"originalPrice": "$24.99"}}},
            "keyImages": [],
            "promotions": {"upcomingPromotionalOffers": [{"promotionalOffers": [
                {
                    "startDate": "2026-10-19T15:00:00.000Z",
                    "endDate": "2026-11-02T16:00:00.000Z",
                    "discountSetting": {"discountType": "PERCENTAGE", "discountPercentage": 20},
                },
                {
                    "startDate": "2026-12-10T16:00:00.000Z",
                    "endDate": "2027-01-07T16:00:00.000Z",
                    "discountSetting": {"discountType": "PERCENTAGE", "discountPercentage": 0},
                },
                {
                    "startDate": "2026-10-22T15:00:00.000Z",
                    "endDate": "2026-10-29T15:00:00.000Z",
                    "discountSetting": {"discountType": "PERCENTAGE", "discountPercentage": 0},
                },
            ]}]},
        },
        {
            "title": "Home - A Unique Horror Adventure",
            "productSlug": "home",
            "price": {"totalPrice": {"fmtPrice": {"originalPrice": "$4.99"}}},
            "keyImages": [],
            "promotions": {"upcomingPromotionalOffers": [{"promotionalOffers": [{
                "startDate": "2026-10-22T15:00:00.000Z",
                "endDate": "2026-10-29T15:00:00.000Z",
                "discountSetting": {"discountType": "PERCENTAGE", "discountPercentage": 0},
            }]}]},
        },
        {
            "title": "Eternal Threads",
            "productSlug": "eternal-threads",
            "price": {"totalPrice": {"fmtPrice": {"originalPrice": "$19.99"}}},
            "keyImages": [],
            "promotions": {"upcomingPromotionalOffers": [{"promotionalOffers": [{
                "startDate": "2026-10-19T15:00:00.000Z",
                "endDate": "2026-11-02T16:00:00.000Z",
                "discountSetting": {"discountType": "PERCENTAGE", "discountPercentage": 20},
            }]}]},
        },
        {
            "title": "Broken Offer",
            "promotions": {"upcomingPromotionalOffers": [{"promotionalOffers": [{
                "startDate": "not-a-date",
                "endDate": "also-not",
                "discountSetting": {"discountPercentage": 0},
            }]}]},
        },
    ]
    build.FAILURES.clear()
    try:
        current, upcoming = build.parse_epic_catalog(elements, now=now)
        check([g["title"] for g in current] == ["Bad Cheese"], f"current epic {current}")
        check(set(current[0]) == {"title", "platform", "source", "url", "worth", "ends", "image"}, f"current shape {current[0]}")
        check("starts" not in current[0] and current[0]["worth"] == "$12.99", f"current price {current[0]}")
        check([g["title"] for g in upcoming] == ["Home - A Unique Horror Adventure", "Sker Ritual"], f"upcoming {upcoming}")
        check(upcoming[1]["worth"] == "$24.99" and upcoming[1]["starts"].startswith("2026-10-22T15:00:00"), f"sker window {upcoming[1]}")
        check(upcoming[1]["url"].endswith("/p/sker-ritual"), f"sker url {upcoming[1]['url']}")
        check("Eternal Threads" not in [g["title"] for g in upcoming] and "Broken Offer" not in [g["title"] for g in upcoming], f"paid or broken included {upcoming}")
        check(any(item.startswith("epic upcoming:") for item in build.FAILURES), f"broken offer not recorded {build.FAILURES}")
        check(build.omit_listed(upcoming, [{"title": "Sker Ritual (Epic Games)"}])[0]["title"] == "Home - A Unique Horror Adventure", "normalized title still listed as upcoming")
        check(build.upcoming_section([]) == "" and build.upcoming_mention([]) == "", "empty upcoming rendered")
        section = build.upcoming_section(upcoming)
        check("Coming next on Epic" in section and "$24.99" in section and "$4.99" in section, f"section prices {section}")
        check("Oct 22, 10:00 AM CT" in section and "Oct 29, 10:00 AM CT" in section, f"section dates {section}")
        check('rel="sponsored nofollow noopener"' in section and 'class="ad"' not in section, "upcoming card rel")
        mention = build.upcoming_mention(upcoming)
        check("Coming next on Epic" in mention and "Sker Ritual ($24.99)" in mention and "#coming-next" in mention, f"mention {mention}")
        check("<h3>" not in mention and "Oct 22, 10:00 AM CT" in mention, f"mention dates {mention}")
        entries = dict(build.faq_entries(current, upcoming))
        check("Bad Cheese" in entries["What games are free on Epic right now?"] and "$12.99" in entries["What games are free on Epic right now?"], entries)
        check("Sker Ritual" in entries["When is the next free Epic game?"] and "Home - A Unique Horror Adventure" in entries["When is the next free Epic game?"], entries)
        check("Eternal Threads" not in entries["When is the next free Epic game?"], entries)
        check("Oct 22, 10:00 AM CT" in entries["How long do I have to claim a free game?"], entries)
        check("$24.99" in entries["What do these Epic games normally cost?"] and "$4.99" in entries["What do these Epic games normally cost?"], entries)
        empty = dict(build.faq_entries([], []))
        check(len(empty) == 4 and "No Epic Games Store game is free" in empty["What games are free on Epic right now?"], empty)
        check("not announced" in empty["When is the next free Epic game?"], empty)
        check("Coming next on Epic" not in " ".join(empty.values()), empty)
        ld = build.faq_jsonld(list(empty.items()))
        raw = json.dumps(ld)
        check(ld["@context"] == "https://schema.org" and ld["@type"] == "FAQPage" and len(ld["mainEntity"]) == 4, ld)
        check(json.loads(raw)["mainEntity"][0]["acceptedAnswer"]["@type"] == "Answer", "FAQ JSON-LD answer")
        check(build.parse_epic_catalog([], now=now) == ([], []), "empty catalog")
        check(build.parse_epic_catalog({"nope": True}, now=now) == ([], []), f"bad catalog {build.FAILURES}")
    finally:
        build.FAILURES.clear()

def test_epic_landing():
    import datetime as dt
    now = dt.datetime(2026, 10, 21, 9, 30, tzinfo=build.CT)
    current = [{
        "title": "Bad Cheese",
        "platform": "Epic Games Store",
        "source": "Epic Games",
        "url": "https://store.epicgames.com/en-US/p/bad-cheese",
        "worth": "$12.99",
        "ends": "2026-10-22T15:00:00+00:00",
        "image": "",
    }]
    upcoming = [{
        "title": "Sker Ritual",
        "platform": "Epic Games Store",
        "source": "Epic Games",
        "url": "https://store.epicgames.com/en-US/p/sker-ritual",
        "worth": "$24.99",
        "starts": "2026-10-22T15:00:00+00:00",
        "ends": "2026-10-29T15:00:00+00:00",
        "image": "",
    }, {
        "title": "Home - A Unique Horror Adventure",
        "platform": "Epic Games Store",
        "source": "Epic Games",
        "url": "https://store.epicgames.com/en-US/p/home",
        "worth": "$4.99",
        "starts": "2026-10-22T15:00:00+00:00",
        "ends": "2026-10-29T15:00:00+00:00",
        "image": "",
    }]
    summer = build.plain_when("2026-10-22T15:00:00+00:00")
    winter = build.plain_when("2026-01-15T18:00:00+00:00")
    rollover = build.plain_when("2026-01-16T05:00:00+00:00")
    check(summer == "October 22, 2026 at 10:00 AM Central Time (October 22, 2026 at 3:00 PM UTC)", summer)
    check(winter == "January 15, 2026 at 12:00 PM Central Time (January 15, 2026 at 6:00 PM UTC)", winter)
    check(rollover == "January 15, 2026 at 11:00 PM Central Time (January 16, 2026 at 5:00 AM UTC)", rollover)
    check(build.plain_when("") == "" and build.plain_when("not-a-date") == "" and build.zone_clocks("2026-10-22T15:00:00") is None, "naive or blank clock accepted")

    def render(now_games, later_games):
        body, blocks, title, desc = build.epic_landing(now_games, later_games)
        src = build.page("/free-games/epic/", title, desc, body, now, jsonld=blocks)
        try:
            docs = jsonld_docs(src)
        except Exception as exc:
            check(False, f"epic JSON-LD parse {exc}")
            docs = []
        return body, blocks, title, desc, src, docs

    body, blocks, title, desc, src, docs = render(current, upcoming)
    types = {doc.get("@type") for doc in docs}
    check({"ItemList", "FAQPage", "BreadcrumbList", "WebPage", "Organization", "WebSite"} <= types, f"epic types {types}")
    check("Bad Cheese" in title and "Bad Cheese" in desc, f"names missing from title or description {title} {desc}")
    check(summer in body, f"intro missed the claim deadline {body[:400]}")
    check("October 29, 2026 at 10:00 AM Central Time" in body and "October 29, 2026 at 3:00 PM UTC" in body, "upcoming end clocks missing")
    intro = body.split("</p>", 1)[0]
    check(intro.count("October 22, 2026 at 10:00 AM Central Time") == 2, f"shared clocks were not grouped {intro}")
    check(intro.count("October 29, 2026 at 10:00 AM Central Time") == 1, f"shared end repeated {intro}")
    shared = build.epic_current_sentence(current + [{**current[0], "title": "Agent A"}])
    check(shared == f"Bad Cheese and Agent A are free until {summer}.", shared)
    check('id="epic-now"' in body and 'id="epic-next"' in body, "filled epic sections missing")
    check('href="' + build.href("/free-games/") + '"' in body and 'href="' + build.href("/") + '"' in body, "epic page links")
    item_lists = [doc for doc in docs if doc.get("@type") == "ItemList"]
    check(len(item_lists) == 1 and item_lists[0].get("numberOfItems") == 3, f"filled ItemList {item_lists}")
    check(item_lists and item_lists[0].get("dateModified") == now.isoformat(), f"ItemList dateModified {item_lists}")
    web = [doc for doc in docs if doc.get("@type") == "WebPage"]
    check(web and web[0].get("dateModified") == now.isoformat(), f"WebPage dateModified {web}")
    crumbs = [doc for doc in docs if doc.get("@type") == "BreadcrumbList"]
    names = [item.get("name") for item in (crumbs[0].get("itemListElement") if crumbs else [])]
    check(names == ["Home", "Free games", "Epic free games"], f"epic crumbs {names}")
    faqs = [doc for doc in docs if doc.get("@type") == "FAQPage"]
    questions = [q.get("name") for q in (faqs[0].get("mainEntity") if faqs else [])]
    check(questions == [
        "When do Epic free games change?",
        "Do you keep Epic free games forever?",
        "Do you need an Epic Games Store account?",
    ], f"epic FAQ {questions}")
    check(all((q.get("acceptedAnswer") or {}).get("text") for q in faqs[0]["mainEntity"]), "blank FAQ answer")
    check("Bad Cheese ends " + summer in (faqs[0]["mainEntity"][0]["acceptedAnswer"]["text"]), "FAQ missed the live deadline")

    only_body, _, only_title, only_desc, _, only_docs = render(current, [])
    check('id="epic-now"' in only_body and 'id="epic-next"' not in only_body, "upcoming section rendered with none scheduled")
    check("Epic has not posted the next free game yet." in only_body, "missing empty upcoming sentence")
    check("Bad Cheese" in only_title and "has not been announced" in only_desc, f"current-only copy {only_title} {only_desc}")
    only_lists = [doc for doc in only_docs if doc.get("@type") == "ItemList"]
    check(len(only_lists) == 1 and only_lists[0].get("numberOfItems") == 1, f"current-only ItemList {only_lists}")
    check(only_lists and only_lists[0]["itemListElement"][0]["item"]["name"] == "Bad Cheese", "current-only item")

    only_next_body, only_next_blocks, next_title, next_desc, _, only_next_docs = render([], upcoming)
    check('id="epic-now"' not in only_next_body and 'id="epic-next"' in only_next_body, "current section rendered with none live")
    check("No Epic game is free to keep right now." in only_next_body, "missing empty current sentence")
    check("Bad Cheese" not in next_title and "No Epic Games Store game is free" in next_desc, f"empty current copy {next_title} {next_desc}")
    next_lists = [doc for doc in only_next_docs if doc.get("@type") == "ItemList"]
    check(len(next_lists) == 1 and next_lists[0].get("numberOfItems") == 2, f"upcoming-only ItemList {next_lists}")
    check(all((q.get("acceptedAnswer") or {}).get("text") for doc in only_next_blocks if doc.get("@type") == "FAQPage" for q in doc["mainEntity"]), "upcoming-only FAQ blank")

    empty_body, empty_blocks, empty_title, empty_desc, empty_src, empty_docs = render([], [])
    check('id="epic-now"' not in empty_body and 'id="epic-next"' not in empty_body and "<ul" not in empty_body, f"empty sections rendered {empty_body}")
    check("No Epic game is free to keep right now." in empty_body and "Epic has not posted the next free game yet." in empty_body, "empty intro")
    check(not any(doc.get("@type") == "ItemList" for doc in empty_blocks), f"empty ItemList {empty_blocks}")
    check(empty_title == f"Epic Games Free Games This Week | {C.SITE_NAME}", empty_title)
    check("No Epic Games Store game is free to keep this week." in empty_desc and "has not been announced" in empty_desc, empty_desc)
    empty_faq = [doc for doc in empty_docs if doc.get("@type") == "FAQPage"]
    check(len(empty_faq) == 1 and len(empty_faq[0].get("mainEntity") or []) == 3, f"empty FAQ {empty_faq}")
    check(all((q.get("acceptedAnswer") or {}).get("text") for q in empty_faq[0]["mainEntity"]), "empty-state FAQ blank")
    check("When do Epic free games change?" in empty_src and "Central Time" in empty_src and "UTC" in empty_src, "empty FAQ text missing")
    check(re.search(r'Updated <time datetime="[^"]+">[^<]+ CT</time>', empty_src), "empty epic page missing Updated")
    try:
        json.loads(json.dumps(empty_faq[0]))
    except Exception as exc:
        check(False, f"empty FAQ JSON-LD {exc}")

def test_rss_and_breadcrumbs():
    import datetime as dt
    updated = dt.datetime(2026, 10, 20, 8, 17, tzinfo=build.CT)
    news = [{
        "title": "Rock & Roll <News>",
        "source": "IGN",
        "url": "https://www.ign.com/articles/rock?a=1&b=2",
        "published": "2026-10-20T15:00:00+00:00",
        "summary": "Publisher has revealed a loud new album for the first time.",
    }, {
        "title": "No date",
        "source": "VGC",
        "url": "https://www.videogameschronicle.com/news/no-date/",
        "summary": "",
    }]
    root = ET.fromstring(build.render_rss(
        f"{C.SITE_NAME} gaming news",
        C.BASE_URL + "/news/",
        "Gaming headlines with a short summary and a link to the original publisher.",
        C.BASE_URL + "/feed.xml",
        updated,
        build.news_rss_items(news),
    ))
    channel = root.find("channel")
    items = channel.findall("item")
    check(root.tag == "rss" and root.get("version") == "2.0", "unit news feed is not RSS 2.0")
    check(len(items) == 2, f"unit news feed item count {len(items)}")
    check(items[0].findtext("title") == news[0]["title"], f"news title escaped wrong {items[0].findtext('title')}")
    check(items[0].findtext("link") == news[0]["url"] and items[0].findtext("guid") == news[0]["url"], f"news link altered {items[0].findtext('link')}")
    blurb = build.voice_blurb(news[0]["title"], news[0]["summary"], news[0]["source"])
    check(bool(blurb) and items[0].findtext("description") == blurb, f"news description {items[0].findtext('description')}")
    check(items[0].findtext("pubDate") == build.rss_datetime(news[0]["published"]), f"news pubDate {items[0].findtext('pubDate')}")
    check(items[1].findtext("link") == news[1]["url"], "second news link altered")
    check(items[1].findtext("pubDate") is None and items[1].findtext("description") is None, "empty news summary gained a date or description")
    self_link = channel.find(ATOM_LINK)
    check(self_link is not None and self_link.get("rel") == "self" and self_link.get("type") == "application/rss+xml", "news atom self")
    check(self_link is not None and self_link.get("href") == C.BASE_URL + "/feed.xml", "news self href")
    check(channel.findtext("link") == C.BASE_URL + "/news/" and channel.findtext("lastBuildDate"), "news channel fields")
    saved = {k: dict(v) for k, v in C.AFFILIATES.items()}
    try:
        C.AFFILIATES["humble"] = {"partner": "staz-humble"}
        games = [{
            "title": "Widget",
            "platform": "Humble Store",
            "source": "Humble",
            "url": "https://www.humblebundle.com/store/widget?x=1",
            "worth": "$5.00",
            "ends": "2026-10-22T15:00:00+00:00",
            "image": "",
        }, {
            "title": "Redirected",
            "platform": "Humble Store",
            "source": "Humble",
            "url": "https://www.cheapshark.com/redirect?dealID=abc",
            "worth": "",
            "ends": "2026-10-31T23:59:00",
            "image": "",
        }, {
            "title": "No End",
            "platform": "Epic Games Store",
            "source": "Epic Games",
            "url": "https://store.epicgames.com/en-US/p/no-end",
            "worth": "",
            "ends": "",
            "image": "",
        }]
        free_items = ET.fromstring(build.render_rss(
            f"{C.SITE_NAME} free games",
            C.BASE_URL + "/free-games/",
            "Current free-to-keep games with the end date of each giveaway.",
            C.BASE_URL + "/free-games/feed.xml",
            updated,
            build.free_rss_items(games),
        )).find("channel").findall("item")
        humble_link, active = build.with_affiliate(games[0]["url"], games[0]["platform"])
        check(active and "partner=staz-humble" in humble_link and free_items[0].findtext("link") == humble_link, f"affiliate link {free_items[0].findtext('link')}")
        check(free_items[0].findtext("description") == "Ends Oct 22, 10:00 AM CT.", f"end date description {free_items[0].findtext('description')}")
        check(free_items[1].findtext("link") == "https://www.cheapshark.com/redirect?dealID=abc" and "partner=" not in free_items[1].findtext("link"), f"cheapshark altered {free_items[1].findtext('link')}")
        check(free_items[1].findtext("description") == "Ends 2026-10-31.", f"plain end date {free_items[1].findtext('description')}")
        check(free_items[2].findtext("link") == games[2]["url"] and free_items[2].findtext("description") is None, "undated free game changed")
    finally:
        C.AFFILIATES.clear()
        C.AFFILIATES.update(saved)
    home = build.page("/", "Home title unique", "Home description unique.", "<h1>Home</h1>", updated)
    deals = build.page("/deals/", "Deals title unique", "Deals description unique.", "<h1>Deals</h1>", updated)
    free_page = build.page("/free-games/", "Free title unique", "Free description unique.", "<h1>Free</h1>", updated)
    home_docs = jsonld_docs(home)
    home_types = {j.get("@type") for j in home_docs}
    deal_docs = jsonld_docs(deals)
    check(home_types == {"Organization", "WebSite", "WebPage"}, f"home JSON-LD {home_types}")
    home_web = next(j for j in home_docs if j.get("@type") == "WebPage")
    check(home_web.get("dateModified") == updated.isoformat() and home_web.get("url") == C.BASE_URL + "/", f"home dateModified {home_web}")
    deal_web = [j for j in deal_docs if j.get("@type") == "WebPage"]
    check(len(deal_web) == 1 and deal_web[0].get("dateModified") == updated.isoformat(), f"deals dateModified {deal_web}")
    sample = build.itemlist("Top game deals", [{"title": "Hades", "url": "https://example.com/hades", "price": "4.99"}])
    check("dateModified" not in sample, "itemlist stamps itself")
    stamped_page = build.page("/deals/", "Deals title unique", "Deals description unique.", "<h1>Deals</h1>", updated, jsonld=[sample])
    stamped = [j for j in jsonld_docs(stamped_page) if j.get("@type") == "ItemList"]
    check(stamped and stamped[0].get("dateModified") == updated.isoformat(), f"stamped list {stamped}")
    check("dateModified" not in sample, "page() mutated the ItemList dict")
    terms = build.page("/terms/", "Terms title unique", "Terms description unique.", "<h1>Terms</h1>", updated)
    check("WebPage" not in {j.get("@type") for j in jsonld_docs(terms)}, "terms gained a WebPage stamp")
    check('rel="alternate" type="application/rss+xml"' in home and build.href("/feed.xml") in home, "home news feed link")
    check(build.href("/free-games/feed.xml") not in home, "home advertises the free-games feed")
    check('name="impact-site-verification" value="16c69e26-fc4d-424f-a478-069c40c6c520"' in home, "impact verification tag")
    crumbs = [j for j in deal_docs if j.get("@type") == "BreadcrumbList"]
    check({"Organization", "WebSite"} <= {j.get("@type") for j in deal_docs} and len(crumbs) == 1, "deals JSON-LD lost or duplicated")
    elements = crumbs[0]["itemListElement"] if crumbs else []
    check(len(elements) == 2 and elements[0]["name"] == "Home" and elements[1]["name"] == "Deals", f"deals breadcrumb {elements}")
    check(elements and elements[1]["item"] == C.BASE_URL + "/deals/", f"deals breadcrumb url {elements}")
    check(build.href("/feed.xml") in deals and build.href("/free-games/feed.xml") not in deals, "deals feed links")
    free_crumbs = [j for j in jsonld_docs(free_page) if j.get("@type") == "BreadcrumbList"]
    check(len(free_crumbs) == 1 and free_crumbs[0]["itemListElement"][1]["name"] == "Free games", "free-games breadcrumb")
    check(build.href("/feed.xml") in free_page and build.href("/free-games/feed.xml") in free_page, "free-games feed links")

def jsonld_docs(src):
    return [json.loads(raw) for raw in re.findall(r'<script type="application/ld\+json">(.*?)</script>', src)]

def test_gamerpower_expiry():
    import datetime as dt
    # 2026-10-25 06:23 CT, when the live feed still listed these two.
    built = dt.datetime(2026, 10, 25, 11, 23, tzinfo=dt.timezone.utc)
    check(build.gamerpower_ended("2026-10-24T23:59:59", built), "The Black Within still open after its UTC end")
    check(build.gamerpower_ended("2026-10-24T23:59:00", built), "Castlevania still open after its UTC end")
    # 02:00 UTC is still before 23:59 US Eastern, and the UTC reading drops it anyway.
    between = dt.datetime(2026, 10, 25, 2, 0, tzinfo=dt.timezone.utc)
    check(build.gamerpower_ended("2026-10-24T23:59:59", between), "UTC end kept until US Eastern")
    exact = dt.datetime(2026, 10, 24, 23, 59, 59, tzinfo=dt.timezone.utc)
    check(not build.gamerpower_ended("2026-10-24T23:59:59", exact), "dropped at the end instant")
    check(build.gamerpower_ended("2026-10-24T23:59:59", exact + dt.timedelta(seconds=1)), "kept one second after the end")
    check(not build.gamerpower_ended("", built) and not build.gamerpower_ended("N/A", built), "blank or N/A end treated as expired")
    check(not build.gamerpower_ended("soon", built), "unparsed end treated as expired")
    check(not build.gamerpower_ended("2026-11-01T23:59:00", built), "future end treated as expired")

    rows = [
        {"title": "The Black Within (itch.io) Giveaway", "platforms": "PC, Itch.io", "worth": "$4.99",
         "open_giveaway_url": "https://example.com/black", "end_date": "2026-10-24 23:59:59",
         "thumbnail": "https://example.com/black.jpg"},
        {"title": "Castlevania: 40th Anniversary (Mobile) Giveaway", "platforms": "Android, iOS", "worth": "N/A",
         "open_giveaway_url": "https://example.com/castlevania", "end_date": "2026-10-24 23:59:00",
         "thumbnail": ""},
        {"title": "Last Room (IndieGala) Giveaway", "platforms": "PC, DRM-Free", "worth": "N/A",
         "gamerpower_url": "https://example.com/last", "end_date": "N/A", "image": "https://example.com/last.jpg"},
        {"title": "MAFIA HOTEL (Epic Games) Giveaway", "platforms": "PC, Epic Games Store", "worth": "$19.99",
         "open_giveaway_url": "https://example.com/mafia", "end_date": "2026-11-01 23:59:00",
         "thumbnail": "https://example.com/mafia.jpg"},
        {"title": "Mystery End Giveaway", "platforms": "PC", "worth": "$1.00",
         "open_giveaway_url": "https://example.com/mystery", "end_date": "whenever", "thumbnail": ""},
    ]

    class Response:
        def __init__(self, payload):
            self.payload = payload
        def json(self):
            return self.payload

    old_get = build.get
    try:
        build.get = lambda url, timeout=20, attempts=1, backoff=2: Response(rows)
        build.FAILURES.clear()
        kept = build.fetch_gamerpower(now=built)
        titles = [g["title"] for g in kept]
        check(titles == ["Last Room (IndieGala)", "MAFIA HOTEL (Epic Games)", "Mystery End"], f"filtered titles {titles}")
        by = {g["title"]: g for g in kept}
        hotel = by["MAFIA HOTEL (Epic Games)"]
        check(hotel["ends"] == "2026-11-01T23:59:00" and "+" not in hotel["ends"] and not hotel["ends"].endswith("Z"), f"ends rewritten {hotel['ends']}")
        check(set(hotel) == {"title", "platform", "source", "url", "worth", "ends", "image"}, f"shape changed {sorted(hotel)}")
        check(hotel["source"] == "GamerPower" and hotel["platform"] == "PC, Epic Games Store" and hotel["worth"] == "$19.99", f"open giveaway fields changed {hotel}")
        check(hotel["url"] == "https://example.com/mafia", f"url changed {hotel['url']}")
        room = by["Last Room (IndieGala)"]
        check(room["ends"] == "" and room["url"] == "https://example.com/last" and room["worth"] == "N/A", f"undated giveaway changed {room}")
        check(by["Mystery End"]["ends"] == "whenever", f"unparsed end rewritten {by['Mystery End']['ends']}")
        published = build.free_list(kept) + json.dumps(build.free_rss_items(kept)) + json.dumps(build.itemlist("Free games right now", kept))
        check("The Black Within" not in published and "Castlevania" not in published, "expired giveaway reached the page, feed, or structured data")
        check("MAFIA HOTEL" in published and "Last Room" in published and "Mystery End" in published, "open giveaway missing from published listings")
        wider = [{"title": f"Game {i} Giveaway", "platforms": "PC", "worth": "$1",
                  "open_giveaway_url": f"https://example.com/{i}",
                  "end_date": "2026-10-24 23:59:59" if i == 0 else "2026-11-01 23:59:00",
                  "thumbnail": ""} for i in range(31)]
        build.get = lambda url, timeout=20, attempts=1, backoff=2: Response(wider)
        capped = [g["title"] for g in build.fetch_gamerpower(now=built)]
        check(capped == [f"Game {i}" for i in range(1, 30)] and "Game 30" not in capped, f"window changed {capped}")
        build.get = lambda url, timeout=20, attempts=1, backoff=2: Response({"status": 0})
        build.FAILURES.clear()
        check(build.fetch_gamerpower(now=built) == [], "bad payload returned rows")
        check(any(item.startswith("gamerpower parse:") for item in build.FAILURES), f"bad payload not recorded {build.FAILURES}")
    finally:
        build.get = old_get
        build.FAILURES.clear()
    check(build.fetch_gamerpower(offline=True) == [], "offline gamerpower changed")

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
    test_gamerpower_expiry()
    test_cards()
    test_voice_and_itemlist()
    test_affiliates()
    test_subscribe()
    test_cheapshark_retry()
    test_epic_upcoming_and_faq()
    test_epic_landing()
    test_static_config()
    test_rss_and_breadcrumbs()
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
