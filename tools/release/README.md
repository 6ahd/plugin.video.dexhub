# Release tools

Not part of the add-on (`tools/` is `export-ignore`, so `git archive` leaves it out).

## Apple TV add-on: `tvos_sync.py`

`plugin.video.dexhub.tvos` is this add-on with its own ids and the Apple TV
parts (Aether and the external players, `selection.py`, `native_presentation.py`).
It fell behind once (5.10.401 while this repository was at 5.10.404). For each
release, bring it up to date from the last Apple build:

```bash
python3 tools/release/tvos_sync.py plugin.video.dexhub.tvos-5.10.404.zip <commit of 5.10.404> out/
# → out/plugin.video.dexhub.tvos-<version of HEAD>.zip
```

Only the files changed since that commit are touched, each merged three ways
(window XMLs per tag), so the Apple changes stay. A conflict leaves that file
as it was and lists it, and no zip is written until it is merged by hand.

## Skins: `skin_build.py`

Builds `skin.dexhub` and `skin.dexhub.tvos` releases from their readable
source (both use the same Home and plot, so run it for each):

```bash
python3 tools/release/skin_build.py skin.dexhub out/ --version 3.28.125 \
    --texturepacker /path/to/TexturePacker --compact
```

* fixes, applied once and then no-ops: hero plot box 68 high, "Ends at"
  only with a duration, the loading artwork over the title page, rounded
  square icons in the Add-ons and File manager lists, the network and
  service logos as 3.28.100 drew them (the title page's own logo, a rounded
  square for the services' app icons, a light chip behind dark logos). The cards'
  `videowindow`s stay: they are the trailer inside the focused card
* `media/Textures.xbt` from `TexturePacker -dupecheck` (or `--xbt file`),
  checked against the loose images first (`xbt_check.py`): a pack with a
  damaged image is left out, since Kodi draws the pack's copy
* `--compact` only in the output, each file checked to keep the same content

The card designs themselves (about 110 controls a card) come from the script
that generates `Home.xml`; that is where scrolling gets faster.

## Title page rows and the island's Friends tab: `skin_title_social.py`

Add-on 5.10.406 lists Reviews (`skin_reviews`) and Pictures (`skin_artwork`)
for the title page and a `friends` tab for the island. This puts them in a
skin's source, once (a second run changes nothing):

```bash
python3 tools/release/skin_title_social.py skin.dexhub
python3 tools/release/skin_title_social.py skin.dexhub.tvos
```

The page's up/down chains are rebuilt with the two new lists; each chain must
first rebuild to exactly what the skin has, else nothing is written and the
chain is named. Then build the release with `skin_build.py` as above.

## The rating card: `skin_capsule.py`

Add-on 5.10.423 shows the rating card when a film starts: the age rating
large, what earned it (IMDb's Parents Guide), a quiet line with the video,
the audio and a rating, published as `dhs.cap.card.*`. This writes the skin's
side between two comments, so a second run only brings it up to date, and
takes out what skins 3.28.134-3.28.137 had for the old playback pill and for
Dex Hub's design of TinyPPI's windows (TinyPPI is its own again):

```bash
python3 tools/release/skin_capsule.py skin.dexhub
python3 tools/release/skin_capsule.py skin.dexhub.tvos
```

The line heights at its top must match `card.h` in
`resources/lib/player_capsule.py`.

## The Home comes whole at Kodi's start: `skin_startup.py`

As Arctic Fuse 3 waits for its widgets on a splash, Dex Hub's screen covers
the Home while its spotlight and first rows load, then goes, so the Home
shows all at once instead of row after row. The add-on's service watches the
rows and lifts it (`resources/lib/skinui/startup_splash.py`, setting
`homeui_startup_splash`). This writes `xml/Custom_1198_DexStartup.xml` and
raises the cover from `xml/Startup.xml`; a second run only brings it up to
date:

```bash
python3 tools/release/skin_startup.py skin.dexhub
python3 tools/release/skin_startup.py skin.dexhub.tvos
```
