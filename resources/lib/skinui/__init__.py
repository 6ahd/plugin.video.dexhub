# -*- coding: utf-8 -*-
"""Dex Hub's side of skin.dexhub, the native Kodi skin (v5.10.109).

The skin draws the Home, the title page and the grids itself; this package
gives it data. Its rows are Kodi containers whose content is a Dex Hub path
(action=skin_row), answered from a per-row cache so a row shows in a few
milliseconds; the add-on's service keeps those caches fresh and publishes
the rows (titles, shapes, paths) as Home window properties.

  common    paths, cache files, window properties (light: no router)
  items     row tiles to ListItems (light)
  serve     the plugin routes (skin_row is answered without the router)
  rows      which rows each tab has, their loading and publishing (router)
  title     the title page's seasons, episodes, cast and related titles (router)
  actions   play, sources, trailer, favourite, menus, grids, sort and filter
  worker    the service thread that publishes and refreshes the rows
"""
