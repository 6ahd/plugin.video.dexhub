#!/usr/bin/env python3
"""Draw TinyPPI's windows in the skin's own design (skin 3.28.135).

    python3 tools/release/skin_tinyppi.py SKIN_DIR [--check]

TinyPPI (script.tinyppi, CoreELEC) reads everything about the playing video
and publishes it as properties of its windows; Kodi takes a script's window
file from the running skin first. So the skin can draw those windows itself
and TinyPPI keeps doing all the reading: Dolby Vision's profile and layers,
the HDR metadata, VS10 and the rest.

These files are written from scratch for the skin (none of TinyPPI's files
or images are used). What they keep is the contract with TinyPPI's code:

* script-tinyppi-main.xml: the overlay. Group 5000 (TinyPPI moves it for its
  offset and arrow-key settings), group 5100 (its Dolby Vision channel
  panel, empty here), progress 9100 and 9101 (it fills them: the CPU's
  temperature and load). Its readings are the window's properties
  (VideoResolutionVar, DoviProfileVar, ...), TinyPPI.HdrType and
  TinyPPI.EffectiveHdrType on Home pick the layout: four columns for SDR,
  five with the HDR metadata, six for Dolby Vision.
* script-tinyppi-dialog.xml, -dialog-bar.xml, -dialog-single.xml: the VS10
  mode dialog. Group 2 is moved by TinyPPI from its panel sizes (471x546,
  1702x206, 700x216, kept here), the buttons are its ids (1001-1013,
  1101/1201/1301 for the player process info, 1500 for the single button),
  one set per kind of source.
* script-tinyppi-dv-metadata.xml is not written: the Dolby Vision metadata
  view stays TinyPPI's own.

The labels are TinyPPI's own strings ($ADDON[script.tinyppi 322xx]), so they
follow Kodi's language as TinyPPI translates them. A file the skin has that
this tool did not write is left alone; --check only reports.
"""
import argparse
import os
import sys

MARK = '<!-- dhs.tinyppi (skin_tinyppi.py) -->'
S = lambda n: '$ADDON[script.tinyppi %d]' % n
NA = S(32230)
H = 'Window(10000).Property(TinyPPI.HdrType)'
EFF = 'Window(10000).Property(TinyPPI.EffectiveHdrType)'
W = lambda name: 'Window().Property(%s)' % name
INFO = lambda name: '$INFO[%s]' % W(name)
EMPTY = lambda name: 'String.IsEmpty(%s)' % W(name)
FULL = lambda name: '!String.IsEmpty(%s)' % W(name)

WHITE, LABEL, DETAIL, ACCENT = 'FFFFFFFF', 'B3FFFFFF', 'A6FFFFFF', 'FFC4B5FD'
GREEN, RED, ORANGE = 'FF2EA053', 'FFE5484D', 'FFFF8C1A'
FOCUS_TEXT = 'FF16161C'
DETAILED = lambda text: '[COLOR %s]%s[/COLOR]' % (DETAIL, text)

# the overlay
COLW, GAP, PADX, HEAD, SECTION, ROWH, ROWS, BOTTOM = 272, 24, 40, 84, 46, 58, 7, 26
PANEL_H = HEAD + SECTION + ROWS * ROWH + BOTTOM
PANEL_LEFT, PANEL_TOP = 44, 1080 - 40 - PANEL_H

# fa-solid glyphs (dh_icon_s): film, microchip, volume, memory, layers, sun, chart
GLYPH = {'video': '', 'process': '', 'audio': '', 'system': '',
         'hdr': '', 'dv': '', 'meta': '', 'yes': '', 'no': ''}


def esc(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def label(x, y, w, h, text, font='dh_small_b', colour=WHITE, visible='', align='left', scroll=False):
    return ('<control type="label"><left>%d</left><top>%d</top><width>%d</width><height>%d</height>'
            '<font>%s</font><textcolor>%s</textcolor><shadowcolor>55000000</shadowcolor><align>%s</align>'
            '<aligny>center</aligny>%s%s<label>%s</label></control>'
            % (x, y, w, h, font, colour, align, ('<visible>%s</visible>' % esc(visible)) if visible else '',
               '<scroll>true</scroll><scrollspeed>40</scrollspeed>' if scroll else '', esc(text)))


def image(x, y, w, h, texture, colour='', border='', visible='', cid=''):
    return ('<control type="image"%s><left>%d</left><top>%d</top><width>%d</width><height>%d</height>%s'
            '<texture%s%s>%s</texture></control>'
            % ((' id="%s"' % cid) if cid else '', x, y, w, h,
               ('<visible>%s</visible>' % esc(visible)) if visible else '',
               (' colordiffuse="%s"' % colour) if colour else '', (' border="%s"' % border) if border else '',
               texture))


def bar(x, y, w, info='', cid=''):
    return ('<control type="progress"%s><left>%d</left><top>%d</top><width>%d</width><height>6</height>%s'
            '<texturebg border="3" colordiffuse="26FFFFFF">dexhub/ppi_bar.png</texturebg>'
            '<lefttexture>-</lefttexture><midtexture border="3" colordiffuse="%s">dexhub/ppi_bar.png</midtexture>'
            '<righttexture>-</righttexture></control>'
            % ((' id="%s"' % cid) if cid else '', x, y, w, ('<info>%s</info>' % info) if info else '', ACCENT))


def glass(x, y, w, h):
    return (image(x - 30, y - 22, w + 60, h + 60, 'dexhub/ppi_shadow.png', border='60')
            + image(x, y, w, h, 'dexhub/ppi_glass.png', border='40'))


# ------------------------------------------------------------------ overlay rows
def _bitrate(live, avg):
    return [(INFO(live) + ' ' + DETAILED('$INFO[%s,(Ø ,)]' % W(avg)), '%s + %s' % (FULL(live), FULL(avg))),
            (INFO(avg), '%s + %s' % (EMPTY(live), FULL(avg))),
            (INFO(live), '%s + %s' % (FULL(live), EMPTY(avg))),
            (NA, '%s + %s' % (EMPTY(live), EMPTY(avg)))]


def _or_na(name):
    return [(INFO(name), FULL(name)), (NA, EMPTY(name))]


TO_DV = ('[String.IsEmpty({h}) | String.IsEqual({h},hdr10) | String.IsEqual({h},hdr10plus) | String.Contains({h},hlg)]'
         ' + String.Contains(Player.Process(amlogic.eoft_gamut),DV)').format(h=H)
TO_HDR10 = ('[String.IsEmpty({h}) | String.IsEqual({h},hdr10plus) | String.Contains({h},dolby)]'
            ' + String.IsEqual(Window().Property(ModeVar),HDR10)').format(h=H)
TO_SDR = ('[String.IsEqual({h},hdr10) | String.IsEqual({h},hdr10plus) | String.Contains({h},hlg) | '
          'String.Contains({h},dolby)] + String.Contains(Player.Process(amlogic.eoft_gamut),SDR)').format(h=H)
CONVERTING = '[%s] | [%s] | [%s]' % (TO_DV, TO_SDR, TO_HDR10)
PVR = '[PVR.IsPlayingTV | PVR.IsPlayingRadio]'
EPG_NONE = ('[String.IsEmpty(PVR.EpgEventDuration) | String.IsEqual(PVR.EpgEventDuration,00:00) | '
            'String.IsEqual(PVR.EpgEventDuration,00:00:00)]')


def _yes_no(name):
    """A ✓ or ✗ glyph from TinyPPI's true / false, N/A when it has none."""
    return [('glyph:yes', 'String.IsEqual(%s,true)' % W(name)), ('glyph:no', 'String.IsEqual(%s,false)' % W(name)),
            (NA, EMPTY(name))]


def columns():
    """(key, glyph, title, rows): a row is (label or [(label, condition)],
    [(value, condition)], extra) where extra draws bars under the value."""
    video = ('video', 'video', S(32218), [
        (S(32219), [(INFO('DisplayModeVar'), '')]),
        (S(32220), [(INFO('VideoResolutionVar'), '')]),
        (S(32221), [(INFO('VideoPixelFormatVar') + ' ' + DETAILED('$INFO[%s,(,)]' % W('DoviTunnelVar')), '')]),
        (S(32222), [(INFO('VideoBitDepthVar'), '')]),
        (S(32223), [('$INFO[%s,,:1]' % W('AspectRatioVar') + DETAILED('$INFO[%s, (,)]' % W('ImaxVar')), '')]),
        (S(32224), [(INFO('VideoDecoderNameVar') + INFO('VideoCodecVar') + ' '
                     + DETAILED('$INFO[%s,(,)]' % W('VideoDecoderVar')), '')]),
        (S(32225), [(INFO('VideoDecoderLongVar'), '')]),
    ])
    process = ('process', 'process', S(32226), [
        (S(32227), [(INFO('DoviProfileVar'), '')]),
        (S(32228), [('convert', '')]),
        (S(32229), _bitrate('VideoLiveBitrateVar', 'VideoBitrateMBVar')),
        (S(32231), [(INFO('ModeVar'), '')]),
        (S(32232), [(INFO('GamutVar'), '')]),
        (S(32233), [(INFO('MediaSourceVar'), '')]),
        (S(32234), [('$INFO[Player.Time] $INFO[Player.Duration,/ ,] ' + DETAILED('$INFO[Player.Progress,(,%)]'),
                     '!String.IsEmpty(Player.Duration) + !%s' % PVR),
                    ('$INFO[PVR.EpgEventElapsedTime(hh:mm:ss)] $INFO[PVR.EpgEventDuration(hh:mm:ss),/ ,] '
                     + DETAILED('$INFO[PVR.EpgEventProgress,(,%)]'), '%s + !%s' % (PVR, EPG_NONE)),
                    (S(32235), '%s + %s' % (PVR, EPG_NONE)),
                    (S(32236), 'String.IsEmpty(Player.Duration) + Player.IsInternetStream + !%s' % PVR),
                    (NA, 'String.IsEmpty(Player.Duration) + !%s + !Player.IsInternetStream' % PVR)]),
    ])
    audio = ('audio', 'audio', S(32237), [
        (S(32238), [(INFO('AudioCodecVar') + ' ' + INFO('AudioChannelsVar') + ' '
                     + DETAILED(INFO('AudioCodecSpatialVar')), '')]),
        (S(32239), [('$INFO[%s,, / ]' % W('AudioBitDepthVar') + INFO('AudioSampleRateVar'),
                     '%s | %s' % (FULL('AudioBitDepthVar'), FULL('AudioSampleRateVar'))),
                    (NA, '%s + %s' % (EMPTY('AudioBitDepthVar'), EMPTY('AudioSampleRateVar')))]),
        (S(32240), [(INFO('AudioChannelsInputVar'), '')]),
        (S(32241), [(S(32242), 'Player.Passthrough + !String.IsEmpty(VideoPlayer.AudioCodec)'),
                    ('$INFO[Player.Process(audiochannelssink)]',
                     '!Player.Passthrough + !String.IsEmpty(VideoPlayer.AudioCodec) + '
                     '!String.IsEmpty(Player.Process(audiochannelssink))'),
                    (S(32243), '!Player.Passthrough + !String.IsEmpty(VideoPlayer.AudioCodec) + '
                               'String.IsEmpty(Player.Process(audiochannelssink))'),
                    (NA, 'String.IsEmpty(VideoPlayer.AudioCodec)')]),
        (S(32229), _bitrate('AudioLiveBitrateVar', 'AudioBitrateKBVar')),
        (S(32244), [(INFO('AudioNameShortVar') + '$INFO[%s, | ]' % W('AudioNameVar'), FULL('AudioNameShortVar')),
                    (NA, EMPTY('AudioNameShortVar'))]),
        (S(32245), [(INFO('SubtitleNameShortVar') + '$INFO[%s, | ]' % W('SubtitleNameVar') + ' '
                     + DETAILED('$INFO[%s,(,)]' % W('SubtitleCodecVar')),
                     'VideoPlayer.SubtitlesEnabled + VideoPlayer.HasSubtitles'),
                    (S(32246), 'VideoPlayer.HasSubtitles + !VideoPlayer.SubtitlesEnabled'),
                    (NA, '!VideoPlayer.HasSubtitles')]),
    ])
    system = ('system', 'system', S(32247), [
        (S(32248), [(INFO('FpsInfoVar') + ' = ' + '$INFO[%s,, FPS]' % W('FpsDropVar'), '')]),
        (S(32249), [(INFO('CpuUsageVar'), '')]),
        (S(32250), [('$INFO[System.CPUTemperature]', '')], 'bar:9100'),
        (S(32252), [('$INFO[Player.CacheLevel,,%]', '')], 'bar:Player.CacheLevel'),
        (S(32251), [(INFO('CpuTopUsageVar') + '  ' + DETAILED('$INFO[system.memory(used.percent)]'), '')],
         'bars:id9101|system.memory(used)'),
        (S(32253), [('$INFO[Player.Process(VideoQueueLevel)]% ' + DETAILED('$INFO[Player.Process(VideoQueueDataLevel)]%'),
                     '')], 'bars:Player.Process(VideoQueueLevel)|Player.Process(VideoQueueDataLevel)'),
        (S(32254), [('$INFO[Player.Process(AudioQueueLevel)]% ' + DETAILED('$INFO[Player.Process(AudioQueueDataLevel)]%'),
                     '')], 'bars:Player.Process(AudioQueueLevel)|Player.Process(AudioQueueDataLevel)'),
    ])
    hdr = ('hdr', 'hdr', S(32255), [
        (S(32256), [(INFO('Hdr10MdlVar'), '')]),
        (S(32257), [(INFO('Hdr10MaxCllFallVar'), '')]),
    ])
    dv = ('dv', 'dv', 'Dolby Vision', [
        (S(32258), _or_na('DoviCmVersionVar')),
        (S(32259), _or_na('DoviStructureVar')),
        (S(32260), _or_na('DoviVersionVar')),
        (S(32261), _or_na('DoviProfileNumberVar')),
        (S(32262), [('pair:DoviRpuPresentVar|DoviBlPresentVar', '')]),
        (S(32263), [('eltype', '')]),
    ])
    meta = ('meta', 'meta', S(32264), [
        (S(32256), [(INFO('Hdr10MdlVar'), '')]),
        (S(32257), [(INFO('Hdr10MaxCllFallVar'), '')]),
        ([(S(32265), FULL('DoviRpuMdlFromSourceVar')), (S(32266), EMPTY('DoviRpuMdlFromSourceVar'))],
         [(INFO('DoviRpuMdlVar'), '')]),
        ([(S(32267), FULL('DoviRpuMdlFromSourceVar')), (S(32268), EMPTY('DoviRpuMdlFromSourceVar'))],
         [(INFO('DoviLevel6RpuMaxCllFallVar'), '')]),
        (S(32269), [(INFO('DoviLevel1FllVar'), '')]),
        (S(32270), [(INFO('DoviLevel1PqVar'), '')]),
        (S(32271), [(INFO('DoviLevel5OffsetsVar'), '')]),
    ])
    return video, process, audio, system, hdr, dv, meta


def _glyph(x, y, which, colour, visible=''):
    return label(x, y, 30, 30, GLYPH[which], font='dh_icon_s', colour=colour, visible=visible)


def _value(x, y, value, visible):
    if value == 'convert':
        return (_glyph(x, y, 'yes', GREEN, CONVERTING) + _glyph(x, y, 'no', RED, '![%s]' % CONVERTING)
                + label(x + 36, y, COLW - 36, 30, '→  DV Profile 8.1', visible=TO_DV)
                + label(x + 36, y, COLW - 36, 30, '→  HDR10', visible=TO_HDR10)
                + label(x + 36, y, COLW - 36, 30, '→  SDR', visible=TO_SDR))
    if value.startswith('pair:'):
        out = ''
        for i, name in enumerate(value[5:].split('|')):
            for v, cond in _yes_no(name):
                if v.startswith('glyph:'):
                    which = v[6:]
                    out += _glyph(x + i * 64, y, which, GREEN if which == 'yes' else RED, cond)
                else:
                    out += label(x + i * 64, y, 60, 30, v, visible=cond)
            if not i:
                out += label(x + 40, y, 20, 30, '|', colour=LABEL)
        return out
    if value == 'eltype':
        el = W('DoviElTypeVar')
        typed = 'String.Contains(%s,FEL) | String.Contains(%s,MEL)' % (el, el)
        out = ''
        for v, cond in _yes_no('DoviElPresentVar'):
            if v.startswith('glyph:'):
                which = v[6:]
                out += _glyph(x, y, which, GREEN if which == 'yes' else RED, cond)
            else:
                out += label(x, y, 60, 30, v, visible=cond)
        out += label(x + 40, y, 20, 30, '|', colour=LABEL)
        out += label(x + 64, y, COLW - 64, 30, '$INFO[%s]' % el, colour=GREEN, visible='String.Contains(%s,FEL)' % el)
        out += label(x + 64, y, COLW - 64, 30, '$INFO[%s]' % el, colour=ORANGE, visible='String.Contains(%s,MEL)' % el)
        out += _glyph(x + 64, y, 'no', RED, '![%s | String.IsEmpty(%s)]' % (typed, el))
        out += label(x + 64, y, COLW - 64, 30, NA, visible='String.IsEmpty(%s)' % el)
        return out
    return label(x, y, COLW, 30, value, visible=visible, scroll=True)     # a long reading runs past


def column(x, y, col):
    key, glyph, title, rows = col
    out = _glyph(x, y + 6, glyph, ACCENT) + label(x + 38, y + 6, COLW - 38, 30, '[B][UPPERCASE]%s[/UPPERCASE][/B]'
                                                   % title, font='font20_title', colour=ACCENT)
    y += SECTION
    for row in rows:
        name, values = row[0], row[1]
        extra = row[2] if len(row) > 2 else ''
        if isinstance(name, list):
            for text, cond in name:
                out += label(x, y, COLW, 22, text, font='dh_tiny', colour=LABEL, visible=cond)
        else:
            out += label(x, y, COLW, 22, name, font='dh_tiny', colour=LABEL)
        for value, cond in values:
            out += _value(x, y + 20, value, cond)
        if extra.startswith('bar:'):
            what = extra[4:]
            out += bar(x, y + 50, COLW - 12, cid=what) if what.isdigit() else bar(x, y + 50, COLW - 12, info=what)
        elif extra.startswith('bars:'):
            first, second = extra[5:].split('|')
            half = (COLW - 24) // 2
            out += (bar(x, y + 50, half, cid=first[2:]) if first.startswith('id') else bar(x, y + 50, half, info=first))
            out += bar(x + half + 12, y + 50, half, info=second)
        y += ROWH
    return out


def main_xml():
    video, process, audio, system, hdr, dv, meta = columns()
    layouts = (('String.IsEmpty(%s)' % EFF, ()),
               ('!String.IsEmpty(%s) + !String.Contains(%s,dolby)' % (EFF, EFF), (hdr,)),
               ('String.Contains(%s,dolby)' % EFF, (dv, meta)))
    x0, y0 = PANEL_LEFT, PANEL_TOP
    out = []
    for cond, extra in layouts:
        n = 4 + len(extra)
        width = 2 * PADX + n * COLW + (n - 1) * GAP
        body = glass(x0, y0, width, PANEL_H)
        body += image(x0 + PADX, y0 + 26, 40, 40, 'dexhub/cap_mark.png')
        body += label(x0 + PADX + 54, y0 + 22, 600, 46, '[B]$LOCALIZE[10116][/B]', font='font30_title')
        body += label(x0 + PADX + 600, y0 + 22, width - 2 * PADX - 600, 46, '$INFO[Player.Filename]',
                      font='dh_small', colour=LABEL, align='right',
                      visible='String.IsEqual(Window(Home).Property(TinyPPI.Filename),true)')
        body += image(x0 + PADX, y0 + HEAD - 6, width - 2 * PADX, 1, 'dexhub/cap_px.png', colour='26FFFFFF')
        for i, col in enumerate(extra):
            body += column(x0 + PADX + (4 + i) * (COLW + GAP), y0 + HEAD, col)
        out.append('<control type="group"><visible>%s</visible>%s</control>' % (esc(cond), body))
    shared = ''.join(column(x0 + PADX + i * (COLW + GAP), y0 + HEAD, col)
                     for i, col in enumerate((video, process, audio, system)))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n%s\n<window>'
            '<onload>Dialog.Close(fullscreenvideo,true)</onload><onload>Dialog.Close(videoosd,true)</onload>'
            '<onload>Dialog.Close(seekbar,true)</onload><onload>Dialog.Close(1159,true)</onload>'
            '<animation effect="fade" start="0" end="100" time="250">WindowOpen</animation>'
            '<animation effect="fade" start="100" end="0" time="200">WindowClose</animation>'
            '<controls><control type="group" id="5000">'
            '<animation effect="slide" start="0,40" end="0,0" time="300" tween="cubic" easing="out">WindowOpen</animation>'
            '%s%s<control type="group" id="9000"/></control>'
            '<control type="group" id="5100"/></controls></window>\n' % (MARK, ''.join(out), shared))


# ------------------------------------------------------------------ the VS10 dialog
HOME = 'Window(10000).Property'
PLAIN = ('String.IsEqual({h}(TinyPPI.HdrType),hdr10plus) | String.Contains({h}(TinyPPI.HdrType),hlg) | '
         'String.IsEqual({h}(TinyPPI.Hdr10PlusPresent),1)').format(h=HOME)
HAS = ('!String.IsEqual({h}(TinyPPI.HdrType),hdr10plus) + !String.Contains({h}(TinyPPI.HdrType),hlg) + '
       '!String.IsEqual({h}(TinyPPI.Hdr10PlusPresent),1)').format(h=HOME)
PPI = '[B]$LOCALIZE[10116][/B]'
BRANCHES = (
    ('String.IsEmpty(%s(TinyPPI.HdrType)) + %s' % (HOME, HAS),
     ((1001, PPI), (1002, '[B]Original[/B]'), (1003, '[B]SDR → HDR10[/B]'), (1004, '[B]SDR → Dolby Vision[/B]'))),
    ('String.IsEqual(%s(TinyPPI.HdrType),hdr10) + %s' % (HOME, HAS),
     ((1101, PPI), (1005, '[B]HDR10 (Original)[/B]'), (1006, '[B]HDR10 → SDR[/B]'),
      (1008, '[B]HDR10 → Dolby Vision[/B]'))),
    ('String.Contains(%s(TinyPPI.HdrType),dolby) + %s' % (HOME, HAS),
     ((1201, PPI), (1012, '[B]Dolby Vision (Original)[/B]'), (1013, '[B]Dolby Vision → SDR[/B]'))),
    (PLAIN, ((1301, PPI),)),
)
PLACED = 'String.IsEqual(Window(10000).Property(TinyPPI.DialogPlaced),1)'


def button(cid, x, y, w, h, text, nav, font='dh_body_b'):
    return ('<control type="button" id="%d"><left>%d</left><top>%d</top><width>%d</width><height>%d</height>'
            '<label>%s</label><font>%s</font><align>center</align><aligny>center</aligny>'
            '<textcolor>%s</textcolor><focusedcolor>%s</focusedcolor><selectedcolor>%s</selectedcolor>'
            '<texturenofocus border="30" colordiffuse="14FFFFFF">dexhub/ppi_btn.png</texturenofocus>'
            '<texturefocus border="30">dexhub/ppi_btn.png</texturefocus>%s</control>'
            % (cid, x, y, w, h, esc(text), font, WHITE, FOCUS_TEXT, WHITE, nav))


def _header(w):
    return (image(30, 26, 36, 36, 'dexhub/cap_mark.png')
            + label(78, 20, w - 110, 48, '[B]VS10[/B]', font='font30_title')
            + image(30, 82, w - 60, 1, 'dexhub/cap_px.png', colour='26FFFFFF'))


def dialog_xml(mode):
    size = {'dialog': (471, 546), 'bar': (1702, 206), 'single': (700, 216)}[mode]
    w, h = size
    left, top = (1920 - w) // 2, 1080 - 50 - h
    inner = glass(0, 0, w, h) + _header(w)
    default = 1500 if mode == 'single' else 1001
    if mode == 'single':
        inner += (label(34, 118, 40, 44, '', font='dh_icon_s', colour=ACCENT)
                  + label(w - 64, 118, 40, 44, '', font='dh_icon_s', colour=ACCENT)
                  + button(1500, 90, 100, w - 180, 80, '',
                           '<onup>1500</onup><ondown>1500</ondown><onleft>1500</onleft><onright>1500</onright>'))
    else:
        for cond, buttons in BRANCHES:
            ids = [b[0] for b in buttons]
            group = ''
            for i, (cid, text) in enumerate(buttons):
                prev, nxt = ids[i - 1], ids[(i + 1) % len(ids)]
                if mode == 'dialog':
                    nav = '<onup>%d</onup><ondown>%d</ondown><onleft>%d</onleft><onright>%d</onright>' % (prev, nxt, cid, cid)
                    group += button(cid, 35, 112 + i * 96, w - 70, 80, text, nav)
                else:
                    n = len(buttons)
                    bw = (w - 70 - (n - 1) * 20) // n
                    nav = '<onleft>%d</onleft><onright>%d</onright><onup>%d</onup><ondown>%d</ondown>' % (prev, nxt, cid, cid)
                    group += button(cid, 35 + i * (bw + 20), 104, bw, 76, text, nav)
            inner += '<control type="group"><visible>%s</visible>%s</control>' % (esc(cond), group)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n%s\n<window type="dialog">'
            '<defaultcontrol always="true">%d</defaultcontrol>'
            '<onload>Dialog.Close(fullscreenvideo,true)</onload><onload>Dialog.Close(videoosd,true)</onload>'
            '<onload>Dialog.Close(seekbar,true)</onload><onload>Dialog.Close(1159,true)</onload>'
            '<animation effect="fade" start="0" end="100" time="200">WindowOpen</animation>'
            '<animation effect="fade" start="100" end="0" time="150">WindowClose</animation>'
            '<controls><control type="group" id="2"><left>%d</left><top>%d</top><width>%d</width><height>%d</height>'
            '<animation effect="slide" start="0,0" end="0,200" time="200" tween="quadratic" easing="in">WindowClose</animation>'
            '<control type="group"><visible>%s</visible>'
            '<animation effect="fade" start="0" end="100" time="200">Visible</animation>'
            '<animation effect="slide" start="0,100" end="0,0" time="220" tween="cubic" easing="out">Visible</animation>'
            '%s</control></control></controls></window>\n'
            % (MARK, default, left, top, w, h, esc(PLACED), inner))


# ------------------------------------------------------------------ images
def images(media):
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    k, size, r = 4, 160, 34
    mask = Image.new('L', (size * k, size * k), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size * k - 1, size * k - 1), r * k, fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    rim = Image.new('L', (size * k, size * k), 0)
    d = ImageDraw.Draw(rim)
    d.rounded_rectangle((0, 0, size * k - 1, size * k - 1), r * k, fill=255)
    d.rounded_rectangle((2 * k, 2 * k, size * k - 1 - 2 * k, size * k - 1 - 2 * k), (r - 2) * k, fill=0)
    rim = rim.resize((size, size), Image.LANCZOS)
    body = Image.new('RGBA', (size, size))
    alpha = Image.new('L', (size, size))
    for y in range(size):
        light = 16 * max(0.0, 1 - y / 40.0) if 3 <= y < 40 else 0
        shade = 0 if y < 40 else (8 if y >= size - 40 else 4)
        base = (80 - shade, 82 - shade, 92 - shade)
        body.paste(tuple(int(c + (255 - c) * light / 255.0) for c in base) + (255,), (0, y, size, y + 1))
        alpha.paste(int(206 + light), (0, y, size, y + 1))
    body.putalpha(ImageChops.multiply(alpha, mask))
    edge = Image.new('RGBA', (size, size), (255, 255, 255, 0))
    edge.putalpha(rim.point(lambda v: v * 40 // 255))
    body.alpha_composite(edge)
    body.save(os.path.join(media, 'ppi_glass.png'))
    s = 220
    shadow = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((30, 30, s - 31, s - 31), r, fill=(0, 0, 0, 120))
    shadow.filter(ImageFilter.GaussianBlur(14)).save(os.path.join(media, 'ppi_shadow.png'))
    btn = Image.new('L', (120 * k, 80 * k), 0)
    ImageDraw.Draw(btn).rounded_rectangle((0, 0, 120 * k - 1, 80 * k - 1), 26 * k, fill=255)
    out = Image.new('RGBA', (120, 80), (255, 255, 255, 0))
    out.putalpha(btn.resize((120, 80), Image.LANCZOS))
    out.save(os.path.join(media, 'ppi_btn.png'))
    b = Image.new('L', (24 * k, 6 * k), 0)
    ImageDraw.Draw(b).rounded_rectangle((0, 0, 24 * k - 1, 6 * k - 1), 3 * k, fill=255)
    out = Image.new('RGBA', (24, 6), (255, 255, 255, 0))
    out.putalpha(b.resize((24, 6), Image.LANCZOS))
    out.save(os.path.join(media, 'ppi_bar.png'))
    return ['ppi_glass.png', 'ppi_shadow.png', 'ppi_btn.png', 'ppi_bar.png']


def run(skin, check=False):
    plan = {'script-tinyppi-main.xml': main_xml(), 'script-tinyppi-dialog.xml': dialog_xml('dialog'),
            'script-tinyppi-dialog-bar.xml': dialog_xml('bar'), 'script-tinyppi-dialog-single.xml': dialog_xml('single')}
    changed, kept = [], []
    for name, text in plan.items():
        path = os.path.join(skin, 'xml', name)
        if os.path.exists(path):
            with open(path, encoding='utf-8') as fh:
                old = fh.read()
            if MARK not in old:
                kept.append(name)
                continue
            if old == text:
                continue
        changed.append('xml/' + name)
        if not check:
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(text)
    if not check:
        changed += ['media/dexhub/' + n for n in images(os.path.join(skin, 'media', 'dexhub'))]
    if kept:
        print('left alone (not written by this tool): ' + ', '.join(kept))
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('skin')
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args(argv)
    changed = run(args.skin, args.check)
    print(('would change: ' if args.check else 'changed: ') + (', '.join(changed) or 'nothing'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
