#!/bin/bash
# Rebuild the images in media/ from the demo fixtures in examples/.
# Same pipeline as production: headless Chrome at the Kindle's 1236x1648, then sips
# to 8-bit gray. No network, no notes folder, nothing personal. macOS only (sips).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MEDIA="$ROOT/media"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
PY="${PYTHON:-python3}"
GRAY="/System/Library/ColorSync/Profiles/Generic Gray Gamma 2.2 Profile.icc"

shot() {  # url out.png width height scale
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor="$5" \
    --user-data-dir="$TMP/profile" --window-size="$3,$4" --virtual-time-budget=3000 \
    --allow-file-access-from-files --screenshot="$2" "$1" >/dev/null 2>&1
}
gray() { sips -m "$GRAY" "$1" --out "$2" >/dev/null; }

# Morning page: exactly the production render, with made-up inputs.
"$PY" "$ROOT/server/kindle.py" --demo "$ROOT/examples/demo-morning.json" > "$TMP/kindle.html"
shot "file://$TMP/kindle.html" "$TMP/kindle.png" 1236 1648 1
gray "$TMP/kindle.png" "$MEDIA/dashboard.png"

# Paper: build the demo edition, unpack it, and shoot pages at the Kindle's pixel size.
# KOReader lays text out at about twice CSS pixel density on this screen, so 618x824 at 2x.
(cd "$ROOT/server" && "$PY" paper.py build --demo "$ROOT/examples/demo-edition.json" >/dev/null)
EPUB="$(ls -t "$ROOT"/server/out/demo/*.epub | head -1)"
mkdir -p "$TMP/epub" && (cd "$TMP/epub" && unzip -q "$EPUB")
for page in front sports watch markets; do
  shot "file://$TMP/epub/OEBPS/$page.xhtml" "$TMP/$page.png" 618 824 2
  gray "$TMP/$page.png" "$MEDIA/paper-$page.png"
done

# The dashboard inside a drawn device frame, for the top of the README.
cat > "$TMP/frame.html" <<HTML
<!DOCTYPE html><html><head><meta charset="utf-8"><style>
html,body{margin:0;background:#e9e7e2;width:900px;height:1180px;display:flex;align-items:center;justify-content:center}
.dev{width:700px;height:1036px;background:#1d1d1f;border-radius:44px;padding:58px 52px 92px;box-sizing:border-box;
     box-shadow:0 30px 60px rgba(0,0,0,.25),inset 0 0 0 2px #333}
.scr{width:100%;height:100%;background:#f4f3ef;overflow:hidden;border-radius:4px;box-shadow:inset 0 0 6px rgba(0,0,0,.35)}
.scr img{width:100%;height:100%;display:block;filter:contrast(.94);mix-blend-mode:multiply}
</style></head><body><div class="dev"><div class="scr"><img src="file://$MEDIA/dashboard.png"></div>
</div></body></html>
HTML
shot "file://$TMP/frame.html" "$MEDIA/device.png" 900 1180 1
echo "media: $(ls "$MEDIA" | tr '\n' ' ')"
