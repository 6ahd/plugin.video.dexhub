# -*- coding: utf-8 -*-
import re
from urllib.parse import urlparse


def size_to_bytes(value):
    try:
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value or '').strip().upper().replace('GIB', 'GB').replace('MIB', 'MB')
        m = re.search(r'(\d+(?:\.\d+)?)\s*(GB|MB|KB|TB)', text)
        if not m:
            return 0
        num = float(m.group(1))
        unit = m.group(2)
        mult = {'KB': 1024, 'MB': 1024**2, 'GB': 1024**3, 'TB': 1024**4}.get(unit, 1)
        return int(num * mult)
    except Exception:
        return 0


def quality_rank(value):
    """Return quality tier (0-4) from entry dict or text blob."""
    if isinstance(value, dict):
        value = entry_text_blob(value)
    q = str(value or '').upper()
    if any(token in q for token in ('2160', '4K', 'UHD')):
        return 4
    if any(token in q for token in ('1080', 'FHD')):
        return 3
    if '720' in q:
        return 2
    if any(token in q for token in ('480', 'SD')):
        return 1
    return 0

def entry_text_blob(entry):
    return ' '.join([
        str(entry.get('quality') or ''),
        str(entry.get('badges') or ''),
        str(entry.get('name') or ''),
        str(entry.get('label2') or ''),
        str(entry.get('source_info') or ''),
        str(entry.get('addon') or ''),
        str(entry.get('source_site') or ''),
        str(entry.get('provider_name_raw') or ''),
        str(entry.get('provider') or ''),
    ]).upper()


def is_cam_entry(entry):
    blob = ' %s ' % entry_text_blob(entry)
    return any(token in blob for token in (' CAM ', ' CAMRIP ', ' HDCAM ', ' TS ', ' TELESYNC ', ' HDTS ', ' TC ', ' TELECINE ', ' SCREENER '))


def is_debrid_entry(entry):
    badge = str(entry.get('provider') or '').upper()
    return badge in ('RD+', 'AD+', 'PM', 'TB', 'ED+')


def entry_matches_last_source(entry, pref):
    if not pref:
        return False
    provider_name = str((pref or {}).get('provider_name') or '').strip().lower()
    provider_id = str((pref or {}).get('provider_id') or '').strip().lower()
    host = str((pref or {}).get('stream_url_host') or '').strip().lower()
    hay = ' '.join([
        str(entry.get('provider_name_raw') or '').lower(),
        str(entry.get('provider') or '').lower(),
        str(entry.get('addon') or '').lower(),
        str(entry.get('source_site') or '').lower(),
        str(entry.get('name') or '').lower(),
        str(entry.get('source_info') or '').lower(),
    ])
    if provider_name and provider_name in hay:
        return True
    if provider_id and provider_id in hay:
        return True
    stream_url = str(entry.get('stream_url') or '').strip()
    entry_host = (urlparse(stream_url).netloc or '').lower() if stream_url else ''
    if host and entry_host and host == entry_host:
        return True
    return bool(host and host in hay)


def _min_quality_required_rank(raw):
    mapping = {
        'بدون فلترة': 0, 'no filter': 0,
        '480p فأعلى': 1, '480p+': 1,
        '720p فأعلى': 2, '720p+': 2,
        '1080p فأعلى': 3, '1080p+': 3,
        '4k فقط': 4, '4k only': 4,
    }
    return mapping.get(str(raw or 'No filter').strip().lower(), 0)


# v3.9.90: Quality Profile presets. New users were overwhelmed by 5
# independent toggles (min_quality + hide_cam_ts + prefer_debrid +
# prefer_hdr + prefer_atmos) and had no idea what combinations made
# sense. A single profile dropdown replaces them with 3 sane presets
# plus a Custom mode that reveals the original toggles.
#
# When the profile is not Custom, the preset values WIN over whatever
# the individual toggles are set to — the toggles are still readable
# but ignored at runtime so the user always gets the behavior the
# profile name promises. The settings dialog also disables the 5
# toggles unless Custom is active so this is clear visually.
_QUALITY_PROFILES = {
    'best': {
        'min_quality':   '4K only',
        'hide_cam_ts':   True,
        'prefer_debrid': True,
        'prefer_hdr':    True,
        'prefer_atmos':  True,
    },
    'balanced': {
        'min_quality':   '1080p+',
        'hide_cam_ts':   True,
        'prefer_debrid': True,
        'prefer_hdr':    False,
        'prefer_atmos':  False,
    },
    'data_saver': {
        'min_quality':   '720p+',
        'hide_cam_ts':   True,
        'prefer_debrid': False,
        'prefer_hdr':    False,
        'prefer_atmos':  False,
    },
}

# Localized labels the user may see in the dropdown -> canonical key.
# The dropdown stores the displayed string, so we normalize both Arabic
# and English variants here.
_PROFILE_ALIASES = {
    'best': 'best',
    'أعلى جودة': 'best',
    'أعلى جوده': 'best',
    'balanced': 'balanced',
    'balanced (recommended)': 'balanced',
    'متوازن': 'balanced',
    'متوازن (مستحسن)': 'balanced',
    'data saver': 'data_saver',
    'data_saver': 'data_saver',
    'موفر بيانات': 'data_saver',
    'موفّر بيانات': 'data_saver',
    'موفّر للبيانات': 'data_saver',
    'custom': 'custom',
    'مخصص': 'custom',
    'مخصّص': 'custom',
    # v5.10.292: the add-on's own order, as Nuvio shows it
    'like nuvio': 'nuvio',
    'like nuvio (add-on order)': 'nuvio',
    'مثل nuvio': 'nuvio',
    'مثل nuvio (ترتيب الإضافة)': 'nuvio',
}


def _resolve_quality_profile(raw):
    key = (raw or '').strip().lower()
    return _PROFILE_ALIASES.get(key, 'balanced')


def source_settings(addon):
    # v3.9.103: "Show all sources, no filtering" master switch. When on, this
    # returns a settings dict with every filter neutralised — sources still get
    # ranked (so good ones float to the top) but nothing is dropped. Useful for
    # debugging, hunting rare versions, or when a stricter filter accidentally
    # hides what the user wanted to see. Ranking-only preferences (prefer_hdr,
    # prefer_atmos, source priority) are kept so the list still feels ordered.
    def _bool_top(name, default=False):
        try:
            raw = (addon.getSetting(name) or ('true' if default else 'false')).strip().lower()
        except Exception:
            raw = 'true' if default else 'false'
        return raw in ('true', '1', 'yes', 'on')

    if _bool_top('bypass_all_filters', False):
        priority_order = []
        try:
            raw = addon.getSetting('source_priority_order') or ''
            priority_order = [p.strip().lower() for p in raw.split(',') if p.strip()]
        except Exception:
            priority_order = []
        if not priority_order:
            try:
                from .dexhub import store as _store
                for prov in (_store.list_providers() or []):
                    name = (prov.get('name') or prov.get('id') or '').strip().lower()
                    if name:
                        priority_order.append(name)
            except Exception:
                pass
        return {
            'hide_cam_ts':           False,
            'prefer_debrid':         _bool_top('prefer_debrid', True),
            'prefer_hdr':            _bool_top('prefer_hdr', True),
            'prefer_atmos':          _bool_top('prefer_atmos', True),
            'min_quality_rank':      0,
            'source_priority_order': priority_order,
            'quality_profile':       'bypass',
            'bypass_all_filters':    True,
        }

    # v3.9.90: read the profile first. If it's a preset, the preset values
    # override the individual toggles entirely. If it's Custom, fall
    # through to the original per-toggle behavior.
    try:
        profile_raw = addon.getSetting('quality_profile') or 'Balanced (recommended)'
    except Exception:
        profile_raw = 'Balanced (recommended)'
    profile_key = _resolve_quality_profile(profile_raw)

    def _bool(name, default=False):
        try:
            raw = (addon.getSetting(name) or ('true' if default else 'false')).strip().lower()
        except Exception:
            raw = 'true' if default else 'false'
        return raw in ('true', '1', 'yes', 'on')

    if profile_key == 'nuvio':
        # v5.10.292: Like Nuvio keeps each add-on's order (AIOStreams sorted
        # by its own rules); CAM/TS stays as the add-on returned it
        preset = dict(_QUALITY_PROFILES['balanced'], hide_cam_ts=False, min_quality='No filter')
        min_quality   = preset['min_quality']
        hide_cam_ts   = preset['hide_cam_ts']
        prefer_debrid = preset['prefer_debrid']
        prefer_hdr    = preset['prefer_hdr']
        prefer_atmos  = preset['prefer_atmos']
    elif profile_key != 'custom':
        preset = _QUALITY_PROFILES.get(profile_key, _QUALITY_PROFILES['balanced'])
        min_quality   = preset['min_quality']
        hide_cam_ts   = preset['hide_cam_ts']
        prefer_debrid = preset['prefer_debrid']
        prefer_hdr    = preset['prefer_hdr']
        prefer_atmos  = preset['prefer_atmos']
    else:
        try:
            min_quality = addon.getSetting('min_quality') or 'No filter'
        except Exception:
            min_quality = 'No filter'
        hide_cam_ts   = _bool('hide_cam_ts', True)
        prefer_debrid = _bool('prefer_debrid', True)
        prefer_hdr    = _bool('prefer_hdr', True)
        prefer_atmos  = _bool('prefer_atmos', True)

    # v3.9.34: priority order now comes from the installed providers list
    # itself (Sources page), so the user can drag-reorder via context
    # menu instead of typing names. The text setting still works as an
    # advanced override — if set, it takes precedence over installed
    # order. Empty/unset = use installed order.
    priority_order = []
    try:
        raw = addon.getSetting('source_priority_order') or ''
        priority_order = [p.strip().lower() for p in raw.split(',') if p.strip()]
    except Exception:
        priority_order = []
    if not priority_order:
        # Fall back to the order of providers as listed in the Sources
        # page. This is what the user sees and reorders directly.
        try:
            from .dexhub import store as _store
            for prov in (_store.list_providers() or []):
                name = (prov.get('name') or prov.get('id') or '').strip().lower()
                if name:
                    priority_order.append(name)
        except Exception:
            pass

    # v3.9.263: device-capability exclusions (POV-style). Opt-in; a device
    # that can't decode AV1 / Dolby Vision / HEVC hides those rows so every
    # visible source actually plays. Personal servers are never excluded.
    codec_exclusions = set()
    if _bool('exclude_av1', False):
        codec_exclusions.add('AV1')
    if _bool('exclude_hevc', False):
        codec_exclusions.add('HEVC')
    dr_exclusions = set()
    if _bool('exclude_dv', False):
        dr_exclusions.add('DV')

    return {
        'hide_cam_ts':           hide_cam_ts,
        'prefer_debrid':         prefer_debrid,
        'prefer_hdr':            prefer_hdr,
        'prefer_atmos':          prefer_atmos,
        'min_quality_rank':      _min_quality_required_rank(min_quality),
        'source_priority_order': priority_order,
        'quality_profile':       profile_key,
        'codec_exclusions':      codec_exclusions,
        'dr_exclusions':         dr_exclusions,
        'order_mode':            'addon' if profile_key == 'nuvio' else 'ranked',
    }


def get_effective_min_quality(addon):
    """v3.9.90: helper for callers (currently plugin._min_quality_required_rank)
    that need only the resolved min_quality rank without paying for the
    full source_settings build. Respects the active profile.

    v3.9.103: also honors the bypass_all_filters master switch — when on,
    no min-quality floor is enforced anywhere."""
    try:
        bypass = (addon.getSetting('bypass_all_filters') or 'false').strip().lower()
        if bypass in ('true', '1', 'yes', 'on'):
            return 'No filter'
    except Exception:
        pass
    try:
        profile_raw = addon.getSetting('quality_profile') or 'Balanced (recommended)'
    except Exception:
        profile_raw = 'Balanced (recommended)'
    profile_key = _resolve_quality_profile(profile_raw)
    if profile_key != 'custom':
        return _QUALITY_PROFILES[profile_key]['min_quality']
    try:
        return addon.getSetting('min_quality') or 'No filter'
    except Exception:
        return 'No filter'



_EXCL_AV1_RE  = re.compile(r'\bAV1\b')
_EXCL_HEVC_RE = re.compile(r'\b(?:HEVC|H\.265|X265|H265)\b')
_EXCL_DV_RE   = re.compile(r'\b(?:DOLBY[ ._-]?VISION|DOVI|DV)\b')


def _entry_codec_hits(entry):
    """Which excludable formats this row advertises (from its text blob)."""
    blob = entry_text_blob(entry)
    hits = set()
    if _EXCL_AV1_RE.search(blob):
        hits.add('AV1')
    if _EXCL_HEVC_RE.search(blob):
        hits.add('HEVC')
    if _EXCL_DV_RE.search(blob):
        hits.add('DV')
    return hits

def filter_stream_entries(entries, settings):
    original = list(entries or [])

    # v3.9.260: the sources window is a complete inventory. Quality profiles
    # rank rows but must never hide valid versions returned by Stremio addons.
    # Some addons use unusual title formatting, so a real 4K/FHD file may parse
    # as unknown and was previously removed by the Balanced/Best floor. Keep
    # every stream here; users can narrow the visible list using the live 4K /
    # 1080p / provider filters in the source window. Only explicit CAM/TS
    # suppression remains below for users who enabled it.

    def _is_personal_server(row):
        text = ' '.join(str(row.get(key) or '') for key in (
            'provider_name_raw', 'provider', 'addon', 'name')).lower()
        return any(marker in text for marker in (
            'plex native', 'plex •', 'emby native', 'emby •',
            'silo native', 'silo •', 'dplex',
            'jellyfin native', 'jellyfin •'))

    # Quality/CAM filters are intended for public Stremio releases.  Never
    # hide a user's own Plex/Emby/Jellyfin/Silo versions: a 720p personal copy or a
    # filename containing a noisy token is still a valid server source and the
    # user explicitly asked to see every resolution from every server.
    personal = [row for row in original if _is_personal_server(row)]
    filtered = [row for row in original if not _is_personal_server(row)]
    if settings.get('hide_cam_ts', True):
        filtered = [e for e in filtered if not is_cam_entry(e)]
    # v3.9.263: device-capability exclusions. A row is hidden only when the
    # user explicitly excluded a format their device can't decode. DV rows are
    # matched by dynamic range; AV1/HEVC by codec. Personal servers already
    # sit in `personal` and are never touched.
    _codec_excl = settings.get('codec_exclusions') or set()
    _dr_excl = settings.get('dr_exclusions') or set()
    if _codec_excl or _dr_excl:
        _all_excl = set(_codec_excl) | set(_dr_excl)
        filtered = [e for e in filtered if not (_entry_codec_hits(e) & _all_excl)]
    # Do not apply the minimum-quality floor as a destructive filter. The
    # profile still affects sorting/preferences, while all 4K, FHD, 720p and
    # unparsed variants remain selectable.
    kept_ids = {id(row) for row in personal + filtered}
    return [row for row in original if id(row) in kept_ids]


def _source_priority_bonus(entry, priority_order):
    """Return a bonus that pins user-prioritized providers to the top.

    Provider names matching the priority list get a large positive bonus
    proportional to their position (top of list = highest bonus). Names
    not in the list get 0 — they fall back to quality-based ordering.

    The bonus dominates the sort key (placed first in the tuple) so a
    pinned 480p stream still ranks above an unpinned 1080p one, which
    matches the user expectation: "I told you these providers go first."
    """
    if not priority_order:
        return 0
    candidates = (
        str(entry.get('provider_name_raw') or '').lower(),
        str(entry.get('addon') or '').lower(),
        str(entry.get('name') or '').lower(),
    )
    best_pos = None
    for idx, needle in enumerate(priority_order):
        if not needle:
            continue
        for hay in candidates:
            if hay and needle in hay:
                if best_pos is None or idx < best_pos:
                    best_pos = idx
                break
    if best_pos is None:
        return 0
    # Position 0 = highest bonus. Use a large base so even position N
    # outranks any non-listed provider.
    return 10000 - best_pos


def stream_sort_key(entry, settings, preferred_source=None):
    badge_text = entry_text_blob(entry)
    # Resolution remains the first quality decision. Within one resolution,
    # prefer the actually useful playback traits before raw file size: HDR10+
    # (then DV/HDR10/HDR), direct/native or cached access, release source and
    # audio. A huge SDR WEBRip must not beat a smaller HDR10+ WEB-DL merely
    # because its filename advertises more gigabytes.
    q_rank = quality_rank(entry) or quality_rank(entry.get('quality'))
    size_rank = size_to_bytes(entry.get('size_label'))
    hdr_bonus = 0
    if settings.get('prefer_hdr', True):
        if 'HDR10+' in badge_text:
            hdr_bonus = 5
        elif any(x in badge_text for x in ('DOLBY VISION', 'DOVI', ' DV ')):
            hdr_bonus = 4
        elif 'HDR10' in badge_text:
            hdr_bonus = 3
        elif any(x in badge_text for x in (' HDR ', ' HLG ')):
            hdr_bonus = 2
    access_bonus = 0
    access_blob = ' %s ' % badge_text
    if any(token in access_blob for token in (
            ' PLEX NATIVE ', ' EMBY NATIVE ', ' JELLYFIN NATIVE ',
            ' DIRECT PLAY ', ' DIRECT ')):
        access_bonus = 3
    elif is_debrid_entry(entry) or any(token in access_blob for token in (
            ' CACHED ', ' RD+ ', ' AD+ ', ' PM ', ' TB ', ' ED+ ')):
        access_bonus = 2
    elif any(token in access_blob for token in (' WEB AUTO ', ' HLS ')):
        access_bonus = 0
    release_bonus = 0
    for token, score in ((' REMUX ', 4), (' BLURAY ', 3),
                         (' WEB-DL ', 2), (' WEBDL ', 2),
                         (' WEBRIP ', 1), (' HDTV ', 1)):
        if token in access_blob:
            release_bonus = max(release_bonus, score)
    audio_bonus = 0
    if settings.get('prefer_atmos', True):
        for token, score in (('ATMOS', 6), ('TRUEHD', 5), ('DTS-X', 4), ('DTS-HD', 3), ('DD+', 2), ('7.1', 2), ('5.1', 1)):
            if token in badge_text:
                audio_bonus = max(audio_bonus, score)
    debrid_bonus = 1 if (settings.get('prefer_debrid', True) and is_debrid_entry(entry)) else 0
    remembered_bonus = 1 if entry_matches_last_source(entry, preferred_source) else 0
    # v3.9.33: user-defined source priority. When set, listed providers
    # rank above unlisted ones in the listed order — overrides quality.
    priority_bonus = _source_priority_bonus(entry, settings.get('source_priority_order') or [])
    return (
        priority_bonus,
        q_rank,
        hdr_bonus,
        access_bonus,
        release_bonus,
        debrid_bonus,
        remembered_bonus,
        audio_bonus,
        size_rank,
        str(entry.get('addon') or ''),
        str(entry.get('name') or ''),
    )

def renumber_stream_entries(entries):
    ordered = list(entries or [])
    for idx, row in enumerate(ordered, start=1):
        row['count'] = '%02d.' % idx
    return ordered


def finalize_stream_entries(entries, settings, preferred_source=None, resort=True):
    original = list(entries or [])
    filtered = filter_stream_entries(original, settings)
    ordered_source = filtered if filtered else original
    if resort:
        keyed = [(stream_sort_key(e, settings, preferred_source=preferred_source), e)
                 for e in ordered_source]
        for key, entry in keyed:
            entry['_dh_rank'] = list(key[:9])
        ordered = [entry for key, entry in sorted(keyed, key=lambda pair: pair[0], reverse=True)]
    else:
        ordered = list(ordered_source)
        # Keep the cursor stable as late providers arrive, but carry their
        # actual ranking so the picker need not mistake arrival order for merit.
        for entry in ordered:
            if not entry.get('_dh_rank'):
                entry['_dh_rank'] = list(stream_sort_key(
                    entry, settings, preferred_source=preferred_source)[:9])
    if settings.get('order_mode') == 'addon':
        ordered = addon_order(ordered, settings)
    return renumber_stream_entries(ordered)


def addon_order(entries, settings):
    """v5.10.292 (Like Nuvio): the sources as the add-ons gave them. The
    add-ons in the Sources order (the list Nuvio syncs), each add-on's rows in
    the order it returned them (AIOStreams sorted by its own rules). A row
    with no add-on position (a media server) keeps its ranked place among
    them, after the pinned providers."""
    rows = list(entries or [])
    priority = settings.get('source_priority_order') or []

    def key(pair):
        idx, entry = pair
        pos = entry.get('_addon_pos')
        row = entry.get('_addon_row')
        has = isinstance(pos, int) and isinstance(row, int)
        return (-_source_priority_bonus(entry, priority),
                pos if has else 99999, row if has else idx)
    return [entry for _idx, entry in sorted(enumerate(rows), key=key)]


RANK_FACTORS = ('provider', 'resolution', 'hdr', 'access', 'release',
                'debrid', 'remembered', 'audio', 'size')


def recommendation(entries, settings=None):
    """(index, deciding factor) among available copies; ties have no winner.

    Names are excluded: alphabetic tie breakers do not prove one copy better.
    This compares parsed facts/preferences, not measured playback speed.
    """
    rows = list(entries or [])
    if len(rows) < 2:
        return None, ''
    keys = []
    for row in rows:
        key = row.get('_dh_rank')
        if not (isinstance(key, (list, tuple)) and len(key) == 9
                and all(isinstance(v, (int, float)) for v in key)):
            key = stream_sort_key(row, settings or {})[:9]
        keys.append(tuple(key))
    index = max(range(len(keys)), key=keys.__getitem__)
    runner = max(key for n, key in enumerate(keys) if n != index)
    if keys[index] == runner:
        return None, ''
    for position, (winner, other) in enumerate(zip(keys[index], runner)):
        if winner != other:
            return index, RANK_FACTORS[position]
    return None, ''

# ── Regex-based stream facts ────────────────────────────────────────────────
_STREAM_REGEX_RULES = [
    ('resolution', '2160P', r'(?<!\d)(?:2160p|4k|uhd)(?!\d)'),
    ('resolution', '1080P', r'(?<!\d)1080p?(?!\d)'),
    ('resolution', '720P',  r'(?<!\d)720p?(?!\d)'),
    ('resolution', '480P',  r'(?<!\d)480p?(?!\d)'),
    ('resolution', 'SD',    r'\b(?:sd|576p|360p)\b'),
    ('dynamic_range', 'DV', r'\b(?:dolby[ ._-]?vision|dovi|dv)\b'),
    ('dynamic_range', 'HDR10+', r'\bhdr10\+\b'),
    ('dynamic_range', 'HDR10', r'\bhdr10\b'),
    ('dynamic_range', 'HDR', r'\b(?:hdr|hlg)\b'),
    ('video_codec', 'AV1', r'\bav1\b'),
    ('video_codec', 'HEVC', r'\b(?:hevc|h\.265|x265|h265)\b'),
    ('video_codec', 'H264', r'\b(?:h\.264|x264|h264|avc)\b'),
    ('source', 'REMUX', r'\b(?:remux|bdremux)\b'),
    ('source', 'BLURAY', r'\b(?:blu[ ._-]?ray|bdrip|brrip|bd25|bd50)\b'),
    ('source', 'WEB-DL', r'\b(?:web[ ._-]?dl|webdl)\b'),
    ('source', 'WEBRIP', r'\bweb[ ._-]?rip\b'),
    ('source', 'HDTV', r'\bhdtv\b'),
    ('audio_codec', 'ATMOS', r'\batmos\b'),
    ('audio_codec', 'TRUEHD', r'\btrue[ ._-]?hd\b'),
    ('audio_codec', 'DTS-X', r'\bdts[ ._-]?x\b'),
    ('audio_codec', 'DTS-HD', r'\bdts[ ._-]?hd(?:[ ._-]?ma)?\b'),
    ('audio_codec', 'DTS', r'\bdts\b'),
    ('audio_codec', 'DD+', r'\b(?:dd\+|eac3|e-ac-3|ec-3|dolby[ ._-]?digital[ ._-]?plus)\b'),
    ('audio_codec', 'DD', r'\b(?:ac3|ac-3|dolby[ ._-]?digital)\b'),
    ('audio_channels', '7.1', r'\b7[ ._-]?1\b'),
    ('audio_channels', '5.1', r'\b5[ ._-]?1\b'),
    ('audio_channels', '2.0', r'\b2[ ._-]?0\b'),
    ('language', 'MULTI', r'\b(?:multi|multilang|dual[ ._-]?audio)\b'),
    ('language', 'AR', r'\b(?:arabic|ara|ar)\b|[\u0600-\u06FF]'),
    ('flags', 'SUBS', r'\b(?:subbed|subs|subtitles?)\b'),
    ('flags', 'DUBBED', r'\b(?:dubbed|dub)\b'),
]
_COMPILED_STREAM_REGEX_RULES = [(g, n, re.compile(p, re.I)) for g, n, p in _STREAM_REGEX_RULES]


