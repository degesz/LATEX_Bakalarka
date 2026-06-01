#!/usr/bin/env bash
set -euo pipefail

SCALE="${1:-1.5}"
OUTDIR="enlarged_pdfs"

if ! command -v python3 &>/dev/null; then
  echo "Error: python3 required" >&2
  exit 1
fi

cd "$(dirname "$0")"
mkdir -p "$OUTDIR"

echo "Scaling SVG text font-size by factor $SCALE..."

python3 << PYEOF
import os, re, sys

svg_dir = "."
outdir = "$OUTDIR"
scale = float("$SCALE")

for fname in sorted(os.listdir(svg_dir)):
    if not fname.endswith(".svg"):
        continue
    path = os.path.join(svg_dir, fname)
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()

    # replace font-size="..." in any element
    def scale_fontsize(m):
        val = float(m.group(1))
        newval = round(val * scale, 4)
        return f'font-size="{newval}"'

    new_content = re.sub(r'font-size="([\d.]+)"', scale_fontsize, content)

    out_path = os.path.join(outdir, fname)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(new_content)
    print(f"  {fname} -> {out_path}")
PYEOF

echo ""
echo "Converting enlarged SVGs to PDFs..."

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

for svg in "$OUTDIR"/*.svg; do
  [ -f "$svg" ] || continue
  base=$(basename "$svg" .svg)
  out_pdf="$OUTDIR/${base}.pdf"
  abs_svg="$(realpath "$svg")"
  tmp_pdf="$TMPDIR/out.pdf"

  cat > "$TMPDIR/page.html" <<EOF
<!DOCTYPE html>
<html>
<head>
<style>
  @page { size: landscape; margin: 0; }
  body { margin: 0; }
  svg, img, object { width: 100vw; height: 100vh; }
</style>
</head>
<body>
<object data="file://$abs_svg" type="image/svg+xml"></object>
</body>
</html>
EOF

  echo "  $base.pdf"
  google-chrome --headless --disable-gpu --no-sandbox \
    --print-to-pdf-no-header \
    --print-to-pdf="$tmp_pdf" \
    "file://$TMPDIR/page.html" 2>/dev/null

  pdfseparate -f 1 -l 1 "$tmp_pdf" "$out_pdf" 2>/dev/null || cp "$tmp_pdf" "$out_pdf"
done

echo ""
echo "Done. Files saved in: $OUTDIR/"
ls -1 "$OUTDIR"/*.pdf 2>/dev/null | sed 's/^/  /'
