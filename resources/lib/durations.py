# -*- coding: utf-8 -*-
"""v5.10.286: runtime values arrive as minutes (TMDb, Stremio 'runtime'),
seconds (Kodi, servers 'duration') or text ('2h 30min', '150 min').
One reading for all of them, always in seconds."""
import re


def to_seconds(runtime='', duration=''):
    """runtime: minutes unless the text says hours; duration: seconds unless
    the text says minutes or the number is a plausible minute count."""
    for value, minutes in ((runtime, True), (duration, False)):
        if value in (None, '', 0, '0'):
            continue
        if isinstance(value, (int, float)):
            n = float(value)
            if n <= 0:
                continue
            if minutes:
                return int(n * 60) if n < 1000 else int(n)
            return int(n * 60) if n < 400 else int(n)
        text = str(value).lower()
        hours = re.search(r'(\d+(?:\.\d+)?)\s*h', text)
        mins = re.search(r'(\d+(?:\.\d+)?)\s*m(?!s)', text)
        if hours or mins:
            return int(float(hours.group(1) if hours else 0) * 3600 + float(mins.group(1) if mins else 0) * 60)
        m = re.search(r'(\d+(?:\.\d+)?)', text)
        if m:
            return to_seconds(float(m.group(1)) if minutes else '', '' if minutes else float(m.group(1)))
    return 0


def sane(seconds):
    """A runtime read twice as minutes (150 min -> 9000 s -> 540000 s showed
    as 150h 0m): anything over 16 hours that divides by 60 is brought back."""
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        return 0
    while seconds > 16 * 3600 and seconds % 60 == 0:
        seconds //= 60
    return seconds
