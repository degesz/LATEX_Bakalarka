#!/usr/bin/env bash
set -euo pipefail

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

for svg in *.svg; do
  [ -f "$svg" ] || continue
  out="${svg%.svg}.pdf"
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

  echo "Converting $svg -> $out"
  google-chrome --headless --disable-gpu --no-sandbox \
    --print-to-pdf-no-header \
    --print-to-pdf="$tmp_pdf" \
    "file://$TMPDIR/page.html" 2>/dev/null

  # extract first page only to drop any trailing blank page
  pdfseparate -f 1 -l 1 "$tmp_pdf" "$out"
done

echo "Done."
