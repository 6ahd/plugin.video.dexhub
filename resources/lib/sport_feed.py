# -*- coding: utf-8 -*-
"""Sport on the Live TV page (v5.10.453).

The games and tables come from ESPN's public scoreboards. Where a game is on
comes from the guide of the channels in Dex Hub itself (DexWorld, Stremio's
TV add-ons, M3U, Xtream; never Kodi's PVR): only sport groups and channels are
read, and the result is kept for three hours in homeui/sport_guide.json. A channel is "sure"
when the guide names both teams, "likely" with one team at the right time, and
"maybe" with only the competition at the right time.

Followed teams and leagues and the reminders are in homeui/sport.json.
"""
import datetime
import json
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from . import profile_scope as _scope

ESPN = 'https://site.api.espn.com/apis/site/v2/sports/'
ESPN_TABLE = 'https://site.api.espn.com/apis/v2/sports/'
GUIDE_TTL = 3 * 3600.0
GUIDE_BUDGET = 45.0
MAX_CHANNELS = 60
LEAD, TAIL = 45 * 60, 150 * 60
REMIND_BEFORE = 5 * 60
SURE, LIKELY, MAYBE = 3, 2, 1

# key, ESPN path, Arabic, English, the words a guide may use
LEAGUES = (
    ('ksa.1', 'soccer/ksa.1', 'دوري روشن السعودي', 'Saudi Pro League', ('دوري روشن', 'الدوري السعودي', 'roshn', 'saudi pro league')),
    ('ksa.kings.cup', 'soccer/ksa.kings.cup', 'كأس الملك', "King's Cup", ('كأس الملك', 'kings cup', "king's cup")),
    ('afc.champions', 'soccer/afc.champions', 'دوري أبطال آسيا', 'AFC Champions League', ('أبطال آسيا', 'afc champions')),
    ('uefa.champions', 'soccer/uefa.champions', 'دوري أبطال أوروبا', 'Champions League', ('أبطال أوروبا', 'champions league')),
    ('uefa.europa', 'soccer/uefa.europa', 'الدوري الأوروبي', 'Europa League', ('الدوري الأوروبي', 'europa league')),
    ('eng.1', 'soccer/eng.1', 'الدوري الإنجليزي', 'Premier League', ('الدوري الإنجليزي', 'premier league')),
    ('esp.1', 'soccer/esp.1', 'الدوري الإسباني', 'LaLiga', ('الدوري الإسباني', 'laliga', 'la liga')),
    ('ita.1', 'soccer/ita.1', 'الدوري الإيطالي', 'Serie A', ('الدوري الإيطالي', 'serie a')),
    ('ger.1', 'soccer/ger.1', 'الدوري الألماني', 'Bundesliga', ('الدوري الألماني', 'bundesliga')),
    ('fra.1', 'soccer/fra.1', 'الدوري الفرنسي', 'Ligue 1', ('الدوري الفرنسي', 'ligue 1')),
    ('eng.fa', 'soccer/eng.fa', 'كأس الاتحاد الإنجليزي', 'FA Cup', ('كأس الاتحاد الإنجليزي', 'fa cup')),
    ('eng.league_cup', 'soccer/eng.league_cup', 'كأس الرابطة الإنجليزية', 'League Cup', ('كأس الرابطة', 'carabao', 'league cup')),
    ('ita.coppa_italia', 'soccer/ita.coppa_italia', 'كأس إيطاليا', 'Coppa Italia', ('كأس إيطاليا', 'coppa italia')),
    ('esp.copa_del_rey', 'soccer/esp.copa_del_rey', 'كأس ملك إسبانيا', 'Copa del Rey', ('كأس ملك إسبانيا', 'copa del rey')),
    ('fifa.world', 'soccer/fifa.world', 'كأس العالم', 'World Cup', ('كأس العالم', 'world cup')),
    ('nba', 'basketball/nba', 'NBA', 'NBA', ('nba',)),
    ('f1', 'racing/f1', 'الفورمولا 1', 'Formula 1', ('فورمولا', 'formula 1', 'f1')),
    ('ufc', 'mma/ufc', 'UFC', 'UFC', ('ufc',)),
)
LEAGUE_BY_KEY = dict((row[0], row) for row in LEAGUES)
DEFAULT_LEAGUES = ('ksa.1', 'ksa.kings.cup', 'afc.champions', 'uefa.champions', 'uefa.europa', 'eng.1', 'esp.1', 'ita.1',
                   'eng.fa', 'esp.copa_del_rey', 'ita.coppa_italia', 'ger.1', 'fra.1')

TEAMS_AR = {
    # Saudi and the AFC
    'Al Hilal': 'الهلال', 'Al Nassr': 'النصر', 'Al Ittihad': 'الاتحاد', 'Al Ahli': 'الأهلي', 'Al Shabab': 'الشباب',
    'Al Ettifaq': 'الاتفاق', 'Al Taawoun': 'التعاون', 'Al Fateh': 'الفتح', 'Al Fayha': 'الفيحاء', 'Al Raed': 'الرائد',
    'Al Qadsiah': 'القادسية', 'Al Khaleej': 'الخليج', 'Al Riyadh': 'الرياض', 'Al Okhdood': 'الأخدود', 'Al Kholood': 'الخلود',
    'Damac': 'ضمك', 'Al Hazem': 'الحزم', 'Al Najma': 'النجمة', 'Al Faisaly': 'الفيصلي', 'Al Orobah': 'العروبة',
    'Al Wehda': 'الوحدة', 'Al Jabalain': 'الجبلين', 'Abha': 'أبها', 'Al Diriyah': 'الدرعية', 'Neom SC': 'نيوم',
    'Al Sadd': 'السد', 'Al Wasl': 'الوصل', 'Al Ain': 'العين', 'Al-Gharafa': 'الغرافة', 'Pakhtakor': 'باختاكور',
    'Tractor': 'تراكتور', 'Al-Quwa Al-Jawiya': 'القوة الجوية',
    # England
    'AFC Bournemouth': 'بورنموث', 'Arsenal': 'آرسنال', 'Aston Villa': 'أستون فيلا', 'Brentford': 'برينتفورد',
    'Brighton & Hove Albion': 'برايتون', 'Chelsea': 'تشيلسي', 'Coventry City': 'كوفنتري', 'Crystal Palace': 'كريستال بالاس',
    'Everton': 'إيفرتون', 'Fulham': 'فولهام', 'Hull City': 'هال سيتي', 'Ipswich Town': 'إيبسويتش', 'Leeds United': 'ليدز يونايتد',
    'Liverpool': 'ليفربول', 'Manchester City': 'مانشستر سيتي', 'Manchester United': 'مانشستر يونايتد',
    'Newcastle United': 'نيوكاسل', 'Nottingham Forest': 'نوتنغهام فورست', 'Sunderland': 'سندرلاند', 'Tottenham Hotspur': 'توتنهام',
    'Wolverhampton Wanderers': 'وولفرهامبتون', 'West Ham United': 'وست هام', 'Burnley': 'بيرنلي',
    # Spain
    'Alavés': 'ألافيس', 'Athletic Club': 'أتلتيك بيلباو', 'Atlético Madrid': 'أتلتيكو مدريد', 'Barcelona': 'برشلونة',
    'Celta Vigo': 'سيلتا فيغو', 'Deportivo': 'ديبورتيفو', 'Elche': 'إلتشي', 'Espanyol': 'إسبانيول', 'Getafe': 'خيتافي',
    'Levante': 'ليفانتي', 'Málaga': 'مالقة', 'Osasuna': 'أوساسونا', 'Racing Santander': 'راسينغ سانتاندير',
    'Rayo Vallecano': 'رايو فاليكانو', 'Real Betis': 'ريال بيتيس', 'Real Madrid': 'ريال مدريد', 'Real Sociedad': 'ريال سوسيداد',
    'Sevilla': 'إشبيلية', 'Valencia': 'فالنسيا', 'Villarreal': 'فياريال', 'Mallorca': 'مايوركا', 'Girona': 'جيرونا',
    # Italy
    'AC Milan': 'ميلان', 'AS Roma': 'روما', 'Atalanta': 'أتالانتا', 'Bologna': 'بولونيا', 'Cagliari': 'كالياري', 'Como': 'كومو',
    'Fiorentina': 'فيورنتينا', 'Frosinone': 'فروزينوني', 'Genoa': 'جنوى', 'Internazionale': 'إنتر ميلان', 'Juventus': 'يوفنتوس',
    'Lazio': 'لاتسيو', 'Lecce': 'ليتشي', 'Monza': 'مونزا', 'Napoli': 'نابولي', 'Parma': 'بارما', 'Sassuolo': 'ساسولو',
    'Torino': 'تورينو', 'Udinese': 'أودينيزي', 'Venezia': 'فينيسيا', 'Hellas Verona': 'فيرونا', 'Pisa': 'بيزا', 'Cremonese': 'كريمونيزي',
    # Europe
    'Bayern Munich': 'بايرن ميونخ', 'Borussia Dortmund': 'بوروسيا دورتموند', 'Paris Saint-Germain': 'باريس سان جيرمان',
    'Marseille': 'مارسيليا', 'Galatasaray': 'غلطة سراي', 'Benfica': 'بنفيكا', 'Porto': 'بورتو', 'Sporting CP': 'سبورتينغ',
}
SPORT_WORDS = ('sport', 'sports', 'espn', 'bein', 'ssc', 'dazn', 'sky sp', 'eurosport', 'tnt sp', 'fox sports', 'رياض',
               'بي ان', 'بين سبورت', 'ثمانية', 'thmanyah', 'shahid sp', 'شاهد سبورت', 'abu dhabi sp', 'ad sport')
RECAP_WORDS = ('highlights', 'replay', 'repeat', 'review', 'magazine', 'preview', 'studio', 'ملخص', 'إعادة', 'اعادة', 'تحليل',
               'استديو', 'أهداف الأسبوع')
_NOISE = {'fc', 'cf', 'sc', 'afc', 'club', 'al', 'the', 'de', 'ac', 'as', 'cd', 'fk', 'sk', 'ال', 'نادي'}
_LOCK = threading.Lock()
_guide_lock = threading.Lock()
_memory = {}


def english():
    try:
        from .i18n import is_english
        return bool(is_english())
    except Exception:
        return False


def _t(ar, en):
    return en if english() else ar


def _log(msg):
    import xbmc
    xbmc.log('[DexHub] sport: %s' % msg, xbmc.LOGINFO)


def league_name(key):
    row = LEAGUE_BY_KEY.get(key)
    return (row[3] if english() else row[2]) if row else key


def team_name(name):
    return name if english() else TEAMS_AR.get(name, name)


# ---------------------------------------------------------------- matching (no Kodi in here)
def norm(text):
    text = unicodedata.normalize('NFKD', str(text or ''))
    text = ''.join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = text.replace('أ', 'ا').replace('إ', 'ا').replace('آ', 'ا').replace('ة', 'ه').replace('ى', 'ي')
    return re.sub(r'[^\w]+', ' ', text).strip()


def has_phrase(text, phrase):
    return bool(phrase) and re.search(r'(?<!\w)%s(?!\w)' % re.escape(phrase), text) is not None


def team_words(name):
    """Every way a guide may write a team, normalised."""
    out = []
    for source in (name, TEAMS_AR.get(name, '')):
        full = norm(source)
        if not full:
            continue
        out.append(full)
        words = [w for w in full.split() if w not in _NOISE]
        if words and ' '.join(words) != full:
            out.append(' '.join(words))
        if source == TEAMS_AR.get(name) and full.startswith('ال') and len(full) > 4:
            out.append(full[2:])
    return list(dict.fromkeys(out))


def is_sport_channel(name):
    low = str(name or '').lower()
    return any(word in low for word in SPORT_WORDS)


def score_programme(event, title, plot, start, end):
    """How surely this programme shows the game (SURE, LIKELY, MAYBE, 0)."""
    kick = int(event.get('start') or 0)
    if end <= kick - LEAD or start >= kick + TAIL:
        return 0
    head = norm(title)
    if any(has_phrase(head, norm(word)) for word in RECAP_WORDS):
        return 0
    text = '%s %s' % (head, norm(plot))
    sides = [event.get('home') or {}, event.get('away') or {}]
    named = [any(has_phrase(text, word) for word in team_words(side.get('name'))) for side in sides if side.get('name')]
    if len(named) == 2 and all(named):
        return SURE
    league = LEAGUE_BY_KEY.get(event.get('league'))
    has_league = bool(league) and any(has_phrase(text, norm(word)) for word in league[4])
    near = abs(start - kick) <= LEAD
    if any(named) and (has_league or near):
        return LIKELY
    if has_league and near:
        return MAYBE
    if not sides[0].get('name') and event.get('title') and has_phrase(text, norm(event['title'])):
        return LIKELY
    return 0


def channels_for(event, index):
    """[{tile, name, score, programme}] of the channels showing a game, surest first."""
    found = []
    for channel in index or []:
        best, shown = 0, ''
        for item in channel.get('items') or []:
            score = score_programme(event, item[0], item[1], item[2], item[3])
            if score > best:
                best, shown = score, item[0]
        if best:
            found.append({'tile': channel['tile'], 'name': channel['tile'].get('title') or '', 'score': best,
                          'programme': shown})
    found.sort(key=lambda row: (-row['score'], row['name'].lower()))
    return found[:8]


# ---------------------------------------------------------------- ESPN
def _http(url, timeout=10):
    """ESPN's JSON. v5.10.457: in the service the shared connection pool kept
    failing every read (the island had no games all day) while a fresh request
    worked, so a failed read is asked once more without the pool."""
    from .homeui import live_sources as LS
    try:
        return LS.http_json(url, timeout=timeout)
    except LS.SourceError as exc:
        first = str(exc)
    from urllib.request import Request, urlopen
    try:
        request = Request(url, headers={'User-Agent': LS.UA, 'Accept': 'application/json'})
        with urlopen(request, timeout=timeout) as handle:
            return json.loads(handle.read().decode('utf-8-sig', 'replace') or 'null')
    except Exception as exc:
        raise LS.SourceError('%s / %s' % (first, LS._describe(exc))) from None


def _epoch(value):
    try:
        return int(datetime.datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp())
    except (ValueError, TypeError, OverflowError):
        return 0


def _side(comp):
    team = comp.get('team') or comp.get('athlete') or {}
    logo = team.get('logo') or ''
    if not logo and team.get('logos'):
        logo = (team['logos'][0] or {}).get('href') or ''
    return {'id': str(team.get('id') or comp.get('id') or ''), 'name': team.get('displayName') or team.get('fullName') or '',
            'short': team.get('shortDisplayName') or team.get('abbreviation') or '', 'logo': logo,
            'color': team.get('color') or '', 'score': str(comp.get('score') or ''),
            'winner': bool(comp.get('winner'))}


def parse_scoreboard(league, data):
    out = []
    for raw in (data or {}).get('events') or []:
        comps = raw.get('competitions') or [{}]
        comp = comps[0] if isinstance(comps[0], dict) else {}
        status = raw.get('status') or comp.get('status') or {}
        kind = status.get('type') or {}
        rows = comp.get('competitors') or []
        home = next((c for c in rows if c.get('homeAway') == 'home'), None)
        away = next((c for c in rows if c.get('homeAway') == 'away'), None)
        broadcasts = []
        for block in comp.get('broadcasts') or []:
            broadcasts += [str(n) for n in block.get('names') or []]
        start = _epoch(raw.get('date') or comp.get('date'))
        if not start or not raw.get('id'):
            continue
        out.append({'id': '%s:%s' % (league, raw['id']), 'league': league, 'start': start,
                    'state': kind.get('state') or 'pre', 'status': kind.get('shortDetail') or kind.get('detail') or '',
                    'clock': status.get('displayClock') or '', 'title': raw.get('name') or '',
                    'home': _side(home) if home else {}, 'away': _side(away) if away else {},
                    'venue': (comp.get('venue') or {}).get('fullName') or '', 'broadcasts': broadcasts})
    return out


STATS = (('possessionPct', 'الاستحواذ', 'Possession', '%'), ('totalShots', 'التسديدات', 'Shots', ''),
         ('shotsOnTarget', 'على المرمى', 'On target', ''), ('wonCorners', 'الركنيات', 'Corners', ''),
         ('accuratePasses', 'تمريرات صحيحة', 'Accurate passes', ''), ('saves', 'التصديات', 'Saves', ''),
         ('foulsCommitted', 'الأخطاء', 'Fouls', ''), ('offsides', 'التسلل', 'Offsides', ''),
         ('yellowCards', 'بطاقات صفراء', 'Yellow cards', ''), ('redCards', 'بطاقات حمراء', 'Red cards', ''))


def _event_kind(code):
    code = str(code or '').lower()
    if 'own' in code and 'goal' in code:
        return 'own'
    if 'penalty' in code and 'scored' in code:
        return 'penalty'
    if 'goal' in code:
        return 'goal'
    if 'yellow' in code:
        return 'yellow'
    if 'red' in code:
        return 'red'
    if 'substitution' in code:
        return 'sub'
    return ''


def parse_game(data):
    """ESPN's summary of one game: the two sides, the statistics side by side
    and what happened, in order. Parts ESPN has not got yet stay empty."""
    data = data or {}
    comp = (((data.get('header') or {}).get('competitions')) or [{}])[0]
    rows = comp.get('competitors') or []
    home = next((c for c in rows if c.get('homeAway') == 'home'), None)
    away = next((c for c in rows if c.get('homeAway') == 'away'), None)
    sides = {'home': _side(home) if home else {}, 'away': _side(away) if away else {}}
    kind = (comp.get('status') or {}).get('type') or {}
    numbers = {}
    for team in (data.get('boxscore') or {}).get('teams') or []:
        tid = str((team.get('team') or {}).get('id') or '')
        side = 'home' if tid == sides['home'].get('id') else 'away'
        numbers[side] = dict((row.get('name'), str(row.get('displayValue') or '')) for row in team.get('statistics') or [])
    stats = []
    for key, ar, en, unit in STATS:
        left, right = (numbers.get('home') or {}).get(key), (numbers.get('away') or {}).get(key)
        if left is None and right is None:
            continue
        try:
            a, b = float(left or 0), float(right or 0)
        except ValueError:
            a = b = 0.0
        whole = (a + b) or 1.0
        stats.append({'name': _t(ar, en), 'home': (left or '0') + unit, 'away': (right or '0') + unit,
                      'home_step': int(round(a / whole * 20)), 'away_step': int(round(b / whole * 20))})
    events = []
    for row in data.get('keyEvents') or []:
        what = _event_kind((row.get('type') or {}).get('type'))
        if not what:
            continue
        people = [((p.get('athlete') or {}).get('displayName') or '') for p in row.get('participants') or []]
        people = [name for name in people if name]
        tid = str((row.get('team') or {}).get('id') or '')
        events.append({'minute': (row.get('clock') or {}).get('displayValue') or '', 'kind': what,
                       'side': 'home' if tid == sides['home'].get('id') else 'away',
                       'text': ' ← '.join(people) if what == 'sub' else (people[0] if people else row.get('shortText') or '')})
    return {'home': sides['home'], 'away': sides['away'], 'state': kind.get('state') or 'pre',
            'status': kind.get('shortDetail') or kind.get('detail') or '',
            'venue': ((data.get('gameInfo') or {}).get('venue') or {}).get('fullName') or '',
            'stats': stats, 'events': events}


def game_detail(game):
    """One game's page, read from ESPN each time: it is open only for a moment."""
    league, _sep, event = str(game.get('id') or '').partition(':')
    path = (LEAGUE_BY_KEY.get(league) or (None, None))[1]
    if not path or not event:
        return {}
    return parse_game(_http('%s%s/summary?event=%s' % (ESPN, path, event)))


def _fetch_day(key, day):
    return parse_scoreboard(key, _http('%s%s/scoreboard?dates=%s&limit=200' % (ESPN, LEAGUE_BY_KEY[key][1], day.strftime('%Y%m%d'))))


def _games_cached(key, day):
    ident = (key, day.isoformat())
    with _LOCK:
        known = _memory.get(ident)
    now = time.time()
    if known and now - known[0] < known[1]:
        return known[2]
    try:
        games = _fetch_day(key, day)
    except Exception as exc:
        _log('%s: %s %s' % (key, exc.__class__.__name__, exc))
        return known[2] if known else []
    today = datetime.date.today()
    if any(game['state'] == 'in' for game in games):
        ttl = 45.0
    elif abs((day - today).days) <= 1:
        ttl = 300.0
    else:
        ttl = 1800.0
    with _LOCK:
        _memory[ident] = (now, ttl, games)
    return games


def games_between(first, last, keys, wide=True):
    """The games of these leagues between two local days, by kick-off.

    ESPN's days are not ours (a late game is on its next day here), and it
    takes one day per request: a day more on each side, a request per league
    and day, each kept a while."""
    span = (last - first).days + (3 if wide else 1)
    jobs = [(key, first + datetime.timedelta(days=n - (1 if wide else 0))) for key in keys for n in range(span)]
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix='DexHubSport') as pool:
        parts = list(pool.map(lambda job: _games_cached(job[0], job[1]), jobs))
    out, seen = [], set()
    for part in parts:
        for game in part:
            day = datetime.date.fromtimestamp(game['start'])
            if first <= day <= last and game['id'] not in seen:
                seen.add(game['id'])
                out.append(game)
    out.sort(key=lambda g: (0 if g['state'] == 'in' else 1, g['start'], g['league']))
    return out


def parse_table(data):
    block = data or {}
    children = block.get('children') or [block]
    rows = []
    for child in children:
        for entry in ((child.get('standings') or {}).get('entries') or []):
            team = entry.get('team') or {}
            stats = dict((s.get('name'), s.get('displayValue')) for s in entry.get('stats') or [] if isinstance(s, dict))
            logos = team.get('logos') or [{}]
            rows.append({'id': str(team.get('id') or ''), 'name': team.get('displayName') or '',
                         'logo': (logos[0] or {}).get('href') or '', 'rank': stats.get('rank') or str(len(rows) + 1),
                         'played': stats.get('gamesPlayed') or '', 'won': stats.get('wins') or '',
                         'drawn': stats.get('ties') or '', 'lost': stats.get('losses') or '',
                         'diff': stats.get('pointDifferential') or '', 'points': stats.get('points') or '',
                         'group': child.get('name') or ''})
        if rows and len(children) > 1 and child is children[-1]:
            break
    return rows


def table(key):
    ident = ('table', key)
    with _LOCK:
        known = _memory.get(ident)
    if known and time.time() - known[0] < 1800:
        return known[2]
    try:
        rows = parse_table(_http('%s%s/standings' % (ESPN_TABLE, LEAGUE_BY_KEY[key][1])))
    except Exception as exc:
        _log('table %s: %s %s' % (key, exc.__class__.__name__, exc))
        return known[2] if known else []
    with _LOCK:
        _memory[ident] = (time.time(), 1800, rows)
    return rows


def teams(key):
    """A league's teams for the follow page: its table's order, else ESPN's list."""
    rows = [{'id': r['id'], 'name': r['name'], 'logo': r['logo']} for r in table(key) if r['id']]
    if rows:
        return rows
    try:
        data = _http('%s%s/teams?limit=200' % (ESPN, LEAGUE_BY_KEY[key][1]))
        leagues = (data.get('sports') or [{}])[0].get('leagues') or [{}]
        for row in leagues[0].get('teams') or []:
            team = row.get('team') or {}
            logos = team.get('logos') or [{}]
            rows.append({'id': str(team.get('id') or ''), 'name': team.get('displayName') or '',
                         'logo': (logos[0] or {}).get('href') or ''})
    except Exception as exc:
        _log('teams %s: %s' % (key, exc.__class__.__name__))
    return rows


# ---------------------------------------------------------------- what is followed
def _store_path():
    return _scope.path('homeui/sport.json')


def load():
    try:
        with open(_store_path(), encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    leagues = [key for key in data.get('leagues') or [] if key in LEAGUE_BY_KEY]
    data['leagues'] = leagues if 'leagues' in data else list(DEFAULT_LEAGUES)
    data.setdefault('teams', {})
    data.setdefault('reminders', {})
    return data


def save(data):
    _scope.require_current()
    path = _store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False)
    os.replace(tmp, path)


def toggle_team(team, league):
    data = load()
    if team['id'] in data['teams']:
        del data['teams'][team['id']]
        state = False
    else:
        data['teams'][team['id']] = {'name': team.get('name') or '', 'logo': team.get('logo') or '', 'league': league}
        state = True
    save(data)
    import xbmcgui
    xbmcgui.Window(10000).setProperty(REFRESH_PROP, '1')
    return state


def toggle_league(key):
    data = load()
    if key in data['leagues']:
        data['leagues'].remove(key)
        state = False
    else:
        data['leagues'].append(key)
        state = True
    save(data)
    return state


def is_mine(game, data):
    sides = (game.get('home') or {}, game.get('away') or {})
    return any(side.get('id') and side['id'] in data['teams'] for side in sides)


def mine(days=14):
    """The next games of the followed teams (and their live ones)."""
    data = load()
    keys = list(dict.fromkeys(row.get('league') for row in data['teams'].values() if row.get('league') in LEAGUE_BY_KEY))
    if not keys:
        return []
    today = datetime.date.today()
    games = games_between(today, today + datetime.timedelta(days=days), keys)
    return [game for game in games if is_mine(game, data)]


def toggle_reminder(game):
    data = load()
    if game['id'] in data['reminders']:
        del data['reminders'][game['id']]
        state = False
    else:
        data['reminders'][game['id']] = {'start': game['start'], 'line': game_line(game)}
        state = True
    save(data)
    return state


def game_line(game):
    home, away = game.get('home') or {}, game.get('away') or {}
    if home.get('name') and away.get('name'):
        return '%s × %s' % (team_name(home['name']), team_name(away['name']))
    return game.get('title') or ''


# ---------------------------------------------------------------- the channels' guide
def _guide_path():
    return _scope.path('homeui/sport_guide.json')


def guide_read():
    from .homeui import live_sources as LS
    data = LS.read_json(_guide_path(), {})
    return data if isinstance(data, dict) else {}


def guide_stale(data=None):
    data = guide_read() if data is None else data
    return not data or time.time() - float(data.get('t') or 0) > GUIDE_TTL


def _dexworld_channels(app, source):
    """The sport groups of a DexWorld or Stremio source; only when it has none, the channels by name."""
    from .homeui import live_stremio as ST
    from .homeui import live_sources as LS
    card = app.media_path('channel_card.jpg')
    specs = ST.rows(source, app)
    sport = [spec for spec in specs if is_sport_channel(spec.get('title'))]
    out, seen = [], set()
    for spec in sport or specs[-1:]:
        tiles = ST.catalog_tiles(spec['params'], card, count=MAX_CHANNELS * 4)[0]
        for tile in tiles or []:
            if tile['path'] in seen or not (sport or is_sport_channel(tile.get('title'))):
                continue
            seen.add(tile['path'])
            out.append((tile, lambda t=tile: LS.guide(t, app)))
    return out


def _m3u_channels(app, source):
    from .homeui import live_iptv as I
    conf = source['conf']
    data = I.playlist(app.profile, conf)
    guide_data = I._epg_data(app.profile, conf)
    groups = data.get('groups') or []
    card = app.media_path('channel_card.jpg')
    out = []
    for raw in data.get('channels') or []:
        channel = (list(raw) + [''] * 11)[:11]
        group = groups[channel[2]] if isinstance(channel[2], int) and channel[2] < len(groups) else ''
        if is_sport_channel(channel[0]) or is_sport_channel(group):
            tile = I._m3u_tile(source['key'], channel, card, group, guide_data)
            out.append((tile, lambda t=tile: I._m3u_guide_items(guide_data, t.get('epg') or {})))
    return out


def _xtream_channels(app, source):
    from .homeui import live_iptv as I
    conf = source['conf']
    card = app.media_path('channel_card.jpg')
    out = []
    for cat in I.categories(app.profile, conf):
        name = str(cat.get('name') or '')
        whole = is_sport_channel(name)
        for item in I.streams(app.profile, conf, cat['id']):
            title, icon, sid, number, _epg = (list(item) + [''] * 5)[:5]
            if whole or is_sport_channel(title):
                params = {'live_src': source['key'], 'category': cat['id']}
                tile = I.LS.channel_tile(source['key'], sid, title, card, logo=icon, group=name, number=number,
                                         stream={'id': sid}, epg={'xt': sid})
                tile['params'] = params
                out.append((tile, lambda s=sid: I.xt_short_epg(conf, s, limit=40)))
    return out


@_scope.bound
def guide_refresh(app, force=False, progress=None):
    """Read the sport channels' guide of every source. Keeps the last one on failure."""
    from .homeui import live_sources as LS
    if not _guide_lock.acquire(False):
        return guide_read()
    try:
        old = guide_read()
        if not force and not guide_stale(old):
            return old
        jobs = []
        for source in LS.sources(app):
            kind = source.get('kind')
            try:
                if kind == 'stremio':
                    jobs += _dexworld_channels(app, source)
                elif kind == 'm3u':
                    jobs += _m3u_channels(app, source)
                elif kind == 'xtream':
                    jobs += _xtream_channels(app, source)
            except Exception as exc:
                _log('%s channels: %s' % (kind, LS.safe(exc)))
        jobs = jobs[:MAX_CHANNELS]
        deadline = time.monotonic() + GUIDE_BUDGET
        now = time.time()
        done = [0]

        def one(job):
            tile, reader = job
            items = []
            if time.monotonic() < deadline:
                try:
                    for prog in reader() or []:
                        if prog.get('end', 0) > now:
                            items.append([prog.get('title') or '', prog.get('plot') or '', int(prog.get('start') or 0),
                                          int(prog.get('end') or 0)])
                except Exception:
                    pass
            done[0] += 1
            if progress:
                progress(done[0], len(jobs))
            return {'tile': dict((k, v) for k, v in tile.items() if k not in ('now', 'next')), 'items': items[:60]}
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix='DexHubSportGuide') as pool:
            channels = list(pool.map(_scope.capture(one), jobs))
        channels = [row for row in channels if row]
        if not channels and old.get('channels'):
            return old
        data = {'t': time.time(), 'channels': channels}
        LS.write_json(_guide_path(), data)
        return data
    except Exception as exc:
        _log('guide: %s' % exc.__class__.__name__)
        return guide_read()
    finally:
        _guide_lock.release()


# ---------------------------------------------------------------- reminders
def due_reminders(data, now):
    """(game ids to remind now, game ids long past)."""
    due, gone = [], []
    for ident, row in (data.get('reminders') or {}).items():
        start = int(row.get('start') or 0)
        if now > start + 3 * 3600:
            gone.append(ident)
        elif now >= start - REMIND_BEFORE:
            due.append(ident)
    return due, gone


# ---------------------------------------------------------------- the island
TOP_LEAGUES = ('ksa.1', 'eng.1', 'esp.1', 'ita.1')    # the island's results when no team is followed
TOP_ROWS = 8
ISLAND_PROP = 'dhs.sport.list'
REFRESH_PROP = 'dhs.sport.refresh'
RESULT_HOURS = 48
NOTICE_HOURS = 3


def island_rows(games, now):
    """The followed teams' games for the island: live, then coming today or tomorrow, then results."""
    live, coming, done = [], [], []
    for game in games:
        home, away = game.get('home') or {}, game.get('away') or {}
        row = {'id': game['id'], 'state': game.get('state') or 'pre', 'start': game['start'], 'league': game['league'],
               'clock': game.get('clock') or game.get('status') or '', 'home': home.get('name') or '',
               'away': away.get('name') or '', 'homelogo': home.get('logo') or '', 'awaylogo': away.get('logo') or '',
               'homescore': home.get('score') or '', 'awayscore': away.get('score') or ''}
        if row['state'] == 'in':
            live.append(row)
        elif row['state'] == 'post':
            if now - row['start'] <= RESULT_HOURS * 3600:
                done.append(row)
        else:
            coming.append(row)
    done.sort(key=lambda r: -r['start'])
    return live + coming + done


def score_line(row):
    if row.get('state') == 'pre':
        return '%s × %s' % (team_name(row['home']), team_name(row['away']))
    return '%s  %s - %s  %s' % (team_name(row['home']), row.get('homescore') or '0', row.get('awayscore') or '0',
                                team_name(row['away']))


def island_notices(rows, now):
    """What the bell says: a game of yours live, or ended in the last hours."""
    out = []
    for row in rows or []:
        over = row.get('state') == 'post'
        if not row.get('mine') or (not over and row.get('state') != 'in'):
            continue
        if over and now - int(row.get('start') or 0) > NOTICE_HOURS * 3600 + 2 * 3600:
            continue
        kicker = _t('انتهت المباراة', 'Full time') if over else _t('مباراتك مباشرة', 'Your game is live')
        sub = league_name(row.get('league')) + ('' if over else '  ·  %s' % (row.get('clock') or ''))
        out.append({'kind': 'sport', 'key': 'sport|%s|%s' % (row.get('id'), row.get('state')), 'kicker': kicker,
                    'title': score_line(row), 'sub': sub, 'poster': row.get('homelogo') or '',
                    'action': 'plugin://plugin.video.dexhub/?action=home_sport'})
    return out


def _island_tick(home, last):
    """Read the followed teams' games. Returns seconds to the next read; nothing is asked without a team."""
    data = load()
    keys = list(dict.fromkeys(row.get('league') for row in data['teams'].values() if row.get('league') in LEAGUE_BY_KEY))
    today = datetime.date.today()
    if keys:
        games = games_between(today - datetime.timedelta(days=2), today + datetime.timedelta(days=1), keys)
        rows = [dict(row, mine=True) for row in island_rows([game for game in games if is_mine(game, data)], time.time())]
    else:
        # nobody followed yet: today's games of the big leagues, one request each
        rows = island_rows(games_between(today, today, TOP_LEAGUES, wide=False), time.time())[:TOP_ROWS]
    text = json.dumps(rows, ensure_ascii=False) if rows else ''
    if text != last[0]:
        last[0] = text
        home.setProperty(ISLAND_PROP, text)
        home.setProperty('dhs.panel.rev', str(int(time.time())))
        try:
            from . import island
            island.changed()
        except Exception:
            pass
    now = time.time()
    if any(row['state'] == 'in' or (row['state'] == 'pre' and row['start'] - now < 900) for row in rows):
        return 60.0
    return 600.0


def run(monitor):
    """The service's thread: the reminders of the games the user asked for, and the island's results."""
    import xbmc
    import xbmcgui
    if monitor.waitForAbort(60.0):
        return
    home = xbmcgui.Window(10000)
    last, next_read = [''], 0.0
    while not monitor.abortRequested():
        try:
            from . import profile_scope
            asked = home.getProperty(REFRESH_PROP)
            if asked or time.monotonic() >= next_read:
                home.clearProperty(REFRESH_PROP)
                with profile_scope.bind():
                    next_read = time.monotonic() + _island_tick(home, last)
        except Exception as exc:
            next_read = time.monotonic() + 300.0
            _log('island: %s' % exc.__class__.__name__)
        try:
            from . import profile_scope
            with profile_scope.bind():
                data = load()
                due, gone = due_reminders(data, time.time())
                for ident in due:
                    row = data['reminders'].pop(ident)
                    if not xbmc.getCondVisibility('Player.HasVideo'):
                        xbmcgui.Dialog().notification(_t('🔔 مباراتك بدأت', '🔔 Your game is starting'), row.get('line') or '',
                                                      time=10000, sound=True)
                for ident in gone:
                    data['reminders'].pop(ident, None)
                if due or gone:
                    save(data)
        except Exception as exc:
            _log('reminders: %s' % exc.__class__.__name__)
        if monitor.waitForAbort(30.0):
            return
