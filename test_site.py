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

def validate(out, external=False):
    pages = {}
    for root,_,fs in os.walk(out):
        for f in fs:
            if f.endswith(".html"):
                path = "/" + os.path.relpath(root, out).replace(".", "").strip("/"); path = path.rstrip("/") + "/"
                pages[path] = open(os.path.join(root,f), encoding="utf-8").read()
    for req in ["/", "/free-games/", "/deals/", "/news/", "/about/", "/newsletter/"]: check(req in pages, f"missing page {req}")
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
        check(any(m.get("name")=="twitter:card" for m in p.tags.get("meta",[])), f"{path}: missing twitter:card")
        check(re.search(r"Updated <time[^>]*>[^<]+ CT</time>", src), f"{path}: missing Updated CT")
        for t in ["header","nav","main","footer","h1"]: check(t in p.tags, f"{path}: missing <{t}>")
        check(len(p.tags.get("h1",[]))==1, f"{path}: needs exactly one h1")
        check("<img" not in src, f"{path}: contains images (not allowed for news)")
        types=set()
        for raw in p.ld:
            try: j=json.loads(raw); check("@context" in j and "@type" in j, f"{path}: JSON-LD missing @context/@type"); types.add(j["@type"])
            except Exception as e: ERR.append(f"{path}: invalid JSON-LD {e}")
        check({"Organization","WebSite"} <= types, f"{path}: missing Org/WebSite JSON-LD")
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
    check(locs == {C.BASE_URL+p for p in pages}, f"sitemap mismatch: {locs ^ {C.BASE_URL+p for p in pages}}")
    rb = open(os.path.join(out,"robots.txt")).read()
    check(f"Sitemap: {C.BASE_URL}/sitemap.xml" in rb, "robots.txt missing sitemap")
    check(f"Allow: {project_prefix() + '/' if project_prefix() else '/'}" in rb, "robots.txt allow path missing project subpath")
    for f,k in [("deals.json","deals"),("free-games.json","games")]:
        j=json.load(open(os.path.join(out,"data",f))); check(isinstance(j.get(k),list) and j["count"]==len(j[k]), f"{f} malformed")
    llms = open(os.path.join(out,"llms.txt")).read()
    check(llms.startswith("# "), "llms.txt malformed")
    for frag in ("/free-games/", "/deals/", "/news/", "/about/", "/data/free-games.json", "/data/deals.json"):
        check(C.BASE_URL + frag in llms, f"llms.txt missing {C.BASE_URL}{frag}")
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


if __name__ == "__main__":
    n, e = validate(C.OUT_DIR, external="--external" in sys.argv)
    print(f"live build: {n} pages, {e} external links {'checked' if '--external' in sys.argv else 'found'}")
    # empty-feed fallback: build to temp dir with all feeds disabled
    live_out = C.OUT_DIR; C.OUT_DIR = "/tmp/staz_empty"; shutil.rmtree(C.OUT_DIR, ignore_errors=True)
    r = build.build(offline=True); validate(C.OUT_DIR)
    home = open(C.OUT_DIR+"/index.html").read()
    check(home.count('class="empty"')==3, "empty-feed fallbacks not shown on home")
    check(json.load(open(C.OUT_DIR+"/data/deals.json"))["count"]==0, "empty deals.json wrong")
    print("empty-feed build:", r)
    C.OUT_DIR = live_out
    print("FAIL\n- " + "\n- ".join(ERR) if ERR else "ALL TESTS PASSED"); sys.exit(1 if ERR else 0)
