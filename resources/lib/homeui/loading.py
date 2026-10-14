# -*- coding: utf-8 -*-
"""Small, shared decisions for cached rows and speculative loading."""


def tile_key(tile):
    tile = tile or {}
    # Paths distinguish editions/episodes that share the same show ID.
    return (tile.get('kind') or '', tile.get('path') or tile.get('src') or
            tile.get('canonical_id') or tile.get('imdb_id') or tile.get('tmdb_id') or '',
            tile.get('season') or '', tile.get('episode') or '')


def selection(previous, current, selected):
    """Keep the selected work after a reorder; clamp if it was removed."""
    selected = max(0, int(selected or 0))
    if selected < len(previous or []):
        key = tile_key(previous[selected])
        if key[1]:
            for index, tile in enumerate(current or []):
                if tile_key(tile) == key:
                    return index
    return min(selected, max(0, len(current or []) - 1))


class Budget(object):
    """Measure source latency without probing a server or a device."""
    def __init__(self):
        self.latency = 0.0

    def record(self, seconds):
        value = max(0.0, min(30.0, float(seconds)))
        self.latency = value if not self.latency else self.latency * 0.7 + value * 0.3

    def depth(self, light=True, busy=False):
        # Focused row, its visible neighbor, and at most one speculative row.
        return 1 if busy else (2 if light or self.latency >= 1.5 else 3)
