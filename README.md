<div align="center">

# Dex Hub

**A modern Stremio-style streaming experience for Kodi, with its own skin**

Stremio & Nuvio add-ons · Plex, Emby, Jellyfin & Silo · Live TV · Native Trakt, Simkl & MDBList · Arabic-first UI · TMDb Helper integration

[![Version](https://img.shields.io/badge/version-5.10.331-7c3aed)]()
[![Skin](https://img.shields.io/badge/skin-3.28.74-ffd23f)]()
[![Kodi](https://img.shields.io/badge/Kodi-21%20Omega%20%7C%2022%20Piers-33d8f7)]()
[![License](https://img.shields.io/badge/license-all%20rights%20reserved-ef4444)](LICENSE.txt)
[![Ko-fi](https://img.shields.io/badge/support-Ko--fi-ff5e5b)](https://ko-fi.com/dexworld)

[Install](#installation) · [What's new](#whats-new) · [Features](#what-makes-dex-hub-different) · [Skin](#the-dex-hub-skin) · [Screenshots](#screenshots) · [Support](#support-the-project)

<img src=".github/screenshots/home.jpg" alt="Dex Hub Home in the Dex Hub skin" />

</div>

## What's new

**Dex Hub 5.10.331 · Dex Hub skin 3.28.74**

* **Trailer inside the poster:** the trailer plays in the focused card in its row, landscape and Legacy cards included, or behind the page as before (Settings, Look, Trailer place).
* **New Legacy home:** the title's logo and details on top, framed rows with card numbers. The side menu can hide while you scroll, the current section's icon takes the accent colour, the last row opens the next section, and sections can open on focus.
* **Title page:** Collections (the saga card, its films in release order), Studios and networks, IMDb Parents Guide and age ratings, a time left bar, and a More button with Read synopsis and Change artwork (backdrop and logo). Up opens an island with Saved, Alerts and the upcoming episodes Schedule.
* **New player, like Nuvio Legacy:** a full subtitle page inside the player (font, size, colour, background, shadow, position, four ready looks), subtitles searched there from Dex Hub and every subtitle add-on (a4kSubtitles, OpenSubtitles, SubDL), tracks with flags, full names and badges (Atmos, DD+, 5.1, SDH), a language filter, and a sources island. The classic design stays as an option.
* **Faster:** a source is picked 2.5 s after the first results, sources get ready in the background on the title page (long press Play opens the list at once), and Plex, Emby and Silo answers are cached.
* **Power:** its own icon in the side menu and the top bar, and a power menu with icons.
* **Apple TV:** the Kodi build with the Aether player (Dolby Vision, Atmos and DTS:X at 7.1.4) is at [6ahd/Kodi-tvOS](https://github.com/6ahd/Kodi-tvOS), with the Dex Hub repository built in so the add-on and skin update on their own.

Full history in [changelog.txt](changelog.txt).

## What makes Dex Hub different

Dex Hub brings the Stremio philosophy to Kodi without sacrificing what makes Kodi powerful. Instead of locking you into one provider, it searches every source you trust in parallel and shows the first results within a second: you click and watch.

* **Stremio and Nuvio add-ons:** paste any Stremio or Nuvio add-on link, or sign in to Nuvio (QR login from your phone) and keep add-ons, collections, watch progress and your saved library in sync.
* **Your own servers:** Plex, Emby, Jellyfin and Silo, as libraries and as sources in the same picker. They show up right after you sign in.
* **Live TV:** an Xtream Codes account or an M3U playlist, with channel groups as Home rows, what is on now and next, a programme guide, VOD movies and series, and the channel playing behind the Home.
* **One unified source picker** with results grouped by type (Usenet, debrid, direct, Plex) and ranked by quality, language and provider. It shows Cached, Uncached or In your account, and its source line can follow your AIOStreams formatter.
* **Native Trakt, Simkl, MDBList and Floppy:** PIN linking, scrobbling, watched history, watchlists and your lists, no third-party add-on needed.
* **Continue Watching and Next Up** rows on the Home, quick play from the source you used last, and auto-play of the next episode (or Up Next, if you prefer it).
* **Subtitles that fit:** subtitles searched while you choose a source, your preferred languages first, AutoSync that fits any external subtitle to the timing of the one inside the video, and AI subtitles when you link DexWorld.
* **Metadata your way:** one metadata and artwork source for everything (AIOMetadata, TMDb, Cinemeta), Fanart.tv clearlogos, BetterPosters, IMDb and TMDb ratings and more, in Arabic or English.
* **TMDb Helper player:** one tap registers Dex Hub, so your skin's TMDb widgets hand playback to it.
* **Arctic Fuse 3 integration:** install Dex Hub's Home and pages into Arctic Fuse 3 in one tap (your skin is backed up first) and restore it whenever you like.
* **Collections:** build your own from your catalogs, or import Fusion Widgets, Kaptain or Nuvio JSON collections, with animated collection art.
* **28 themes**, from Dex Crimson and Midnight Ocean to OLED Black and Nuvio White.
* **Light on low-end boxes:** a one-tap speed profile, a quick lightweight setup and covers that load on focus, built to stay fast on CoreELEC and Zidoo boxes.
* **Arabic-first UI** with full RTL support, and English too.
* **Kodi 22 ready:** playback handoff uses the modern VideoInfoTag API, no deprecated calls.

## The Dex Hub skin

An optional skin made for Dex Hub. Kodi draws Dex Hub's Home itself, so it opens fast and stays smooth:

* a moving spotlight with trailers that slide the rows down Apple TV style, your Dex Hub rows and collection animations;
* a trailer or a live channel playing behind the whole Home;
* icon tabs, an Edit Home button and per-row styles;
* your Nuvio profile, Dex Hub's title pages, grids and My watching;
* quality badges in the player: Elite, Gold, Minimalist white or your own images;
* Arctic Fuse 3's video player, settings pages and dialogs, from ABUKARIM's copy with his CoreELEC player tools (PPI, Dolby Vision VS10) and Arabic font sets.

Install it from the Dex Hub repository (Look and feel → Skin), or turn it on from Dex Hub's settings with **Home → Dex Hub skin: use it** (it is installed from the repository when missing). **Home → Dex Hub skin: back to the skin used before it** takes you back. The skin needs Kodi 21 or later.

## Screenshots

<table>
  <tr>
    <td align="center"><img src=".github/screenshots/title.jpg" alt="Title page" /><br><sub>Title page with episodes, progress and ratings</sub></td>
    <td align="center"><img src=".github/screenshots/sources.jpg" alt="Source picker" /><br><sub>Source picker with Cached status and badges</sub></td>
  </tr>
  <tr>
    <td align="center"><img src=".github/screenshots/my-library.jpg" alt="My Library" /><br><sub>My Library: every service in one place</sub></td>
    <td align="center"><img src=".github/screenshots/dex-community.jpg" alt="Dex Community" /><br><sub>Dex Community: ready made catalogs</sub></td>
  </tr>
  <tr>
    <td align="center"><img src=".github/screenshots/collections.jpg" alt="Collections" /><br><sub>Collections from your catalogs</sub></td>
    <td align="center"><img src=".github/screenshots/spotlight-cards.jpg" alt="Spotlight cards" /><br><sub>Spotlight cards for whole catalogs</sub></td>
  </tr>
  <tr>
    <td align="center"><img src=".github/screenshots/control-centre.jpg" alt="Control centre" /><br><sub>Control centre</sub></td>
    <td align="center"><img src=".github/screenshots/screensaver.jpg" alt="Cinema screensaver" /><br><sub>Cinema screensaver: what's coming this week</sub></td>
  </tr>
</table>

## Installation

Dex Hub supports Kodi 21 (Omega) and 22 (Piers) on Android, Linux, Windows, macOS, CoreELEC, LibreELEC and Fire TV. The Dex Hub skin needs Kodi 21 or later.

### From the Dex Hub repository (recommended, updates arrive automatically)

1. In Kodi, go to **Settings → System → Add-ons** and enable **Unknown sources**.
2. Go to **Settings → File manager → Add source**, enter `https://dexworld.cc/dexhub/` and name it **Dex Hub**.
3. Go to **Add-ons → Install from zip file → Dex Hub** and pick the `repository.dexhub` zip.
4. Go to **Add-ons → Install from repository → Dex Hub Repository**, install **Dex Hub** from Video add-ons, then, if you want it, the **Dex Hub** skin from Look and feel → Skin.

### From zip files

Download both zips from the [latest release](../../releases/latest) and install them with **Add-ons → Install from zip file**: first `plugin.video.dexhub`, then `skin.dexhub`. Zip installs don't update themselves, so the repository is the better choice.

On first launch, Quick Start offers three ways in: connect Nuvio, add Stremio add-ons (Cinemeta and Torrentio are good starters), or set things up by hand.

## Quick start

In Dex Hub's Control centre:

* **Add a source:** Accounts & Sources → Add a source → paste a Stremio or Nuvio add-on link
* **Connect Nuvio:** Accounts & Sources → Connect & sync Nuvio
* **Add Live TV:** Accounts & Sources → Live TV (IPTV) → add your Xtream Codes account, or an M3U playlist through IPTV Simple
* **Connect Trakt:** Accounts & Sources → Trakt account & sync
* **Add ready made catalogs:** Home → Dex Community
* **Arrange your Home:** Home → Arrange Home rows
* **Back up your setup:** System → Backup and restore
* **Speed up a slow box:** System → Speed profile, one tap
* **Switch to the Dex Hub skin:** Home → Dex Hub skin: use it
* **Dex Hub inside Arctic Fuse 3:** Home → Dex Hub in Arctic Fuse 3: install or update
* **Choose player badges:** Player → Player badges / Badge pack

## Support the project

Dex Hub is built and maintained by one person in his free time. A big thank you to everyone who has supported it with a donation: you're the reason it keeps getting this much time, and many of the features above came from your suggestions and logs ❤️

If you'd like the updates to keep coming, you can support the project on **[Ko‑fi](https://ko-fi.com/dexworld)**.

Found a bug? Open an [issue](../../issues) and attach your full `kodi.log`. It helps a lot.

## Tech

Built in Python 3 on Kodi's own APIs (xbmc, xbmcgui, xbmcplugin, xbmcvfs), with no required third-party modules. A background service keeps the Home rows, account sync and player info up to date. The Dex Hub skin is plain Kodi skin XML based on Estuary, with Arctic Fuse 3's player and dialogs.

## Credits

* Dex Hub skin: based on Estuary by Team Kodi
* The skin's video player, settings pages and dialogs: Arctic Fuse 3 by jurialmunkey, from ABUKARIM's modified copy with his CoreELEC player tools and Arabic font sets
* Fonts: Inter, IBM Plex Sans Arabic, League Spartan and Font Awesome 6 Free
* Icons: Font Awesome Free and Simple Icons
* Badge packs: [Elite](https://github.com/leonevz/Elite-Badges) by leonevz, [Gold](https://github.com/k45sle/NUVIO_BADGES) by k45sle and [Minimalist white](https://github.com/sweatycab/nuvio-minimalist-badges) by sweatycab, downloaded from their own repositories when you pick them

## Disclaimer

The author of this add-on does not host any of the content which is found and has no affiliation with any of the content providers. The author is in no way affiliated with Kodi, Team Kodi, the XBMC Foundation, Stremio, Nuvio, Plex, Emby, Jellyfin, Silo, Trakt or TMDb.

## License

**Dex Hub (the add-on) is free to use on your own devices, with all rights reserved.** Redistributing it, modifying it, publishing a version based on it or bundling it with a paid service needs written permission. Read the full terms in [LICENSE.txt](LICENSE.txt). Third-party parts keep their own licences ([THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt)). Versions before 5.10.216 were released under the MIT license.

The Dex Hub skin keeps the licenses of the work it builds on: CC BY-SA 4.0 (Estuary), CC BY-NC-SA 4.0 (Arctic Fuse 3 player) and GPL 2.0.
