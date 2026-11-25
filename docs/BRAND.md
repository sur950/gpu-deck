# Brand

GPU Deck is presented plainly: the app mark, the name, a short descriptor and no company byline. It belongs to the same family as its sibling [Container Deck](https://github.com/sur950/container-deck): a white mark on a crimson tile, warm off-white and near-black surfaces, and crimson as the only accent. The brand files live in [`assets/brand/`](../assets/brand/) and are built by [`assets/brand/build.py`](../assets/brand/build.py) (the README header and the ecosystem diagram by [`readme_art.py`](../assets/brand/readme_art.py)), so a change there reaches the web app, the desktop app, the website and the README in one place.

## Name

**GPU Deck**: two words, `GPU` in capitals, `Deck` capitalised. Not "GPUDeck", "Gpu Deck", "GPU-Deck" or "GD".

In code and identifiers the name is lowercase: `gpudeck` for binaries, the CLI, Terraform types (`gpudeck_*`), env prefixes (`GPUDECK_*`) and the deep-link scheme (`gpudeck://`); `gpu-deck` for the repository, images, the Helm chart and the Redis key prefix.

## Words

| Use | Text |
|---|---|
| Descriptor (under the name in the lockup, `APP_BYLINE`) | GPU control plane |
| Tagline | The control plane for your own GPUs. |
| One-liner | Deploy models across your GPU fleet, switch layouts by time of day, load and unload by rule, and meter every request. |
| Capabilities row (social preview, website) | MODELS · LAYOUTS · RULES · SWAPS |
| Positioning next to gateways | Put any AI gateway (LiteLLM, Kong, Portkey, your own) in front of GPU Deck for users, quotas and rate limits; GPU Deck stores nothing about end users. |

Write "your AI gateway" or "an AI gateway", never a specific gateway product as if GPU Deck depended on it.

## Why crimson

GPU Deck is infrastructure people keep open all day next to terminals and dashboards, and it ships next to Container Deck. The two share one visual family so they read as siblings: the same crimson tile, the same warm neutrals (a paper-like `#F4F3F0` page with white cards in light mode, a soft near-black `#0E0F12` in dark mode) and the same rule that **crimson is the only accent**. It marks what you can act on and where you are: primary buttons, links, the active page, selection, focus rings and the headline chart series. Green, amber and vermilion are kept for **state** (running, warning, error) and the other chart hues for **data**, so when something on the screen is coloured, it means something.

## The mark

The mark is a **graphics card**: the mounting bracket, a shroud with a **cut top-right corner**, two fans and the edge connector, drawn as one silhouette in one colour (white on the crimson tile). It has no letters, so it works in a 16 px favicon, a menu bar and a 1024 px app icon alike. The cut corner is the signature that makes the silhouette ours instead of a generic GPU icon; keep it top right.

### Construction

Drawn on a 64-unit grid (the glyph's box is x 6-58, y 14-52: 52 × 38 units).

| Part | Geometry |
|---|---|
| Bracket | 4 units wide, 38 tall, with a 10-unit tab at the top |
| Shroud | 42 × 28 units, 8-unit chamfer top right, 4-unit radius bottom right, 2-unit radius on the left |
| Fans | two openings of radius 9, 20 units apart, centred on the shroud; hub radius 2.4 to 3 |
| Edge connector | 4 units tall, a 6-unit key and an 18-unit run, 2 units under the shroud |

The fans and the cut corner carry the identity; every other edge is straight.

### Optical sizes

One drawing does not survive from 16 px to 1024 px, so the mark ships in optical sizes (the build picks them for every PNG):

| Size | Where | Drawing |
|---|---|---|
| Large, 128 px and up | app icon, PWA, social preview, stacked lockup (all on the tile) | fans with seven swept blades, connector with contact fingers |
| Medium, 24-96 px | the bare glyph (`glyph-small.svg`, `glyph-adaptive.svg`, `GpuDeckMark`), the macOS tray | fans with three swept blades, solid connector; every edge on an even unit, so it is pixel-exact at 32 px |
| Small, 16 px | `glyph-16.svg` | redrawn on the 16 px grid: every edge on a whole pixel, fans as openings with a hub pixel |
| Tile, 24-96 px | `mark-small.svg` (favicon.svg), lockups, the web app sidebar and sign-in tile, the connect screen, icons 24-64, Windows / Linux tray | inside the tile, fans as rings with a hub, pixel-exact at 32 px |
| Tile, 16 px | `mark-16.svg`, favicon.ico 16 | inside the tile, on the 16 px grid |

### The tile

The app icon form is the white glyph on the **crimson tile**: a vertical gradient from `#D3263F` at the top to `#A10F28` (radius 14 of 64), a soft white top glow, a 20 % white hairline edge. At 128 px and up the glyph is warmed slightly towards the bottom (`#FFFFFF` to `#F7E6E9`) and lifted with a crimson-black contact shadow (`#4A0712`), exactly Container Deck's icon recipe. The desktop app icon follows the macOS grid (824 px body on 1024, soft shadow, 22 % edge). A light variant (white to `#F1EFEA` tile, crimson glyph) exists for platforms and listings that need one.

### Clear space and minimum sizes

- Clear space: the diameter of one fan (18 units, about a third of the glyph's width) on every side of the glyph or lockup.
- Minimum sizes: the glyph 16 px wide (use the 16 px drawing), the tile 16 px, the horizontal lockup 20 px tall.

### Do and don't

- **Do** use the crimson tile for app icons, favicons, lockups and anywhere the mark sits alone; the bare glyph in ink (`#111215`) on light surfaces or paper (`#F2F1EE`) on dark ones only where a tile does not fit (menu-bar templates, monochrome print); the optical size that matches the rendered size.
- **Don't** recolour the tile or put the glyph in status colours or other gradients, add letters or a frame, rotate or mirror it (the bracket stays left, the cut corner top right), outline or stretch it, put it on a busy photo, use the large drawing under 128 px, or set the wordmark in another typeface.

## Lockups and wordmark

The wordmark is "GPU Deck" in **Satoshi Bold**, tracking -0.02 em, converted to outlines in every asset (no font files are shipped), in ink `#111215` on light and paper `#F2F1EE` on dark. In the horizontal lockup the crimson tile comes first (42 units, the 24-96 px drawing) and the name's caps (15.5 units, about three eighths of the tile) are centred on it, 11 units after the tile; the lockup is about 3.6 : 1, so size it by height. The stacked lockup puts the large tile over the name.

## Colours

### The family (shared with Container Deck)

| Role | Light | Dark |
|---|---|---|
| Accent (crimson) | `#B3122E` | `#D3263F` |
| Accent hover | `#9C0F27` | `#E0354D` |
| Accent as text and icons (links, the active nav icon) | `#B3122E` (6.2:1 on the page) | `#F2687B` (5.7:1 on cards) |
| Accent gradient (tiles) | `#D3263F` to `#A10F28` | |
| Text | `#111215` | `#F2F1EE` |
| Secondary text | `#5B5E66` (6.5:1 on cards) | `#A1A3AB` (6.8:1 on cards) |
| Window surface (page) | `#F4F3F0` | `#0E0F12` |
| Cards | `#FFFFFF` | `#1B1C20` to `#15161A` |

Crimson is the only accent. Green, amber and vermilion are used for state only.

### Web app neutrals

| Role | Light | Dark |
|---|---|---|
| Page (`--background`) | `#F4F3F0` | `#0E0F12` |
| Cards (`--card`) | `#FFFFFF` | `#1B1C20` |
| Popovers | `#FFFFFF` | `#222328` |
| Shell (sidebar, top bar, page bands, `--surface`) | `#ECEAE5` | `#121317` |
| Muted surface (hover, secondary, badges) | `#ECEAE5` | `#24252A` |
| Borders | `#E2E0DA` | `#2A2B30` |
| Input borders | `#D5D2CB` | `#36373D` |
| Text on a crimson fill | `#FFFFFF` (6.9:1 / 5.1:1) | `#FFFFFF` |
| Filled tiles with white text (avatars, `--brand-strong`) | `#A10F28` | `#A10F28` |

`#B3122E` is the canonical brand colour and the default of `BRAND_COLOR` / `NEXT_PUBLIC_BRAND_COLOR`. With the default the web app uses the per-mode steps above (`#B3122E` on light, `#D3263F` and `#F2687B` on dark). A custom colour (white label) replaces the accent in both modes, for buttons, links, focus and chart slot 1, with black or white text on its fills, whichever has more contrast.

### Status (colour means state)

Each with a lighter step for dots and fills on dark, and a deep step for text on light. Always shown with an icon or a label, never colour alone. The error is a **vermilion** (OKLCH hue about 37°), not a crimson (about 21°), and lighter, so an error never reads as the accent: the pair is ΔE 13 apart under normal vision and 13 under deuteranopia (OKLab ×100), and the warning is a golden amber apart from both.

| State | Default | Light step | Deep step (text on light) |
|---|---|---|---|
| Success | `#1F8A5B` | `#34A873` | `#146B45` |
| Warning | `#C29100` | `#E8B83A` | `#8F6A00` |
| Error | `#DC4E22` | `#F5713A` | `#A8381A` |
| Info | `#5560D0` | `#7B84DC` | `#3F4AA8` |

Destructive buttons use the error's deep vermilion (`#B93A14` light, `#C4401A` dark, both at least 5:1 with white), never the crimson.

### Chart series

Slot 1 is the crimson, so single-series and headline charts (sparklines, bar lists) carry the brand. Slots 2-5 are muted categorical hues that sit quietly next to it: blue, amber, teal and plum. Validated with a palette checker (lightness band, chroma floor, CVD separation under protanopia and deuteranopia, normal-vision floor, contrast) on `#FFFFFF` (light) and on `#1B1C20` and `#0E0F12` (dark), **all pairs** of the five: worst CVD ΔE 10.7 light / 8.0 dark, worst normal-vision ΔE 16.3 light / 15.3 dark, every slot at least 3:1 on its surface. Entity colours (deployments, GPUs, nodes) start at slot 2; a fifth entity and beyond fold into the muted "Other" colour.

| Slot | Hue | Light | Dark |
|---|---|---|---|
| 1 | Crimson (the accent) | `#B3122E` | `#D3263F` |
| 2 | Blue | `#3B6EB9` | `#5286D3` |
| 3 | Amber | `#C38323` | `#B8833B` |
| 4 | Teal | `#00A291` | `#20A594` |
| 5 | Plum | `#753A7A` | `#935799` |

The tokens live in `apps/web/app/globals.css` (`--brand`, `--brand-text`, `--brand-strong`, `--primary`, `--ring`, `--chart-1` to `--chart-5`, the neutrals) and `apps/web/utils/brand-defaults.ts`; the desktop connect screen mirrors them in `apps/desktop/src/styles.css`, the website in `website/assets/css/site.css` (`--accent`, `--accent-text`, `--accent-soft`, `--accent-line`), and both follow the system appearance.

## Type

- **Satoshi** (Indian Type Foundry, [Fontshare](https://www.fontshare.com/fonts/satoshi), ITF Free Font License) for the interface and the wordmark. Bold for the product name and headings; Satoshi has no 600, so "semibold" maps to Bold. The web app and the desktop connect screen load it from the Fontshare CDN and fall back to the system font stack offline. The license allows commercial use and wordmarks made from the font, but not redistributing the font files, so **never commit Satoshi files**; the brand SVGs carry the wordmark as outlines.
- **JetBrains Mono** (SIL Open Font License) only for identifiers, addresses, commands, versions and eyebrow labels (uppercase, wide tracking). The web app bundles it at build time through `next/font`.

## Files

Everything in `assets/brand/` is generated by `build.py` (the README header and the ecosystem diagram by `readme_art.py`, see below); edit the scripts, not the files. SVG is the master format and the only one committed here, apart from `social-preview.png`: a raster exists only where a platform requires one (GitHub's social preview and the Open Graph card, favicon / apple-touch / PWA icons, the Tauri icon set).

| File | Use |
|---|---|
| `mark.svg` | The crimson tile with the large glyph. 128 px and up. |
| `mark-small.svg` | The tile for 24-96 px (pixel-exact at 32). Also `favicon.svg`. |
| `mark-16.svg` | The tile drawn for 16 px. |
| `mark-light.svg` | Light tile, crimson glyph. |
| `glyph-on-light.svg`, `glyph-on-dark.svg` | The large glyph in ink / paper, no tile, tight box. |
| `glyph-mono.svg` | The large glyph in `currentColor`. |
| `glyph-small.svg` | The medium glyph in `currentColor` (24-96 px). |
| `glyph-16.svg` | The 16 px drawing in `currentColor`. |
| `glyph-adaptive.svg` | Square medium glyph that switches ink / paper with `prefers-color-scheme`, for places that need the bare glyph. |
| `glyph-tray.svg` | Black glyph with plain fan openings, source of the macOS tray templates. |
| `wordmark-light.svg`, `wordmark-dark.svg` | "GPU Deck" in Satoshi Bold, outlined. |
| `lockup-light.svg`, `lockup-dark.svg` | Horizontal lockup (crimson tile + name), transparent, for light / dark pages. The website header and footer; the README header and the ecosystem diagram take their drawing from these. |
| `lockup-stacked-light.svg`, `lockup-stacked-dark.svg` | Stacked lockup (large tile over the name). |
| `readme-header-light.svg`, `readme-header-dark.svg` | The README header: the lockup, the capabilities row and a row of GPU cells on its own card. Built by `readme_art.py`. |
| `ecosystem-light.svg`, `ecosystem-dark.svg` | What GPU Deck supports, with GPU Deck in the middle: how you drive it, the APIs and model types it serves, the engines, accelerators and places it runs on. Built by `readme_art.py`. |
| `app-icon-1024.svg` | The desktop app icon on the macOS grid. `make-icons.py` renders it to `src-tauri/icons/app-icon.png` (git-ignored), the source for `tauri icon`. |
| `app-icon-light-1024.svg` | Light app icon variant. |
| `social-preview.svg`, `social-preview.png` | 1280 × 640 GitHub social preview (the PNG is the file to upload; GitHub needs a raster) and Open Graph image (`website/sync-screenshots.sh` copies the PNG to `website/assets/brand/og-image.png` at build time, git-ignored), laid out like Container Deck's: the tile, name, tagline and a crimson line on the warm surface, and a GPU Deck fleet window cropped by the right edge. |

The build also writes:

- `apps/web/public/`: `favicon.ico`, `favicon.svg` (the tile), `apple-touch-icon.png`, `logo.svg` / `logo-dark.svg` (lockups), `brand/gpudeck-mark.svg` and the PWA icons `brand/gpudeck-icon-{192,512,maskable-512}.png` (listed in `/manifest.webmanifest`).
- `apps/web/components/shared/gpu-deck-glyph.ts`: the glyph's path data. The web app draws the mark inline from it (`components/shared/gpu-deck-mark.tsx`: `GpuDeckTile`, the crimson tile at the right optical size, used by the sidebar lockup and the sign-in page; `GpuDeckMark` for the bare glyph in `currentColor`), so it stays sharp and needs no request.
- `website/` (when present): the favicons and PWA icons at the site root and `assets/brand/{lockup-*,mark.svg,glyph-mono.svg}`. Each app is deployed separately, so `apps/web/public` and `website/` each keep their own copy of the few rasters a browser needs (`favicon.ico`, `apple-touch-icon.png`, `icon-192/512.png`, `icon-maskable-512.png`). The tile PNGs they are copied from are built into `assets/brand/.build/png/` (git-ignored): `favicon.ico` (16/32/48), `apple-touch-icon.png` (180, full bleed), `icon-maskable-512.png` (glyph inside the 80 % safe circle).

Desktop icons come from these files: `pnpm --filter desktop icons` runs `apps/desktop/scripts/make-icons.py` and then `tauri icon`:

- the app icon set (`icon.icns`, `icon.ico`, PNGs) from `app-icon-1024.svg`;
- **macOS menu-bar icons** `icons/tray/tray-{idle,ok,degraded,down}.png`: template images (black + alpha, 27 × 18 pt @2x) that macOS tints for light and dark menu bars and wallpapers. Template images carry no colour by platform rule, so they stay monochrome and the state is in the badge's shape: none (idle), solid dot (ok), ring (degraded), dot with a bar cut out (down);
- **Windows / Linux tray icons** `tray-*-color.png`: the crimson tile with a dot in the status colours, set in a transparent gap so it reads on any task bar;
- the connect-screen mark `public/gpudeck-mark.svg` (`mark-small.svg`).

## Identifiers

| What | Value |
|---|---|
| Repository | `github.com/sur950/gpu-deck` |
| Website | `https://sur950.github.io/gpu-deck/` |
| Desktop app id | `io.github.sur950.gpudeck` |
| Agent launchd label | `io.github.sur950.gpudeck-agent` (`.dev` for the per-user development agent) |
| Agent container labels | `io.github.sur950.gpudeck.ssh_instance`, `io.github.sur950.gpudeck.node_id` |
| Kubernetes node labels | `gpudeck.sur950.github.io/gpu` (agent DaemonSet), `gpudeck.sur950.github.io/gpu-node` (Helm) |
| JSON Schema ids | `https://sur950.github.io/gpu-deck/schemas/{rules,agent-protocol}/v1/…` |
| Images | `ghcr.io/sur950/gpu-deck-api`, `gpu-deck-web`, `gpu-deck-agent` |
| Terraform | module `github.com/sur950/gpu-deck/apps/terraform-provider`, address `registry.terraform.io/sur950/gpudeck` |
| systemd unit | `gpudeck-agent.service` |

## White label

Every brand value is configurable: `APP_NAME`, `APP_BYLINE`, `BRAND_COLOR`, `LOGO_URL`, `FAVICON_URL` for the backend (`GET /api/v1/branding`) and their `NEXT_PUBLIC_*` mirrors for the web build (see `libs/contracts/env.md`). A custom logo replaces the lockup (and the tile on the sign-in page) and hides the credit line in the account menu; a custom colour replaces the crimson in both modes (buttons, links, focus, active nav, chart slot 1).

## README header and ecosystem diagram

GitHub picks a `<picture>` source from the browser's colour scheme, which is not always the GitHub theme: a reader with a dark GitHub theme on a light system gets the light image on a dark page. A transparent lockup then shows the tile and an unreadable name. So every image in the README carries its own background card (warm paper on light, near-black on dark): either variant reads on either page, and the pair only decides which one blends in. Check a change by rendering both variants on `#ffffff` and `#0d1117` at the README width (about 830 px).

The ecosystem diagram lists only what GPU Deck supports today (from [`docs/hardware/`](hardware/README.md) and the engine catalog) and marks with a green dot only what [`docs/TESTING.md`](TESTING.md) says ran on real hardware, and with a half-filled amber dot what ran with known gaps (partial). Names are plain text: no third-party logos. The same lists are written into `README.md` (the image's alt text and a table under it) and `website/index.html` (real text, laid out by `site.css`), each between `<!-- ecosystem:begin -->` and `<!-- ecosystem:end -->`, so edit `ROWS` in [`assets/brand/readme_art.py`](../assets/brand/readme_art.py) and run it instead of editing those blocks:

```bash
python3 -m venv /tmp/brand-venv && /tmp/brand-venv/bin/pip install -r scripts/requirements-art.txt
/tmp/brand-venv/bin/python assets/brand/readme_art.py           # write the SVGs and both text blocks
/tmp/brand-venv/bin/python assets/brand/readme_art.py --check   # exit 1 when anything is stale
```

Its text is outlined from the website's own fonts (Figtree and JetBrains Mono, SIL OFL, in `website/assets/fonts/`), and the wordmark comes from the lockups, so it needs neither Satoshi nor any font installed.

## Documentation diagrams

The diagrams in the docs (system overview, node join, request path, safe swap, rule loop, GPU-minute allocation, support at a glance, preparing a GPU machine) are drawn by [`assets/brand/doc_art.py`](../assets/brand/doc_art.py) into `docs/diagrams/<name>-light.svg` and `<name>-dark.svg`, with the same recipe as the README art: each on its own warm card with a faint dot grid, text outlined from Figtree and JetBrains Mono, GPU Deck itself as the dark slab with the crimson top edge, crimson for the main path (the request, the agent's outbound connection, a fired rule), green and amber only for status, the chart slots for data, and neutral wires for everything else. Edit the script, never the SVGs:

```bash
/tmp/brand-venv/bin/python assets/brand/doc_art.py           # write every diagram
/tmp/brand-venv/bin/python assets/brand/doc_art.py --check   # exit 1 when anything is stale
```

Their status marks follow [`docs/TESTING.md`](TESTING.md) and their support marks [`docs/hardware/README.md`](hardware/README.md), like the ecosystem diagram. How to embed them is in [DEVELOPMENT.md](DEVELOPMENT.md#writing-docs-that-read-well-on-github-and-on-the-site).

## Rebuilding the assets

```bash
# Satoshi from https://www.fontshare.com/fonts/satoshi, JetBrains Mono from GitHub (not committed)
python3 assets/brand/build.py --satoshi ~/Downloads/Satoshi_Complete/Fonts/OTF \
    --jetbrains-mono ~/Downloads/JetBrainsMono-Medium.ttf
pnpm --filter desktop icons
```

Needs Python 3.10+ with Pillow, NumPy and fontTools, and `rsvg-convert` (librsvg). Large PNGs are rendered without gradient banding (the tile gradients are re-graded and dithered after rendering).

## Credit

The project is made by Suresh Konakanchi ([suresh-konakanchi.in](https://suresh-konakanchi.in)). The credit line "Made by Suresh Konakanchi", linking that site, appears in the website footer, the web app's account menu and the desktop About window (credits), and in the Helm chart maintainers, never as a byline under the product name.
