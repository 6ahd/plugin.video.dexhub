"""Pure, inexpensive presentation of source-session completion (no Kodi I/O)."""

def search_status(done, total, pending=None, failed=0, tr=lambda s: s):
    total = max(0, int(total or 0))
    failed = max(0, int(failed or 0))
    pending = None if pending is None else max(0, int(pending or 0))
    if not done:
        title = tr('جار البحث عن المصادر')
        detail = tr('%s نتيجة متاحة — يمكنك الاختيار الآن') % total
        if pending is not None:
            detail = tr('%s مصادر قيد البحث · %s نتيجة متاحة') % (pending, total)
        return title, detail, 'FF27C8E0', 'searching'
    incomplete = failed + (pending or 0)
    if incomplete:
        return (tr('انتهى البحث — نتائج جزئية'),
                tr('%s نتيجة · %s مصادر تعثرت أو لم تكتمل') % (total, incomplete),
                'FFFFC366', 'partial')
    if not total:
        return (tr('اكتمل البحث — لا توجد نتائج'), tr('جرّب عملًا آخر أو راجع إعدادات المصادر'),
                'FFABB7C9', 'empty')
    return (tr('اكتمل البحث'), tr('%s نتيجة جاهزة للاختيار') % total,
            'FF83E6BD', 'complete')
