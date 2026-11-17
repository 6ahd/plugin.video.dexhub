# -*- coding: utf-8 -*-
"""All public Dex Hub settings in the add-on's own menu, from its schema."""
import ast
import os
import re
from decimal import Decimal
from xml.etree import ElementTree as ET

SUPPORTED = {'action', 'bool', 'enum', 'labelenum', 'slider', 'text', 'number', 'folder', 'preference'}

def schema(path):
    root = ET.parse(path).getroot()
    return [(c, list(c.findall('setting'))) for c in root.findall('category')]

def include_preferences(categories, options, arabic):
    seen = {s.get('id') for _, siblings in categories for s in siblings if public(s) and s.get('id')}
    lookup = {c.get('label'): siblings for c, siblings in categories}
    for key, (default, choices, ar, en) in options.items():
        if key in seen: continue
        category = '30931' if key.startswith('player_') else '31000' if key.startswith('homeui_') else '31060' if key.startswith('iptv_') else '31140' if 'badge' in key else '32910' if key.startswith(('library_', 'favorites_', 'continue_', 'nextup_')) else '30930'
        if category not in lookup: continue
        lookup[category].append(ET.Element('setting', {'id': key, 'type': 'preference', 'label': ar if arabic else en, 'default': str(default)}))
    return categories

def public(setting):
    return setting.get('type') in SUPPORTED and setting.get('visible', '').strip().lower() != 'false'

def read_labels(path):
    labels = {}; current = None; field = ''; fields = {}
    try:
        lines = open(path, encoding='utf-8').read().splitlines()
    except OSError:
        return labels
    def flush():
        if current is not None:
            labels[current] = fields.get('msgstr') or fields.get('msgid') or ''
    for line in lines + ['']:
        line = line.strip()
        if line.startswith('msgctxt '):
            flush(); fields = {}; field = ''
            match = re.search(r'#(\d+)', line); current = match.group(1) if match else None
        elif not line:
            flush(); current = None; fields = {}; field = ''
        elif line.startswith(('msgid ', 'msgstr ')):
            field, raw = line.split(' ', 1)
            fields[field] = ast.literal_eval(raw)
        elif line.startswith('"') and field:
            fields[field] += ast.literal_eval(line)
    return labels

def enum_choices(setting, label):
    values = setting.get('values', '').split('|') if setting.get('values') else []
    lvalues = setting.get('lvalues', '').split('|') if setting.get('lvalues') else []
    entries = setting.get('entries', '').split('|') if setting.get('entries') else values
    names = [label(x) for x in lvalues] if lvalues else entries
    if setting.get('type') == 'enum':
        return [str(i) for i in range(len(names))], names
    return values, names or values

def slider_choices(setting):
    parts = [Decimal(v) for v in setting.get('range', '').split(',')]
    if len(parts) == 2:
        low, high = parts; step = Decimal(1)
    else:
        low, step, high = parts
    if step <= 0 or high < low or (high-low)/step > 1000:
        raise ValueError('invalid slider range')
    return [format(low+i*step, 'f').rstrip('0').rstrip('.') if '.' in format(low+i*step,'f') else str(low+i*step)
            for i in range(int((high-low)/step)+1)]

def condition(expression, siblings, index, read, kodi):
    expression = (expression or '').strip()
    if not expression or expression.lower() == 'true': return True
    if expression.lower() == 'false': return False
    if '|' in expression:
        return any(condition(x, siblings, index, read, kodi) for x in expression.split('|'))
    if '+' in expression:
        return all(condition(x, siblings, index, read, kodi) for x in expression.split('+'))
    match = re.fullmatch(r'(!?)(eq|gt|lt)\((-?\d+),([^)]*)\)', expression)
    if not match: return bool(kodi(expression))
    negate, operation, offset, expected = match.groups(); target=index+int(offset)
    if not 0 <= target < len(siblings): return False
    actual=read(siblings[target])
    if operation == 'eq': result=str(actual).lower()==expected.lower()
    else:
        try: result=float(actual)>float(expected) if operation=='gt' else float(actual)<float(expected)
        except ValueError: result=False
    return not result if negate else result

def run(category=''):
    import xbmc, xbmcgui, xbmcaddon
    from .routes.simple_entry import _select_dialog, _row, _close_native_settings_dialog
    from .settings_cache import invalidate
    addon=xbmcaddon.Addon(); base=addon.getAddonInfo('path')
    arabic=str(addon.getSetting('ui_language')).lower().startswith('ar')
    def text(ar,en): return ar if arabic else en
    lang='ar_sa' if arabic else 'en_gb'
    labels=read_labels(os.path.join(base,'resources','language','resource.language.'+lang,'strings.po'))
    def label(token): return labels.get(str(token)) or (addon.getLocalizedString(int(token)) if str(token).isdigit() else str(token))
    from . import ui_preferences as prefs
    from .menu_taxonomy import organize, LEGACY, value_label, expose_user_settings
    categories=include_preferences(expose_user_settings(schema(os.path.join(base,'resources','settings.xml')),prefs.OPTIONS),prefs.OPTIONS,arabic)
    categories, contexts=organize(categories,arabic)
    category=LEGACY.get(category,category)
    _close_native_settings_dialog()
    def read(s): return addon.getSetting(s.get('id')) or s.get('default','') if s.get('id') else ''
    def shown(s,siblings,index):
        original,position=contexts.get(id(s),(siblings,index))
        return public(s) and condition(s.get('visible'),original,position,read,xbmc.getCondVisibility)
    def write(key, value):
        from . import ui_preferences as prefs
        if key in prefs.OPTIONS:
            return prefs.set_value(key, value)
        addon.setSetting(key,value);invalidate();prefs.publish();return True
    def display(s):
        raw=read(s);kind=s.get('type')
        if kind=='preference':
            raw=prefs.value(s.get('id'))
            return value_label(s.get('id'),raw,arabic)
        if kind=='bool':return text('مفعّل','On') if raw.lower()=='true' else text('مطفأ','Off')
        if kind in ('enum','labelenum'):
            vals,names=enum_choices(s,label)
            return names[vals.index(raw)] if raw in vals and vals.index(raw)<len(names) else raw
        return raw if kind!='action' else text('فتح','Open')
    def edit(s,siblings,index):
        siblings,index=contexts.get(id(s),(siblings,index))
        title=label(s.get('label',''));kind=s.get('type');key=s.get('id');old=read(s)
        if not condition(s.get('enable'),siblings,index,read,xbmc.getCondVisibility):
            xbmcgui.Dialog().ok('Dex Hub',text('هذا الخيار يعتمد على تفعيل الخيار المرتبط به أولًا.','Enable the related option first.'));return
        if key in ('theme_preset','homeui_layout_mode'):
            from .visual_preferences import edit as visual_edit
            return visual_edit(key)
        if kind=='action':
            action=s.get('action','')
            if 'action=appearance_options' in action:
                from .appearance_options import open_settings
                open_settings()
            elif action:
                xbmc.executebuiltin(action)
            return 'launched'
        if kind=='preference':
            old=prefs.value(key);choices=prefs.OPTIONS[key][1]
            if choices is None:write(key,'false' if old=='true' else 'true')
            else:
                selected=_select_dialog(title,[_row(value_label(key,v,arabic)) for v in choices],preselect=choices.index(old) if old in choices else 0)
                if 0 <= selected < len(choices):write(key,choices[selected])
        elif kind=='bool': write(key,'false' if old.lower()=='true' else 'true')
        elif kind in ('enum','labelenum','slider'):
            vals,names=(lambda v:(v,v))(slider_choices(s)) if kind=='slider' else enum_choices(s,label)
            selected=_select_dialog(title,[_row(name) for name in names],preselect=vals.index(old) if old in vals else 0)
            if 0 <= selected < len(vals):write(key,vals[selected])
        elif kind in ('text','number'):
            keyboard=xbmc.Keyboard(old,title)
            keyboard.doModal()
            if keyboard.isConfirmed():
                result=keyboard.getText()
                if kind!='number' or result.replace('.', '', 1).isdigit():write(key,result)
        elif kind=='folder':
            result=xbmcgui.Dialog().browse(3,title,'files',defaultt=old)
            if result:write(key,result)
    def open_category(c,siblings):
        cursor=0
        while True:
            rows=[(i,s) for i,s in enumerate(siblings) if shown(s,siblings,i)]
            if not rows:return
            selected=_select_dialog(label(c.get('label')),[_row(label(s.get('label','')),display(s)) for _,s in rows],preselect=min(cursor,len(rows)-1))
            if selected<0:return
            cursor=selected;index,s=rows[selected]
            if edit(s,siblings,index)=='launched':return 'launched'
    while True:
        visible=[(c,s) for c,s in categories if any(shown(v,s,i) for i,v in enumerate(s))]
        if category:
            selected=next(((c,s) for c,s in visible if c.get('id')==category),None)
            if selected:open_category(*selected)
            return True
        selected=_select_dialog(text('إعدادات الإضافة','Add-on settings'),[_row(label(c.get('label'))) for c,_ in visible])
        if selected<0:return True
        if open_category(*visible[selected])=='launched':return True
