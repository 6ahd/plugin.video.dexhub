# -*- coding: utf-8 -*-
"""Dex Hub's own Debrid layer (v5.10.568).

Dex Hub plays torrents through the viewer's own Debrid account, without POV,
Fen Light, Umbrella or Red Light in between:

  accounts.py   the linked accounts (QR sign-in, tokens kept on this device)
  services.py   Real-Debrid, AllDebrid, Premiumize and TorBox: what is cached,
                a torrent's file as a link, the account's own files
  stremthru.py  what Real-Debrid and AllDebrid have cached (they stopped saying)
  files.py      the right file of a season pack
  hashcache.py  what each service answered, kept for a while on this device
  signin.py     the TV's sign-in window (QR, a code, waiting for the phone)

Written for Dex Hub from the services' public APIs. Nothing is copied from
another add-on.
"""
