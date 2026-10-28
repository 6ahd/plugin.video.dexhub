"""Languages picked by name from a list, as Nuvio does (v5.10.427): a primary,
a secondary and a third language, each chosen from the list instead of typing
codes such as ar,en with the remote."""

# your languages first, then the rest by their English name
LANGS = (
    ('ar', 'العربية', 'Arabic'), ('en', 'الإنجليزية', 'English'), ('fr', 'الفرنسية', 'French'),
    ('tr', 'التركية', 'Turkish'), ('es', 'الإسبانية', 'Spanish'), ('de', 'الألمانية', 'German'),
    ('ja', 'اليابانية', 'Japanese'), ('ko', 'الكورية', 'Korean'), ('hi', 'الهندية', 'Hindi'),
    ('ur', 'الأردية', 'Urdu'), ('fa', 'الفارسية', 'Persian'), ('it', 'الإيطالية', 'Italian'),
    ('pt', 'البرتغالية', 'Portuguese'), ('pt-br', 'البرتغالية البرازيلية', 'Brazilian Portuguese'),
    ('ru', 'الروسية', 'Russian'), ('zh', 'الصينية', 'Chinese'), ('id', 'الإندونيسية', 'Indonesian'),
    ('ms', 'الملايوية', 'Malay'), ('nl', 'الهولندية', 'Dutch'), ('pl', 'البولندية', 'Polish'),
    ('sv', 'السويدية', 'Swedish'), ('no', 'النرويجية', 'Norwegian'), ('da', 'الدنماركية', 'Danish'),
    ('fi', 'الفنلندية', 'Finnish'), ('el', 'اليونانية', 'Greek'), ('he', 'العبرية', 'Hebrew'),
    ('ro', 'الرومانية', 'Romanian'), ('hu', 'المجرية', 'Hungarian'), ('cs', 'التشيكية', 'Czech'),
    ('uk', 'الأوكرانية', 'Ukrainian'), ('bg', 'البلغارية', 'Bulgarian'), ('hr', 'الكرواتية', 'Croatian'),
    ('sr', 'الصربية', 'Serbian'), ('th', 'التايلاندية', 'Thai'), ('vi', 'الفيتنامية', 'Vietnamese'),
    ('bn', 'البنغالية', 'Bengali'), ('ta', 'التاميلية', 'Tamil'), ('te', 'التيلوغوية', 'Telugu'),
    ('ml', 'المالايالامية', 'Malayalam'), ('tl', 'الفلبينية', 'Filipino'), ('ku', 'الكردية', 'Kurdish'),
)
SLOTS = (('الأساسية', 'Primary'), ('البديلة', 'Secondary'), ('الثالثة', 'Third'))


def name(code, txt):
    for c, ar, en in LANGS:
        if c == code:
            return txt(ar, en)
    return code.upper() if code else txt('بدون', 'None')


def pick_one(txt, title, current='', none=True, extra=()):
    """One language from the list ('' for none); None when cancelled.
    extra: (value, ar, en) rows shown first (Kodi default, original audio)."""
    import xbmcgui
    rows = list(extra) + ([('', 'بدون', 'None')] if none else []) + list((c, ar, en) for c, ar, en in LANGS)
    labels = [txt(ar, en) + ('' if not c or c in ('default', 'original') else '  ·  ' + c) for c, ar, en in rows]
    at = next((i for i, r in enumerate(rows) if r[0] == current), 0)
    n = xbmcgui.Dialog().select(title, labels, preselect=at)
    return rows[n][0] if n >= 0 else None


def edit_order(txt, title, codes, slots=3, extra=(), first_none=False):
    """Primary, secondary (and third) languages, each picked by name.
    Returns the new ordered list, or None when nothing changed."""
    import xbmcgui
    codes = [c for c in codes if c][:slots]
    start = list(codes)
    while True:
        rows = []
        for i in range(slots):
            value = codes[i] if i < len(codes) else ''
            label = next((txt(ar, en) for c, ar, en in extra if c == value), None) or name(value, txt)
            rows.append('%s: [B]%s[/B]' % (txt(*SLOTS[i]), label))
        rows.append(txt('تم', 'Done'))
        n = xbmcgui.Dialog().select(title, rows)
        if n < 0 or n == slots:
            return codes if codes != start else None
        if n > len(codes):
            n = len(codes)        # an empty slot fills the first free one
        picked = pick_one(txt, '%s — %s' % (title, txt(*SLOTS[n])), codes[n] if n < len(codes) else '',
                          none=(n > 0 or first_none), extra=extra if n == 0 else ())
        if picked is None:
            continue
        if picked == '':
            codes = codes[:n] + codes[n + 1:]
        else:
            codes = [c for c in codes if c != picked]
            codes.insert(min(n, len(codes)), picked)
            # Kodi default / original audio are a primary only
            specials = set(c for c, _ar, _en in extra)
            codes = (codes[:1] + [c for c in codes[1:] if c not in specials])[:slots]


def names(codes, txt):
    return txt('، ', ', ').join(name(c, txt) for c in codes if c) or txt('بدون', 'None')
