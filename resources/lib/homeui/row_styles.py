"""Shared row looks and real, ordered collection previews (no extra fetches)."""
WORK_STYLES = ('poster_large', 'feature', 'landscape_info')
COLLECTION_STYLES = ('collection_spotlight', 'collection_compact', 'collection_ranking')
LARGE_STYLES = ('poster_large', 'feature')
PREVIEW_KEYS = tuple('%s%d' % (prefix, n) for n in range(1, 4) for prefix in ('preview_art', 'preview_title', 'preview_meta'))


def preview_fields(entries):
    out = {}
    selected = []
    for entry in entries or []:
        kind = (entry.get('props') or {}).get('kind') or entry.get('kind')
        if kind not in ('work', None):
            continue
        art, info = entry.get('art') or entry, entry.get('info') or entry
        title = info.get('title') or entry.get('label') or ''
        image = art.get('fanart') or art.get('landscape') or art.get('poster') or ''
        if not image:
            continue
        selected.append((title, image, str(info.get('year') or '')))
        if len(selected) == 3:
            break
    for n, (title, art, meta) in enumerate(selected, 1):
        out.update({'preview_title%d' % n: title, 'preview_art%d' % n: art, 'preview_meta%d' % n: meta})
    return out
