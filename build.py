"""StazGames static site generator. Usage: python build.py [--offline]"""
import json, os, re, sys, html, time, datetime as dt
from email.utils import format_datetime
from zoneinfo import ZoneInfo
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit
from xml.sax.saxutils import escape as xml_escape
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
AFFILIATE_NOTICE = "Some deal links may be affiliate links that pay StazGames a commission at no extra cost to you."
AMAZON_DISCLOSURE = "As an Amazon Associate I earn from qualifying purchases."
OUTBOUND_REL = "sponsored nofollow noopener"

def get(url, timeout=20, attempts=1, backoff=2):
    """Fetch a URL. Extra attempts are for callers that opt in; one failure is recorded."""
    error = None
    for attempt in range(attempts):
        try:
            r = requests.get(url, headers=UA, timeout=timeout); r.raise_for_status(); return r
        except Exception as e:
            error = e
            if attempt + 1 < attempts:
                time.sleep(backoff)
    FAILURES.append(f"{url}: {error}"); return None

def cheapshark_get(url):
    """Deal and store calls. A single slow CheapShark response should not wipe the list."""
    return get(url, timeout=45, attempts=3, backoff=2)

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

PREP = r"(?:in|on|for|with|about|after|before|from|at|during|within|into|over|under|around|through|via)"

def _norm_text(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()

def same(a, b):
    return bool(_norm_text(a)) and _norm_text(a) == _norm_text(b)

def cap(text):
    text = (text or "").strip()
    return text[:1].upper() + text[1:] if text else text

def lower_func(text):
    return re.sub(r"^(The|A|An|This|That|Its|It's)\b", lambda m: m.group(1).lower(), (text or "").strip())

def finish(text, ellipsis=False):
    text = re.sub(r"\s+", " ", text or "").strip(" ,;:-–—")
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",,+", ",", text)
    if not text:
        return ""
    text = cap(text)
    if ellipsis:
        return text.rstrip(".!?… ") + "…"
    if text[-1] not in ".!?":
        text += "."
    return text

def mask_quotes(text):
    quotes = []
    def hold(m):
        quotes.append(m.group(0))
        return f"\x00{len(quotes)-1}\x00"
    masked = re.sub(r"[“\"][^”\"]*[”\"]|[‘'][^’']*[’']", hold, text)
    def restore(part):
        return re.sub(r"\x00(\d+)\x00", lambda m: quotes[int(m.group(1))], part)
    return masked, restore

PREP_WORDS = {"in", "on", "for", "with", "about", "after", "before", "from", "at", "during", "within", "into", "over", "under", "around", "through", "via", "including", "of"}

def front_prep(clause):
    """Move a short, comma-free ending phrase ("for $12", "after October") to the front."""
    masked, restore = mask_quotes(clause)
    matches = list(re.finditer(rf"\s+({PREP})\b", masked, re.I))
    for m in reversed(matches):
        main = restore(masked[:m.start()]).strip(" ,;")
        prep = restore(masked[m.start():]).strip(" ,;")
        if "," in prep or not main:
            continue
        if main.split()[-1].lower().strip(".”\"'") in PREP_WORDS:
            continue
        if len(main.split()) >= 4 and 2 <= len(prep.split()) <= 8:
            return f"{cap(prep)}, {lower_func(main)}"
    return None

def rewrite_one(core):
    """Return 1 grammatical sentence-or-two (no source tag), or None."""
    s = core.strip()
    s = re.sub(r"\s+", " ", s)

    m = re.fullmatch(r"[\"“](.+)[\"”]", s)
    if m:
        return f'The story quotes this line: "{m.group(1).strip()}"'

    m = re.match(r"^(?P<who>.+?) has finally spoken up about (?P<what>.+)$", s)
    if m:
        return f"The remarks from {m.group('who')} are about {m.group('what')}"

    m = re.match(r"^(?P<who>.+?) has shown off (?P<what>.+?) for the first time$", s)
    if m:
        return f"A first look at {m.group('what')} comes from {m.group('who')}"

    m = re.match(r"^(?P<who>.+?) have returned to (?P<where>.+?) after (?P<why>.+)$", s)
    if m:
        return f"After {m.group('why')}, {m.group('who')} are back at {m.group('where')}"

    m = re.match(r"^(?P<who>.+?) has revealed (?P<what>.+?), which is (?P<which>.+)$", s)
    if m:
        what = re.sub(r"^(his|her|their) approach for\s+", "", m.group("what"), flags=re.I)
        return f"{m.group('who')} revealed an approach for {what}, described as {m.group('which')}"

    m = re.match(r"^(?P<who>.+?) has revealed (?P<what>.+)$", s)
    if m:
        return f"What {m.group('who')} revealed is {m.group('what')}"

    m = re.match(r"^(?P<who>.+?) has described (?P<what>.+?), which is currently known by its codename (?P<code>[^,]+), as (?P<how>.+)$", s)
    if m:
        how = m.group("how")
        extra = ""
        cut = re.split(r",\s*revealing\s+", how, maxsplit=1)
        if len(cut) == 2:
            how, extra = cut
        line = f'{m.group("who")} calls {m.group("what")}, currently known by the codename {m.group("code")}, {how}'
        if extra:
            line += f". The note also says {extra}"
        return line

    m = re.match(r"^(?P<who>.+?) has described (?P<what>.+?) as (?P<how>.+)$", s)
    if m:
        return f"{m.group('who')} calls {m.group('what')} {m.group('how')}"

    m = re.match(r"^(?P<what>.+?) is now available for (?P<where>.+)$", s)
    if m:
        where = m.group("where")
        verb = "are" if " and " in where else "is"
        return f"{where} {verb} where {lower_func(m.group('what'))} is out now"

    m = re.match(r"^(?P<who>.+?) says (?P<what>.+)$", s)
    if m:
        what = m.group("what")
        # "he has a "healthy separation" where Overwatch is no longer part of his life"
        m2 = re.match(r"^(?:he|she|they) has (?P<a>.+?) where (?P<b>.+)$", what, re.I)
        if m2:
            return f'{m2.group("a")} is {m.group("who")}\'s account, and {m2.group("b")}'
        return f"{what} is the account from {m.group('who')}"

    m = re.match(r"^(?P<who>.+?) suggests (?P<what>.+)$", s)
    if m:
        return f"{cap(m.group('what'))}, which is the suggestion from {m.group('who')}"

    m = re.match(r"^(?P<what>.+?) was allegedly cancell?ed by (?P<who>.+)$", s, re.I)
    if m:
        return f"{m.group('who')}, allegedly, cancelled {lower_func(m.group('what'))}"

    m = re.match(r"^(?P<what>.+?), (?P<appos>.+), has been cancell?ed (?P<rest>.+)$", s, re.I)
    if m:
        return f"{m.group('what')} is cancelled {m.group('rest')}. The note calls it {m.group('appos')}"

    m = re.match(r"^(?P<what>.+?) will launch (?P<rest>.+)$", s)
    if m:
        return f"A launch {m.group('rest')} is the plan for {lower_func(m.group('what'))}"

    m = re.match(r"^(?P<what>.+?) will release (?P<rest>.+)$", s)
    if m:
        rest = m.group("rest")
        if not re.match(r"(?i)^(the|a|an)\b", rest):
            rest = "the " + lower_func(rest)
        pause = "," if len(rest.split()) > 8 else ""
        return f"{cap(rest)}{pause} is the release {m.group('what')} has lined up"

    m = re.match(r"^(?P<what>.+?) will be (?P<rest>.+)$", s)
    if m:
        return f"Being {m.group('rest')} is the prospect for {lower_func(m.group('what'))}"

    m = re.match(r"^(?P<who>.+?) can now (?P<a>.+?) and (?P<b>.+)$", s)
    if m and len(m.group("a").split()) <= 10:
        return f"{m.group('who')} can {m.group('a')} now. They can also {m.group('b')}"

    m = re.match(r"^(?P<who>.+?) (?:has|have) released (?P<what>.+)$", s)
    if m:
        who = re.sub(r"^Publisher\s+", "publisher ", m.group("who"))
        what = m.group("what")
        verb = "are" if re.search(r"\band\b", what.split(",")[0]) else "is"
        pause = "," if "," in what else ""
        return f"{cap(what)}{pause} {verb} out from {who}"

    m = re.match(r"^(?P<who>.+?) recently sat down with (?P<whom>.+)$", s)
    if m:
        return f"{m.group('whom')} recently sat down with {m.group('who')}"

    m = re.match(r"^Recently, I visited (?P<rest>.+)$", s)
    if m:
        return f"The writer visited {m.group('rest')}"

    m = re.match(r"^In my office, I have (?P<what>.+)$", s, re.I)
    if m:
        what = re.sub(r"\b[Mm]y\b", "their", m.group("what"))
        return f"The writer describes {what} in their office"

    m = re.match(r"^(?P<who>.+?) has discussed (?P<what>.+), calling it (?P<quote>.+)$", s)
    if m:
        return f"{m.group('who')} discussed {m.group('what')} and called it {m.group('quote').rstrip('.')}"

    m = re.match(r"^(?P<what>.+?) may not be (?P<rest>.+)$", s)
    if m:
        return f"{cap(m.group('rest'))} is what {lower_func(m.group('what'))} may not be"

    m = re.match(r"^(?P<what>.+?) adds (?P<rest>.+)$", s)
    if m and len(m.group("what").split()) >= 2:
        return f"{cap(m.group('rest'))} are what {lower_func(m.group('what'))} adds"

    m = re.match(r"^It's in beta now, but will likely release widely in (?P<when>.+)$", s)
    if m:
        return f"A wide release in {m.group('when')} is the likely plan, and it is in beta now"

    m = re.match(r"^In the wake of (?P<scene>[^,]+),\s*(?P<rest>.+)$", s)
    if m:
        return f"{m.group('rest').rstrip('.')}, in the wake of {m.group('scene')}"

    m = re.match(r"^(?P<body>.+), it's claimed$", s, re.I)
    if m:
        return f"The claim is that {lower_func(m.group('body'))}"

    m = re.match(r"^(?P<who>.+?) will introduce (?P<what>.+)$", s)
    if m:
        what = m.group("what")
        pause = "," if "," in what else ""
        return f"{cap(what)}{pause} is what {m.group('who')} will introduce"

    m = re.match(r"^(?P<a>.+?) continues as we (?P<b>.+)$", s)
    if m:
        verb, _, rest = m.group("b").partition(" ")
        if verb.endswith("y") and len(verb) > 1 and verb[-2].lower() not in "aeiou":
            verb = verb[:-1] + "ies"
        elif verb.endswith(("s", "sh", "ch", "x", "z", "o")):
            verb = verb + "es"
        else:
            verb = verb + "s"
        return f"{m.group('a')} continues. The piece {verb}{(' ' + rest) if rest else ''}"

    m = re.match(r"^(?P<who>.+?) features (?P<what>.+)$", s)
    if m and len(m.group("who").split()) >= 2:
        return f"What {m.group('who')} features is {m.group('what')}"

    m = re.match(r"^(?P<head>.+?),\s+but\s+(?P<tail>.+)$", s)
    if m and len(m.group("head").split()) >= 4 and len(m.group("tail").split()) >= 4:
        return f"{cap(m.group('tail'))}, but the earlier point is that {lower_func(m.group('head'))}"

    m = re.match(r"^(?P<who>.+?) has explained (?P<what>.+)$", s)
    if m and len(m.group("who").split()) <= 14:
        return f"What {m.group('who')} explained is {m.group('what')}"

    m = re.match(r"^(?P<what>.+?) have entered (?P<rest>.+)$", s)
    if m:
        return f"{m.group('what')} are in {m.group('rest')}"

    m = re.match(r"^(?P<who>.+?) joins the show!?$", s)
    if m:
        return f"{m.group('who')} is joining the show"

    m = re.match(r"^(?P<what>.+?) is well underway, with the latest seeing (?P<rest>.+)$", s)
    if m:
        return f"The latest sign is {m.group('rest')}, from {m.group('what')}, which the note calls well underway"

    m = re.match(r"^(?P<what>.+?) is well underway, with (?P<rest>.+)$", s)
    if m:
        return f"{cap(m.group('rest'))} is the latest piece of {m.group('what')}, which the note calls well underway"

    m = re.match(r"^(?P<who>.+?) said it (?:doesn't|doesn’t|does not) want to (?P<avoid>.+?), instead (?P<alt>.+)$", s)
    if m:
        avoid = re.sub(r"^do\s+", "", m.group("avoid"))
        return f"Doing {avoid} is not what {lower_func(m.group('who'))} said it wants, and the alternative it names is {m.group('alt')}"

    m = re.match(r"^(?P<head>.+?), and though (?P<though>.+?) were (?P<adj>well [\w]+), (?P<end>.+)$", s)
    if m:
        return f"{m.group('head')}, and {m.group('though')} were {m.group('adj')}. {cap(m.group('end'))}"

    # Copula swap. Keep both sides intact so names and figures stay put.
    m = re.match(r"^(?P<left>.+?)\s+is\s+(?P<right>.+)$", s)
    if m:
        left, right = m.group("left").strip(), m.group("right").strip()
        if len(left.split()) >= 2 and 1 <= len(right.split()) <= 14:
            if right.lower().startswith("not "):
                return f"{cap(right)} is the description of {lower_func(left)}"
            return f"{cap(right)} is {lower_func(left)}"

    m = re.match(r"^(?P<left>.+?)\s+was\s+(?P<right>.+)$", s)
    if m and len(m.group("right").split()) <= 14:
        return f"{cap(m.group('right'))} is what the note records for {m.group('left')}"

    flipped = front_prep(s)
    if flipped and not same(flipped, s):
        return flipped

    m = re.match(r"^(?P<head>.+?),\s+with (?P<tail>.+)$", s)
    if m and len(m.group("tail").split()) >= 3:
        return f"{cap(m.group('tail'))} comes along with this: {lower_func(m.group('head'))}"

    return None

def place_ellipsis(bits, core):
    """Keep a feed ellipsis on the clause that still holds the unfinished ending."""
    tail = " ".join(core.split()[-3:]).lower()
    placed = False
    out = []
    for bit in bits:
        done = finish(bit)
        if not placed and tail and tail in done.lower():
            done = done.rstrip(".!?") + "…"
            placed = True
        out.append(done)
    if not placed and out:
        out[-1] = out[-1].rstrip(".!?") + "…"
    return out

def clean_core(raw):
    core = re.sub(r"(?:…|\.\.\.)\s*$", "", raw).strip()
    core = re.sub(r"[.!?]+$", "", core).strip()
    core = re.sub(r'([.!?])\.(["”’\'])', r"\1\2", core)
    core = re.sub(r'(["”])\.$', r"\1", core)
    # A clipped teaser sometimes ends on a gerund whose object was cut off.
    bits = core.split()
    if bits and bits[-1].lower().strip(".”\"'") in {"introducing", "including", "calling", "featuring", "showing", "adding", "bringing"}:
        core = " ".join(bits[:-1]).rstrip(" ,;")
        return core, True
    return core, False

def rewrite_sentence(sentence, source):
    raw = sentence.strip()
    ellipsis = bool(re.search(r"(?:…|\.\.\.)\s*$", raw))
    core, clipped = clean_core(raw)
    ellipsis = ellipsis or clipped
    rewritten = rewrite_one(core)
    if not rewritten or same(rewritten, core):
        # Keep an unmatched teaser grammatical. A midpoint split was producing fragments.
        rewritten = f"{(source + ' notes that ') if source else ''}{lower_func(core)}"
    bits = [b.strip() for b in re.split(r"(?<=[.!?])\s+", rewritten) if b.strip()][:2]
    if ellipsis:
        return " ".join(place_ellipsis(bits, core))
    return " ".join(finish(bit) for bit in bits)

VERB_MAP = {
    "explained": "explains",
    "announced": "has the announcement on",
    "revealed": "reveals",
    "confirmed": "confirms",
    "detailed": "goes through",
    "delayed": "covers a delay for",
    "cancelled": "covers a cancellation of",
    "canceled": "covers a cancellation of",
    "released": "covers a release of",
}

def pull_trailing_quote(title):
    """Return (bare headline, quote text or ''). Handles apostrophes inside single quotes."""
    m = re.search(r"""[:]\s*(["“‘'])(.+)\1\s*$""", title)
    if not m:
        m = re.search(r"""[:]\s*(["“‘'])(.+)["”’']\s*$""", title)
    if not m:
        return title, ""
    return title[:m.start()].strip(" :-–—"), m.group(2).strip()

def voice_from_title(title, source):
    t = strip(title).strip().rstrip(".")
    bare, quote = pull_trailing_quote(t)
    bare = re.sub(r"\s+", " ", bare).strip(" ,")
    sentence = ""
    m = re.match(r"^(?P<body>.+?)\s+(?P<verb>explained|announced|revealed|confirmed|detailed|delayed|cancelled|canceled|released)$", bare, re.I)
    if m and len(m.group("body").split()) >= 2:
        sentence = finish(f"{source} {VERB_MAP[m.group('verb').lower()]} {lower_func(m.group('body'))}")
    else:
        m = re.match(r"(?i)^here(?:'s| is)\s+(?:our\s+|your\s+|a\s+|the\s+)?(?P<rest>.+)$", bare)
        if m:
            sentence = finish(f"{source} shares {lower_func(m.group('rest'))}")
        else:
            m = re.match(r"^(?P<who>.+?)\s+explains why\s+(?P<why>.+)$", bare, re.I)
            if m:
                why = m.group("why")
                extra = ""
                cut = re.split(r",\s+and then\s+", why, maxsplit=1)
                if len(cut) == 2:
                    why, extra = cut
                sentence = finish(f"Why {lower_func(why)} is what {m.group('who')} explains")
                if extra and not quote:
                    sentence += " " + finish(f"The headline also says {m.group('who').split()[-1]} then {extra}" if extra.lower().startswith("drops") else f"The headline also says {extra}")
            else:
                parts = re.split(r"\s*[:|–—]\s+|\s+-\s+", bare, maxsplit=1)
                if len(parts) == 2 and len(parts[0].split()) >= 1 and len(parts[1].split()) >= 3:
                    left, right = (p.strip() for p in parts)
                    sentence = finish(f"{cap(lower_func(right))}") + " " + finish(f"That is what {source} is covering under {lower_func(left)}")
                else:
                    m = re.match(r"^(?P<body>.+),\s*(?P<who>[A-Z][\w.'’&-]*(?:\s+[\w.'’&-]+){0,4})\s+(?P<verb>confirms|says|announces|reveals|explains|reports)$", bare)
                    if m and len(m.group("body").split()) >= 3:
                        sentence = finish(f"{m.group('who')} {m.group('verb')} {lower_func(m.group('body'))}")
                    else:
                        m = re.match(r"(?i)^save\s+(?P<pct>\d+)%\s+off\s+(?P<what>.+?)(?:\s+well)?\s+ahead of\s+(?P<when>.+)$", bare)
                        if m:
                            sentence = finish(f"{source} notes {m.group('pct')}% off {m.group('what')}, ahead of {m.group('when')}")
                        else:
                            flipped = front_prep(bare)
                            if flipped and not same(flipped, bare):
                                sentence = finish(flipped)
                            else:
                                sentence = finish(f"{source} reports on {lower_func(bare)}")
    if quote:
        sentence += " " + finish(f'The headline quotes "{quote}"')
    return sentence

def title_pasted(blurb, title):
    nt, nb = _norm_text(title), _norm_text(blurb)
    if len(nt.split()) >= 6 and nt in nb:
        return True
    return same(blurb, title)

def leaks_numbers(text, *parts):
    allowed = set(re.findall(r"\d+(?:\.\d+)?", " ".join(parts)))
    found = set(re.findall(r"\d+(?:\.\d+)?", text or ""))
    return not found <= allowed

def attach_source(text, source):
    if not text or not source or re.search(rf"\b{re.escape(source)}\b", text, re.I):
        return text
    if text.startswith("The story quotes this line:"):
        return f"Via {source}, the story quotes this line:{text.split(':', 1)[1]}"
    parts = [p for p in re.split(r"(?<=[.!?…])\s+", text) if p.strip()]
    if parts and parts[-1].endswith("…") and len(parts) >= 2 and parts[0][-1:] in ".!?":
        parts[0] = parts[0][:-1] + f", via {source}{parts[0][-1]}"
        return " ".join(parts)
    if text.endswith("…"):
        return text + f" Via {source}."
    if text[-1] in ".!?":
        return text[:-1] + f", via {source}{text[-1]}"
    return text + f", via {source}."

def strip_title_echo(title, summary):
    """Drop a headline repeated before a colon, keeping the words after that separator.

    "Title will launch…" stays intact so the headline can remain the subject of the sentence.
    """
    sw, tw = summary.split(), title.split()
    if len(tw) >= 4 and len(sw) > len(tw) + 3 and _norm_text(" ".join(sw[:len(tw)])) == _norm_text(title):
        rest = " ".join(sw[len(tw):]).lstrip()
        if rest[:1] in ":|-–—" or sw[len(tw) - 1][-1:] in ":|-–—":
            rest = rest.lstrip(":|-–— ")
            if len(rest.split()) >= 4:
                return rest
    return summary

def voice_blurb(title, summary, source=""):
    """One or two original sentences from a cleaned teaser, or from the headline when the teaser is empty.

    Facts stay inside the headline and the teaser. Glue words and the source name are the only additions.
    """
    title, summary, source = strip(title), strip(summary), strip(source)
    if summary and same(summary, title):
        summary = ""
    if summary:
        summary = strip_title_echo(title, summary)
    if summary:
        parts = [p for p in re.split(r"(?<=[.!?…])\s+", summary) if p.strip()][:2]
        rewritten = [rewrite_sentence(p, source) for p in parts]
        # Keep the blurb to two sentences.
        sentences = []
        for chunk in rewritten:
            sentences.extend([s for s in re.split(r"(?<=[.!?…])\s+", chunk) if s.strip()])
        text = " ".join(sentences[:2])
    elif len(title.split()) >= 4 and not same(title, source):
        text = voice_from_title(title, source)
    else:
        return ""
    text = re.sub(r"\s+:\s*", ": ", text)
    text = attach_source(text, source)
    return text

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

def match_store(url, store=""):
    """Map a deal URL or store name onto an AFFILIATES key."""
    host = urlparse(str(url or "")).netloc.lower()
    name = re.sub(r"\s+", " ", str(store or "")).strip().lower()
    if "amazon." in host or host.endswith("amzn.to") or name == "amazon":
        return "amazon"
    if "fanatical.com" in host or name == "fanatical":
        return "fanatical"
    if "greenmangaming.com" in host or name in {"green man gaming", "gmg"}:
        return "gmg"
    if "humblebundle.com" in host or "humblestore.com" in host or "humble" in name:
        return "humble"
    return ""

def affiliate_params(key):
    raw = (getattr(C, "AFFILIATES", {}) or {}).get(key) or {}
    return {str(k): str(v) for k, v in raw.items() if str(k).strip() and str(v).strip()}

def with_affiliate(url, store=""):
    """Return (url, affiliate_active). Empty store config leaves the URL unchanged."""
    url = str(url or "")
    params = affiliate_params(match_store(url, store))
    if not params:
        return url, False
    parts = urlsplit(url)
    if "cheapshark.com" in parts.netloc.lower() and parts.path.rstrip("/").endswith("/redirect"):
        return url, True
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)), True

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

def norm_title(title):
    return re.sub(r"\s*\([^)]*\)\s*$", "", title or "").strip().lower()

def epic_slug(game):
    return (game.get("productSlug") or "") or next((m.get("pageSlug") for m in (game.get("catalogNs") or {}).get("mappings") or [] if m.get("pageSlug")), "") or (game.get("offerMappings") or [{}])[0].get("pageSlug", "")

def epic_full_discount(offer):
    """True when Epic is giving the game away.

    The promotions API stores a 100% discount as discountPercentage 0.
    Any other percentage is a paid sale and is not free to keep.
    """
    if not isinstance(offer, dict):
        return False
    return (offer.get("discountSetting") or {}).get("discountPercentage") == 0

def epic_span(offer):
    start = dt.datetime.fromisoformat(offer["startDate"].replace("Z", "+00:00"))
    end = dt.datetime.fromisoformat(offer["endDate"].replace("Z", "+00:00"))
    return start, end

def epic_game(game, end, start=None):
    slug = epic_slug(game)
    row = {"title": game["title"], "platform": "Epic Games Store", "source": "Epic Games",
        "url": f"https://store.epicgames.com/en-US/p/{slug}" if slug else "https://store.epicgames.com/en-US/free-games",
        "worth": (game.get("price") or {}).get("totalPrice", {}).get("fmtPrice", {}).get("originalPrice", ""),
        "ends": end.isoformat(), "image": epic_image(game)}
    if start is None:
        return row
    return {"title": row["title"], "platform": row["platform"], "source": row["source"], "url": row["url"],
        "worth": row["worth"], "starts": start.isoformat(), "ends": row["ends"], "image": row["image"]}

def parse_epic_catalog(elements, now=None):
    """Split an Epic free-games catalog into current and upcoming free-to-keep titles.

    Upcoming reads upcomingPromotionalOffers only, and only 100% discounts
    (discountPercentage 0) that have not started. A bad offer is recorded and
    skipped. It does not drop games already parsed and it does not raise.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    current = []
    if not isinstance(elements, list):
        FAILURES.append("epic parse: catalog elements missing")
        return [], []
    try:
        for g in elements:
            promos = (g.get("promotions") or {}).get("promotionalOffers") or []
            for p in promos:
                for o in p.get("promotionalOffers", []):
                    if not epic_full_discount(o):
                        continue
                    start, end = epic_span(o)
                    if not (start <= now <= end):
                        continue
                    current.append(epic_game(g, end))
    except Exception as e:
        FAILURES.append(f"epic parse: {e}")
    upcoming = []
    seen = {g.get("title") for g in current if g.get("title")}
    for g in elements:
        title = ""
        try:
            if not isinstance(g, dict):
                continue
            title = g.get("title") or ""
            if not title or title in seen:
                continue
            best = None
            groups = (g.get("promotions") or {}).get("upcomingPromotionalOffers") or []
            for group in groups:
                offers = group.get("promotionalOffers", []) if isinstance(group, dict) else []
                for offer in offers or []:
                    if not epic_full_discount(offer):
                        continue
                    start, end = epic_span(offer)
                    if not (now < start and start < end):
                        continue
                    if best is None or (start, end) < best:
                        best = (start, end)
            if best:
                upcoming.append(epic_game(g, best[1], best[0]))
                seen.add(title)
        except Exception as e:
            FAILURES.append(f"epic upcoming: {title or 'game'}: {e}")
    upcoming.sort(key=lambda row: (row.get("starts") or "", row.get("title") or ""))
    return current, upcoming

def fetch_epic(offline=False):
    """Return (current, upcoming) Epic free-to-keep games. Offline or a bad response is empty."""
    if offline:
        return [], []
    r = get("https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=en-US&country=US")
    if not r:
        return [], []
    try:
        elements = r.json()["data"]["Catalog"]["searchStore"]["elements"]
    except Exception as e:
        FAILURES.append(f"epic parse: {e}")
        return [], []
    return parse_epic_catalog(elements)

def omit_listed(upcoming, listed):
    """Drop upcoming titles that are already on the current free list."""
    have = {norm_title(g.get("title")) for g in listed}
    return [g for g in upcoming if norm_title(g.get("title")) not in have]

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
    r = cheapshark_get("https://www.cheapshark.com/api/1.0/deals?storeID=1,7,11,25&sortBy=Deal%20Rating&pageSize=40&onSale=1")
    stores = {}
    s = cheapshark_get("https://www.cheapshark.com/api/1.0/stores")
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
.ad{display:inline-block;margin-left:6px;border:1px solid var(--mut);color:var(--mut);border-radius:4px;padding:0 5px;font-size:.68rem;font-weight:800;letter-spacing:.03em;vertical-align:middle}
@media(max-width:699px){.thumb,.icon,.mark{width:96px;height:54px}.signup input[type=email],.signup button{flex:1 1 100%}.legal{gap:10px}}
footer{color:var(--mut);font-size:.85rem;padding:24px 16px;border-top:1px solid var(--card);margin-top:32px}
.legal{display:flex;flex-wrap:wrap;gap:14px;margin:0 0 8px}.legal a{color:var(--mut)}
.notice{margin:0 0 8px}.empty{color:var(--mut);font-style:italic}
.faq h3{font-size:1.05rem;margin:1.1em 0 .25em}.faq p{margin:.2em 0 0}"""

LOGO_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#18221d"/><ellipse cx="32" cy="38" rx="20" ry="16" fill="#a0703f"/><circle cx="25" cy="34" r="3" fill="#222"/><circle cx="39" cy="34" r="3" fill="#222"/><ellipse cx="32" cy="43" rx="7" ry="4" fill="#6b4423"/><path d="M12 32a20 20 0 0 1 40 0" stroke="#4ade80" stroke-width="5" fill="none"/><rect x="8" y="30" width="7" height="12" rx="3" fill="#4ade80"/><rect x="49" y="30" width="7" height="12" rx="3" fill="#4ade80"/></svg>'

NAV = [("/", "Home"), ("/free-games/", "Free games"), ("/deals/", "Deals"), ("/news/", "News"), ("/newsletter/", "Newsletter"), ("/about/", "About")]
# Pages rebuilt from live feeds. They get the visible Updated line, WebPage.dateModified, and ItemList.dateModified.
FRESHNESS_PATHS = ("/", "/free-games/", "/free-games/epic/", "/deals/", "/news/")
CRUMBS = {
    "/free-games/": "Free games",
    "/free-games/epic/": "Epic free games",
    "/deals/": "Deals",
    "/news/": "News",
    "/newsletter/": "Newsletter",
    "/about/": "About",
    "/disclosure/": "Disclosure",
    "/privacy/": "Privacy",
    "/contact/": "Contact",
    "/terms/": "Terms",
    "/404.html": "Page not found",
}

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

def breadcrumb_ld(path):
    """Home, then this page. The Epic page also lists Free games. The homepage itself has no breadcrumb."""
    items = [
        {"@type": "ListItem", "position": 1, "name": "Home", "item": C.BASE_URL + "/"},
    ]
    if path == "/free-games/epic/":
        items.append({"@type": "ListItem", "position": 2, "name": CRUMBS["/free-games/"], "item": C.BASE_URL + "/free-games/"})
        items.append({"@type": "ListItem", "position": 3, "name": CRUMBS[path], "item": C.BASE_URL + path})
    else:
        items.append({"@type": "ListItem", "position": 2, "name": CRUMBS.get(path) or "Page", "item": C.BASE_URL + path})
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items}

def feed_links(path):
    """News feed on every page. The free-games feed is discovered from that page."""
    tags = [("/feed.xml", f"{C.SITE_NAME} news")]
    if path == "/free-games/":
        tags.append(("/free-games/feed.xml", f"{C.SITE_NAME} free games"))
    return "".join(
        f'<link rel="alternate" type="application/rss+xml" href="{href(feed_path)}" title="{esc(label)}">'
        for feed_path, label in tags
    )

def webpage_ld(path, title, desc, iso):
    """WebPage stamp for a feed-backed page. iso is the build time with a numeric offset."""
    return {"@context": "https://schema.org", "@type": "WebPage", "name": title, "description": desc,
            "url": C.BASE_URL + path, "dateModified": iso, "inLanguage": "en",
            "isPartOf": {"@type": "WebSite", "name": C.SITE_NAME, "url": C.BASE_URL + "/"}}

def page(path, title, desc, body, updated, jsonld=()):
    url = C.BASE_URL + path
    org = {"@context": "https://schema.org", "@type": "Organization", "name": C.SITE_NAME, "url": C.BASE_URL + "/",
           "logo": C.BASE_URL + "/logo.svg", "sameAs": [f"https://x.com/{C.X_HANDLE}"],
           "founder": {"@type": "Person", "name": C.OPERATOR, "sameAs": f"https://x.com/{C.OPERATOR_X}"}}
    site = {"@context": "https://schema.org", "@type": "WebSite", "name": C.SITE_NAME, "url": C.BASE_URL + "/", "description": C.TAGLINE}
    iso = updated.isoformat()
    stamped = []
    for block in jsonld:
        if path in FRESHNESS_PATHS and isinstance(block, dict) and block.get("@type") == "ItemList":
            block = {**block, "dateModified": iso}
        stamped.append(block)
    blocks = [org, site, *stamped]
    if path in FRESHNESS_PATHS:
        blocks.append(webpage_ld(path, title, desc, iso))
    if path != "/":
        blocks.append(breadcrumb_ld(path))
    lds = "".join(f'<script type="application/ld+json">{json.dumps(j, ensure_ascii=False).replace("</", "<\\/")}</script>' for j in blocks)
    nav = "".join(f'<a href="{href(h)}"{" aria-current=page" if h == path else ""}>{n}</a>' for h, n in NAV)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="impact-site-verification" value="16c69e26-fc4d-424f-a478-069c40c6c520">
<title>{esc(title)}</title><meta name="description" content="{esc(desc)}"><link rel="canonical" href="{url}">
<meta property="og:type" content="website"><meta property="og:site_name" content="{C.SITE_NAME}"><meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}"><meta property="og:url" content="{url}"><meta property="og:image" content="{C.BASE_URL}/og.png">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:site" content="@{C.X_HANDLE}"><meta name="twitter:title" content="{esc(title)}"><meta name="twitter:description" content="{esc(desc)}"><meta name="twitter:image" content="{C.BASE_URL}/og.png">
<link rel="icon" href="{href("/logo.svg")}" type="image/svg+xml"><link rel="alternate" type="text/plain" href="{href("/llms.txt")}" title="LLM summary">{feed_links(path)}
<link rel="stylesheet" href="{href("/style.css")}">{lds}</head><body>
<header><a class="logo" href="{href("/")}">Staz<span>Games</span></a><nav aria-label="Main">{nav}</nav></header>
<main><p class="upd">Updated <time datetime="{iso}">{updated.strftime("%b %d, %Y %I:%M %p")} CT</time></p>{body}</main>
{footer(path)}
</body></html>"""

def footer(path):
    links = [("/disclosure/", "Disclosure"), ("/privacy/", "Privacy"), ("/contact/", "Contact"), ("/terms/", "Terms")]
    nav = "".join(
        f'<a href="{href(h)}"{" aria-current=page" if h == path else ""}>{name}</a>'
        for h, name in links
    )
    return f"""<footer>
<nav class="legal" aria-label="Legal">{nav}</nav>
<p class="notice">{AFFILIATE_NOTICE}</p>
<p>{C.SITE_NAME} is run by {C.OPERATOR} and updated automatically. Follow <a href="https://x.com/{C.X_HANDLE}">@{C.X_HANDLE}</a>. News headlines link to and credit their original publishers.</p>
</footer>"""

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
        url, affiliate = with_affiliate(g.get("url"), g.get("platform") or g.get("source") or "")
        label = '<span class="ad">Affiliate</span>' if affiliate else ""
        media = media_html(g.get("image"), g["title"], short_label(g.get("source") or g.get("platform")))
        inner = f'<h3><a class="out" href="{esc(url)}" rel="{OUTBOUND_REL}">{esc(g["title"])}</a>{label}</h3><p class="meta">{esc(g["platform"])}{worth}{ends} · via {esc(g["source"])}</p>'
        li.append(card(media, inner))
    return '<ul class="cards">' + "".join(li) + "</ul>"

def join_names(items):
    names = [str(item) for item in items if str(item or "").strip()]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + ", and " + names[-1]

def listed_price(worth):
    worth = str(worth or "").strip()
    if worth in ("", "N/A", "0", "$0.00"):
        return ""
    return worth

def time_html(iso):
    iso = str(iso or "")
    label = ct(iso) if iso and ("+" in iso or "Z" in iso) else ""
    if not label:
        return ""
    return f'<time datetime="{esc(iso)}">{esc(label)}</time>'

def upcoming_mention(items):
    """One homepage sentence. Empty when Epic has not posted the next giveaway."""
    if not items:
        return ""
    shown = list(items[:4])
    shared = len({g.get("starts") for g in shown}) == 1 and len({g.get("ends") for g in shown}) == 1
    bits = []
    for g in shown:
        price = listed_price(g.get("worth"))
        if shared:
            bits.append(f"{g.get('title') or 'Game'} ({price})" if price else (g.get("title") or "Game"))
        else:
            window = " – ".join(part for part in (ct(g.get("starts") or ""), ct(g.get("ends") or "")) if part)
            detail = ", ".join(part for part in (price, window) if part)
            bits.append(f"{g.get('title') or 'Game'} ({detail})" if detail else (g.get("title") or "Game"))
    sentence = join_names(bits)
    if shared:
        start_label, end_label = ct(shown[0].get("starts") or ""), ct(shown[0].get("ends") or "")
        if start_label and end_label:
            sentence += f", free {start_label} – {end_label}"
    more = f" Plus {len(items) - len(shown)} more." if len(items) > len(shown) else ""
    link = esc(href("/free-games/") + "#coming-next")
    return f'<p>Coming next on Epic: {esc(sentence)}.{more} <a href="{link}">See upcoming free games</a>.</p>'

def upcoming_section(items):
    """Card list for scheduled Epic giveaways. Omitted entirely when there are none."""
    if not items:
        return ""
    cards = []
    for g in items:
        start, end = time_html(g.get("starts")), time_html(g.get("ends"))
        window = f" · free {start} – {end}" if start and end else ""
        price = listed_price(g.get("worth"))
        worth = f" · normally <s>{esc(price)}</s>" if price else ""
        url, affiliate = with_affiliate(g.get("url"), g.get("platform") or g.get("source") or "")
        label = '<span class="ad">Affiliate</span>' if affiliate else ""
        media = media_html(g.get("image"), g.get("title") or "Upcoming game", short_label(g.get("source") or g.get("platform")))
        inner = f'<h3><a class="out" href="{esc(url)}" rel="{OUTBOUND_REL}">{esc(g.get("title"))}</a>{label}</h3><p class="meta">{esc(g.get("platform") or "")}{worth}{window} · via {esc(g.get("source") or "")}</p>'
        cards.append(card(media, inner))
    body = '<ul class="cards">' + "".join(cards) + "</ul>"
    return ('<section id="coming-next" aria-labelledby="coming-next-title">'
            '<h2 id="coming-next-title">Coming next on Epic</h2>'
            '<p>These Epic Games Store games are scheduled to be free to keep. Dates are Central Time.</p>'
            f'{body}</section>')

def faq_entries(free, upcoming):
    """Four free-games answers filled from the listings this build just collected."""
    epic = [g for g in free if g.get("source") == "Epic Games"]
    if not epic:
        current_answer = "No Epic Games Store game is free to keep right now. The list refreshes several times a day."
    else:
        bits = []
        for g in epic:
            price = listed_price(g.get("worth"))
            bits.append(f"{g['title']} (normally {price})" if price else g["title"])
        names = join_names(bits)
        verb = "is" if len(epic) == 1 else "are"
        ends = [ct(g.get("ends") or "") for g in epic]
        ends = [label for label in ends if label]
        if ends and len(set(ends)) == 1:
            current_answer = f"{names} {verb} free to keep on the Epic Games Store right now. Claim by {ends[0]}."
        elif ends:
            deadlines = join_names(
                f"{g['title']} by {ct(g.get('ends') or '')}" for g in epic if ct(g.get("ends") or "")
            )
            current_answer = f"{names} {verb} free to keep on the Epic Games Store right now. Claim {deadlines}."
        else:
            current_answer = f"{names} {verb} free to keep on the Epic Games Store right now."
    if not upcoming:
        next_answer = "Epic has not announced the next free-to-keep game yet. This page updates several times a day."
    else:
        first = upcoming[0]
        same = [g for g in upcoming if g.get("starts") == first.get("starts") and g.get("ends") == first.get("ends")]
        rest = [g for g in upcoming if g not in same]
        names = join_names(g.get("title") or "A game" for g in same)
        verb = "is" if len(same) == 1 else "are"
        start, end = ct(first.get("starts") or ""), ct(first.get("ends") or "")
        next_answer = f"{names} {verb} next, free to keep from {start} until {end}."
        if rest:
            later = join_names(
                f"{g.get('title') or 'A game'} from {ct(g.get('starts') or '')} until {ct(g.get('ends') or '')}" for g in rest
            )
            next_answer += f" Also scheduled: {later}."
    if not epic:
        claim_answer = "A free Epic game can be claimed only while its promotion is live. End times on this page are Central Time, and the price goes back to normal after that."
    else:
        deadlines = [(g["title"], ct(g.get("ends") or "")) for g in epic if ct(g.get("ends") or "")]
        if len(deadlines) == 1:
            claim_answer = f"You can claim {deadlines[0][0]} until {deadlines[0][1]}. After that the Epic price goes back to normal, so add it to your library before then."
        elif deadlines and len({when for _, when in deadlines}) == 1:
            claim_answer = f"You can claim the current Epic giveaways until {deadlines[0][1]}. After that the price goes back to normal, so add each game to your library before then."
        elif deadlines:
            bits = join_names(f"{title} until {when}" for title, when in deadlines)
            claim_answer = f"Each current Epic giveaway has its own deadline: {bits}. Add the game to your library before its end time, or the price goes back to normal."
        else:
            claim_answer = "Claim a free Epic game before the end time shown on its listing. That time is Central Time; after it passes, the price goes back to normal."
    priced = []
    for g in list(epic) + list(upcoming):
        price = listed_price(g.get("worth"))
        if price:
            priced.append(f"{g.get('title') or 'A game'} normally costs {price}")
    if priced:
        cost_answer = join_names(priced) + ". The giveaway price is free during the dates listed above."
    else:
        cost_answer = "Epic did not list a normal price for these games. During each promotion on this page the offer price is free."
    return [
        ("What games are free on Epic right now?", current_answer),
        ("When is the next free Epic game?", next_answer),
        ("How long do I have to claim a free game?", claim_answer),
        ("What do these Epic games normally cost?", cost_answer),
    ]

def faq_section(entries, heading="Free games FAQ"):
    blocks = "".join(f"<h3>{esc(q)}</h3><p>{esc(a)}</p>" for q, a in entries)
    return f'<section class="faq" id="faq" aria-labelledby="faq-title"><h2 id="faq-title">{esc(heading)}</h2>{blocks}</section>'

def faq_jsonld(entries):
    return {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
        for q, a in entries
    ]}

def zone_clocks(iso):
    """Spoken US Central and UTC clocks for an aware timestamp, or None when it cannot be read."""
    raw = str(iso or "").strip()
    if not raw:
        return None
    try:
        when = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if when.tzinfo is None:
        return None

    def speak(moment):
        clock = moment.strftime("%I:%M %p").lstrip("0")
        return f"{moment.strftime('%B')} {moment.day}, {moment.year} at {clock}"

    central = when.astimezone(CT)
    utc = when.astimezone(dt.timezone.utc)
    return speak(central) + " Central Time", speak(utc) + " UTC"

def plain_when(iso):
    """Exact timestamp in US Central Time, with the same moment in UTC. Empty when iso is unusable."""
    clocks = zone_clocks(iso)
    if not clocks:
        return ""
    return f"{clocks[0]} ({clocks[1]})"

def time_sentence(iso):
    """Visible clock with a machine-readable <time> on the Central Time half."""
    clocks = zone_clocks(iso)
    if not clocks:
        return ""
    ct_text, utc_text = clocks
    return f'<time datetime="{esc(iso)}">{esc(ct_text)}</time> ({esc(utc_text)})'

def epic_named(games):
    return join_names(g.get("title") or "A game" for g in games)

def epic_title(current):
    if current:
        return f"Epic Games Free Games This Week: {epic_named(current)} | {C.SITE_NAME}"
    return f"Epic Games Free Games This Week | {C.SITE_NAME}"

def epic_description(current, upcoming):
    if current:
        verb = "is" if len(current) == 1 else "are"
        text = f"{epic_named(current)} {verb} free on the Epic Games Store this week."
    else:
        text = "No Epic Games Store game is free to keep this week."
    if upcoming:
        label = "Next free Epic game" if len(upcoming) == 1 else "Next free Epic games"
        text += f" {label}: {epic_named(upcoming[:3])}."
    else:
        text += " The next free Epic game has not been announced yet."
    return text + " See exact start and end times in US Central Time and UTC."

def epic_intro(current, upcoming):
    """Short original summary. Dates are written out so the first paragraph stands alone."""
    parts = [
        "Epic Games Store puts one or more PC games up for free every week. Claim a game before its promotion ends and it stays in your library. The windows below are the times Epic published, written in US Central Time with the same moment in UTC.",
    ]
    if current:
        parts.append(epic_current_sentence(current))
    else:
        parts.append("No Epic game is free to keep right now.")
    if upcoming:
        label = "Next free Epic game" if len(upcoming) == 1 else "Next free Epic games"
        parts.append(f"{label}: {epic_upcoming_sentence(upcoming)}.")
    else:
        parts.append("Epic has not posted the next free game yet.")
    return " ".join(parts)

def epic_current_sentence(current):
    """One sentence for the live Epic games. A shared deadline is written once."""
    named = []
    for game in current:
        named.append((game.get("title") or "A game", plain_when(game.get("ends"))))
    ends = {when for _, when in named if when}
    if len(named) == 1:
        title, when = named[0]
        return f"{title} is free until {when}." if when else f"{title} is free to keep right now."
    if ends and len(ends) == 1 and all(when for _, when in named):
        return f"{join_names(title for title, _ in named)} are free until {next(iter(ends))}."
    bits = []
    for title, when in named:
        bits.append(f"{title} is free until {when}" if when else f"{title} is free to keep right now")
    return join_names(bits) + "."

def epic_upcoming_sentence(upcoming):
    """Names plus the claim window. Games that share a window are grouped."""
    rows = []
    for game in upcoming:
        rows.append((game.get("title") or "A game", plain_when(game.get("starts")), plain_when(game.get("ends"))))
    windows = {(start, end) for _, start, end in rows}
    if len(windows) == 1:
        start, end = next(iter(windows))
        names = join_names(title for title, _, _ in rows)
        verb = "is" if len(rows) == 1 else "are"
        if start and end:
            return f"{names} {verb} free from {start} until {end}"
        if start:
            return f"{names} {verb} free starting {start}"
        if end:
            return f"{names} {verb} free until {end}"
        return names
    bits = []
    for title, start, end in rows:
        if start and end:
            bits.append(f"{title} is free from {start} until {end}")
        elif start:
            bits.append(f"{title} starts {start}")
        elif end:
            bits.append(f"{title} is free until {end}")
        else:
            bits.append(f"{title} is scheduled next")
    return join_names(bits)

def epic_schedule_html(game, upcoming=False):
    if upcoming:
        start, end = time_sentence(game.get("starts")), time_sentence(game.get("ends"))
        if start and end:
            return f"Free from {start} until {end}."
        if start:
            return f"Free from {start}."
        if end:
            return f"Free until {end}."
        return "Epic has not published a claim window for this game yet."
    end = time_sentence(game.get("ends"))
    if end:
        return f"Claim by {end}."
    return "Epic has not published an end time for this giveaway."

def epic_list_section(section_id, heading, note, games, upcoming=False):
    """Card list for one Epic group. Omitted entirely when that group is empty."""
    if not games:
        return ""
    cards = []
    for game in games:
        url, affiliate = with_affiliate(game.get("url"), game.get("platform") or game.get("source") or "")
        label = '<span class="ad">Affiliate</span>' if affiliate else ""
        title = game.get("title") or "Epic game"
        media = media_html(game.get("image"), title, short_label(game.get("source") or game.get("platform")))
        price = listed_price(game.get("worth"))
        worth = f" Normally {esc(price)}." if price else ""
        inner = (
            f'<h3><a class="out" href="{esc(url)}" rel="{OUTBOUND_REL}">{esc(title)}</a>{label}</h3>'
            f'<p class="meta">{epic_schedule_html(game, upcoming=upcoming)}{worth}</p>'
        )
        cards.append(card(media, inner))
    heading_id = section_id + "-title"
    return (
        f'<section id="{section_id}" aria-labelledby="{heading_id}">'
        f'<h2 id="{heading_id}">{esc(heading)}</h2>'
        f'<p>{esc(note)}</p>'
        f'<ul class="cards">{"".join(cards)}</ul>'
        "</section>"
    )

def epic_faq_entries(current, upcoming):
    """Stable Epic questions. Answers stay complete when a list is empty."""
    change = (
        "Epic Games Store free games usually change on Thursday at 10:00 AM US Central Time (3:00 PM UTC). "
        "A giveaway stays claimable until the end time written on this page, and the next game opens at its published start time."
    )
    dated = []
    for game in current or []:
        when = plain_when(game.get("ends"))
        if when:
            dated.append((game.get("title") or "The current game", when))
    if dated:
        ends = {when for _, when in dated}
        if len(ends) == 1:
            names = join_names(title for title, _ in dated)
            verb = "ends" if len(dated) == 1 else "end"
            change += f" {names} {verb} {dated[0][1]}."
        else:
            change += " " + join_names(f"{title} ends {when}" for title, when in dated) + "."
    elif upcoming:
        first = upcoming[0]
        start = plain_when(first.get("starts"))
        if start:
            change += f" The next one, {first.get('title') or 'the next game'}, starts {start}."
    keep = (
        "Yes. A game you claim during a free-to-keep promotion stays in your Epic Games library after the promotion ends. "
        "You have to claim it before the end time. When the window closes, the price goes back to normal and a game you did not claim is not added for you."
    )
    account = (
        "Yes. You need a free Epic Games Store account. Sign in, open the game page, and choose Get while the promotion is live. "
        "The game is then tied to that Epic account and can be installed in the Epic Games Launcher."
    )
    return [
        ("When do Epic free games change?", change),
        ("Do you keep Epic free games forever?", keep),
        ("Do you need an Epic Games Store account?", account),
    ]

def epic_landing(current, upcoming):
    """Body, JSON-LD blocks, title, and description for /free-games/epic/.

    An empty current list or an empty upcoming list drops that section and does not emit an ItemList for it.
    Both empty still leaves the intro, the FAQ, and valid FAQPage JSON-LD.
    """
    current = [game for game in (current or []) if game.get("title")]
    upcoming = [game for game in (upcoming or []) if game.get("title")]
    faqs = epic_faq_entries(current, upcoming)
    links = (
        f'<p>Other stores are on the <a href="{href("/free-games/")}">free games</a> page. '
        f'Deals and headlines are on the <a href="{href("/")}">homepage</a>.</p>'
    )
    current_note = "These games are free to keep on the Epic Games Store right now. Each line is the claim deadline in US Central Time, with the same moment in UTC."
    if len(upcoming) == 1:
        next_heading = "Next free Epic game"
    else:
        next_heading = "Next free Epic games"
    next_note = "Epic has already scheduled these free games. Each line is the exact start and end in US Central Time, with the same moment in UTC."
    body = (
        "<h1>Epic Games free games this week</h1>"
        f"<p>{esc(epic_intro(current, upcoming))}</p>"
        f"{links}"
        f"{epic_list_section('epic-now', 'Free on Epic right now', current_note, current)}"
        f"{epic_list_section('epic-next', next_heading, next_note, upcoming, upcoming=True)}"
        f"{faq_section(faqs, 'Epic free games FAQ')}"
    )
    blocks = []
    rows = current + upcoming
    if rows:
        blocks.append(itemlist("Epic Games free games this week", rows))
    blocks.append(faq_jsonld(faqs))
    return body, blocks, epic_title(current), epic_description(current, upcoming)

def llms_upcoming(items):
    if not items:
        return "- None scheduled"
    lines = []
    for g in items:
        price = listed_price(g.get("worth"))
        price_bit = f", normally {price}" if price else ""
        start, end = ct(g.get("starts") or ""), ct(g.get("ends") or "")
        lines.append(f"- {g.get('title')} (Epic Games Store{price_bit}): free {start} – {end} — {g.get('url')}")
    return "\n".join(lines)

def deal_list(items, n=None):
    items = items[:n] if n else items
    if not items: return '<p class="empty">No deals available right now. The list refreshes several times a day.</p>'
    li = []
    for d in items:
        meta = f'<span class="badge">-{d["savings"]}%</span> <span class="price">${esc(d["price"])}</span> <s>${esc(d["normal"])}</s> at {esc(d["store"])}'
        if d.get("metacritic") not in (None, "0"):
            meta += " · Metacritic " + esc(d["metacritic"])
        url, affiliate = with_affiliate(d.get("url"), d.get("store") or "")
        label = '<span class="ad">Affiliate</span>' if affiliate else ""
        media = media_html(d.get("image"), d["title"], short_label(d.get("store")), d.get("icon"))
        inner = f'<h3><a class="out" href="{esc(url)}" rel="{OUTBOUND_REL}">{esc(d["title"])}</a>{label}</h3><p class="meta">{meta}</p>'
        li.append(card(media, inner))
    return '<ul class="cards">' + "".join(li) + "</ul>"

def news_list(items, n=None):
    items = items[:n] if n else items
    if not items: return '<p class="empty">News feeds are temporarily unavailable. Please check back shortly.</p>'
    li = []
    for a in items:
        blurb = voice_blurb(a.get("title"), a.get("summary"), a.get("source"))
        summary = f'<p>{esc(blurb)}</p>' if blurb else ""
        media = media_html(a.get("image"), a["title"], short_label(a.get("source")))
        inner = f'<h3><a href="{esc(a["url"])}" rel="noopener">{esc(a["title"])}</a></h3>{summary}<p class="meta">Source: {esc(a["source"])} · <time datetime="{esc(a["published"])}">{ct(a["published"])}</time></p>'
        li.append(card(media, inner))
    return '<ul class="cards">' + "".join(li) + "</ul>"

def schema_price(row):
    """Sale price as a USD string, or 0.00 for a free-to-keep listing. None when unknown."""
    if "price" in row and str(row.get("price")).strip() not in ("", "None"):
        raw = str(row["price"]).replace("$", "").replace(",", "").strip()
        try:
            value = float(raw)
        except ValueError:
            return None
        if value < 0:
            return None
        return f"{value:.2f}"
    if row.get("platform") and "price" not in row:
        return "0.00"
    return None

def itemlist(name, rows):
    """ItemList of the games or deals on the page, with a price when we have one."""
    elements = []
    for i, row in enumerate(rows):
        title = row.get("title") or ""
        url = row.get("url") or ""
        item = {"@type": "Product", "name": title, "url": url}
        price = schema_price(row)
        if price is not None:
            item["offers"] = {"@type": "Offer", "price": price, "priceCurrency": "USD", "url": url}
        elements.append({"@type": "ListItem", "position": i + 1, "item": item})
    return {"@context": "https://schema.org", "@type": "ItemList", "name": name,
            "numberOfItems": len(elements), "itemListElement": elements}

def signup():
    action = esc(form_action())
    privacy = esc(href("/privacy/"))
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

def end_label(ends):
    """End date as the free-games page shows it."""
    ends = str(ends or "")
    if not ends:
        return ""
    if "+" in ends or "Z" in ends:
        return ct(ends) or ends
    return ends[:10]

def xml_text(value):
    return xml_escape("" if value is None else str(value))

def xml_attr(value):
    return xml_escape("" if value is None else str(value), {'"': "&quot;"})

def rss_datetime(value):
    """RFC 822 timestamp for RSS pubDate and lastBuildDate."""
    if isinstance(value, dt.datetime):
        when = value
    else:
        when = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return format_datetime(when)

def render_rss(title, link, description, self_url, updated, items):
    """RSS 2.0. Item links are XML-escaped and otherwise left unchanged."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        f"<title>{xml_text(title)}</title>",
        f"<link>{xml_text(link)}</link>",
        f"<description>{xml_text(description)}</description>",
        f"<lastBuildDate>{xml_text(rss_datetime(updated))}</lastBuildDate>",
        f'<atom:link href="{xml_attr(self_url)}" rel="self" type="application/rss+xml"></atom:link>',
    ]
    for item in items:
        lines.append("<item>")
        lines.append(f"<title>{xml_text(item.get('title'))}</title>")
        lines.append(f"<link>{xml_text(item.get('link'))}</link>")
        if item.get("description"):
            lines.append(f"<description>{xml_text(item['description'])}</description>")
        if item.get("pubDate"):
            lines.append(f"<pubDate>{xml_text(item['pubDate'])}</pubDate>")
        if item.get("link"):
            lines.append(f'<guid isPermaLink="true">{xml_text(item["link"])}</guid>')
        lines.append("</item>")
    lines.extend(["</channel>", "</rss>", ""])
    return "\n".join(lines)

def news_rss_items(news):
    """Headlines for /feed.xml. The description is the summary already published on the site."""
    items = []
    for item in news:
        row = {"title": item.get("title") or "", "link": item.get("url") or ""}
        summary = voice_blurb(item.get("title"), item.get("summary"), item.get("source"))
        if summary:
            row["description"] = summary
        if item.get("published"):
            try:
                row["pubDate"] = rss_datetime(item["published"])
            except Exception:
                pass
        items.append(row)
    return items

def free_rss_items(games):
    """Current free-to-keep games. Store links match the free-games page, including affiliate tags."""
    items = []
    for game in games:
        link, _active = with_affiliate(game.get("url"), game.get("platform") or game.get("source") or "")
        row = {"title": game.get("title") or "", "link": link}
        label = end_label(game.get("ends"))
        if label:
            row["description"] = f"Ends {label}."
        items.append(row)
    return items

def write(rel, text):
    p = os.path.join(C.OUT_DIR, rel.lstrip("/")); os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    open(p, "w", encoding="utf-8").write(text)

def build(offline=False):
    FAILURES.clear()
    now = dt.datetime.now(CT).replace(microsecond=0)
    free, upcoming = fetch_epic(offline)
    titles = {norm_title(g["title"]) for g in free}
    free += [g for g in fetch_gamerpower(offline) if norm_title(g["title"]) not in titles]
    upcoming = omit_listed(upcoming, free)
    deals, news = fetch_deals(offline), fetch_news(offline)
    form = signup()
    pages = []
    def P(path, *a, **k): write(path + "index.html", page(path, *a, now, **k)); pages.append(path)
    P("/", f"{C.SITE_NAME}: Free Games, Game Deals & Gaming News Today",
      f"Today's free PC games, the best game deals and the latest gaming news, updated several times a day. {len(free)} free games right now.",
      f'<h1>Free games, deals and gaming news, updated daily</h1><p>{esc(C.TAGLINE)}</p><h2>Free right now</h2>{free_list(free, 6)}{upcoming_mention(upcoming)}<p><a href="{href("/free-games/")}">All free games →</a> · <a href="{href("/free-games/epic/")}">Epic free games this week</a></p><h2>Top deals</h2>{deal_list(deals, 6)}<p><a href="{href("/deals/")}">All deals →</a></p><h2>Latest news</h2>{news_list(news, 6)}<p><a href="{href("/news/")}">All news →</a></p>{form}',
      jsonld=[itemlist("Free games right now", free[:6])] if free else [])
    faqs = faq_entries(free, upcoming)
    free_ld = [itemlist("Free games right now", free)] if free else []
    P("/free-games/", f"Free Games Right Now ({now:%B %Y}) | {C.SITE_NAME}",
      "Every free-to-keep game giveaway we can find on Epic Games Store, Steam, GOG and more, with end dates in Central Time.",
      f'<h1>Free games right now</h1><p>Games you can claim and keep for free. <a href="{href("/free-games/epic/")}">Epic Games free games this week</a> lists the current Epic giveaways and the next free Epic game. Data: Epic Games Store and <a href="https://www.gamerpower.com/">GamerPower</a>. Machine-readable: <a href="{href("/data/free-games.json")}">free-games.json</a>.</p><section id="free-now">{free_list(free)}</section>{upcoming_section(upcoming)}{faq_section(faqs)}{form}',
      jsonld=[*free_ld, faq_jsonld(faqs)])
    epic_free = [g for g in free if g.get("source") == "Epic Games"]
    epic_body, epic_ld, epic_title_text, epic_desc = epic_landing(epic_free, upcoming)
    P("/free-games/epic/", epic_title_text, epic_desc, epic_body, jsonld=epic_ld)
    P("/deals/", f"Best PC Game Deals Today | {C.SITE_NAME}",
      "The highest-rated PC game discounts on Steam, GOG, Humble and Fanatical, with sale prices and savings, refreshed several times a day.",
      f'<h1>Top PC game deals</h1><p>Deal data from <a href="https://www.cheapshark.com/">CheapShark</a>. Prices can change at any time. Machine-readable: <a href="{href("/data/deals.json")}">deals.json</a>.</p>{deal_list(deals)}{form}',
      jsonld=[itemlist("Top game deals", deals)] if deals else [])
    arts = [{"@context": "https://schema.org", "@type": "NewsArticle", "headline": a["title"][:110], "url": a["url"], "datePublished": a["published"],
             "publisher": {"@type": "Organization", "name": a["source"]}} for a in news[:20]]
    P("/news/", f"Latest Gaming News Headlines | {C.SITE_NAME}",
      "A fast roundup of today's gaming headlines from IGN, PC Gamer, VGC, Eurogamer and Gematsu, each with a short summary and a link to the source.",
      f'<h1>Latest gaming news</h1><p>Headlines with a short summary in our own words. Read the full stories at the original publishers.</p>{news_list(news)}', jsonld=arts)
    P("/newsletter/", f"Daily Gaming Newsletter | {C.SITE_NAME}",
      "The StazGames morning email covers free games and the best deals. Joining is free, and you can unsubscribe anytime.",
      f"<h1>The StazGames daily newsletter</h1>{form}")
    P("/about/", f"About {C.SITE_NAME} | Who Runs This Site",
      f"{C.SITE_NAME} is run by {C.OPERATOR}. Learn how our automated free-game and deal alerts work, plus our affiliate disclosure.",
      f"""<h1>About {C.SITE_NAME}</h1><p>{esc(C.TAGLINE)}</p>
<h2>Who runs it</h2><p>{C.SITE_NAME} and <a href="https://x.com/{C.X_HANDLE}">@{C.X_HANDLE}</a> are operated by {C.OPERATOR} (<a href="https://x.com/{C.OPERATOR_X}" rel="me">@{C.OPERATOR_X}</a>), based in US Central Time.</p>
<h2>Automated account disclosure</h2><p>This site and the @{C.X_HANDLE} X account are updated automatically by software that checks public feeds (Epic Games Store, GamerPower, CheapShark and publisher RSS feeds). A human operator, {C.OPERATOR}, oversees it. Report mistakes to <a href="https://x.com/{C.OPERATOR_X}">@{C.OPERATOR_X}</a>.</p>
<h2>News policy</h2><p>We publish headlines, a short summary, a thumbnail when the source provides one, and a credited link to the original publisher. We do not republish full articles.</p>
<h2>Affiliate disclosure</h2><p>Some deal links may be affiliate links. Read the full <a href="{href("/disclosure/")}">affiliate disclosure</a>, including the Amazon Associates statement.</p>""")
    P("/disclosure/", f"Affiliate disclosure | {C.SITE_NAME}",
      f"How {C.SITE_NAME} uses affiliate links, including Amazon Associates, and who runs the site.",
      f"""<h1>Affiliate disclosure</h1>
<p>Some deal links may be affiliate links that pay {C.SITE_NAME} a commission at no extra cost to the reader. We pick deals from public store data. A commission does not raise the price you pay.</p>
<p>{AMAZON_DISCLOSURE}</p>
<p>{C.SITE_NAME} is run by {C.OPERATOR}. Game listings, news summaries, and the @{C.X_HANDLE} account are updated by automation that {C.OPERATOR} oversees.</p>
<p>Questions go through <a href="https://x.com/{C.X_HANDLE}">@{C.X_HANDLE}</a> on X.</p>""")
    P("/privacy/", f"Privacy policy | {C.SITE_NAME}",
      f"How {C.SITE_NAME} handles newsletter emails with Resend, affiliate cookies, and Vercel hosting logs.",
      f"""<h1>Privacy policy</h1>
<p>This policy covers {C.SITE_NAME}, run by {C.OPERATOR}.</p>
<h2>Newsletter</h2>
<p>If you join the daily email, we store your email address with Resend so we can send that message. You can unsubscribe anytime from the link in each email. We do not sell your data, and we do not share it with advertisers.</p>
<h2>Affiliate links</h2>
<p>Some deal and store links are affiliate links. When you click through, that store or its affiliate partner may set tracking cookies in your browser. Those cookies belong to the partner. {C.SITE_NAME} does not control them. See the <a href="{href("/disclosure/")}">affiliate disclosure</a>.</p>
<h2>Hosting</h2>
<p>The site is hosted on Vercel. Vercel keeps standard hosting logs, such as IP address, browser, and the page requested, to run and protect the service.</p>
<h2>Contact</h2>
<p>Privacy questions: {C.OPERATOR} on X at <a href="https://x.com/{C.X_HANDLE}">@{C.X_HANDLE}</a> (<a href="https://x.com/{C.X_HANDLE}">https://x.com/{C.X_HANDLE}</a>).</p>""")
    P("/contact/", f"Contact | {C.SITE_NAME}",
      f"Reach {C.SITE_NAME} on X at @{C.X_HANDLE}. The site is run by {C.OPERATOR}.",
      f"""<h1>Contact</h1>
<p>{C.SITE_NAME} is run by {C.OPERATOR}. The site and @{C.X_HANDLE} are updated by automation that he oversees.</p>
<p>Contact us on X: <a href="https://x.com/{C.X_HANDLE}">@{C.X_HANDLE}</a> (<a href="https://x.com/{C.X_HANDLE}">https://x.com/{C.X_HANDLE}</a>).</p>""")
    P("/terms/", f"Terms of use | {C.SITE_NAME}",
      f"Short terms for using {C.SITE_NAME}, including deals, news summaries, and affiliate links.",
      f"""<h1>Terms</h1>
<p>By using {C.SITE_NAME} you agree to these terms.</p>
<ul>
<li>The site is information, not a store. Prices and giveaways change. Confirm the offer on the store's own page before you buy or claim it.</li>
<li>Purchases and downloads are with the store, not with {C.SITE_NAME}.</li>
<li>Some links may be affiliate links. Read the <a href="{href("/disclosure/")}">affiliate disclosure</a>.</li>
<li>News items are short summaries with a link to the original publisher.</li>
<li>The site is provided as is. We are not liable for a missed deal, a changed price, or a store's decision.</li>
<li>These terms can be updated. The time at the top of this page is the current version.</li>
</ul>
<p>The site is run by {C.OPERATOR}. Questions go to <a href="https://x.com/{C.X_HANDLE}">@{C.X_HANDLE}</a> on X.</p>""")
    write("/404.html", page("/404.html", f"Page not found | {C.SITE_NAME}",
      "That page isn't on StazGames. Head back for free games, deals, and gaming news.",
      f'<h1>Page not found</h1><p>That page isn\'t on {C.SITE_NAME}. The free games, deals, and news are on the homepage.</p><p><a href="{href("/")}">Back to the homepage</a></p>',
      now))
    write("/style.css", CSS); write("/logo.svg", LOGO_SVG); write_og()
    write("/data/deals.json", json.dumps({"updated": now.isoformat(), "source": "CheapShark", "count": len(deals), "deals": deals}, indent=1, ensure_ascii=False))
    write("/data/free-games.json", json.dumps({"updated": now.isoformat(), "sources": ["Epic Games Store", "GamerPower"], "count": len(free), "games": free, "upcoming": upcoming}, indent=1, ensure_ascii=False))
    news_rows = []
    for item in news:
        row = {"title": item.get("title", ""), "source": item.get("source", ""), "url": item.get("url", ""),
               "summary": voice_blurb(item.get("title"), item.get("summary"), item.get("source"))}
        if item.get("published"):
            row["published"] = item["published"]
        news_rows.append(row)
    write("/data/news.json", json.dumps({"updated": now.isoformat(), "count": len(news_rows), "news": news_rows}, indent=1, ensure_ascii=False))
    write("/feed.xml", render_rss(
        f"{C.SITE_NAME} gaming news",
        C.BASE_URL + "/news/",
        "Gaming headlines with a short summary and a link to the original publisher.",
        C.BASE_URL + "/feed.xml",
        now,
        news_rss_items(news),
    ))
    write("/free-games/feed.xml", render_rss(
        f"{C.SITE_NAME} free games",
        C.BASE_URL + "/free-games/",
        "Current free-to-keep games with the end date of each giveaway.",
        C.BASE_URL + "/free-games/feed.xml",
        now,
        free_rss_items(free),
    ))
    write("/robots.txt", f"User-agent: *\nAllow: {href("/")}\n\nSitemap: {C.BASE_URL}/sitemap.xml\n")
    write("/sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
          "".join(f"<url><loc>{C.BASE_URL}{p}</loc><lastmod>{now.isoformat()}</lastmod><changefreq>hourly</changefreq></url>\n" for p in pages) + "</urlset>\n")
    top = "\n".join(f"- {g['title']} ({g['platform']}): {g['url']}" for g in free[:10]) or "- None right now"
    coming = llms_upcoming(upcoming)
    write("/llms.txt", f"""# {C.SITE_NAME}

> {C.TAGLINE} Run by {C.OPERATOR} (https://x.com/{C.OPERATOR_X}). Updated automatically several times a day; last update {now:%Y-%m-%d %H:%M} CT.

## Pages
- [Free games]({C.BASE_URL}/free-games/): current free-to-keep game giveaways with end dates, plus upcoming Epic free games
- [Epic free games]({C.BASE_URL}/free-games/epic/): Epic Games Store games free this week and the next free Epic game, with claim times in US Central Time and UTC
- [Deals]({C.BASE_URL}/deals/): top-rated PC game discounts (CheapShark data)
- [News]({C.BASE_URL}/news/): headlines with short summaries, linked to original publishers
- [About]({C.BASE_URL}/about/): operator, automation and affiliate disclosures
- [Disclosure]({C.BASE_URL}/disclosure/): affiliate disclosure, including Amazon Associates
- [Privacy]({C.BASE_URL}/privacy/): newsletter emails, cookies, and hosting logs
- [Contact]({C.BASE_URL}/contact/): reach the operator on X
- [Terms]({C.BASE_URL}/terms/): short terms of use

## Data
- [free-games.json]({C.BASE_URL}/data/free-games.json): JSON list of current free games (`games`) and scheduled Epic giveaways (`upcoming`)
- [deals.json]({C.BASE_URL}/data/deals.json): JSON list of current deals
- [news.json]({C.BASE_URL}/data/news.json): JSON list of current gaming headlines
- [feed.xml]({C.BASE_URL}/feed.xml): RSS 2.0 feed of gaming news headlines
- [free-games/feed.xml]({C.BASE_URL}/free-games/feed.xml): RSS 2.0 feed of current free-to-keep games

## Free right now
{top}

## Coming next
{coming}
""")
    write("/build-report.json", json.dumps({"built": now.isoformat(), "free": len(free), "upcoming": len(upcoming), "deals": len(deals), "news": len(news), "failures": FAILURES}, indent=1))
    return {"free": len(free), "upcoming": len(upcoming), "deals": len(deals), "news": len(news), "failures": list(FAILURES)}

if __name__ == "__main__":
    print(json.dumps(build("--offline" in sys.argv), indent=1))
