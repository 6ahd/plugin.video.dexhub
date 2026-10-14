"""Pure source-count labels shared by UI and search; no router imports."""

def provider_stats_line(entries, max_names=4):
    """Fen Light-style provider tally: "AIOStreams: 12 | Torrentio: 8".

    Gives instant confidence in the scan and makes a silent provider
    (0 rows) visible at a glance instead of needing kodi.log.
    """
    try:
        counts = {}
        for e in entries or []:
            name = str(e.get('provider_name_raw') or e.get('addon') or '?').strip()
            # Collapse "Plex • Server" style to the head token for brevity.
            head = name.split(' \u2022 ')[0].strip() or '?'
            counts[head] = counts.get(head, 0) + 1
        if not counts:
            return ''
        ranked = sorted(counts.items(), key=lambda kv: -kv[1])
        parts = ['%s: %d' % (n, c) for n, c in ranked[:max_names]]
        extra = len(ranked) - max_names
        if extra > 0:
            parts.append('+%d' % extra)
        return ' | '.join(parts)
    except Exception:
        return ''
