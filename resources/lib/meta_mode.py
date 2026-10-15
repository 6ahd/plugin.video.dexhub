# -*- coding: utf-8 -*-
"""One metadata source picker for the whole addon (v5.10.68).

Dex Hub used to expose the same decision in several places (a global picker,
a legacy labelenum, an "Auto" row, an AIOMetadata row, per-provider
overrides). This module is the single writer: whatever the user picks here
is applied as a COMPLETE, consistent profile, and the legacy settings are
kept in sync so no two settings disagree.

Sources:
  * 'auto'         complete missing metadata by exact identity for every source
  * 'tmdb_helper'  TMDb Helper's local database only: no network at all from
                   Dex Hub for metadata or posters, the fastest option when
                   the skin already runs on TMDb Helper
  * <provider id>  a Stremio metadata addon (AIOMetadata, Cinemeta, ...): one
                   self-hosted source for data and artwork on every screen,
                   7-day local cache and bounded page preloading
  * 'native'       every provider keeps its own metadata

Speed profile (tmdb_helper and Stremio sources): BetterPosters,
TMDb API enrichment and the metahub "clean poster" rewrite are switched off,
because each of them adds per-item work or a third host between the poster
and the screen. Everything stays reversible from the normal settings.

Import-light: pulled in only when a picker action runs.
"""
import time

import xbmc
import xbmcaddon
import xbmcgui

from .dexhub import store as _store

SOURCE_AUTO = 'auto'
SOURCE_NATIVE = 'native'
SOURCE_HELPER = 'tmdb_helper'
SOURCE_TMDB = 'tmdb'               # v5.10.301: TMDb in one language everywhere
HELPER_ADDON_ID = 'plugin.video.themoviedb.helper'

_AIO_MANIFEST_IDS = ('aio-metadata', 'aiometadata', 'com.aiometadata', 'org.aiometadata')
_AIO_NAME_TOKENS = ('aiometadata', 'aio metadata', 'aio-metadata')

# Speed profile shared by TMDb Helper and Stremio sources.
_SPEED_SETTINGS = (
    ('global_meta_source_all', 'true'),
    ('global_poster_source_id', 'same'),
    ('deep_meta_enrich', 'false'),
    ('betterposters_enabled', 'false'),
    ('poster_reliability_mode', 'Keep decorated'),
    ('meta_source_warm_pages', 'true'),
)
# Legacy labelenum kept in sync with the real choice.
_LEGACY_LABEL = {
    SOURCE_AUTO: 'Auto (smart)',
    SOURCE_HELPER: 'TMDb Helper (recommended)',
    SOURCE_NATIVE: 'Native (addon)',
}


def _addon():
    from .settings_cache import cached_addon
    return cached_addon()


def _arabic():
    try:
        return (_addon().getSetting('ui_language') or 'English').strip().lower() == 'arabic'
    except Exception:
        return False


def _txt(ar, en):
    return ar if _arabic() else en


def _notify(message):
    try:
        xbmcgui.Dialog().notification('Dex Hub', message, xbmcgui.NOTIFICATION_INFO, 2600)
    except Exception:
        pass


def helper_installed():
    try:
        return bool(xbmc.getCondVisibility('System.HasAddon(%s)' % HELPER_ADDON_ID))
    except Exception:
        return False


# ── providers ───────────────────────────────────────────────────────────

def _supports_meta(row):
    manifest = (row or {}).get('manifest') or {}
    for resource in manifest.get('resources') or []:
        if resource == 'meta' or (isinstance(resource, dict) and resource.get('name') == 'meta'):
            return True
    return False


def _host_of(url):
    try:
        from urllib.parse import urlparse
        return (urlparse(str(url or '')).hostname or '').lower()
    except Exception:
        return ''


def _provider_haystack(row):
    manifest = (row or {}).get('manifest') or {}
    return ' '.join([
        str((row or {}).get('name') or ''),
        str(manifest.get('name') or ''),
        str(manifest.get('id') or ''),
        str((row or {}).get('manifest_url') or ''),
    ]).lower()


def is_aiometadata(row):
    manifest = (row or {}).get('manifest') or {}
    if str(manifest.get('id') or '').strip().lower() in _AIO_MANIFEST_IDS:
        return True
    return any(token in _provider_haystack(row) for token in _AIO_NAME_TOKENS)


def find_aiometadata(rows=None):
    """The registered AIOMetadata provider row (first match), or None."""
    rows = list(rows if rows is not None else (_store.list_providers() or []))
    for row in rows:
        manifest = (row or {}).get('manifest') or {}
        if str(manifest.get('id') or '').strip().lower() in _AIO_MANIFEST_IDS:
            return row
    for row in rows:
        if any(token in _provider_haystack(row) for token in _AIO_NAME_TOKENS):
            return row
    return None


def meta_providers(rows=None):
    """Stremio providers eligible as a metadata source (same rule as the
    per-provider picker in meta_source.py)."""
    try:
        from . import meta_source as _ms
        eligible = {s['id'] for s in _ms.list_available_meta_sources() if s.get('kind') == 'stremio'}
    except Exception:
        eligible = None
    out = []
    for row in (rows if rows is not None else (_store.list_providers() or [])):
        pid = str((row or {}).get('id') or '')
        if not pid:
            continue
        if eligible is not None and pid not in eligible:
            continue
        if eligible is None and not _supports_meta(row):
            continue
        out.append(row)
    return out


def _replace_manifest_url(row, manifest_url, manifest):
    """Point an existing provider row at a new manifest URL in place, so a
    host change (nzb.example -> meta.example) never leaves a duplicate."""
    rows = _store.list_providers() or []
    pid = str(row.get('id') or '')
    for existing in rows:
        if str(existing.get('id') or '') == pid:
            existing['manifest_url'] = manifest_url
            existing['base_url'] = manifest_url.rsplit('/manifest.json', 1)[0]
            existing['manifest'] = manifest
            if manifest.get('name'):
                existing['name'] = manifest.get('name')
            _store.write_providers_raw(rows)
            return existing
    return _store.add_provider(name=manifest.get('name') or 'AIOMetadata', manifest_url=manifest_url, manifest=manifest)


def add_or_update_aiometadata(prefill=''):
    """Keyboard flow: add AIOMetadata, or update the URL of the existing row."""
    existing = find_aiometadata()
    keyboard = xbmc.Keyboard(prefill or (existing or {}).get('manifest_url') or '',
                             _txt('رابط manifest.json حق AIOMetadata', 'AIOMetadata manifest.json URL'))
    from . import kb_private
    with kb_private.private():      # v5.10.110: no keyboard suggestions for it
        keyboard.doModal()
    if not keyboard.isConfirmed():
        return None
    from .dexhub.links import manifest_link     # v5.10.143: stremio:// links
    manifest_url = manifest_link(keyboard.getText() or '')
    if not manifest_url:
        return None
    if manifest_url.lower().startswith('http://'):
        manifest_url = 'https://' + manifest_url[7:]
    if not manifest_url.endswith('/manifest.json'):
        manifest_url = manifest_url.rstrip('/') + '/manifest.json'
    try:
        from .dexhub.client import validate_manifest
        manifest = validate_manifest(manifest_url, force_refresh=True)
    except Exception as exc:
        xbmcgui.Dialog().ok('Dex Hub', _txt('تعذر قراءة الـ manifest: %s', 'Could not read the manifest: %s') % exc)
        return None
    if existing is not None:
        row = _replace_manifest_url(existing, manifest_url, manifest)
        _notify(_txt('تم تحديث رابط %s', 'Updated the URL of %s') % (row.get('name') or 'AIOMetadata'))
    else:
        name = manifest.get('name') or manifest.get('id') or 'AIOMetadata'
        row = _store.add_provider(name=name, manifest_url=manifest_url, manifest=manifest)
        _notify(_txt('تمت إضافة المصدر: %s', 'Source added: %s') % name)
    try:
        from .dexhub.client import purge_meta_cache
        purge_meta_cache()
    except Exception:
        pass
    return row


def _reachable(row):
    try:
        from .dexhub.client import validate_manifest
        fresh = validate_manifest(str(row.get('manifest_url') or ''), force_refresh=True)
        if fresh:
            _store.refresh_provider_manifest(row.get('id'), fresh)
        return True
    except Exception as exc:
        xbmc.log('[DexHub] meta source: manifest refresh failed: %s' % exc, xbmc.LOGWARNING)
        return False


# ── state ───────────────────────────────────────────────────────────────

def current_source():
    """(source_id, human label) of the global choice."""
    try:
        addon = _addon()
        selected = (addon.getSetting('global_meta_source_id') or '').strip()
        apply_all = (addon.getSetting('global_meta_source_all') or 'true') == 'true'
    except Exception:
        selected, apply_all = '', True
    if not selected or selected == SOURCE_AUTO or not apply_all:
        return SOURCE_AUTO, _txt('تلقائي (ذكي)', 'Auto (smart)')
    if selected == SOURCE_HELPER:
        return SOURCE_HELPER, _txt('TMDb Helper (محلي)', 'TMDb Helper (local)')
    if selected == SOURCE_TMDB:
        return SOURCE_TMDB, _txt('TMDb • لغة وحدة لكل الأقسام', 'TMDb • one language everywhere')
    if selected == SOURCE_NATIVE:
        return SOURCE_NATIVE, _txt('مثل Nuvio • بيانات وبوسترات المصدر', 'Like Nuvio • source metadata & posters')
    row = _store.get_provider(selected)
    if row:
        return selected, str(row.get('name') or selected)
    return SOURCE_AUTO, _txt('تلقائي (ذكي)', 'Auto (smart)')


def status_label():
    return current_source()[1]


# ── apply ───────────────────────────────────────────────────────────────

def _refresh_everything(addon):
    # The in-memory meta cache keys on this generation stamp; the on-disk
    # /meta/ cache is purged so the very next render reflects the new source.
    try:
        addon.setSetting('metadata_generation', str(time.time()))
    except Exception:
        pass
    try:
        from .dexhub.client import purge_meta_cache
        purge_meta_cache()
    except Exception:
        pass
    try:
        from . import settings_cache
        settings_cache.invalidate()
    except Exception:
        pass
    try:
        from . import meta_source as _ms
        _ms._invalidate_map_cache()
    except Exception:
        pass
    try:
        # Module-level config cache survives the invocation under
        # reuselanguageinvoker; drop it so the poster swap stops right away.
        from . import better_posters as _bp
        _bp.invalidate()
    except Exception:
        pass
    try:
        from . import widget_cache
        widget_cache.clear()
        if xbmc.getSkinDir() == 'skin.dexhub':
            from .skinui import common
            common.ask_republish('metadata')
    except Exception:
        pass
    try:
        xbmcgui.Window(10000).setProperty('widgetreload', str(time.time()))
        xbmc.executebuiltin('Container.Refresh')
    except Exception:
        pass


def apply_source(source_id, notify=True):
    """Apply one metadata source for everything, as a complete profile."""
    source_id = str(source_id or SOURCE_AUTO).strip() or SOURCE_AUTO
    addon = _addon()
    row = None
    if source_id not in (SOURCE_AUTO, SOURCE_NATIVE, SOURCE_HELPER, SOURCE_TMDB):
        row = _store.get_provider(source_id)
        if not row:
            _notify(_txt('المصدر غير موجود', 'Source not found'))
            return False

    def _set(key, value):
        try:
            addon.setSetting(key, str(value))
        except Exception:
            pass

    if source_id == SOURCE_AUTO:
        _set('global_meta_source_id', SOURCE_AUTO)
        for key, value in _SPEED_SETTINGS:
            _set(key, value)
        _set('default_meta_source', _LEGACY_LABEL[SOURCE_AUTO])
        _set('deep_meta_enrich', 'false')
        label = _txt('تلقائي (ذكي)', 'Auto (smart)')
    elif source_id == SOURCE_NATIVE:
        _set('global_meta_source_id', SOURCE_NATIVE)
        for key, value in _SPEED_SETTINGS:
            _set(key, value)
        _set('server_art_from_tmdb', 'false')
        _set('homeui_metadata_language', 'Auto')
        _set('meta_source_warm_pages', 'false')
        _set('global_meta_source_all', 'true')
        _set('default_meta_source', _LEGACY_LABEL[SOURCE_NATIVE])
        label = _txt('مثل Nuvio • بيانات وبوسترات المصدر', 'Like Nuvio • source metadata & posters')
    else:
        _set('global_meta_source_id', source_id)
        for key, value in _SPEED_SETTINGS:
            _set(key, value)
        if not (addon.getSetting('meta_source_cache_days') or '').strip():
            _set('meta_source_cache_days', '7')
        if source_id == SOURCE_TMDB:
            # v5.10.301: every section (and the servers' missing art) from
            # TMDb, in the metadata language
            _set('server_art_from_tmdb', 'true')
            _set('meta_source_warm_pages', 'true')
            _set('default_meta_source', _LEGACY_LABEL[SOURCE_HELPER])
            label = _txt('TMDb • لغة وحدة لكل الأقسام', 'TMDb • one language everywhere')
        elif source_id == SOURCE_HELPER:
            # Plex/Emby rows take their posters from TMDb Helper's own DB
            # (tier 1 of _server_tmdb_art): local, shared texture cache.
            _set('server_art_from_tmdb', 'true')
            _set('default_meta_source', _LEGACY_LABEL[SOURCE_HELPER])
            label = _txt('TMDb Helper (محلي)', 'TMDb Helper (local)')
        else:
            # A Stremio source owns the artwork; never mix TMDb API posters in.
            _set('server_art_from_tmdb', 'false')
            _set('default_meta_source', _LEGACY_LABEL[SOURCE_AUTO])
            if is_aiometadata(row) and not (addon.getSetting('selfhost_aio_manifest_url') or '').strip():
                _set('selfhost_aio_manifest_url', str(row.get('manifest_url') or ''))
            label = str(row.get('name') or source_id)
    _refresh_everything(addon)
    if notify:
        _notify(_txt('مصدر الميتاداتا والصور: %s ✓', 'Metadata & artwork source: %s ✓') % label)
    return True


# ── UI ──────────────────────────────────────────────────────────────────

def _close_native_settings():
    """Kodi re-saves its in-memory settings tree when addonsettings closes,
    reverting anything written behind it. Close that editor first."""
    try:
        if xbmc.getCondVisibility('Window.IsVisible(addonsettings)'):
            xbmc.executebuiltin('Dialog.Close(addonsettings)')
            xbmc.sleep(180)
    except Exception:
        pass


def _options():
    """Ordered (key, label) rows for the select dialog."""
    current_id, _ = current_source()
    rows = _store.list_providers() or []
    providers = meta_providers(rows)
    names = {}
    for row in providers:
        names[str(row.get('name') or '').lower()] = names.get(str(row.get('name') or '').lower(), 0) + 1
    options = []
    helper_note = '' if helper_installed() else _txt(' (غير مثبت)', ' (not installed)')
    options.append((SOURCE_TMDB, _txt('TMDb • لغة وحدة لكل الأقسام (الموصى به)', 'TMDb • one language everywhere (recommended)')))
    options.append((SOURCE_NATIVE, _txt('مثل Nuvio • بيانات وبوسترات المصدر', 'Like Nuvio • source metadata & posters')))
    options.append((SOURCE_HELPER, _txt('TMDb Helper • محلي، بدون شبكة، الأسرع', 'TMDb Helper • local, no network, fastest') + helper_note))
    aio_seen = False
    for row in providers:
        pid = str(row.get('id') or '')
        name = str(row.get('name') or pid)
        if names.get(name.lower(), 0) > 1:
            name = '%s (%s)' % (name, _host_of(row.get('manifest_url')) or pid)
        if is_aiometadata(row):
            aio_seen = True
            name = _txt('%s • مستضاف عندك، بيانات وصور لكل شي', '%s • self-hosted, data and artwork for everything') % name
        options.append((pid, name))
    if aio_seen:
        options.append(('__aio_url__', _txt('AIOMetadata: تغيير رابط الـ manifest', 'AIOMetadata: change the manifest URL')))
    else:
        options.append(('__aio_add__', _txt('إضافة AIOMetadata (رابط manifest)', 'Add AIOMetadata (manifest URL)')))
    options.append((SOURCE_AUTO, _txt('تلقائي (ذكي)', 'Auto (smart)')))
    labels = []
    for key, label in options:
        labels.append(('✓ ' if key == current_id else '') + label)
    return options, labels


def pick():
    """Entry point for the unified picker action."""
    _close_native_settings()
    options, labels = _options()
    current_id, _ = current_source()
    preselect = next((i for i, (key, _) in enumerate(options) if key == current_id), 0)
    descriptions = {
        SOURCE_TMDB: _txt('العنوان والقصة والبوستر والخلفية والشعار من TMDb بلغة واحدة (لغة الواجهة أو اللي تختارها) في الرئيسية والكتالوجات والمجموعات والمتابعة.',
                          'Title, plot, poster, backdrop and logo from TMDb in one language (the interface\'s or the one you pick) on the Home, catalogs, collections and Continue Watching.'),
        SOURCE_NATIVE: _txt('مثل Nuvio تمامًا لملفك الشخصي: بوسترات المصدر (أو قالب البوسترات اللي في Nuvio مثل RPDB)، وTMDb للخلفية والشعار والقصة بلغة الملف بس إذا كان مفعّل في Nuvio. بدون حساب Nuvio: بيانات وصور كل مصدر كما هي.',
                            'Exactly like Nuvio for your profile: the source posters (or the poster pattern set in Nuvio, such as RPDB), and TMDb for the backdrop, logo and plot in the profile\'s language only when Nuvio has it on. Without a Nuvio account: each source\'s own metadata and art as they are.'),
        SOURCE_HELPER: _txt('بيانات وصور موحّدة من TMDb Helper عند توفرها في الكاش المحلي؛ قد تختلف عن Nuvio.',
                            'Use unified metadata and artwork from TMDb Helper’s local cache when available; appearance can differ from Nuvio.'),
        SOURCE_AUTO: _txt('يكمل القصة والتصنيفات والتقييم والصور الناقصة من الكاش ثم TMDb ومصادر الميتاداتا، مع الحفاظ على بيانات العمل الموجودة.',
                         'Fill missing plot, genres, rating and artwork from caches, TMDb and metadata sources, preserving existing title data.'),
    }
    items = [xbmcgui.ListItem(label=label,
              label2=descriptions.get(key, _txt('بيانات وصور من إضافة الميتاداتا المختارة، وتتبع إعداد لغتها.',
                                               'Metadata and artwork from this add-on, following its language settings.')),
              offscreen=True) for (key, _), label in zip(options, labels)]
    choice = xbmcgui.Dialog().select(_txt('طريقة عرض البيانات والبوسترات', 'Metadata & poster appearance'),
                                     items, preselect=preselect, useDetails=True)
    if choice < 0:
        return False
    key = options[choice][0]
    if key in ('__aio_add__', '__aio_url__'):
        row = add_or_update_aiometadata()
        if row is None:
            return False
        return apply_source(row.get('id'))
    if key == SOURCE_HELPER and not helper_installed():
        xbmcgui.Dialog().ok('Dex Hub', _txt('TMDb Helper غير مثبت. ثبته أولاً ثم اختر هذا المصدر.',
                                            'TMDb Helper is not installed. Install it first, then pick this source.'))
        return False
    if key not in (SOURCE_AUTO, SOURCE_NATIVE, SOURCE_HELPER, SOURCE_TMDB):
        row = _store.get_provider(key)
        if row is not None and not _reachable(row):
            if not xbmcgui.Dialog().yesno('Dex Hub', _txt('ما قدرت أوصل لـ %s الحين. أطبق الاختيار على كل حال؟',
                                                            'Could not reach %s right now. Apply anyway?') % (row.get('name') or key)):
                return False
    return apply_source(key)


def apply_aiometadata():
    """Compatibility for the 5.10.67 `aiometadata_fast_mode` action."""
    _close_native_settings()
    row = find_aiometadata()
    if row is None:
        row = add_or_update_aiometadata()
        if row is None:
            return False
    return apply_source(row.get('id'))


# ── cache ───────────────────────────────────────────────────────────────

def clear_metadata_caches(notify=True, reload_ui=True):
    """Drop every metadata cache Dex Hub keeps and redraw the current view.

    Cleared: the HTTP response cache (/meta/, catalogs, manifests), the SQLite
    meta cache, the in-memory meta and search caches (via the generation
    stamp their keys include), TMDb Helper DB bundles, the warm-up negative
    list and the source's rate-limit override. Kodi's own texture cache is
    left alone: posters do not change when the metadata does.
    """
    addon = _addon()
    cleared = []
    try:
        from .dexhub.client import purge_meta_cache
        purge_meta_cache()
        cleared.append('http')
    except Exception:
        pass
    try:
        from . import meta_cache as _mc
        _mc.clear_all()
        cleared.append('sqlite')
    except Exception:
        pass
    try:
        from . import tmdbhelper as _tmdbh
        _tmdbh._CACHE.clear()
        _tmdbh._CACHE_ORDER[:] = []
        _tmdbh._LANG_CACHE['value'] = None
        try:
            _tmdbh._active_db_path.cache_clear()
        except Exception:
            pass
        cleared.append('helper')
    except Exception:
        pass
    try:
        from . import meta_source as _ms
        _ms._WARM_NEGATIVE.clear()
        _ms._WARM_POLICY_HOSTS.clear()
        _ms._invalidate_map_cache()
    except Exception:
        pass
    try:
        addon.setSetting('metadata_generation', str(time.time()))
    except Exception:
        pass
    try:
        from . import settings_cache
        settings_cache.invalidate()
    except Exception:
        pass
    if reload_ui:
        try:
            xbmcgui.Window(10000).setProperty('widgetreload', str(time.time()))
            xbmc.executebuiltin('Container.Refresh')
        except Exception:
            pass
    if notify:
        _notify(_txt('تم مسح كاش الميتاداتا وإعادة التحميل ✓', 'Metadata cache cleared, reloading ✓'))
    return cleared


def migrate_to_tmdb():
    """v5.10.301, once: who still has the shipped default (TMDb Helper's
    database, every section) moves to TMDb in one language, which fills
    what TMDb Helper never looked up instead of showing each add-on's own
    poster, title and language. A source picked by hand stays."""
    addon = _addon()
    try:
        if (addon.getSetting('meta_tmdb_default_v1') or '') == 'done':
            return False
        addon.setSetting('meta_tmdb_default_v1', 'done')
        if (addon.getSetting('global_meta_source_id') or SOURCE_HELPER) != SOURCE_HELPER:
            return False
        if (addon.getSetting('global_meta_source_all') or 'true') != 'true':
            return False
    except Exception:
        return False
    return apply_source(SOURCE_TMDB, notify=False)
