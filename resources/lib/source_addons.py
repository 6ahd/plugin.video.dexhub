# -*- coding: utf-8 -*-
"""Which add-ons the source search asks (v5.10.203).

Every linked add-on whose manifest offers streams is asked for sources. A
Nuvio or Stremio account often carries add-ons that also declare streams
but are not where the user gets them (AIOMetadata next to AIOStreams), and
each one costs a request and can leave the search "partial". The user can
leave such add-ons out of the search; their catalogs and metadata stay.
"""
import json

import xbmcaddon
import xbmcgui

SETTING = 'source_skip_addons'


def key(provider):
    provider = provider or {}
    manifest = provider.get('manifest') or {}
    return str(provider.get('id') or provider.get('manifest_url') or provider.get('base_url')
               or manifest.get('id') or provider.get('name') or '')


def skipped():
    """The keys of the add-ons left out of the source search."""
    try:
        value = json.loads(xbmcaddon.Addon().getSetting(SETTING) or '[]')
    except Exception:
        return frozenset()
    return frozenset(str(x) for x in value if x) if isinstance(value, list) else frozenset()


def _save(keys):
    xbmcaddon.Addon().setSetting(SETTING, json.dumps(sorted(keys)))


def _stream_providers():
    from .dexhub import store
    from .providers import stremio_provider
    out = []
    for provider in store.list_providers():
        if (provider or {}).get('enabled') is False:
            continue
        try:
            streams = any(stremio_provider.supports_stream(provider, mt) for mt in ('movie', 'series'))
        except Exception:
            streams = False
        if streams and key(provider):
            out.append(provider)
    return out


def choose(tr):
    """A list of the add-ons with streams; the checked ones are searched."""
    providers = _stream_providers()
    if not providers:
        xbmcgui.Dialog().ok('Dex Hub', tr('ما فيه إضافات مصادر مربوطة'))
        return False
    off = skipped()
    labels, pre = [], []
    for index, provider in enumerate(providers):
        manifest = provider.get('manifest') or {}
        labels.append(str(provider.get('name') or manifest.get('name') or key(provider)))
        if key(provider) not in off:
            pre.append(index)
    picked = xbmcgui.Dialog().multiselect(tr('الإضافات اللي يبحث فيها عن المصادر'), labels, preselect=pre)
    if picked is None:
        return False
    keep = set(picked)
    if not keep:
        xbmcgui.Dialog().ok('Dex Hub', tr('اختر إضافة وحدة على الأقل'))
        return False
    # keys of add-ons no longer linked are kept: relinking one keeps its choice
    others = {k for k in off if k not in {key(p) for p in providers}}
    _save(others | {key(p) for i, p in enumerate(providers) if i not in keep})
    return True
