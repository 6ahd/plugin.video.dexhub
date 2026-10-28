"""Per-profile audio and embedded-subtitle preferences for DexHub playback.

Selection policy is independent of the decoder. Manual choices and source-switch
subtitles win. Language ranks precede optional original/default/SDH tie breakers.
"""
import re

_ALIASES = {'ara':'ar','arabic':'ar','العربية':'ar','eng':'en','english':'en',
 'fre':'fr','fra':'fr','french':'fr','ger':'de','deu':'de','german':'de',
 'spa':'es','spanish':'es','por':'pt','portuguese':'pt','pob':'pt-br','pb':'pt-br',
 'chi':'zh','zho':'zh','chinese':'zh','jpn':'ja','japanese':'ja','kor':'ko','korean':'ko',
 'tur':'tr','turkish':'tr','per':'fa','fas':'fa','persian':'fa','hin':'hi','hindi':'hi',
 'rus':'ru','russian':'ru','ita':'it','italian':'it','dut':'nl','nld':'nl','dutch':'nl',
 'cze':'cs','ces':'cs','gre':'el','ell':'el','heb':'he','iw':'he','ind':'id','in':'id',
 'ron':'ro','rum':'ro','ukr':'uk','tha':'th','vie':'vi','pol':'pl','swe':'sv','dan':'da',
 'fin':'fi','nor':'no','nob':'nb','nno':'nn','hun':'hu','urd':'ur','ben':'bn','tam':'ta',
 'tel':'te','mal':'ml','may':'ms','msa':'ms','slo':'sk','slk':'sk','srp':'sr','hrv':'hr',
 'bul':'bg','cat':'ca','est':'et','lav':'lv','lit':'lt','fil':'tl'}

def language(raw):
    value = str(raw or '').strip().lower().replace('_','-')
    if value in ('', 'und', 'unknown'):return ''
    if value in _ALIASES:return _ALIASES[value]
    parts=value.split('-');base=_ALIASES.get(parts[0],parts[0])
    if re.fullmatch('[a-z]{2,3}',base):return '-'.join([base]+parts[1:])
    try:
        import xbmc
        converted=xbmc.convertLanguage(value,xbmc.ISO_639_1)
        return str(converted or '').lower() if converted else ''
    except Exception:return ''

def languages(raw):
    return list(dict.fromkeys(c for v in str(raw or '').split(',') if (c:=language(v))))

def _rank(track,target):
    code=language(track.get('language'))
    if not code:return 99
    if code==target:return 0
    return 1 if code.split('-')[0]==target.split('-')[0] else 99

def _index(track,fallback):
    try:return int(track.get('index',fallback))
    except (TypeError,ValueError):return fallback

def choose_audio(tracks, primary='default', secondary='', original=True, avoid_commentary=True):
    if primary=='default':return None # Kodi/device preference remains authoritative.
    indexed=list(enumerate(tracks or []))
    if not indexed:return None
    def commentary(t):
        return bool(t.get('isimpaired') or re.search(r'commentary|audio description|descriptive|تعليق',str(t.get('name') or ''),re.I)) if avoid_commentary else False
    def tie(pair):
        i,t=pair
        return (commentary(t),not bool(original and t.get('isoriginal')),not bool(t.get('isdefault')),i)
    if primary=='original':
        found=[p for p in indexed if p[1].get('isoriginal') and not commentary(p[1])]
        return _index(min(found,key=tie)[1],min(found,key=tie)[0]) if found else None
    for target in languages(primary+','+secondary):
        found=[p for p in indexed if _rank(p[1],target)<99]
        if found:
            i,t=min(found,key=lambda p:(_rank(p[1],target),)+tie(p));return _index(t,i)
    return None # No match: keep the playable track Kodi already chose.

def choose_subtitle(tracks,prefs,mode='preferred',sdh='any'):
    if mode=='kodi':return None
    if mode=='off':return -1
    indexed=[(i,t) for i,t in enumerate(tracks or []) if isinstance(t,dict)]
    forced=mode=='forced'
    for target in languages(prefs):
        found=[(i,t) for i,t in indexed if _rank(t,target)<99 and (not forced or t.get('isforced'))]
        if found:
            def rank(pair):
                i,t=pair
                impaired=bool(t.get('isimpaired') or re.search(r'\b(sdh|cc|hearing impaired)\b',str(t.get('name') or ''),re.I))
                return (_rank(t,target),not bool(t.get('isforced')) if forced else bool(t.get('isforced')),
                        impaired if sdh=='avoid' else not impaired if sdh=='prefer' else False,
                        not bool(t.get('isdefault')),i)
            i,t=min(found,key=rank);return _index(t,i)
    return -1 if forced else None

def _rpc(method,params):
    from .kodi_rpc import call
    return call(method,params) or {}

def player_tracks():
    players=_rpc('Player.GetActivePlayers',{})
    if not isinstance(players,list):return None,{}
    pid=next((p.get('playerid') for p in players if p.get('type')=='video'),None)
    if pid is None:return None,{}
    props=_rpc('Player.GetProperties',{'playerid':pid,'properties':['audiostreams','currentaudiostream','subtitles','currentsubtitle','subtitleenabled']})
    return pid,props if isinstance(props,dict) else {}

def manual(ctx):
    return bool((ctx or {}).get('selected_subtitle') or (ctx or {}).get('switch_subtitle_path') or (ctx or {}).get('source_switch_reuse_subtitle'))

def apply_audio(player,ctx,uid):
    """Service-only, once at startup. Never runs for a foreign title/trailer."""
    from .session_store import load_session
    from .settings_cache import cached_addon
    if not uid or str((load_session() or {}).get('playback_uid') or '')!=str(uid):return False
    addon=cached_addon();primary=addon.getSetting('preferred_audio_language') or 'default'
    if primary=='default':return False
    pid,props=player_tracks()
    for attempt in range(4):
        if props.get('audiostreams'):break
        if not player.isPlayingVideo() or str((load_session() or {}).get('playback_uid') or '')!=str(uid):return False
        import xbmc
        xbmc.sleep(200)
        pid,props=player_tracks()
    if pid is None or not player.isPlayingVideo():return False
    selected=choose_audio(props.get('audiostreams'),primary,addon.getSetting('secondary_audio_language'),
                          addon.getSetting('audio_prefer_original')!='false',addon.getSetting('audio_avoid_commentary')!='false')
    if selected is None or selected==(props.get('currentaudiostream') or {}).get('index'):return False
    if str((load_session() or {}).get('playback_uid') or '')!=str(uid):return False
    player.setAudioStream(selected)
    return True

def apply_subtitles(player,show=False,uid=''):
    from .settings_cache import cached_addon
    from .session_store import load_session
    if uid and str((load_session() or {}).get('playback_uid') or '')!=str(uid):return False
    addon=cached_addon();mode=addon.getSetting('embedded_subtitle_mode') or 'preferred'
    if mode=='kodi':return False
    pid,props=player_tracks()
    if pid is None:return False
    selected=choose_subtitle(props.get('subtitles'),addon.getSetting('preferred_subtitle_langs') or 'ar,en',mode,addon.getSetting('subtitle_sdh_preference') or 'any')
    if selected is None:return False
    if uid and str((load_session() or {}).get('playback_uid') or '')!=str(uid):return False
    if selected<0:
        player.showSubtitles(False);return True
    if selected!=(props.get('currentsubtitle') or {}).get('index'):player.setSubtitleStream(selected)
    if show or mode=='forced':player.showSubtitles(True)
    return True
