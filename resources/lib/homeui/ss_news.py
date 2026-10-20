# -*- coding: utf-8 -*-
"""Film news for the screensaver's News style (v5.10.357).

Kodi-import-free: the feeds are read with urllib, parsed with the standard
library, and kept in a small JSON file the caller names (the Home's profile
folder), so the screensaver opens at once with the last news while the new
ones load.

Sources: IMDb's movie news (the page's own JSON), and the RSS feeds of the
film trade press. Each item: id, title, summary, body (the feed's own full
text when it has one), link, image, ts (epoch seconds), source.
"""
import html
import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

UA = ('Mozilla/5.0 (Linux; Android 12; Kodi) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/124.0 Safari/537.36')
TTL = 30 * 60
MAX_ITEMS = 40
PER_SOURCE = 12

# (key, label, kind, url)
SOURCES = (
    ('imdb', 'IMDb', 'imdb', 'https://www.imdb.com/news/movie/'),
    ('variety', 'Variety', 'rss', 'https://variety.com/v/film/feed/'),
    ('deadline', 'Deadline', 'rss', 'https://deadline.com/feed/'),
    ('thr', 'The Hollywood Reporter', 'rss', 'https://www.hollywoodreporter.com/c/movies/feed/'),
    ('screenrant', 'Screen Rant', 'rss', 'https://screenrant.com/feed/movie-news/'),
    ('collider', 'Collider', 'rss', 'https://collider.com/feed/category/movie-news/'),
    ('slashfilm', '/Film', 'rss', 'https://www.slashfilm.com/feed/'),
    ('indiewire', 'IndieWire', 'rss', 'https://www.indiewire.com/c/film/feed/'),
)
SOURCE_KEYS = tuple(s[0] for s in SOURCES)
DEFAULT_SOURCES = ('imdb', 'variety', 'deadline', 'screenrant')

_NS = {
    'media': 'http://search.yahoo.com/mrss/',
    'content': 'http://purl.org/rss/1.0/modules/content/',
    'atom': 'http://www.w3.org/2005/Atom',
    'dc': 'http://purl.org/dc/elements/1.1/',
}


# ------------------------------------------------------------------ text
_BLOCK = re.compile(r'(?is)<\s*(?:br|/p|/div|/h[1-6]|/li|/blockquote)\b[^>]*>')
_DROP = re.compile(r'(?is)<\s*(script|style|figure|figcaption|iframe|noscript|aside)\b.*?<\s*/\s*\1\s*>')
_TAG = re.compile(r'(?s)<[^>]+>')


def clean(text, keep_paragraphs=False):
    """HTML to plain text; paragraphs kept as blank lines when asked."""
    text = str(text or '')
    if not text:
        return ''
    text = _DROP.sub(' ', text)
    if keep_paragraphs:
        text = _BLOCK.sub('\n\n', text)
    text = _TAG.sub(' ', text)
    text = html.unescape(text).replace('\xa0', ' ')
    if keep_paragraphs:
        paras = [' '.join(p.split()) for p in re.split(r'\n\s*\n', text)]
        return '\n\n'.join(p for p in paras if p)
    return ' '.join(text.split())


def short(text, limit=320):
    text = ' '.join(str(text or '').split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(' ', 1)[0]
    return cut.rstrip(',;:') + '…'


_TAIL = re.compile(r'\s*(The post .{0,200}? appeared first on .{0,80}\.?|Read more.*|Continue reading.*)$', re.I | re.S)


_ELLIPSIS = re.compile(r'\s*\[\s*(?:…|\.\.\.|&#8230;)\s*\]\s*$')


def _summary(text):
    return _ELLIPSIS.sub('…', _TAIL.sub('', clean(text)).strip()).strip()


def quoted_titles(headline):
    """Titles a headline names in quotes ('Dune: Part Three', “Sinners”), longest first."""
    text = str(headline or '')
    found = []
    for pattern in (r'‘([^’]{2,80})’', r'“([^”]{2,80})”',
                    r"(?:^|[\s(\[:])'([^']{2,80}?)'(?=[\s,.:;!?)\]]|$|s\s)", r'"([^"]{2,80})"'):
        for match in re.finditer(pattern, text):
            value = match.group(1).strip(' ,.:;')
            if value and value not in found and not value.lower().startswith(('the post', 'http')):
                found.append(value)
    return sorted(found, key=len, reverse=True)


def bigger_image(url):
    """Ask the image hosts we know for a large copy."""
    url = html.unescape(str(url or '')).strip()
    if not url:
        return ''
    if url.startswith('//'):
        url = 'https:' + url
    # IMDb / Amazon: ..._V1_QL75_UX300_.jpg -> ..._V1_UX1920_.jpg
    if 'media-amazon.com/images/' in url:
        url = re.sub(r'\._V1_[^/]*?\.(jpe?g|png|webp)$', r'._V1_UX1920_.\1', url)
    # WordPress (Penske, Valnet): ?w=300 / ?resize=300,169 -> 1920 wide
    url = re.sub(r'([?&])w=\d{2,4}\b', r'\g<1>w=1920', url)
    url = re.sub(r'([?&])resize=\d+(?:%2C|,)\d+', r'\g<1>w=1920', url)
    url = re.sub(r'-\d{2,4}x\d{2,4}(\.(?:jpe?g|png|webp))(\?|$)', r'\1\2', url)
    return url


def _ts(raw):
    raw = str(raw or '').strip()
    if not raw:
        return 0.0
    try:
        return parsedate_to_datetime(raw).timestamp()
    except Exception:
        pass
    try:
        from datetime import datetime
        value = re.sub(r'(T\d\d:\d\d:\d\d)\.\d+', r'\1', raw.replace('Z', '+00:00'))
        return datetime.fromisoformat(value).timestamp()
    except Exception:
        return 0.0


def ago(ts, english=False, now=None):
    """'3 h ago' / 'قبل 3 س'."""
    try:
        seconds = max(0, int((now or time.time()) - float(ts)))
    except (TypeError, ValueError):
        return ''
    if not ts:
        return ''
    minutes = seconds // 60
    if minutes < 60:
        n, en, ar = max(1, minutes), '%d min ago', 'قبل %d د'
    elif minutes < 24 * 60:
        n, en, ar = minutes // 60, '%d h ago', 'قبل %d س'
    else:
        n, en, ar = minutes // (24 * 60), '%d d ago', 'قبل %d يوم'
    return (en if english else ar) % n


# ------------------------------------------------------------------ feeds
def _text(node, path):
    found = node.find(path, _NS)
    return (found.text or '') if found is not None else ''


def _first_img(markup):
    match = re.search(r'(?is)<img\b[^>]*?\b(?:data-src|src)\s*=\s*["\']([^"\']+)["\']', str(markup or ''))
    return match.group(1) if match else ''


def parse_rss(data, label):
    """RSS 2.0 or Atom bytes -> items."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        # some feeds carry a BOM or junk before the prolog
        text = data.decode('utf-8', 'replace') if isinstance(data, bytes) else str(data)
        start = text.find('<rss')
        if start < 0:
            start = text.find('<feed')
        if start < 0:
            return []
        try:
            root = ET.fromstring(text[start:].encode('utf-8'))
        except ET.ParseError:
            return []
    nodes = root.findall('./channel/item') or root.findall('.//item')
    atom = False
    if not nodes:
        nodes = root.findall('atom:entry', _NS)
        atom = True
    out = []
    for node in nodes:
        title = clean(_text(node, 'atom:title' if atom else 'title'))
        if not title:
            continue
        if atom:
            links = node.findall('atom:link', _NS)
            pick = next((l for l in links if l.get('rel') in (None, '', 'alternate')), links[0] if links else None)
            link = pick.get('href', '') if pick is not None else ''
            summary_raw = _text(node, 'atom:summary')
            body_raw = _text(node, 'atom:content')
            date = _text(node, 'atom:published') or _text(node, 'atom:updated')
        else:
            link = (_text(node, 'link') or '').strip()
            summary_raw = _text(node, 'description')
            body_raw = _text(node, 'content:encoded')
            date = _text(node, 'pubDate') or _text(node, 'dc:date')
        image = ''
        for path in ('media:content', 'media:group/media:content', 'media:thumbnail', 'enclosure'):
            for media in node.findall(path, _NS):
                url = media.get('url') or ''
                kind = (media.get('type') or media.get('medium') or 'image').lower()
                if url and ('image' in kind or re.search(r'\.(jpe?g|png|webp)(\?|$)', url, re.I)):
                    image = url
                    break
            if image:
                break
        if not image:
            image = _first_img(body_raw) or _first_img(summary_raw)
        body = clean(body_raw, keep_paragraphs=True)
        summary = _summary(summary_raw) or short(body.replace('\n\n', ' '), 400)
        out.append({'id': link or title, 'title': title, 'summary': short(summary, 420),
                    'body': body if len(body) > len(summary) + 200 else '', 'link': link,
                    'image': bigger_image(image), 'ts': _ts(date), 'source': label})
    return out


def _walk(node, found, depth=0):
    if depth > 40:
        return
    if isinstance(node, dict):
        if 'articleTitle' in node and ('externalUrl' in node or 'text' in node):
            found.append(node)
            return
        for value in node.values():
            _walk(value, found, depth + 1)
    elif isinstance(node, list):
        for value in node:
            _walk(value, found, depth + 1)


def _plain(value):
    if isinstance(value, dict):
        for key in ('plainText', 'plaidHtml', 'value', 'text'):
            if value.get(key):
                return str(value[key])
        return ''
    return str(value or '')


def parse_imdb(page, label='IMDb'):
    """IMDb's news page -> items (from the page's own __NEXT_DATA__ JSON)."""
    text = page.decode('utf-8', 'replace') if isinstance(page, bytes) else str(page or '')
    match = re.search(r'(?s)<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', text)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return []
    nodes = []
    _walk(data, nodes)
    out = []
    for node in nodes:
        title = clean(_plain(node.get('articleTitle')))
        if not title:
            continue
        image = node.get('image') or {}
        image = image.get('url') if isinstance(image, dict) else str(image or '')
        source = node.get('source') or {}
        outlet = ''
        if isinstance(source, dict):
            home = source.get('homepage') or {}
            outlet = (home.get('label') if isinstance(home, dict) else '') or source.get('label') or ''
        body = clean(_plain(node.get('text')), keep_paragraphs=True)
        link = str(node.get('externalUrl') or '')
        if not link and node.get('id'):
            link = 'https://www.imdb.com/news/%s/' % node['id']
        out.append({'id': str(node.get('id') or link or title), 'title': title,
                    'summary': short(body.replace('\n\n', ' '), 420), 'body': body if len(body) > 450 else '',
                    'link': link, 'image': bigger_image(image), 'ts': _ts(node.get('date')),
                    'source': ('%s · %s' % (label, outlet)) if outlet else label})
    return out


# ------------------------------------------------------------------ network
def fetch(url, timeout=8):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'en-US,en;q=0.8',
                                               'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(3 * 1024 * 1024)


def read_source(key, timeout=8):
    for skey, label, kind, url in SOURCES:
        if skey == key:
            data = fetch(url, timeout)
            items = parse_imdb(data, label) if kind == 'imdb' else parse_rss(data, label)
            return items[:PER_SOURCE]
    return []


def _norm(title):
    return re.sub(r'[^a-z0-9؀-ۿ]+', '', str(title or '').lower())[:80]


def merge(groups):
    """Newest first, the same story once, the sources interleaved when undated."""
    seen, out = set(), []
    for group in groups:
        for item in group:
            key = _norm(item.get('title'))
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
    out.sort(key=lambda i: float(i.get('ts') or 0), reverse=True)
    return out[:MAX_ITEMS]


def cached(keys, cache_path):
    """The kept news for these sources, never the network (the window's first paint)."""
    keys = [k for k in keys if k in SOURCE_KEYS] or list(DEFAULT_SOURCES)
    try:
        with open(cache_path, 'r', encoding='utf-8') as handle:
            data = json.load(handle) or {}
    except Exception:
        return []
    return list(data.get('items') or []) if data.get('keys') == keys else []


def load(keys, cache_path, refresh=False, log=None):
    """(items, fresh). The kept news when they are recent enough or the
    network fails; otherwise every chosen source read again."""
    keys = [k for k in keys if k in SOURCE_KEYS] or list(DEFAULT_SOURCES)
    try:
        with open(cache_path, 'r', encoding='utf-8') as handle:
            cached = json.load(handle) or {}
    except Exception:
        cached = {}
    same = cached.get('keys') == keys
    items = list(cached.get('items') or []) if same else []
    fresh = same and time.time() - float(cached.get('t') or 0) < TTL
    if fresh and not refresh:
        return items, True
    groups = []
    for key in keys:
        try:
            groups.append(read_source(key))
        except Exception as exc:
            if log:
                log('news %s failed (%s)' % (key, exc.__class__.__name__))
    found = merge(groups)
    if not found:
        return items, False
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        tmp = '%s.%d.%d.tmp' % (cache_path, os.getpid(), int(time.time() * 1000) % 1000000)
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump({'t': time.time(), 'keys': keys, 'items': found}, handle, ensure_ascii=False)
        os.replace(tmp, cache_path)
    except Exception:
        pass
    return found, True


# ------------------------------------------------------------------ the article
def article_text(page):
    """The paragraphs of an article page (the <article> when it has one)."""
    text = page.decode('utf-8', 'replace') if isinstance(page, bytes) else str(page or '')
    text = _DROP.sub(' ', text)
    scope = text
    match = re.search(r'(?is)<article\b.*?</article>', text)
    if match and len(match.group(0)) > 1500:
        scope = match.group(0)
    paras = []
    for raw in re.findall(r'(?is)<p\b[^>]*>(.*?)</p>', scope):
        para = clean(raw)
        if len(para) < 40 or para.lower().startswith(('sign up', 'subscribe', 'advertisement', 'related:')):
            continue
        if paras and para == paras[-1]:
            continue
        paras.append(para)
    body = '\n\n'.join(paras)
    return body[:14000]


def og_image(page):
    text = page.decode('utf-8', 'replace') if isinstance(page, bytes) else str(page or '')
    for prop in ('og:image', 'twitter:image'):
        match = re.search(r'(?is)<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']+)' % re.escape(prop), text) \
            or re.search(r'(?is)<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']%s["\']' % re.escape(prop), text)
        if match:
            return bigger_image(match.group(1))
    return ''


def full_story(item, timeout=8):
    """(body, image): the feed's full text, else the article page's."""
    body = str(item.get('body') or '')
    image = str(item.get('image') or '')
    if len(body) > 600 and image:
        return body, image
    link = str(item.get('link') or '')
    if link.startswith('http'):
        try:
            page = fetch(link, timeout)
            found = article_text(page)
            if len(found) > len(body):
                body = found
            image = image or og_image(page)
        except Exception:
            pass
    return body or str(item.get('summary') or ''), image
