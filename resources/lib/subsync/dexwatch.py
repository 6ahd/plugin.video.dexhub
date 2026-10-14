# -*- coding: utf-8 -*-
# Vendored from DexSubtitles 5.7.3 (service.subtitles.dexworld, AUTOSYNC_V573) for
# Dex Hub 5.10.117: Dex Hub runs this AutoSync itself only while DexSubtitles
# is not enabled (subsync/runner.py), so the two never sync the same subtitle.
"""DEX_WATCH_V573: AutoSync لأي ترجمة خارجية يشغلها كودي، من أي إضافة أو ملف جنب الفيديو.

كودي ما يعطي مسار ملف الترجمة الشغالة، بس يعطي اسمها ولغتها، والاسم يطلع من اسم الملف
بقاعدة كودي نفسها (CUtil::GetExternalStreamDetailsFromFilename): يشيل اسم الفيديو من أول
اسم الملف، يقسم الباقي على المسافة والنقطة والشرطة، ويشيل اللغة وforced/default، والباقي
هو الاسم ومعه «(External)». فنطبق نفس القاعدة (وجداول لغات كودي نفسها) على الملفات
المرشحة ونلقى اللي يطابق. لو أكثر من ملف يطابق ومحتواهم مختلف ما نخمن.

وين نبحث: special://temp (نافذة الترجمات تنسخ فيه أي ترجمة تختارها من أي إضافة لما يكون
الفيديو رابط)، مجلدات الإضافات داخله (DexHub)، مجلد الترجمات المخصص، جنب الفيديو ومجلدات
Subs فيه، ومسارات الترجمات اللي أرفقتها الإضافة مع الفيديو. ملفات DexSubtitles نفسها لها طريقها.
الشغل اللي يلمس الشبكة (مجلدات smb/nfs) يصير في خيط لحاله، فخدمة الإضافة ما توقف.
"""

import io
import os
import re
import json
import time
import hashlib
import threading
from collections import Counter
from urllib.parse import urlsplit

import xbmc
import xbmcvfs

SUB_EXTS = ('.srt', '.ass', '.ssa', '.vtt')
MAX_BYTES = 8 * 1024 * 1024
SETTLE_S = 3.0          # بعد بداية التشغيل: نخلي كودي والإضافات يرفقون ترجماتهم ويختارون
DEBOUNCE_S = 1.0        # الترجمة الشغالة لازم تثبت ثانية قبل ما نعالجها
VFS_CHECK_S = 5.0       # ملف الترجمة الشغالة على الشبكة: نشيك عليه كل 5 ثواني
_DELIMS = re.compile(r'[ .\-]+')
_FLAGS = ('none', 'default', 'forced')
_SUB_DIRS = ('subs', 'subtitles', 'vobsubs', 'sub', 'vobsub', 'subtitle')   # نفس كودي
_XTREAM = re.compile(r'/(movie|series)/[^/]+/[^/]+/\d+\.\w+$', re.I)       # IPTV غالباً اتصال واحد

# جداول لغات كودي (xbmc/utils/LangCodeExpander.cpp، نسخة Omega): رمز حرفين ← ISO 639-2/B،
# ورموز الدول الثلاثية (كودي يعدّها لغة)، وأسماء اللغات بكلمة وحدة (أول تطابق يغلب مثل كودي).
_ISO = ('aa:aar ab:abk af:afr ak:aka am:amh ar:ara an:arg as:asm av:ava ae:ave ay:aym az:aze ba:bak bm:bam be:bel bn:ben bh:bih bi:bis bo:tib bs:bos br:bre bg:bul ca:cat cs:cze ch:cha ce:che cu:chu cv:chv kw:cor co:cos cr:cre cy:wel da:dan de:ger dv:div dz:dzo el:gre en:eng eo:epo et:est eu:baq ee:ewe fo:fao fa:per fj:fij fi:fin fr:fre fy:fry ff:ful gd:gla ga:gle gl:glg gv:glv gn:grn gu:guj ht:hat ha:hau he:heb hz:her hi:hin ho:hmo hr:hrv hu:hun hy:arm ig:ibo io:ido ii:iii iu:iku ie:ile ia:ina id:ind in:ind ik:ipk is:ice it:ita iw:heb jv:jav ja:jpn kl:kal kn:kan ks:kas ka:geo kr:kau kk:kaz km:khm ki:kik rw:kin ky:kir kv:kom kg:kon ko:kor kj:kua ku:kur lo:lao la:lat lv:lav li:lim ln:lin lt:lit lb:ltz lu:lub lg:lug mk:mac mh:mah ml:mal mi:mao mr:mar ms:may mg:mlg mt:mlt mn:mon my:bur na:nau nv:nav nr:nbl nd:nde ng:ndo ne:nep nl:dut nn:nno nb:nob no:nor ny:nya oc:oci oj:oji or:ori om:orm os:oss pa:pan pb:pob pi:pli pl:pol pt:por ps:pus qu:que rm:roh ro:rum rn:run ru:rus sh:scr sg:sag sa:san si:sin sk:slo sl:slv se:sme sm:smo sn:sna sd:snd so:som st:sot es:spa sq:alb sc:srd sr:srp ss:ssw su:sun sw:swa sv:swe ty:tah ta:tam tt:tat te:tel tg:tgk tl:tgl th:tha ti:tir to:ton tn:tsn ts:tso tk:tuk tr:tur tw:twi ug:uig uk:ukr ur:urd uz:uzb ve:ven vi:vie vo:vol wa:wln wo:wol xh:xho yi:yid yo:yor za:zha zh:chi zu:zul zv:und zx:zxx zy:mis zz:mul')
_REGIONS = ('abw afg ago aia ala alb and ant are arg arm asm ata atf atg aus aut aze bdi bel ben bfa bgd bgr bhr bhs bih blm blr blz bmu bol bra brb brn btn bvt bwa caf can che chl chn civ cmr cod cog cok col com cpv cri cub cxr cym cyp cze deu dji dma dnk dom dza ecu egy eri esh esp est eth fin fji flk fra fro fsm gab gbr geo ggy gha gib gin glp gmb gnb gnq grc grd grl gtm guf gum guy hkg hmd hnd hrv hti hun idn imn ind iot irl irn irq isl isr ita jam jey jor jpn kaz ken kgz khm kir kna kor kwt lao lbn lbr lby lca lie lka lso ltu lux lva mac maf mar mco mda mdg mdv mex mhl mkd mli mlt mmr mne mng mnp moz mrt msr mtq mus mwi mys myt nam ncl ner nfk nga nic niu nld nor npl nru nzl omn pak pan pcn per phl plw png pol pri prk prt pry pse pyf qat reu rou rus rwa sau sdn sen sgp sgs shn sjm slb sle slv smr som spm srb stp sur svk svn swe swz syc syr tca tcd tgo tha tjk tkl tkm tls ton tto tun tur tuv twn tza uga ukr umi ury usa uzb vat vct ven vgb vir vnm vut wlf wsm yem zaf zmb zwe')
_NAMES = ("abkhaz:abk abkhazian:abk achinese:ace acoli:ach adangme:ada adygei:ady adyghe:ady afar:aar afrihili:afh afrikaans:afr akan:aka akkadian:akk albanian:alb aleut:ale amharic:amh arabic:ara aragonese:arg aramaic:arc arapaho:arp araucanian:arn arawak:arw armenian:arm assamese:asm asturian:ast avaric:ava avestan:ave awadhi:awa aymara:aym azerbaijani:aze bable:ast balinese:ban baluchi:bal bambara:bam banda:bad basa:bas bashkir:bak basque:baq beja:bej belarusian:bel bemba:bem bengali:ben bhojpuri:bho bihari:bih bikol:bik bilin:byn bini:bin bislama:bis blin:byn bosnian:bos braj:bra breton:bre buginese:bug bulgarian:bul buriat:bua burmese:bur caddo:cad carib:car spanish:spa catalan:cat cebuano:ceb chagatai:chg chamorro:cha chechen:che cherokee:chr chewa:nya cheyenne:chy chibcha:chb chichewa:nya chinese:chi chipewyan:chp choctaw:cho chuang:zha chuukese:chk chuvash:chv coptic:cop cornish:cor corsican:cos cree:cre creek:mus croatian:hrv czech:cze dakota:dak danish:dan dargwa:dar dayak:day delaware:del dinka:din divehi:div dogri:doi dogrib:dgr duala:dua dutch:dut dyula:dyu dzongkha:dzo efik:efi ekajuk:eka elamite:elx english:eng erzya:myv esperanto:epo estonian:est ewondo:ewo fang:fan fanti:fat faroese:fao fijian:fij filipino:fil finnish:fin flemish:dut french:fre frisian:fry friulian:fur fulah:ful gaelic:gla gallegan:glg ganda:lug gayo:gay gbaya:gba geez:gez georgian:geo german:ger gikuyu:kik gilbertese:gil gondi:gon gorontalo:gor gothic:got grebo:grb greenlandic:kal guarani:grn gujarati:guj gwich\\xc2\\xb4in:gwi haida:hai haitian:hat hausa:hau hawaiian:haw hebrew:heb herero:her hiligaynon:hil himachali:him hindi:hin hittite:hit hmong:hmn hungarian:hun hupa:hup iban:iba icelandic:ice igbo:ibo iloko:ilo indonesian:ind ingush:inh interlingue:ile inuktitut:iku inupiaq:ipk irish:gle italian:ita japanese:jpn javanese:jav kabardian:kbd kabyle:kab kachin:kac kalaallisut:kal kalmyk:xal kamba:kam kannada:kan kanuri:kau karen:kar kashmiri:kas kashubian:csb kawi:kaw kazakh:kaz khasi:kha khmer:khm khotanese:kho kikuyu:kik kimbundu:kmb kinyarwanda:kin kirghiz:kir klingon:tlh komi:kom kongo:kon konkani:kok korean:kor kosraean:kos kpelle:kpe kuanyama:kua kumyk:kum kurdish:kur kurukh:kru kutenai:kut ladino:lad lahnda:lah lamba:lam latin:lat latvian:lav letzeburgesch:ltz lezghian:lez limburgan:lim limburger:lim limburgish:lim lingala:lin lithuanian:lit lojban:jbo lozi:loz luiseno:lui lunda:lun lushai:lus luxembourgish:ltz macedonian:mac madurese:mad magahi:mag maithili:mai makasar:mak malagasy:mlg malay:may malayalam:mal maltese:mlt manchu:mnc mandar:mdr mandingo:man manipuri:mni manx:glv maori:mao marathi:mar mari:chm marshallese:mah marwari:mwr masai:mas mende:men micmac:mic mi'kmaq:mic minangkabau:min mirandese:mwl mohawk:moh moksha:mdf moldavian:mol mongo:lol mongolian:mon mossi:mos nahuatl:nah nauru:nau navajo:nav ndonga:ndo neapolitan:nap nepali:nep newari:new nias:nia niuean:niu nogai:nog norwegian:nor nyamwezi:nym nyanja:nya nyankole:nyn nyoro:nyo nzima:nzi ojibwa:oji oriya:ori oromo:orm osage:osa ossetian:oss ossetic:oss pahlavi:pal palauan:pau pali:pli pampanga:pam pangasinan:pag panjabi:pan papiamento:pap pedi:nso persian:per phoenician:phn pilipino:fil pohnpeian:pon polish:pol portuguese:por proven\\xc3\\xa7:oci punjabi:pan pushto:pus quechua:que rajasthani:raj rapanui:rap rarotongan:rar romanian:rum romany:rom rundi:run russian:rus samoan:smo sandawe:sad sango:sag sanskrit:san santali:sat sardinian:srd sasak:sas scots:sco selkup:sel sepedi:nso serbian:scc serer:srr shan:shn shona:sna sicilian:scn sidamo:sid siksika:bla sindhi:snd sinhala:sin sinhalese:sin slovak:slo slovenian:slv sogdian:sog somali:som songhai:son soninke:snk castilian:spa sukuma:suk sumerian:sux sundanese:sun susu:sus swahili:swa swati:ssw swedish:swe syriac:syr tagalog:tgl tahitian:tah tajik:tgk tamashek:tmh tamil:tam tatar:tat telugu:tel tereno:ter tetum:tet thai:tha tibetan:tib tigre:tig tigrinya:tir timne:tem tlingit:tli tokelau:tkl tsimshian:tsi tsonga:tso tswana:tsn tumbuka:tum turkish:tur turkmen:tuk tuvalu:tvl tuvinian:tyv udmurt:udm ugaritic:uga uighur:uig ukrainian:ukr umbundu:umb undetermined:und urdu:urd uyghur:uig uzbek:uzb valencian:cat venda:ven vietnamese:vie volap\\xc3\\xbck:vol votic:vot walamo:wal walloon:wln waray:war washo:was welsh:wel wolof:wol xhosa:xho yakut:sah yapese:yap yiddish:yid yoruba:yor zande:znd zapotec:zap zenaga:zen zhuang:zha zulu:zul zuni:zun")
_A2, _B3, _NAME2C = {}, set(), {}
for _item in _ISO.split():
    _k, _v = _item.split(':')
    _A2.setdefault(_k, _v)
    _B3.add(_v)
_REGION3 = set(_REGIONS.split())
for _item in _NAMES.split():
    _k, _v = _item.split(':')
    _NAME2C.setdefault(_k, _v)
_C2A, _C2NAME = {}, {}
for _k, _v in _A2.items():
    _C2A.setdefault(_v, _k)
for _k, _v in _NAME2C.items():
    _C2NAME.setdefault(_v, _k)


def _log(msg, level=None):
    xbmc.log('[DexHub] subtitle watch: %s' % msg, xbmc.LOGINFO if level is None else level)


def kodi_lang(token):
    """نفس CLangCodeExpander::ConvertToISO6392B: يرجّع رمز اللغة أو ''."""
    low = str(token or '').strip().lower()
    if len(low) == 2:
        return _A2.get(low, '')
    if len(low) == 3:
        return low if (low in _B3 or low in _REGION3) else ''
    if len(low) > 3:
        return _NAME2C.get(low, '')
    return ''


def norm_lang(value):
    v = str(value or '').strip().lower()
    return _A2.get(v, v) if len(v) == 2 else v


def lang2(value):
    """ara → ar."""
    return _C2A.get(norm_lang(value), 'ar')


def lang_name(value):
    """ara → Arabic: نافذة الترجمات في كودي تاخذ لغة النسخة من اسم العنصر (Label)."""
    name = _C2NAME.get(norm_lang(value))
    return name.title() if name else 'Arabic'


# ── قاعدة كودي لاسم الترجمة الخارجية ──
def _is_url(p):
    return str(p or '').lower().startswith(('http://', 'https://'))


def _base_no_ext(path):
    p = str(path or '').split('|', 1)[0]   # صيغة كودي: رابط|Header=Value
    if _is_url(p):
        p = urlsplit(p).path                # كودي يخلي ترميز %xx مثل ما هو
    name = p.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]
    return os.path.splitext(name)[0]


def _ext(path):
    p = str(path or '').split('|', 1)[0]
    if _is_url(p):
        p = urlsplit(p).path
    return os.path.splitext(p)[1].lower()


def kodi_parts(video_path, sub_path):
    """يرجّع (tokens، لغة، forced؟، الاسم المتوقع بدون (External)، يبدأ باسم الفيديو؟)."""
    video_base = _base_no_ext(video_path)
    to_parse = _base_no_ext(sub_path)
    prefixed = bool(video_base) and to_parse.lower().startswith(video_base.lower())
    if prefixed:
        to_parse = to_parse[len(video_base):]
    m = re.search(r'[A-Za-z0-9]', to_parse)
    to_parse = to_parse[m.start():] if m else ''
    tokens = [t for t in _DELIMS.split(to_parse) if t]
    lang, forced, name = '', False, []
    for tok in reversed(tokens):
        low = tok.lower()
        if low in _FLAGS:
            forced = forced or low == 'forced'
            continue
        if not lang:
            code = kodi_lang(tok)
            if code:
                lang = code
                continue
        name.insert(0, tok)
    return [t.lower() for t in tokens], lang, forced, ' '.join(name).lower(), prefixed


def _external_labels():
    labels = ['(External)']
    try:
        loc = xbmc.getLocalizedString(21602)
        if loc and loc not in labels:
            labels.insert(0, loc)
    except Exception:
        pass
    return labels


def _jsonrpc(method, params=None):
    try:
        raw = xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}))
        return json.loads(raw).get('result')
    except Exception:
        return None


def video_player_id():
    for p in (_jsonrpc('Player.GetActivePlayers') or []):
        if (p or {}).get('type') == 'video':
            return p.get('playerid')
    return None


def current_subtitle(pid=None):
    """الترجمة الشغالة. None لو ما فيه فيديو. index = None لو الفيديو ما فيه ولا ترجمة."""
    if pid is None:
        pid = video_player_id()
    if pid is None:
        return None
    r = _jsonrpc('Player.GetProperties', {'playerid': pid, 'properties': ['currentsubtitle', 'subtitleenabled']})
    if r is None:
        return None
    cur = r.get('currentsubtitle') or {}
    raw_name = str(cur.get('name') or '')
    name = raw_name.strip()
    external = False
    for lab in _external_labels():
        if lab and name.lower().endswith(lab.lower()):
            name = name[:-len(lab)].strip()
            external = True
            break
    return {
        'index': cur.get('index'),
        'raw_name': raw_name,
        'name': name.lower(),
        'language': norm_lang(cur.get('language')),
        'forced': bool(cur.get('isforced')),
        'enabled': bool(r.get('subtitleenabled')),
        'external': external,
        'tokens': [t for t in _DELIMS.split(name.lower()) if t],
    }


# ── الملفات المرشحة ──
def _is_local(p):
    p = str(p or '')
    return p.startswith('/') or (len(p) > 2 and p[1] == ':' and p[2] in '\\/')


def _list(folder, prefix=''):
    """(اسم، مسار، mtime، حجم) لملفات الترجمة. prefix: بس اللي تبدأ فيه، قبل أي stat على الشبكة."""
    out = []
    folder = str(folder or '')
    if not folder:
        return out
    prefix = str(prefix or '').lower()
    try:
        if _is_local(folder):
            with os.scandir(folder) as it:
                for e in it:
                    if os.path.splitext(e.name)[1].lower() in SUB_EXTS and e.name.lower().startswith(prefix) and e.is_file():
                        st = e.stat()
                        out.append((e.name, e.path, st.st_mtime, st.st_size))
        else:
            if not folder.endswith('/'):
                folder += '/'
            _dirs, files = xbmcvfs.listdir(folder)
            for n in files:
                if os.path.splitext(n)[1].lower() in SUB_EXTS and n.lower().startswith(prefix):
                    p = folder + n
                    st = xbmcvfs.Stat(p)
                    out.append((n, p, float(st.st_mtime()), int(st.st_size())))
    except Exception:
        pass
    return out


def _local_subdirs(folder, newer_than):
    out = []
    try:
        with os.scandir(folder) as it:
            for e in it:
                if e.is_dir() and e.stat().st_mtime >= newer_than:
                    out.append(e.path)
    except Exception:
        pass
    return out


def _common_sub_dirs(folder):
    """مجلدات Subs و Subtitles ... داخل مجلد الفيديو (كودي يدور فيها)."""
    out = []
    try:
        if _is_local(folder):
            names = [e.name for e in os.scandir(folder) if e.is_dir()]
            sep = os.sep
        else:
            names = xbmcvfs.listdir(folder if folder.endswith('/') else folder + '/')[0]
            sep = '/'
        for n in names:
            if n.lower() in _SUB_DIRS:
                out.append(folder.rstrip('/\\') + sep + n)
    except Exception:
        pass
    return out


def _video_dir(media_url):
    base = str(media_url or '').split('|', 1)[0]
    low = base.lower()
    if _is_url(base) or not base or low.startswith(('plugin://', 'pvr://', 'stack://', 'upnp://')):
        return ''
    cut = max(base.rfind('/'), base.rfind('\\'))
    return base[:cut + 1] if cut >= 0 else ''


def item_subtitles():
    """الترجمات اللي أرفقتها الإضافة مع الفيديو (ListItem.setSubtitles → subtitle:1, subtitle:2 ...)."""
    out = []
    try:
        item = xbmc.Player().getPlayingItem()
        for i in range(1, 31):
            v = item.getProperty('subtitle:%d' % i)
            if not v:
                break
            out.append(v)
    except Exception:
        pass
    return out


def collect(media_url, since, own_dir, deep_since=None, network=True):
    """الملفات المرشحة: list of dict {path, name, mtime, size, where}.
    since: نسخ نافذة الترجمات في special://temp أحدث منه. deep_since: مجلدات الإضافات وملفاتنا.
    network=False: ما نلمس مجلدات smb/nfs (زر القائمة وقت البحث)."""
    deep_since = since if deep_since is None else deep_since
    video_base = _base_no_ext(media_url).lower()
    cands, seen = [], set()

    def add(n, p, mt, size, where):
        key = str(p).replace('\\', '/').lower()
        if key in seen or size <= 0 or size > MAX_BYTES:
            return
        seen.add(key)
        cands.append({'path': p, 'name': n, 'mtime': mt, 'size': size, 'where': where})

    # ترجماتنا (اختيارات DexSubtitles والنسخ المتزامنة ونسخ المراقب): لو طابقت نعرف إنها لنا
    own = os.path.normcase(os.path.abspath(own_dir)) if own_dir else ''
    if own:
        for n, p, mt, size in _list(own_dir):
            if mt >= deep_since:
                add(n, p, mt, size, 'self')
        for sub in ('autosync_cache', 'autosync_watch'):
            for n, p, mt, size in _list(os.path.join(own_dir, sub)):
                add(n, p, mt, size, 'self')

    temp = xbmcvfs.translatePath('special://temp/')
    for n, p, mt, size in _list(temp):
        if mt >= since:
            add(n, p, mt, size, 'temp')
    for d in _local_subdirs(temp, deep_since):
        if own and os.path.normcase(os.path.abspath(d)) == own:
            continue
        folders = [d]
        if os.path.basename(d.rstrip('/\\')).lower() == 'dexhub_subs':
            folders += _local_subdirs(d, deep_since)
        for f in folders:
            for n, p, mt, size in _list(f):
                if mt >= deep_since:
                    add(n, p, mt, size, 'addon')

    if video_base:
        # كودي يلقى الترجمات هنا بأسماء تبدأ باسم الفيديو، ونسخ نافذة الترجمات بنفس التسمية
        try:
            custom = xbmcvfs.translatePath('special://subtitles')
        except Exception:
            custom = ''
        vdir = _video_dir(media_url)
        folders = [(custom, 'custom')]
        if vdir:
            folders.append((vdir, 'video'))
            if network or _is_local(vdir):
                folders += [(d, 'video') for d in _common_sub_dirs(vdir)]
        for folder, where in folders:
            if folder and (network or _is_local(folder)):
                for n, p, mt, size in _list(folder, video_base):
                    add(n, p, mt, size, where)

    for v in item_subtitles():
        if _is_url(v):
            add(_base_no_ext(v) + (_ext(v) or '.srt'), v, 0.0, 1, 'item-url')
            continue
        p = xbmcvfs.translatePath(v) if v.lower().startswith('special://') else v
        try:
            if _is_local(p):
                st = os.stat(p)
                add(os.path.basename(p), p, st.st_mtime, st.st_size, 'item')
            elif network and xbmcvfs.exists(p):
                st = xbmcvfs.Stat(p)
                add(_base_no_ext(p) + _ext(p), p, float(st.st_mtime()), int(st.st_size()), 'item')
        except Exception:
            pass
    return cands


_WHERE_RANK = {'self': 6, 'item': 5, 'temp': 4, 'addon': 3, 'video': 2, 'custom': 2, 'item-url': 1}


def identify(cur, cands, media_url):
    """يطابق الترجمة الشغالة بملف. يرجّع (الملف أو None، السبب)."""
    if not cur or not cur.get('external'):
        return None, 'الترجمة الشغالة مو خارجية'
    want_tokens = Counter(cur.get('tokens') or [])
    want_name = ' '.join(cur.get('tokens') or [])
    want_lang = cur.get('language') or ''
    scored = []
    for c in cands:
        tokens, lang, forced, name, prefixed = kodi_parts(media_url, c['name'] if c['where'] == 'item-url' else c['path'])
        if lang != want_lang:            # نفس اللغة اللي طلعها كودي من اسم الملف بالضبط
            continue
        if bool(cur.get('forced')) != forced:
            continue
        have = Counter(tokens)
        if any(have[t] < n for t, n in want_tokens.items()):
            continue
        exact = 1 if name == want_name else 0
        if not want_tokens:
            # الاسم فاضي («(External)» بس): الملف اسمه اسم الفيديو واللغة مثل ما يسميه كودي
            if not exact or not (prefixed or c['where'] in ('item', 'self')):
                continue
        scored.append(((exact, _WHERE_RANK.get(c['where'], 0), c['mtime']), c))
    if not scored:
        return None, 'ما لقيت ملف الترجمة الشغالة'
    scored.sort(key=lambda x: x[0], reverse=True)
    top_key, top = scored[0]
    for key, other in scored[1:]:
        if key[0] != top_key[0]:
            break
        if top['where'] == 'temp' and top_key[2] > key[2] + 1:
            break   # نسخة جديدة من نافذة الترجمات للتو
        if top['where'] == 'item-url' or other['where'] == 'item-url':
            return None, 'أكثر من ترجمة بالمصدر نفس الاسم'
        h1, h2 = _file_hash(top['path']), _file_hash(other['path'])
        if not h1 or h1 != h2:
            return None, 'أكثر من ملف يطابق الترجمة الشغالة'
    return top, ''


# ── نسخ الترجمة عندنا ──
def _read_any(path, session=None):
    if _is_url(path):
        try:
            import requests
        except Exception:
            from . import minireq as requests
        r = (session or requests).get(path, timeout=20, verify=False, stream=True)
        if r.status_code != 200:
            return b''
        data = b''
        for chunk in r.iter_content(65536):
            data += chunk
            if len(data) > MAX_BYTES:
                return b''
        if data[:2] == b'PK':
            import zipfile
            z = zipfile.ZipFile(io.BytesIO(data))
            for fn in z.namelist():
                if os.path.splitext(fn)[1].lower() in SUB_EXTS:
                    return z.read(fn)[:MAX_BYTES + 1]
            return b''
        if data[:2] == b'\x1f\x8b':
            import gzip
            data = gzip.decompress(data)
        return data
    if _is_local(path):
        with io.open(path, 'rb') as f:
            return f.read(MAX_BYTES + 1)
    f = xbmcvfs.File(path)
    try:
        return bytes(f.readBytes(MAX_BYTES + 1))
    finally:
        f.close()


def _file_hash(path):
    try:
        data = _read_any(path)
        return hashlib.sha1(data).hexdigest() if data else ''
    except Exception:
        return ''


def snapshot(cand, dest_dir, session=None):
    """ينسخ الترجمة لمجلدنا (لو كودي أو الإضافة كتبوا فوقها بعدين ما تتأثر المزامنة). يرجّع (مسار، sha1)."""
    try:
        data = _read_any(cand['path'], session)
    except Exception as e:
        _log('read failed: %s' % e, xbmc.LOGWARNING)
        return None, ''
    if not data or len(data) > MAX_BYTES:
        return None, ''
    h = hashlib.sha1(data).hexdigest()
    ext = _ext(cand.get('name') or cand['path'])
    if ext not in SUB_EXTS:
        ext = '.srt'
    safe = re.sub(r'[^\w\-. ]+', ' ', os.path.splitext(cand.get('name') or 'subtitle')[0]).strip()[:60] or 'subtitle'
    out = os.path.join(dest_dir, '%s.%s%s' % (safe, h[:10], ext))
    if not os.path.exists(out):
        tmp = out + '.tmp'
        with io.open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, out)
    return out, h


def _stat(path):
    try:
        if _is_local(path):
            st = os.stat(path)
            return (st.st_mtime, st.st_size)
        st = xbmcvfs.Stat(path)
        return (float(st.st_mtime()), int(st.st_size()))
    except Exception:
        return None


# ── المراقب (دورة كل ثانية داخل خدمة الإضافة) ──
class Watcher(object):
    def __init__(self, autosync, setting_bool, own_dir):
        self.autosync = autosync
        self.setting_bool = setting_bool
        self.own_dir = own_dir
        self.busy = False
        self.reset()

    def reset(self, media=None):
        self.media = media
        self.start = time.time()
        self.kind = ''
        self.pid = None
        self.sig = None
        self.due = 0.0
        self.done = set()
        self.watch = None   # ملف الترجمة الشغالة: كودي يكتب الاختيار الجاي بنفس المسار ونفس رقم الترجمة

    def _spawn(self, fn, *args):
        def run():
            try:
                fn(*args)
            except Exception as e:
                _log('%s failed: %s' % (getattr(fn, '__name__', 'job'), e), xbmc.LOGWARNING)
            finally:
                self.busy = False
        self.busy = True
        try:
            threading.Thread(target=run, daemon=True).start()
        except Exception as e:
            self.busy = False
            _log('thread start failed: %s' % e, xbmc.LOGWARNING)

    def tick(self, player):
        if not (self.setting_bool('autosync_enabled', True) and self.setting_bool('autosync_any', True)):
            return
        try:
            playing = player.isPlayingVideo()
            media = player.getPlayingFile() if playing else ''
        except Exception:
            playing, media = False, ''
        if not playing or not media:
            if self.media:
                self.reset()
            return
        now = time.time()
        if media != self.media:
            self.reset(media)
            self.kind = self.autosync._media_kind(media)
            return
        if not self.kind or now - self.start < SETTLE_S:
            return
        if self.pid is None:
            self.pid = video_player_id()
        cur = current_subtitle(self.pid)
        if cur is None:
            self.pid = None
            return
        sig = (cur['index'], cur['name'], cur['language'], cur['enabled'])
        if sig != self.sig:
            self.sig = sig
            self.due = now + DEBOUNCE_S
            return
        w = self.watch
        if w and not self.due:
            if w['local']:
                if _stat(w['path']) != w['stat']:
                    self.due = now + DEBOUNCE_S
            elif now >= w['next'] and not self.busy:
                w['next'] = now + VFS_CHECK_S
                self._spawn(self._check_remote, w, self.media)
        if not self.due or now < self.due:
            return
        if self.busy:
            self.due = now + 0.5
            return
        self.due = 0.0
        if not (cur['enabled'] and cur['external']):
            self.watch = None
            return
        self._spawn(self.handle, cur, now, self.media)

    def _check_remote(self, w, media):
        if media == self.media and self.watch is w and _stat(w['path']) != w['stat']:
            self.due = time.time() + DEBOUNCE_S

    def handle(self, cur, now, media):
        cands = collect(media, since=self.start - 5, own_dir=self.own_dir, deep_since=self.start - 900)
        cands = [c for c in cands if c['where'] != 'item-url']  # الروابط للزر بس: ما نحمّل من الشبكة بالخلفية
        pick, why = identify(cur, cands, media)
        if media != self.media:
            return
        if not pick:
            self.watch = None
            _log('%s (%s)' % (why, cur['name'] or cur['language'] or '?'))
            return
        if pick['where'] == 'self':
            self.watch = None
            return  # ترجمة من DexSubtitles نفسه أو نسخة متزامنة: لها طريقها
        self.watch = {'path': pick['path'], 'stat': _stat(pick['path']), 'local': _is_local(pick['path']),
                      'next': time.time() + VFS_CHECK_S}
        if pick['where'] == 'temp' and abs(pick['mtime'] - self.autosync.last_live_pick_at()) < 8:
            return  # كودي نسخ ترجمة AI المباشرة من DexSubtitles للتو (ملفها يكبر فبصمته تتغير)
        snap, h = snapshot(pick, self.autosync.WATCH_DIR)
        if not snap or h in self.done:
            return
        self.done.add(h)
        info = self.autosync.handled_info(h)
        is_ai = bool(info and info.get('ai'))
        if info and not is_ai and time.time() - float(info.get('t') or 0) < 1800:
            # اختيار من DexSubtitles وله مهمته: نربطها بنسخة كودي عشان اختيار جديد فوقها يلغيها
            self.autosync.attach_source(h, media, pick['path'], self.watch['stat'])
            return
        # watch: اخترتها أنت من نافذة الترجمات (نسخة جديدة بعد بداية التشغيل)، فتنبيه بأي نتيجة.
        # startup: جنب الفيديو أو أرفقتها إضافة لحالها، فتنبيه بس لو انضبطت.
        if pick['where'] == 'temp' or (pick['where'] in ('video', 'custom') and pick['mtime'] > self.start + 1):
            origin = 'watch'
        else:
            origin = 'startup'
            if _XTREAM.search(str(media).split('|', 1)[0].split('?', 1)[0]):
                _log('startup skip: IPTV link (one connection)')
                return
        _log('%s: %s (%s)' % (origin, pick['name'], pick['where']))
        self.autosync.enqueue(snap, media_url=media, display_name=os.path.splitext(pick['name'])[0],
                              provider='kodi:' + pick['where'], origin=origin, quiet=True, is_ai=is_ai,
                              expect={'index': cur['index'], 'name': cur['raw_name']},
                              source={'path': pick['path'], 'stat': list(self.watch['stat'] or [])})
