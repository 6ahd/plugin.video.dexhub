# -*- coding: utf-8 -*-
"""Geometry and control ids of the Live TV guide (v5.10.131).

Shared by guide.py (which places the programme blocks) and the generator of
dexhub_live.xml, so the window and the code always agree. Plain Python: no
Kodi module is imported here.

Like every Dex Hub window the guide comes in three sizes (ui_size.py): each
is laid out on its own canvas that Kodi zooms to fill the screen, so the
numbers below are canvas units. Heights that hold text stay the same on
every canvas (the fonts are the same size there); what spans the screen
follows the canvas.
"""

# the sizes: (file suffix, canvas width, canvas height, zoom in percent)
SIZES = {
    'normal': ('', 1920, 1080, 100),
    'large': ('_l', 1600, 900, 120),
    'extra large': ('_xl', 1477, 831, 130),
}

# control ids
VIDEO = 30
RAIL = 300
BTNS = 310
CATS = 320
GRID = 400
TOOLS = 410
VOD = 450           # v5.10.133: the films and series posters (a panel control)
PANEL = 700
CONTENT = 800
TOP = 810
BAR_FG = 821
HEADER = 830
GRID_GROUP = 900
NOW_LINE = 950
NOW_DOT = 951
ROW_BASE = 1000
ROW_STEP = 100
BLOCK_BASE = 10
BLOCK_STEP = 10
BLOCKS = 8          # programme blocks per row (two hours hold at most eight)

# inside a row (a group): its tile, logo, number, name (no logo) and focus mark
R_TILE, R_LOGO, R_NUMBER, R_NAME, R_MARK = 1, 2, 3, 4, 5
# inside a block (a group, block_id itself): its background, progress, time
# line and title
B_BG, B_BAR, B_SMALL, B_TITLE = 1, 2, 3, 4

WINDOW_MIN = 120    # the timeline shows two hours
STEP_MIN = 30       # in half hours


def row_id(row):
    return ROW_BASE + row * ROW_STEP


def block_id(row, block):
    return ROW_BASE + row * ROW_STEP + BLOCK_BASE + block * BLOCK_STEP


class Layout(object):
    """Every position of the guide on one canvas."""

    def __init__(self, width=1920, height=1080, zoom=100, suffix=''):
        self.W, self.H, self.zoom, self.suffix = width, height, zoom, suffix
        small = width < 1900
        # the icon rail, then the categories panel (it slides away in the guide)
        self.RAIL_W = 104
        self.PANEL_X = self.RAIL_W
        self.PANEL_W = max(400, int(round(width * 0.25)))
        # everything right of the panel moves with it: placed for the closed
        # panel, slid right by PANEL_SHIFT while the panel is open (its right
        # end is then beyond the screen, as in UHF)
        self.CONTENT_X = self.RAIL_W + 24
        self.CONTENT_W = width - self.CONTENT_X - 40
        self.PANEL_SHIFT = self.PANEL_W + 8
        self.SLIDE_MS = 220
        # the preview and the programme on the focused channel
        self.PREVIEW_X = 0
        self.PREVIEW_Y = 28 if small else 40
        self.PREVIEW_W = int(round(width * 0.3))
        self.PREVIEW_H = int(round(self.PREVIEW_W * 9 / 16.0))
        self.INFO_X = self.PREVIEW_W + 40
        self.INFO_W_WIDE = self.CONTENT_W - self.INFO_X
        self.INFO_W_NARROW = width - 40 - (self.CONTENT_X + self.PANEL_SHIFT + self.INFO_X)
        self.BAR_W = min(520, self.INFO_W_NARROW)
        # the timeline and the rows (the full guide moves both up)
        self.HEADER_Y = self.PREVIEW_Y + self.PREVIEW_H + 26
        self.HEADER_H = 48
        self.GRID_Y = self.HEADER_Y + self.HEADER_H + 8
        self.FULL_SHIFT = self.HEADER_Y - self.PREVIEW_Y
        self.ROW_H = 92 if not small else 84
        self.TILE_H = self.ROW_H - 8
        self.TILE_W = 196 if not small else 176
        self.PROG_X = self.TILE_W + 12
        self.PROG_W = self.CONTENT_W - self.PROG_X
        self.GAP = 8
        # rows the cursor moves over before the rows scroll, and rows drawn
        # (one more shows its top under the last whole row)
        self.VISIBLE = max(3, (height - self.GRID_Y) // self.ROW_H)
        grid_full = self.GRID_Y - self.FULL_SHIFT
        self.VISIBLE_FULL = max(self.VISIBLE, (height - grid_full) // self.ROW_H)
        self.ROWS = self.VISIBLE_FULL + 1
        # v5.10.133: films and series: the focused title's backdrop and text
        # where the preview and the programme are, its category's posters
        # below (no timeline)
        self.VOD_Y = self.HEADER_Y - 6
        self.VOD_COLS = 9 if width >= 1900 else (8 if width >= 1550 else 7)
        self.VOD_CELL_W = self.CONTENT_W // self.VOD_COLS
        self.VOD_POSTER_W = self.VOD_CELL_W - 22
        self.VOD_POSTER_H = int(round(self.VOD_POSTER_W * 1.5))
        self.VOD_CELL_H = self.VOD_POSTER_H + 30

    def px_per_min(self):
        return float(self.PROG_W) / WINDOW_MIN

    def visible(self, full):
        return self.VISIBLE_FULL if full else self.VISIBLE


def for_size(size):
    """The layout of a size name of ui_size ('normal', 'large', 'extra large')."""
    suffix, width, height, zoom = SIZES.get(size) or SIZES['large']
    return Layout(width, height, zoom, suffix)
