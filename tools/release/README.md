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

* fixes, applied once and then no-ops: hero plot box 68 high, no
  `videowindow` inside card layouts, "Ends at" only with a duration
* `media/Textures.xbt` from `TexturePacker -dupecheck` (or `--xbt file`)
* `--compact` only in the output, each file checked to keep the same content

The card designs themselves (about 110 controls a card) come from the script
that generates `Home.xml`; that is where scrolling gets faster.
