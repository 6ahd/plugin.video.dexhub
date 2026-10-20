# -*- coding: utf-8 -*-
"""Display real playback positions in seconds, independently of Kodi."""
import datetime
import math

def seconds(value):
    try:
        value = float(value or 0)
        return max(0.0, value) if math.isfinite(value) else 0.0
    except (ValueError, TypeError, OverflowError):
        return 0.0

def clock(value):
    value = int(seconds(value))
    return ('%d:%02d:%02d' % (value // 3600, value // 60 % 60, value % 60)
            if value >= 3600 else '%02d:%02d' % (value // 60, value % 60))

def progress(position, duration, english=False):
    position, duration = seconds(position), seconds(duration)
    if not position:
        return '', 0
    if duration:
        position = min(position, duration)
        text = ('Watched %s / %s · %s left' if english else 'شاهدت %s / %s · باقي %s') % (
            clock(position), clock(duration), clock(duration-position))
        return text, int(100 * position / duration)
    return ('Watched %s' if english else 'شاهدت %s') % clock(position), 0

def local_time(value):
    """Only format explicit offset-aware airtimes; date-only is not midnight."""
    try:
        dt = datetime.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            return ''
        return dt.astimezone().strftime('%H:%M %Z').strip()
    except (ValueError, TypeError, OverflowError):
        return ''

def stamp(value, english=False):
    try:
        dt = datetime.datetime.fromtimestamp(seconds(value)).astimezone()
        if not seconds(value):
            return ''
        return ('Detected ' if english else 'رُصد ') + dt.strftime('%d/%m · %H:%M')
    except (ValueError, OverflowError, OSError):
        return ''
