# -*- coding: utf-8 -*-
"""Subtitle AutoSync on the device (v5.10.117, from DexSubtitles 5.7.3).

autosync, dexcues, dexalign and dexwatch are DexSubtitles' own modules: the
timing of the subtitle the video file carries (its MKV cue index, read in two
or three small requests) is the reference, and an external subtitle whose
timing matches it well enough is shifted onto it (offset and the 23.976, 24
and 25 fps stretch). runner.py runs the queue and the subtitle watcher in Dex
Hub's service only while DexSubtitles is not enabled: with both installed,
DexSubtitles' service does it, never both.
"""
