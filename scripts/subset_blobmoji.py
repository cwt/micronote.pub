"""Utility script to subset BlobMoji2 COLRv1 TTF font into optimized WOFF2 chunks.

This script takes a full Noto-COLRv1.ttf from BlobMoji2 (DavidBerdik/blobmoji2)
and produces 12 Unicode-range chunked .woff2 files for micronote.pub,
dramatically reducing latency by enabling browsers to lazy-load font subsets.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Each entry is a tuple of:
# 1. Chunk filename slug (BlobMoji-<slug>.woff2)
# 2. CSS unicode-range string
# 3. Subsetting unicodes (including U+fe0f and U+200d so variation selectors and ZWJ ligatures are kept)
# 4. Description of characters covered
CHUNK_DEFINITIONS: list[tuple[str, str, str, str]] = [
    (
        "0000-329f",
        "U+0000-329f",
        "U+0000-329f,U+fe0f,U+200d",
        "Basic Latin, symbols, dingbats, arrows, enclosed alphanumerics",
    ),
    (
        "1f000-1f0ff",
        "U+1f000-1f0ff",
        "U+1f000-1f0ff,U+fe0f,U+200d",
        "Mahjong tiles, domino tiles, playing cards",
    ),
    (
        "1f100-1f1ff",
        "U+1f100-1f1ff",
        "U+1f100-1f1ff,U+fe0f,U+200d",
        "Regional indicator symbols (national flags) & enclosed supplement",
    ),
    (
        "1f200-1f2ff",
        "U+1f200-1f2ff",
        "U+1f200-1f2ff,U+fe0f,U+200d",
        "Enclosed ideographic supplement (CJK symbols)",
    ),
    (
        "1f300-1f3ff",
        "U+1f300-1f3ff",
        "U+1f300-1f3ff,U+fe0f,U+200d",
        "Miscellaneous symbols & pictographs (weather, food, plants)",
    ),
    (
        "1f400-1f4ff",
        "U+1f400-1f4ff",
        "U+1f400-1f4ff,U+fe0f,U+200d",
        "Pictographs & people (objects, animals, clothing, tech)",
    ),
    (
        "1f500-1f5ff",
        "U+1f500-1f5ff",
        "U+1f500-1f5ff,U+fe0f,U+200d",
        "Miscellaneous symbols, UI symbols, clock faces, tools",
    ),
    (
        "1f600-1f6ff",
        "U+1f600-1f6ff",
        "U+1f600-1f6ff,U+fe0f,U+200d",
        "Emoticons, smileys, transport & map symbols",
    ),
    (
        "1f700-1f7ff",
        "U+1f700-1f7ff",
        "U+1f700-1f7ff,U+fe0f,U+200d",
        "Geometric shapes extended (colored circles, squares, math)",
    ),
    (
        "1f900-1f9ff",
        "U+1f900-1f9ff",
        "U+1f900-1f9ff,U+fe0f,U+200d",
        "Supplemental symbols & pictographs (gestures, food, animals)",
    ),
    (
        "1fa00-1faff",
        "U+1fa00-1faff",
        "U+1fa00-1faff,U+fe0f,U+200d",
        "Symbols and pictographs extended-A (Unicode 13-17 additions)",
    ),
    (
        "e0000-fffff",
        "U+e0000-fffff",
        "U+e0000-fffff,U+fe0f,U+200d",
        "Tags (subdivision flag sequences) & private use area",
    ),
]


def format_bytes(num_bytes: int) -> str:
    """Format byte count into a human-friendly string."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    num_kb = num_bytes / 1024.0
    if num_kb < 1024:
        return f"{num_kb:.1f} KB"
    num_mb = num_kb / 1024.0
    return f"{num_mb:.2f} MB"


def check_prerequisites() -> str:
    """Verify that pyftsubset and brotli are installed and find pyftsubset path."""
    pyftsubset_path = shutil.which("pyftsubset")
    if not pyftsubset_path:
        # Check current python environment's bin directory
        candidate = Path(sys.executable).parent / "pyftsubset"
        if candidate.is_file():
            pyftsubset_path = str(candidate)

    if not pyftsubset_path:
        sys.exit("ERROR: 'pyftsubset' command not found. Please install fonttools:\n    pip install fonttools brotli")

    try:
        import brotli  # noqa: F401
    except ImportError:
        sys.exit("ERROR: 'brotli' Python library not found. Required for WOFF2 compression:\n    pip install brotli")

    return pyftsubset_path


def parse_arguments() -> argparse.Namespace:
    """Parse command line options."""
    parser = argparse.ArgumentParser(description="Subset BlobMoji2 Noto-COLRv1.ttf into chunked WOFF2 files.")
    parser.add_argument(
        "input_font",
        type=Path,
        help="Path to source Noto-COLRv1.ttf (downloaded from BlobMoji2 releases)",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=Path("src/micronote/static/fonts/BlobMoji"),
        help="Target directory for generated .woff2 files (default: src/micronote/static/fonts/BlobMoji)",
    )
    return parser.parse_args()


def subset_chunk(
    pyftsubset_path: str,
    source_font: Path,
    output_file: Path,
    unicodes: str,
) -> int:
    """Run pyftsubset to create one chunk and return output file size."""
    cmd = [
        pyftsubset_path,
        str(source_font),
        f"--unicodes={unicodes}",
        "--flavor=woff2",
        f"--output-file={output_file}",
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    return output_file.stat().st_size


def main() -> None:
    """Main execution entrypoint."""
    args = parse_arguments()

    if not args.input_font.is_file():
        sys.exit(f"ERROR: Input font file not found: {args.input_font}")

    pyftsubset_path = check_prerequisites()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    source_size = args.input_font.stat().st_size
    print(f"Source TTF: {args.input_font} ({format_bytes(source_size)})")
    print(f"Output directory: {args.output_dir}\n")

    total_out_size = 0
    results: list[tuple[str, int, str]] = []

    for slug, css_range, subset_unicodes, desc in CHUNK_DEFINITIONS:
        output_file = args.output_dir / f"BlobMoji-{slug}.woff2"
        print(f"Subsetting chunk: {slug} ({css_range})...")
        file_size = subset_chunk(
            pyftsubset_path=pyftsubset_path,
            source_font=args.input_font,
            output_file=output_file,
            unicodes=subset_unicodes,
        )
        total_out_size += file_size
        results.append((output_file.name, file_size, desc))

    print("\n" + "=" * 70)
    print(f"{'Filename':<30} {'Size':<10} {'Description'}")
    print("-" * 70)
    for filename, size, desc in results:
        print(f"{filename:<30} {format_bytes(size):<10} {desc}")
    print("=" * 70)
    print(f"Total chunked size: {format_bytes(total_out_size)}")
    reduction_pct = (1.0 - (total_out_size / source_size)) * 100.0
    print(f"Overall reduction compared to un-subsetted TTF: {reduction_pct:.1f}%\n")


if __name__ == "__main__":
    main()
