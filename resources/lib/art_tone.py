# -*- coding: utf-8 -*-
"""A page's soft light in the colour of the title on show (v5.10.456), read after the cursor rests."""
import threading

DEFAULT = '40506CFF'


class Tone(object):
    """Window property ``tone``; only the last rest counts, a colour already read comes at once."""
    def __init__(self, window, delay=0.45):
        self.window, self.delay = window, delay
        self.gen = 0
        self.last = ''
        self.timer = None
        window.setProperty('tone', DEFAULT)

    def follow(self, art):
        art = art or ''
        if art == self.last:
            return
        self.last = art
        self.gen += 1
        gen = self.gen
        if self.timer:
            self.timer.cancel()
        if not art:
            self.window.setProperty('tone', DEFAULT)
            return
        self.timer = threading.Timer(self.delay, self._read, (art, gen))
        self.timer.daemon = True
        self.timer.start()

    def _read(self, art, gen):
        try:
            from . import art_colour
            found = art_colour.colour(art) or {}
            glow = str(found.get('accent') or found.get('glow') or '')
        except Exception:
            glow = ''
        if gen == self.gen and len(glow) == 8:
            self.window.setProperty('tone', '59' + glow[2:])

    def stop(self):
        self.gen += 1
        if self.timer:
            self.timer.cancel()
