---
type: runbook
title: BlobMoji Font Subsetting and Upgrade Guide
description: Operational runbook and technical reference for chunking BlobMoji2 COLRv1 fonts into optimized WOFF2 web font subsets.
status: stable
sources:
  - https://github.com/DavidBerdik/blobmoji2
  - src/micronote/static/css/BlobMoji.css
  - src/micronote/static/fonts/BlobMoji/
  - scripts/subset_blobmoji.py
verified: machine-confirmed
stale_after: 2027-01-01T00:00:00Z
tags:
  - fonts
  - emoji
  - blobmoji
  - web-performance
  - runbook
timestamp: 2026-09-16T20:50:00Z
---

# BlobMoji Font Subsetting and Upgrade Guide

This guide documents the rationale, technical architecture, and operational procedure for chunking and updating the [BlobMoji2](https://github.com/DavidBerdik/blobmoji2) font used in `micronote.pub`.

---

## 1. Context & Motivation (The "Why")

### 1.1. Why BlobMoji?
The classic Google "blob" emoji design is an intentional aesthetic choice for `micronote.pub`. It conveys a friendly, warm tone and provides a cohesive visual experience across all operating systems and browsers without relying on platform-specific vendor fonts (Apple, Microsoft, or Samsung emojis).

### 1.2. Evolution from BlobMoji to BlobMoji2
- **Original BlobMoji (`C1710/blobmoji`)**: The upstream repository was archived in 2022 after Unicode 13/14. It packaged emoji as bitmap strikes (CBDT/CBLC tables embedded in TTF/WOFF). The un-split font file was over 6 MB, and bitmaps lacked sharpness on modern retina/high-DPI screens.
- **BlobMoji2 (`DavidBerdik/blobmoji2`)**: Actively maintained fork updated through modern Unicode standards (Unicode 17+). It generates modern **OpenType COLRv1** vector fonts (`Noto-COLRv1.ttf`). COLRv1 fonts use vector paint graphs, supporting gradients, sharp scaling at arbitrary DPI, and dramatically smaller file sizes compared to embedded bitmaps.

### 1.3. Why Font Splitting (Chunking)?
The monolithic `Noto-COLRv1.ttf` file is approximately **4.4 MB**. Loading a 4.4 MB font on initial page load incurs unacceptable latency, especially for mobile clients or on metered network connections.

By partitioning the font into 12 targeted subsets based on Unicode blocks and using CSS `@font-face` with `unicode-range`:
1. **Lazy Loading on Demand**: The browser inspects the page content and fetches **only** the font files containing glyphs actually rendered on that page.
2. **Bandwidth Savings**: A reader browsing notes with standard smileys downloads only the 70 KB `BlobMoji-1f600-1f6ff.woff2` chunk rather than a 4.4 MB monolithic asset.
3. **Total Disk Footprint Reduction**: With modern WOFF2 compression and elimination of legacy bitmap tables, all 12 chunks combined consume only **1.6 MB** on disk (down from 3.1 MB for the legacy 10 `.woff` chunks).

### 1.4. Why WOFF2?
- **Universal Browser Support**: WOFF2 has been supported across all evergreen browsers (Chrome, Firefox, Safari, Edge) since 2016.
- **COLRv1 Compatibility**: COLRv1 vector fonts were introduced in Chrome 98 (2022), Safari 16 (2022), and Firefox 109 (2023). Every client engine capable of rendering COLRv1 already supports WOFF2.
- **Superior Compression**: WOFF2 utilizes the Brotli compression algorithm, yielding 25% to 35% smaller file sizes than legacy WOFF1.

---

## 2. Font Chunking Map

The font is partitioned into 12 distinct ranges:

| Chunk Filename | Unicode Range | Approx Size | Description & Emoji Examples |
| --- | --- | --- | --- |
| `BlobMoji-0000-329f.woff2` | `U+0000-329f` | 48 KB | Basic Latin symbols, dingbats, stars, copyright, arrows (✂️, ☀️, ❄️, ✈️, ❤️) |
| `BlobMoji-1f000-1f0ff.woff2` | `U+1f000-1f0ff` | 2.3 KB | Mahjong tiles, domino tiles, playing cards (🀄, 🃏) |
| `BlobMoji-1f100-1f1ff.woff2` | `U+1f100-1f1ff` | 694 KB | Regional indicators & national flags (🇦-🇿 ligatures) |
| `BlobMoji-1f200-1f2ff.woff2` | `U+1f200-1f2ff` | 3.6 KB | Enclosed ideographic supplement (CJK symbols: 🈁, 🈲, 🈚) |
| `BlobMoji-1f300-1f3ff.woff2` | `U+1f300-1f3ff` | 167 KB | Miscellaneous symbols & pictographs (weather, food, plants: 🌈, 🍎, 🍕) |
| `BlobMoji-1f400-1f4ff.woff2` | `U+1f400-1f4ff` | 172 KB | Pictographs & people (objects, animals, tech: 💩, 💻, 📱) |
| `BlobMoji-1f500-1f5ff.woff2` | `U+1f500-1f5ff` | 52 KB | Miscellaneous symbols & UI tools (clock faces, search, fire: 🔥, 🔍) |
| `BlobMoji-1f600-1f6ff.woff2` | `U+1f600-1f6ff` | 70 KB | Classic smileys & transport (😀, 😂, 🚀, 🚗) |
| `BlobMoji-1f700-1f7ff.woff2` | `U+1f700-1f7ff` | 856 B | Geometric shapes extended (colored circles/squares: 🟠, 🟡, 🟥, 🟦, 🟰) |
| `BlobMoji-1f900-1f9ff.woff2` | `U+1f900-1f9ff` | 210 KB | Supplemental symbols & pictographs (gestures, food, animals: 🥰, 🥺, 🥑) |
| `BlobMoji-1fa00-1faff.woff2` | `U+1fa00-1faff` | 89 KB | Symbols & pictographs extended-A (Unicode 13-17: 🫠, 🫡, 🫀, 🫂, 🩷, 🩵, 🩶) |
| `BlobMoji-e0000-fffff.woff2` | `U+e0000-fffff` | 26 KB | Tag characters for subdivision flags (🏴󠁧󠁢󠁳󠁣󠁴󠁿 Scotland, England, Wales) & PUA |

### Special OpenType Layout Handling:
- **Variation Selector 16 (`U+FE0F`)**: Appending `U+fe0f` to the subset list preserves OpenType `cmap` format 14 (Unicode Variation Sequences). This ensures that characters defaulting to text presentation (like `\u2600\ufe0f`) render as color emoji.
- **Zero-Width Joiner (`U+200D`)**: Retained so multi-part ZWJ sequences within a block (such as family or occupation combinations) maintain their OpenType `GSUB` substitution tables.
- **CSS Declaration**: In CSS `@font-face`, only the pure block range is specified (e.g., `unicode-range: U+1f600-1f6ff;`), preventing non-relevant chunks from being requested prematurely.

---

## 3. Step-by-Step Upgrade Procedure (The "How")

When a new version of BlobMoji2 is published:

### Step 1: Install Subsetting Tooling
Ensure `fonttools` and `brotli` are present in your local Python virtualenv:
```bash
pip install "fonttools>=4.50.0" "brotli>=1.1.0"
```

### Step 2: Download the New Upstream TTF
Go to the [BlobMoji2 Releases page](https://github.com/DavidBerdik/blobmoji2/releases) and download the COLRv1 TrueType font asset (`Noto-COLRv1.ttf`):
```bash
curl -fSL -o /tmp/Noto-COLRv1.ttf \
  https://github.com/DavidBerdik/blobmoji2/releases/download/<release-tag>/Noto-COLRv1.ttf
```

### Step 3: Run the Subsetting Script
Execute the project utility script [`scripts/subset_blobmoji.py`](../scripts/subset_blobmoji.py):
```bash
python3 scripts/subset_blobmoji.py /tmp/Noto-COLRv1.ttf
```
This script will:
1. Verify `pyftsubset` and `brotli` availability.
2. Slice the input TTF into 12 `.woff2` files under `src/micronote/static/fonts/BlobMoji/`.
3. Output a summary table comparing chunk sizes and calculating total compression savings.

### Step 4: Verify Unicode Range Coverage
If the upstream release introduced new Unicode emoji blocks (e.g., new blocks beyond `U+1FAFF`):
1. Inspect the new codepoints using `fonttools`:
   ```bash
   python3 -c "from fontTools.ttLib import TTFont; f = TTFont('/tmp/Noto-COLRv1.ttf'); print(sorted(f.getBestCmap().keys()))"
   ```
2. If new blocks exist, add them to `CHUNK_DEFINITIONS` in [`scripts/subset_blobmoji.py`](../scripts/subset_blobmoji.py) and re-run.
3. Add the corresponding `@font-face` blocks to [`src/micronote/static/css/BlobMoji.css`](../src/micronote/static/css/BlobMoji.css).

### Step 5: Update Footer Acknowledgment & Version
If applicable, check [`src/micronote/templates/layout.html`](../src/micronote/templates/layout.html) to confirm the upstream repository link is current:
```html
Also thanks to <a href="https://github.com/DavidBerdik/blobmoji2">BlobMoji</a>
for bringing back the cute yellow blob. 😇
```

### Step 6: Validate Quality Gates & Commit
Run tests, type-checking, and linting:
```bash
pytest
./scripts/type-check.sh
./scripts/lint-check-fix-format.sh
make lint
```

Stage and commit changes via Mercurial:
```bash
hg add src/micronote/static/fonts/BlobMoji/*.woff2
hg commit -m "feat(static): upgrade BlobMoji to blobmoji2 <release-tag>"
```

---

## 4. Verification & Testing

To verify font behavior in a running instance:
1. Start the development server:
   ```bash
   ./run.sh
   ```
2. Open developer tools in a browser (Network tab, filter by `Font`).
3. Navigate to a note containing smileys (`😀`) — observe that only `BlobMoji-1f600-1f6ff.woff2` (~70 KB) is loaded.
4. Navigate to a note containing modern emojis (`🫠`, `🫡`, `🩷`) — observe that `BlobMoji-1fa00-1faff.woff2` (~89 KB) is fetched.
5. Verify that flags (`🇹🇭`, `🇯🇵`) correctly resolve into national flag graphics via `BlobMoji-1f100-1f1ff.woff2`.
