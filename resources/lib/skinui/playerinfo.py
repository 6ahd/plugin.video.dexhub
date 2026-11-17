# -*- coding: utf-8 -*-
"""The player's info page. tick() runs every second while it is open and fills
Window(Home).Property(dh.ppi.*). Only the server's name shows as location, never the address."""
import json
import os
import re
import time
import urllib.parse
import zlib
import struct

import xbmc
import xbmcgui
import xbmcvfs

HOME = xbmcgui.Window(10000)

def _arabic():
    """Dex Hub's language choice, else Kodi's."""
    try:
        import xbmcaddon
        return xbmcaddon.Addon("plugin.video.dexhub").getSetting("ui_language").strip().lower().startswith("ar")
    except Exception:  # noqa: BLE001
        return xbmc.getLanguage(xbmc.ISO_639_1) == "ar"


AR = False
WORDS = {'SOURCE': 'المصدر', 'VIDEO': 'الفيديو', 'AUDIO': 'الصوت', 'NETWORK': 'الشبكة', 'SYSTEM': 'الجهاز', 'File': 'الملف', 'Location': 'الموقع', 'Type': 'النوع', 'Bitrate': 'السرعة', 'Format': 'الصيغة', 'Decoder': 'فك الترميز', 'Deinterlace': 'إزالة التشابك', 'Display': 'الشاشة', 'Queue': 'الطابور', 'Buffer': 'التخزين', 'CPU clock': 'تردد المعالج', 'Memory': 'الذاكرة', 'Player info': 'معلومات التشغيل', 'Picture': 'الصورة', 'Sound': 'الصوت', 'Subtitles': 'الترجمة', 'Container': 'الحاوية', 'Resolution': 'الدقة', 'Track': 'المسار', 'Quality': 'الجودة', 'Temperature CPU': 'حرارة المعالج', 'Temperature GPU': 'حرارة الرسوميات', 'Render': 'الرسم', 'Video queue': 'طابور الفيديو', 'Overview': 'نظرة عامة', 'switch': 'للتبديل', 'Aspect ratio': 'النسبة', 'Frame rate': 'الإطارات', 'Scan': 'المسح', 'Brightness · HDR': 'السطوع · HDR', 'Colour': 'الألوان', 'From file to screen': 'من الملف إلى الشاشة', 'Picture bitrate': 'سرعة الصورة', 'From file to your speakers': 'من الملف إلى السماعات', 'What you hear': 'اللي تسمعه', 'Output': 'الخرج', 'Device': 'الجهاز', 'Configuration': 'الإعداد', 'Volume': 'الصوت', 'Audio queue': 'طابور الصوت', 'sofa': 'الكنبة', 'HEIGHT': 'الارتفاع', 'Playing': 'شغال', 'this device': 'هذا الجهاز', 'progressive': 'تدريجي', 'interlaced': 'متشابك', 'Off': 'مطفية', 'Local file': 'ملف محلي', 'Stream · internet': 'بث · إنترنت', 'not needed': 'ما يحتاج', 'hardware ✓': 'عتادي ✓', 'drawn per second': 'رسم في الثانية', 'decodes itself': 'يفك الترميز', 'untouched ✓': 'بدون تغيير ✓', 'Out': 'الخرج', 'Receiver': 'المستقبل', 'plays': 'يشغّل', 'Speakers': 'السماعات', 'Fixed': 'محدد', 'Best match': 'الأفضل', 'Optimized': 'الأنسب', 'no delay': 'بدون تأخير', 'muted': 'صامت', 'Your room': 'غرفتك', 'Channels': 'القنوات', 'Audio tracks': 'مسارات الصوت', 'Unknown': 'غير محددة', 'standard definition': 'دقة عادية', '1 billion colours': 'مليار لون', '16.7 million colours': '16.7 مليون لون', 'Colours: ': 'الألوان: ', 'Transfer: ': 'النقل: '}
LABELS = {'31190': 'SOURCE', '31191': 'VIDEO', '31192': 'AUDIO', '31193': 'NETWORK', '31194': 'SYSTEM', '31195': 'File', '31196': 'Location', '31197': 'Type', '31198': 'Bitrate', '31200': 'HDR', '31201': 'Format', '31202': 'Decoder', '31203': 'Deinterlace', '31204': 'Display', '31205': 'Queue', '31208': 'Buffer', '31212': 'CPU clock', '31213': 'Memory', '31726': 'Player info', '31727': 'Picture', '31728': 'Sound', '31729': 'Subtitles', '31730': 'Container', '31731': 'Resolution', '31732': 'Track', '31733': 'Quality', '31734': 'Temperature CPU', '31735': 'Temperature GPU', '31736': 'Render', '31737': 'Video queue', '31738': 'Overview', '31739': 'switch', '31740': 'Resolution', '31741': 'Aspect ratio', '31742': 'Frame rate', '31743': 'Scan', '31744': 'Brightness · HDR', '31745': 'Colour', '31746': 'From file to screen', '31747': 'Picture bitrate', '31748': 'From file to your speakers', '31749': 'What you hear', '31750': 'Output', '31751': 'Device', '31752': 'Configuration', '31753': 'Volume', '31754': 'Audio queue', '31755': 'sofa', '31756': 'HEIGHT', '31757': 'Playing'}
P = "dh.ppi."
_STATE = {"hist": [], "vhist": [], "peak": 0.0, "set": {}, "sett": 0.0, "path": None, "size": "", "rpc": 0.0, "tracks": {}, "maxclk": 0.0, "spark": 0, "open": False}
SPARK_DIR = "special://temp/dexhub_ppi/"


def t(en, nl):
    return WORDS.get(en, en) if AR else en

def chan_word(n):
    return t(f"{n} channel", f"{n} kanaal") if str(n) == "1" else t(f"{n} channels", f"{n} kanalen")


def app_volume():
    """Kodi's own volume in percent (Player.Volume only gives dB)."""
    try:
        req = {"jsonrpc": "2.0", "id": 1, "method": "Application.GetProperties", "params": {"properties": ["volume", "muted"]}}
        res = json.loads(xbmc.executeJSONRPC(json.dumps(req))).get("result") or {}
        return res.get("volume"), bool(res.get("muted"))
    except Exception:
        return None, xbmc.getCondVisibility("Player.Muted")



def info(name):
    value = xbmc.getInfoLabel(name)
    return "" if value == name else value              # Kodi gives the name back for a label it does not know


DV2_NAME = re.compile(r"(?i)(?<![a-z0-9])(?:dv2|dovi[ ._-]?2|dolby[ ._-]?vision[ ._-]?2)(?![a-z0-9])")


def dv_from_name(name):
    """What the release name says about Dolby Vision: Dolby Vision 2, the profile (P5 / P7 / P8) and the layer (FEL / MEL)."""
    name = name or ""
    prof = re.search(r"(?i)(?<![a-z0-9])(?:dv|dovi)?[ ._-]?p([578])(?:[ ._]\d)?(?![0-9a-z])", name)
    layer = re.search(r"(?i)(?<![a-z0-9])(fel|mel)(?![a-z0-9])", name)
    return {"dv2": bool(DV2_NAME.search(name)), "prof": prof.group(1) if prof else "", "layer": layer.group(1).upper() if layer else ""}


def dv_line(hdr, prof, layer, dvn):
    """Video tab: what Kodi, the file name and the box + screen say about Dolby Vision, and what a Dolby Vision 2 TV does with it."""
    if hdr != "dolbyvision":
        put("v.dv.on", "")
        return
    put("v.dv.on", "1")
    kodi = "Dolby Vision" + ((t(" · profile ", " · profiel ") + prof.group(1) + (f" {layer}" if layer else "")) if prof else "")
    named = " · ".join(x for x in ((f"P{dvn['prof']}" + (f" {dvn['layer']}" if dvn["layer"] else "")) if dvn.get("prof") else dvn.get("layer", ""),
                                    "DV2" if dvn.get("dv2") else "") if x) or t("nothing extra", "niets extra")
    hdr_types = info("System.SupportedHDRTypes").lower()
    screen = t("Dolby Vision ✓", "Dolby Vision ✓") if "dolby" in hdr_types else (t("no Dolby Vision reported", "geen Dolby Vision gemeld") if hdr_types
                                                                                    else t("not reported", "niet gemeld"))
    v = "[COLOR FFFFFFFF][B]{}[/B][/COLOR]"
    put("v.dv.line", "   ·   ".join((t("Kodi: ", "Kodi: ") + v.format(kodi), t("File name: ", "Bestandsnaam: ") + v.format(named),
                                     t("Box + screen: ", "Box + scherm: ") + v.format(screen))))
    put("v.dv.note", t("The release says Dolby Vision 2: a TV with Dolby Vision 2 uses its extra information.",
                       "De release noemt Dolby Vision 2: een tv met Dolby Vision 2 gebruikt die extra informatie.") if dvn.get("dv2") else
        t("A TV with Dolby Vision 2 applies its new picture processing to this Dolby Vision picture by itself.",
          "Een tv met Dolby Vision 2 past zijn nieuwe beeldverwerking zelf toe op dit Dolby Vision-beeld."))


def put(key, value):
    value = "" if value is None else str(value)
    if HOME.getProperty(P + key) != value:
        HOME.setProperty(P + key, value)


def num(text, default=0.0):
    m = re.search(r"-?\d+(?:[.,]\d+)?", text or "")
    return float(m.group(0).replace(",", ".")) if m else default


def step(percent, steps=40):
    try:
        return max(0, min(steps, round(float(percent) * steps / 100.0)))
    except (TypeError, ValueError):
        return 0


# ----------------------------------------------------------------------------- names
ACODEC = {"truehd_atmos": "TrueHD Atmos", "eac3_ddp_atmos": "Dolby Digital+ Atmos", "dtshd_ma_x_imax": "DTS:X IMAX",
          "dtshd_ma_x": "DTS:X", "truehd": "TrueHD", "dtshd_ma": "DTS-HD MA", "dtshd_hra": "DTS-HD HRA", "dts": "DTS",
          "eac3": "Dolby Digital+", "ac3": "Dolby Digital", "aac": "AAC", "flac": "FLAC", "opus": "Opus", "mp3": "MP3",
          "vorbis": "Vorbis", "mp2": "MP2"}
ABADGE = {"truehd_atmos": "TrueHD", "truehd": "TrueHD", "eac3_ddp_atmos": "DD+", "eac3": "DD+", "ac3": "DD",
          "dtshd_ma_x_imax": "DTS-HD MA", "dtshd_ma_x": "DTS-HD MA", "dtshd_ma": "DTS-HD MA", "dtshd_hra": "DTS-HD HRA",
          "dts": "DTS", "aac": "AAC", "flac": "FLAC", "opus": "OPUS", "mp3": "MP3"}
VCODEC = {"hevc": "HEVC", "h264": "H.264", "av1": "AV1", "vp9": "VP9", "mpeg2video": "MPEG-2", "vc1": "VC-1"}
CONTAINER = {"mkv": "Matroska (MKV)", "mp4": "MPEG-4 (MP4)", "m4v": "MPEG-4 (M4V)", "ts": "MPEG-TS", "m2ts": "Blu-ray (M2TS)",
             "avi": "AVI", "webm": "WebM", "mov": "QuickTime (MOV)", "iso": "ISO", "m3u8": "HLS (M3U8)", "mpd": "DASH (MPD)",
             "wmv": "WMV", "flv": "FLV"}
CHANNELS = {1: "1.0", 2: "2.0", 3: "2.1", 6: "5.1", 7: "6.1", 8: "7.1"}
LANG_NL = {"eng": "Engels", "dut": "Nederlands", "nld": "Nederlands", "ger": "Duits", "deu": "Duits", "fre": "Frans",
           "fra": "Frans", "spa": "Spaans", "ita": "Italiaans", "por": "Portugees", "jpn": "Japans", "kor": "Koreaans",
           "chi": "Chinees", "zho": "Chinees", "rus": "Russisch", "ara": "Arabisch", "tur": "Turks", "pol": "Pools",
           "swe": "Zweeds", "nor": "Noors", "dan": "Deens", "fin": "Fins", "ukr": "Oekraïens", "cze": "Tsjechisch",
           "ces": "Tsjechisch", "hin": "Hindi", "gre": "Grieks", "ell": "Grieks", "hun": "Hongaars", "heb": "Hebreeuws"}


def lang_name(code):
    code = (code or "").lower()
    if not code or code in ("und", "unk"):
        return ""
    if False and code in LANG_NL:
        return LANG_NL[code]
    try:
        return xbmc.convertLanguage(code, xbmc.ENGLISH_NAME) or code.upper()
    except Exception:  # noqa: BLE001
        return code.upper()


def lang_short(code):
    try:
        two = xbmc.convertLanguage(code or "", xbmc.ISO_639_1)
    except Exception:  # noqa: BLE001
        two = ""
    return (two or code or "").upper()[:3]


def middle(text, keep=64):
    """A long file name with the middle left out: 'The.Expanse.S03E07.2160p … TrueHD.Atmos.7.1-FGT.mkv'."""
    if len(text) <= keep:
        return text
    head = keep * 55 // 100
    return f"{text[:head].rstrip('.')} … {text[-(keep - head - 3):].lstrip('.')}"


def location(path):
    """Only the server name (or the add-on, or 'local'), never the whole address."""
    if not path:
        return ""
    if path.startswith("plugin://"):
        addon = urllib.parse.urlparse(path).netloc
        try:
            import xbmcaddon
            return xbmcaddon.Addon(addon).getAddonInfo("name")
        except Exception:  # noqa: BLE001
            return addon
    parts = urllib.parse.urlparse(path)
    if parts.scheme in ("http", "https", "rtmp", "rtsp", "udp", "rtp", "mms"):
        host = (parts.hostname or "").lower()
        return host[4:] if host.startswith("www.") else host
    if parts.scheme in ("smb", "nfs", "ftp", "sftp", "dav", "davs"):
        return f"{parts.hostname or ''} · {parts.scheme.upper()}"
    return t("this device", "dit apparaat")


def local_size(path):
    if not path or "://" in path and not path.startswith(("smb://", "nfs://", "special://")):
        return ""
    try:
        size = xbmcvfs.Stat(path).st_size()
    except Exception:  # noqa: BLE001
        return ""
    if not size:
        return ""
    return f"{size / 1073741824:.1f} GB" if size >= 1073741824 else f"{size / 1048576:.0f} MB"


# ----------------------------------------------------------------------------- tracks (JSON-RPC, every 5 s)
def tracks(now):
    if now - _STATE["rpc"] < 5 and _STATE["tracks"]:
        return _STATE["tracks"]
    _STATE["rpc"] = now
    out = {}
    try:
        req = {"jsonrpc": "2.0", "id": 1, "method": "Player.GetProperties",
               "params": {"playerid": 1, "properties": ["currentaudiostream", "audiostreams", "currentsubtitle", "subtitles",
                                                        "subtitleenabled", "currentvideostream"]}}
        res = json.loads(xbmc.executeJSONRPC(json.dumps(req))).get("result") or {}
        out = res
    except Exception:  # noqa: BLE001
        pass
    _STATE["tracks"] = out
    return out


# ----------------------------------------------------------------------------- the bitrate graph (a small PNG)
def _png(width, height, rows):
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(kind, data):
        c = struct.pack(">I", len(data)) + kind + data
        return c + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def spark(values, W=760, H=60, tag="spark"):
    """Area and line of the last values (shown at half size), colour #7CC4FF."""
    vals = values[-40:]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or max(hi, 1.0)
    ys = []
    for x in range(W):
        pos = x / (W - 1) * (len(vals) - 1)
        i = int(pos)
        f = pos - i
        v = vals[i] if i + 1 >= len(vals) else vals[i] * (1 - f) + vals[i + 1] * f
        ys.append(H - 4 - (v - lo) / span * (H - 12) - (H * 0.1 if hi == lo else 0))
    rows = []
    for y in range(H):
        row = bytearray(W * 4)
        for x in range(W):
            ly = ys[x]
            prev = ys[x - 1] if x else ly
            top, bot = min(ly, prev) - 2.5, max(ly, prev) + 2.5
            if top <= y <= bot:
                a = 255
            elif y > ly:
                a = 46
            else:
                continue
            row[x * 4:x * 4 + 4] = bytes((0x7C, 0xC4, 0xFF, a))
        rows.append(row)
    folder = xbmcvfs.translatePath(SPARK_DIR)
    os.makedirs(folder, exist_ok=True)
    _STATE["spark"] += 1
    name = f"{tag}_{int(time.time())}_{_STATE['spark']}.png"
    with open(os.path.join(folder, name), "wb") as handle:
        handle.write(_png(W, H, rows))
    for old in os.listdir(folder):
        if old != name and old.startswith(tag + "_"):
            try:
                if time.time() - os.path.getmtime(os.path.join(folder, old)) > 4:
                    os.remove(os.path.join(folder, old))
            except OSError:
                pass
    return SPARK_DIR + name


# ----------------------------------------------------------------------------- one update
def visible():
    return bool(HOME.getProperty(P + "open")) and xbmc.getCondVisibility("Player.HasVideo")


def tick():
    if not visible():
        if _STATE["open"]:
            _STATE.update(open=False, hist=[], vhist=[], peak=0.0)
        return
    _STATE["open"] = True
    now = time.time()
    path = info("Player.Filenameandpath")
    if path != _STATE["path"]:
        _STATE.update(path=path, hist=[], vhist=[], peak=0.0, size=local_size(path), rpc=0.0)

    # header
    show = info("VideoPlayer.TVShowTitle")
    title = info("VideoPlayer.Title")
    if show:
        s, e = info("VideoPlayer.Season"), info("VideoPlayer.Episode")
        se = f"S{s} E{e}" if s and e else ""
        put("title", " · ".join(x for x in (show, se, title) if x))
    else:
        year = info("VideoPlayer.Year")
        put("title", f"{title} ({year})" if title and year else title or re.split(r"[?|]", info("Player.Filename"))[0])
    tm = f"{info('Player.Time')} / {info('Player.Duration')}" if info("Player.Duration") else info("Player.Time")
    try:
        chap, chaps = int(info("Player.Chapter") or 0), int(info("Player.ChapterCount") or 0)
    except ValueError:
        chap = chaps = 0
    if chaps > 1 and chap:
        tm += t(f" · chapter {chap} of {chaps}", f" · hoofdstuk {chap} van {chaps}")
    put("time", tm)

    # video facts
    w, h = info("Player.Process(videowidth)").replace(",", "").replace(".", ""), info("Player.Process(videoheight)").replace(",", "").replace(".", "")
    fps = info("Player.Process(videofps)")
    res = info("VideoPlayer.VideoResolution")
    res_badge = {"8K": "8K", "4K": "4K", "1080": "1080p", "720": "720p", "576": "576p", "540": "540p", "480": "480p"}.get(res, res.upper())
    hdr = info("VideoPlayer.HdrType").lower()
    detail = info("VideoPlayer.HdrDetail")
    prof = re.search(r"(\d+)", detail or "")
    layer = "FEL" if "FEL" in (detail or "").upper() else "MEL" if "MEL" in (detail or "").upper() else ""
    pix = info("Player.Process(pixformat)")
    ten = bool(re.search(r"10|12", pix or ""))
    vcodec = info("VideoPlayer.VideoCodec").lower()
    vname = VCODEC.get(vcodec, vcodec.upper())
    fname = re.split(r"[?|]", info("Player.Filename"))[0]
    dvn = dv_from_name(fname + " " + info("Player.FilenameAndPath")) if hdr == "dolbyvision" else {}

    # audio facts
    acodec = info("VideoPlayer.AudioCodec").lower()
    try:
        chn = int(info("VideoPlayer.AudioChannels") or 0)
    except ValueError:
        chn = 0
    chs = CHANNELS.get(chn, f"{chn}.0" if chn else "")
    aname_stream = info("VideoPlayer.AudioName") or ""
    atmos = "atmos" in acodec or "atmos" in aname_stream.lower()
    dtsx = acodec.startswith("dtshd_ma_x") or "dts:x" in aname_stream.lower() or "dts-x" in aname_stream.lower()
    along = ACODEC.get(acodec, acodec.upper())
    if atmos and "Atmos" not in along:
        along += " Atmos"
    rate = float(re.sub(r"\D", "", info("Player.Process(audiosamplerate)") or "") or 0)   # '44,100' / '48.000': thousands marks
    bits = info("Player.Process(audiobitspersample)")

    # badges
    put("b.res", res_badge)
    if hdr == "dolbyvision":
        put("b.dv", ("DOLBY VISION 2" if dvn.get("dv2") else "DOLBY VISION") + (f" P{prof.group(1)}" if prof else "") + (f" {layer}" if layer else ""))
        put("b.hdr", "")
    else:
        put("b.dv", "")
        put("b.hdr", {"hdr10": "HDR10", "hdr10plus": "HDR10+", "hlg": "HLG"}.get(hdr, ""))
    put("b.vc", f"{vname} 10-bit" if vname and ten else vname)
    put("b.remux", "REMUX" if re.search(r"remux", fname or "", re.I) else "")
    put("b.ac", ABADGE.get(acodec, acodec.upper()))
    put("b.imm", f"ATMOS {chs}".strip() if atmos else f"DTS:X {chs}".strip() if dtsx else "")
    put("b.ch", "" if (atmos or dtsx) else chs)
    subs_on = xbmc.getCondVisibility("VideoPlayer.SubtitlesEnabled")
    sl = info("VideoPlayer.SubtitlesLanguage")
    put("b.sub", t(f"{lang_short(sl)} subtitles", f"{lang_short(sl)} ondertitels") if subs_on and sl else "")

    # tiles
    hdr_word = {"dolbyvision": "Dolby Vision", "hdr10": "HDR10", "hdr10plus": "HDR10+", "hlg": "HLG"}.get(hdr, "")
    put("t1.big", f"{res_badge} HDR" if hdr_word else res_badge)
    put("t1.sub", " · ".join(x for x in (f"{w}×{h}" if w and h else "", fps) if x))
    put("t2.big", hdr_word or "SDR")
    put("t2.sub", t(f"profile {prof.group(1)}", f"profiel {prof.group(1)}") + (f" {layer}" if layer else "") if hdr == "dolbyvision" and prof else "")
    put("t3.big", (f"Atmos {chs}" if atmos else f"DTS:X {chs}" if dtsx else chs or along).strip())
    khz = (f"{rate / 1000:g} kHz" if rate else "")
    put("t3.sub", " · ".join(x for x in (ABADGE.get(acodec, along) if (atmos or dtsx or chs) else "", khz) if x))
    vbr = num(info("Player.Process(videolivebitrate)"))
    abr_k = float(re.sub(r"\D", "", info("Player.Process(audiolivebitrate)") or "") or 0)
    abr = abr_k / 1024.0
    total = vbr + abr
    if total:
        _STATE["hist"] = (_STATE["hist"] + [total])[-40:]
    put("t4.big", f"{total:.1f} Mb/s" if total else "–")
    put("t4.sub", t(f"picture {vbr:.1f} · audio {abr:.1f}", f"beeld {vbr:.1f} · audio {abr:.1f}") if total else "")
    if len(_STATE["hist"]) >= 2:
        try:
            put("spark", spark(_STATE["hist"]))
        except Exception as error:  # noqa: BLE001
            xbmc.log(f"[plugin.video.dexhub] playerinfo spark: {error}", xbmc.LOGINFO)
    cache = num(info("Player.CacheLevel"))
    ahead = ""
    try:
        total_s = xbmc.Player().getTotalTime()
        pc, pp = num(info("Player.ProgressCache")), num(info("Player.Progress"))
        if total_s and pc > pp:
            ahead_s = int((pc - pp) / 100.0 * total_s)
            ahead = t(f"{ahead_s} s ahead", f"{ahead_s} s vooruit") if ahead_s < 600 else t(f"{ahead_s // 60} min ahead", f"{ahead_s // 60} min vooruit")
    except Exception:  # noqa: BLE001
        pass
    put("t5.big", f"{cache:.0f}%")
    put("t5.sub", ahead)
    put("t5.bar", step(cache))

    # system
    cpu_text = info("System.CpuUsage")
    cores = [float(x) for x in re.findall(r"#\d+:\s*([\d.,]+)%", cpu_text.replace(",", "."))]
    avg = sum(cores) / len(cores) if cores else num(cpu_text)
    put("cores", min(len(cores), 8))
    for i in range(8):
        put(f"core{i}", max(1, min(27, round(cores[i] * 27 / 100))) if i < len(cores) else "")
    put("cores.label", t(f"CPU per core · avg. {avg:.0f}%", f"CPU per kern · gem. {avg:.0f}%") if cores else t(f"CPU · {avg:.0f}%", f"CPU · {avg:.0f}%"))
    ct, gt = num(info("System.CPUTemperature"), -1), num(info("System.GPUTemperature"), -1)
    mem_p = num(info("System.Memory(used.percent)"))
    mem_u = info("System.Memory(used)").replace("MB", " MB").replace("GB", " GB").replace("  ", " ")
    put("t6.big", f"CPU {avg:.0f}%")
    put("t6.sub", " · ".join(x for x in (f"{ct:.0f} °C" if ct >= 0 else "", t(f"{mem_p:.0f}% memory", f"{mem_p:.0f}% geheugen")) if x))
    put("t6.bar", step(mem_p))
    put("m.ct", f"{ct:.0f} °C" if ct >= 0 else "–")
    put("m.ct.bar", step(ct) if ct >= 0 else 0)
    put("m.gt", f"{gt:.0f} °C" if gt >= 0 else "–")
    put("m.gt.bar", step(gt) if gt >= 0 else 0)
    put("m.mem", f"{mem_p:.0f}% · {mem_u}".strip(" ·"))
    put("m.mem.bar", step(mem_p))
    clk = num(info("System.CpuFrequency"))
    if not _STATE["maxclk"]:
        try:
            with open("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq") as handle:
                _STATE["maxclk"] = int(handle.read().strip()) / 1000.0
        except (OSError, ValueError):
            _STATE["maxclk"] = 0.0
    _STATE["maxclk"] = max(_STATE["maxclk"], clk)
    put("m.clk", f"{clk:.0f} MHz" if clk else "–")
    put("m.clk.bar", step(clk / _STATE["maxclk"] * 100) if clk and _STATE["maxclk"] else 0)
    rfps = num(info("System.FPS"))
    vfps = num(fps)
    put("m.fps", f"{rfps:.2f} fps" if rfps else "–")
    put("m.fps.bar", step(min(100.0, rfps / vfps * 100)) if rfps and vfps else 0)
    vq = num(info("Player.Process(videoqueuelevel)"))
    aq = num(info("Player.Process(audioqueuelevel)"))
    put("m.vq", f"{vq:.0f}%")
    put("m.vq.bar", step(vq))

    # rows: source
    put("r.file", middle(fname or ""))
    stream = xbmc.getCondVisibility("Player.IsInternetStream")
    loc = location(path)
    put("r.loc", f"{loc} · stream" if stream and loc else loc)
    ext = os.path.splitext(urllib.parse.urlparse(fname or "").path)[1].lower().lstrip(".")
    put("r.cont", " · ".join(x for x in (CONTAINER.get(ext, ext.upper()), _STATE["size"]) if x))
    put("r.rate", t(f"{total:.1f} Mb/s · picture {vbr:.1f} · audio {abr:.1f}", f"{total:.1f} Mb/s · beeld {vbr:.1f} · audio {abr:.1f}") if total else "–")
    # video
    scan = info("Player.Process(videoscantype)").lower()
    scan_word = t("progressive", "progressief") if scan.startswith("p") else t("interlaced", "geïnterlaced") if scan.startswith("i") else ""
    put("r.res", " · ".join(x for x in (f"{w}×{h}" if w and h else "", f"{fps} fps" if fps else "", scan_word) if x))
    if hdr == "dolbyvision":
        put("r.hdr", " · ".join(x for x in ("Dolby Vision", (t("profile ", "profiel ") + prof.group(1) + (f" {layer}" if layer else "")) if prof else "", "BT.2020") if x))
    elif hdr_word:
        put("r.hdr", f"{hdr_word} · BT.2020")
    else:
        put("r.hdr", "SDR")
    dar = info("Player.Process(videodar)")
    put("r.fmt", " · ".join(x for x in (pix, f"{dar}:1" if dar else "") if x))
    hw = xbmc.getCondVisibility("Player.Process(videohwdecoder)")
    put("r.vdec", " · ".join(x for x in (info("Player.Process(videodecoder)"), "hardware" if hw else "software") if x))
    disp = info("System.ScreenResolution")
    disp = re.sub(r"(\d)\s*x\s*(\d)", r"\1×\2", disp).replace(" - ", " · ")
    disp = re.sub(r"(\d)Hz", r"\1 Hz", disp)
    put("r.disp", disp)
    put("r.vq", f"{vq:.0f}%")
    put("r.vq.dot", "low" if vq <= 20 else "ok")
    # audio
    tr = tracks(now)
    streams = tr.get("audiostreams") or []
    cur = tr.get("currentaudiostream") or {}
    idx = next((n for n, s in enumerate(streams, 1) if s.get("index") == cur.get("index")), 0)
    bits_track = [t(f"{idx} of {len(streams)}", f"{idx} van {len(streams)}") if idx and len(streams) > 1 else "",
                  lang_name(cur.get("language") or info("VideoPlayer.AudioLanguage")), cur.get("name") or aname_stream]
    put("r.atrack", " · ".join(x for x in bits_track if x))
    ch_word = (t(f"{chn} channel ({chs})", f"{chn} kanaal ({chs})") if chn == 1 else
               t(f"{chn} channels ({chs})", f"{chn} kanalen ({chs})")) if chn else ""
    put("r.afmt", " · ".join(x for x in (along, ch_word) if x))
    put("r.aq", " · ".join(x for x in (khz, f"{bits} bit" if bits and bits != "0" else "") if x))
    adec = info("Player.Process(audiodecoder)")
    adec = "" if adec.lower() in ("unknown", "none") else adec
    if adec.lower().startswith("pt-"):
        adec += t(" · passthrough to receiver", " · passthrough naar receiver")
    put("r.adec", adec)
    put("r.aqueue", f"{aq:.0f}%")
    put("r.aqueue.dot", "low" if aq <= 20 else "ok")
    # subtitles
    subs = tr.get("subtitles") or []
    cs = tr.get("currentsubtitle") or {}
    if tr.get("subtitleenabled") and cs:
        sidx = next((n for n, s in enumerate(subs, 1) if s.get("index") == cs.get("index")), 0)
        codec = (cs.get("codec") or "").upper().replace("SUBRIP", "SRT")
        put("r.sub", " · ".join(x for x in (lang_name(cs.get("language")) or cs.get("name"), codec,
                                             t(f"{sidx} of {len(subs)}", f"{sidx} van {len(subs)}") if sidx else "") if x))
    else:
        put("r.sub", t("Off", "Uit") + (t(f" · {len(subs)} available", f" · {len(subs)} beschikbaar") if subs else ""))
    # network
    put("r.buf", " · ".join(x for x in (f"{cache:.0f}%", ahead) if x))
    put("r.buf.dot", "low" if cache <= 20 else "ok")
    put("r.type", t("Stream · internet", "Stream · internet") if stream else t("Local file", "Lokaal bestand"))

    # ======================================================================== tab 2: video
    try:
        video_tab(locals())
    except Exception as error:  # noqa: BLE001
        xbmc.log(f"[plugin.video.dexhub] playerinfo video: {error}", xbmc.LOGINFO)
    try:
        audio_tab(locals())
    except Exception as error:  # noqa: BLE001
        xbmc.log(f"[plugin.video.dexhub] playerinfo audio: {error}", xbmc.LOGINFO)


def kodi_settings(now):
    """Kodi's audio output settings (every 10 s)."""
    if now - _STATE["sett"] < 10 and _STATE["set"]:
        return _STATE["set"]
    _STATE["sett"] = now
    out = {}
    for key in ("audiooutput.audiodevice", "audiooutput.passthroughdevice", "audiooutput.config", "audiooutput.channels", "audiooutput.passthrough"):
        try:
            req = {"jsonrpc": "2.0", "id": 1, "method": "Settings.GetSettingValue", "params": {"setting": key}}
            out[key] = (json.loads(xbmc.executeJSONRPC(json.dumps(req))).get("result") or {}).get("value")
        except Exception:  # noqa: BLE001
            out[key] = None
    _STATE["set"] = out
    return out


def device_name(raw):
    """'ALSA:hdmi:CARD=PCH,DEV=0|HDA Intel PCH, HDMI 0' -> 'HDA Intel PCH, HDMI 0'; 'AudioTrack (RAW)' stays."""
    raw = str(raw or "")
    if "|" in raw:
        raw = raw.split("|", 1)[1]
    elif ":" in raw:
        raw = raw.split(":", 1)[1]
    return raw.strip() or ""


SPEAKER_CONFIG = {0: "2.0", 1: "2.1", 2: "3.0", 3: "3.1", 4: "4.0", 5: "4.1", 6: "5.0", 7: "5.1", 8: "7.0", 9: "7.1", 10: "7.1"}


def video_tab(v):
    w, h, fps, hdr = v["w"], v["h"], v["fps"], v["hdr"]
    res_badge, prof, layer, pix, ten, vname = v["res_badge"], v["prof"], v["layer"], v["pix"], v["ten"], v["vname"]
    dvn = v.get("dvn") or {}                       # what the release name says about Dolby Vision (profile, FEL, DV2)
    try:
        width = int(w or 0)
    except ValueError:
        width = 0
    box = "4k" if width >= 3000 else "1080" if width >= 1200 else "sd"
    put("v.resbox", box)
    put("v.reslabel", f"{res_badge} · {w}×{h}" if w and h else res_badge)
    put("v.resshort", res_badge)
    put("v.resnote", {"4k": t("4× the pixels of Full HD", "4× zoveel beeldpunten als Full HD"),
                      "1080": t("Full HD" if width >= 1900 else "HD", "Full HD" if width >= 1900 else "HD"),
                      "sd": t("standard definition", "standaard beeld")}[box])
    dar = info("Player.Process(videodar)")
    try:
        ratio = float(dar.replace(",", "."))
        named = "16:9" if abs(ratio - 1.78) < 0.02 else "4:3" if abs(ratio - 1.33) < 0.02 else "21:9" if 2.3 < ratio < 2.45 else ""
    except ValueError:
        named = ""
    put("v.dar", f"{dar}:1" + (f" ({named})" if named else "") if dar else "–")
    put("v.fps", f"{fps} fps" if fps else "–")
    put("v.scan", v["scan_word"] or "–")
    deint = info("Player.Process(deintmethod)")
    put("v.deint", deint if deint and deint.lower() not in ("none", "unknown", "") else t("not needed", "niet nodig"))
    bar = {"dolbyvision": "dv", "hdr10": "hdr10", "hdr10plus": "hdr10", "hlg": "hdr10"}.get(hdr, "sdr")
    put("v.hdrbar", bar)
    put("v.hb.dv", ("DOLBY VISION 2" if dvn.get("dv2") else "DOLBY VISION") if hdr == "dolbyvision" else "")
    dv_line(hdr, prof, layer, dvn)
    put("v.hb.hdr", {"hdr10": "HDR10", "hdr10plus": "HDR10+", "hlg": "HLG"}.get(hdr, ""))
    put("v.hb.sdr", "SDR" if bar == "sdr" else "")
    put("v.hb2", (t("PROFILE ", "PROFIEL ") + prof.group(1) + (f" {layer}" if layer else "")) if hdr == "dolbyvision" and prof else "")
    wide = bar != "sdr"
    put("v.gamut", "2020" if wide else "709")
    put("v.c1", t("Colours: ", "Kleuren: ") + ("[COLOR FFFFFFFF][B]BT.2020[/B][/COLOR]" if wide else "[COLOR FFFFFFFF][B]BT.709[/B][/COLOR]"))
    transfer = {"dolbyvision": "PQ", "hdr10": "PQ", "hdr10plus": "PQ", "hlg": "HLG"}.get(hdr, "SDR (gamma)")
    put("v.c2", t("Transfer: ", "Overdracht: ") + f"[COLOR FFFFFFFF][B]{transfer}[/B][/COLOR]")
    put("v.c3", ("[COLOR FFFFFFFF][B]10-bit[/B][/COLOR] · " + t("1 billion colours", "1 miljard kleuren")) if ten
        else ("[COLOR FFFFFFFF][B]8-bit[/B][/COLOR] · " + t("16.7 million colours", "16,7 miljoen kleuren")))
    put("v.p1k", t("File", "Bestand"))
    put("v.p1", f"{vname} 10-bit" if vname and ten else vname or "–")
    put("v.p1s", pix)
    put("v.p2k", "Decoder")
    put("v.p2", info("Player.Process(videodecoder)") or "–")
    hw = xbmc.getCondVisibility("Player.Process(videohwdecoder)")
    put("v.p2s", t("hardware ✓", "hardware ✓") if hw else "software")
    rfps = num(info("System.FPS"))
    put("v.p3k", t("Picture", "Beeld"))
    put("v.p3", f"{rfps:.2f} fps" if rfps else "–")
    put("v.p3s", t("drawn per second", "getekend per seconde"))
    disp = info("System.ScreenResolution")
    m = re.search(r"(\d+)\s*x\s*(\d+)", disp)
    hz = num(re.search(r"@\s*([\d.,]+)", disp).group(1)) if re.search(r"@\s*([\d.,]+)", disp) else 0.0
    put("v.p4k", "Tv")
    put("v.p4", f"{m.group(1)}×{m.group(2)}" if m else disp)
    vfps = num(fps)
    match = hz and vfps and any(abs(hz - vfps * k) < 0.05 for k in (1, 2))
    put("v.p4s", f"@ {hz:.2f} Hz" + (" ✓" if match else "") if hz else "")
    vbr = v["vbr"]
    if vbr:
        _STATE["vhist"] = (_STATE["vhist"] + [vbr])[-40:]
        _STATE["peak"] = max(_STATE["peak"], vbr)
    put("v.br", f"{vbr:.1f} Mb/s" if vbr else "–")
    put("v.peak", t(f"peak {_STATE['peak']:.1f}", f"piek {_STATE['peak']:.1f}") if _STATE["peak"] else "")
    if len(_STATE["vhist"]) >= 2:
        put("v.spark", spark(_STATE["vhist"], 940, 120, "vspark"))


LAYOUT = {1: ["c"], 2: ["l", "r"], 3: ["l", "r", "lfe"], 4: ["l", "r", "sl", "sr"], 5: ["l", "r", "c", "sl", "sr"],
          6: ["l", "r", "c", "lfe", "sl", "sr"], 7: ["l", "r", "c", "lfe", "sl", "sr", "sbl"], 8: ["l", "r", "c", "lfe", "sl", "sr", "sbl", "sbr"]}


def audio_tab(v):
    acodec, chn, chs, atmos, dtsx, along = v["acodec"], v["chn"], v["chs"], v["atmos"], v["dtsx"], v["along"]
    rate, bits, khz, adec, aq = v["rate"], v["bits"], v["khz"], v["adec"], v["aq"]
    sett = kodi_settings(v["now"])
    passthrough = adec.lower().startswith("pt-") if adec else False
    long_name = {"truehd_atmos": "Dolby TrueHD", "truehd": "Dolby TrueHD", "eac3_ddp_atmos": "Dolby Digital+", "eac3": "Dolby Digital+",
                 "ac3": "Dolby Digital", "dtshd_ma_x_imax": "DTS:X", "dtshd_ma_x": "DTS:X", "dtshd_ma": "DTS-HD MA", "dtshd_hra": "DTS-HD HRA",
                 "dts": "DTS"}.get(acodec, v["along"].replace(" Atmos", ""))
    lossless = acodec.startswith(("truehd", "dtshd_ma", "flac", "pcm"))
    # the way of the sound
    put("a.c1k", t("File", "Bestand"))
    put("a.c1", f"{ABADGE.get(acodec, acodec.upper())}" + (" Atmos" if atmos else ""))
    put("a.c1s", (f"{chs} + " + t("objects", "objecten")) if (atmos or dtsx) and chs else (chan_word(chn) if chn else ""))
    put("a.c2k", "Kodi")
    put("a.c2", "passthrough" if passthrough else t("decodes itself", "decodeert zelf"))
    put("a.c2s", t("untouched ✓", "niet aangeraakt ✓") if passthrough else (adec or ""))
    put("a.c3k", "HDMI" if passthrough else t("Out", "Uit"))
    put("a.c3", "bitstream" if passthrough else "PCM")
    put("a.c3s", " · ".join(x for x in (khz, f"{bits} bit" if bits and bits != "0" else "") if x))
    dev = device_name(sett.get("audiooutput.passthroughdevice") if passthrough else sett.get("audiooutput.audiodevice"))
    put("a.c4k", t("Receiver", "Receiver") if passthrough else t("Device", "Apparaat"))
    put("a.c4", dev or "–")
    put("a.c4s", (t("decodes Atmos", "decodeert Atmos") if atmos else t("decodes DTS:X", "decodeert DTS:X") if dtsx else t("decodes", "decodeert"))
        if passthrough else t("plays", "speelt af"))
    cfg_ch = SPEAKER_CONFIG.get(sett.get("audiooutput.channels") if isinstance(sett.get("audiooutput.channels"), int) else -1, "")
    put("a.c5k", t("Speakers", "Luidsprekers"))
    put("a.c5", (chs or cfg_ch) + (" + Atmos" if atmos else " + DTS:X" if dtsx else ""))
    put("a.c5s", chan_word(chn) + (t(" + height", " + hoogte") if (atmos or dtsx) else "") if chn else "")
    # what you hear
    put("a.h1", long_name)
    put("a.h2", "Atmos" if atmos else "")
    put("a.hs", " · ".join(x for x in ((chs + t(" + height", " + hoogte")) if (atmos or dtsx) and chs else chs, khz,
                                       f"{bits} bit" if bits and bits != "0" else "", "lossless" if lossless else "") if x))
    # output
    cfg = {1: t("Fixed", "Vast"), 2: t("Best match", "Beste match"), 3: t("Optimized", "Geoptimaliseerd")}.get(sett.get("audiooutput.config"), "")
    put("a.dev", ("HDMI · " if passthrough and dev and "hdmi" not in dev.lower() else "") + (dev or "–"))
    put("a.cfg", " · ".join(x for x in (cfg, cfg_ch) if x) or "–")
    vol, muted = app_volume()
    vol = f"{vol}%" if vol is not None else info("Player.Volume")
    delay = info("Player.AudioDelay")
    try:
        ms = round(float(delay.split()[0].replace(",", ".")) * 1000)
        delay = t(f"delay {ms} ms", f"vertraging {ms} ms") if ms else t("no delay", "geen vertraging")
    except (ValueError, IndexError, AttributeError):
        delay = ""
    put("a.vol", " · ".join(x for x in ((t("muted", "gedempt") if muted else vol), delay) if x) or "–")
    put("r.aqueue2", f"{aq:.0f}%")
    put("r.aqueue2.bar", step(aq))
    # speakers and bars: the channels this track has
    have = set(LAYOUT.get(chn, LAYOUT[8] if chn > 8 else []))
    if atmos or dtsx:
        have |= {"tfl", "tfr", "trl", "trr"}
    for nm in ("l", "c", "r", "sl", "sr", "sbl", "sbr", "lfe", "tfl", "tfr", "trl", "trr"):
        put(f"sp.{nm}", "1" if nm in have else "")
    room = chs + (" + Atmos" if atmos else " + DTS:X" if dtsx else "")
    put("a.roomlabel", t("Your room", "Je kamer") + (f" · {room}" if room else ""))
    put("a.chlabel", t("Channels", "Kanalen") + (f" · {chs}" if chs else "") + (t(" + 4 height", " + 4 hoogte") if (atmos or dtsx) else ""))
    # tracks
    tr = v["tr"]
    streams = tr.get("audiostreams") or []
    cur = (tr.get("currentaudiostream") or {}).get("index")
    put("a.tlabel", t("Audio tracks", "Audiosporen") + f" · {len(streams)}")
    for n in range(1, 5):
        s = streams[n - 1] if n <= len(streams) else None
        if not s:
            for k in ("lang", "codec", "sub", "on"):
                put(f"a.t{n}.{k}", "")
            continue
        codec = (s.get("codec") or "").lower()
        ch = CHANNELS.get(s.get("channels") or 0, "")
        put(f"a.t{n}.lang", lang_name(s.get("language")) or t("Unknown", "Onbekend"))
        put(f"a.t{n}.codec", ACODEC.get(codec, codec.upper()) or "–")
        put(f"a.t{n}.sub", " · ".join(x for x in (ch, s.get("name") or "") if x))
        put(f"a.t{n}.on", "1" if s.get("index") == cur else "")


class Info(xbmcgui.WindowXMLDialog):
    thread = None
    closing = False

    def onInit(self):
        self.closing = False
        HOME.setProperty(P + "tab", "1")
        try:
            self.setFocusId(101)
        except Exception:  # noqa: BLE001
            pass
        import threading
        self.thread = threading.Thread(target=self.loop, name="DexHubPlayerInfo", daemon=True)
        self.thread.start()

    def loop(self):
        mon = xbmc.Monitor()
        while not self.closing and not mon.abortRequested():
            try:
                tick()
            except Exception as error:  # noqa: BLE001
                xbmc.log(f"[plugin.video.dexhub] playerinfo: {error}", xbmc.LOGINFO)
            if not xbmc.getCondVisibility("Player.HasVideo"):
                self.close()
                break
            if mon.waitForAbort(1):
                break

    def onClick(self, control_id):
        if control_id == 104:
            self.close()
            xbmc.executebuiltin("RunScript(script.tinyppi)")

    def onAction(self, action):
        if action.getId() in (9, 10, 92, 216, 247, 257, 275, 61467, 61448, 11):     # back, close, info
            self.close()

    def close(self):
        self.closing = True
        HOME.clearProperty(P + "open")
        super().close()


def open_window(params=None):
    # 'open' stays behind when Kodi stopped the call while the screen was open (a skin update, a crash): only trust it
    # when the screen is really on
    if HOME.getProperty(P + "open") and xbmc.getCondVisibility("Window.IsVisible(dexhub_playerinfo.xml)"):
        return None
    global AR
    AR = _arabic()
    xbmc.executebuiltin("Dialog.Close(videoosd,true)")
    HOME.setProperty(P + "open", "1")
    for key, text in LABELS.items():
        HOME.setProperty(P + "w" + key, WORDS.get(text, text) if AR else text)
    try:
        import xbmcaddon
        win = Info("dexhub_playerinfo.xml", xbmcaddon.Addon("plugin.video.dexhub").getAddonInfo("path"), "Default", "1080i")
        tick()
        win.doModal()
        win.closing = True
        if win.thread is not None:      # a call must not end with its threads alive (Kodi 22 gc crash)
            win.thread.join(2)
        del win
    finally:
        HOME.clearProperty(P + "open")
    return None
