#!/bin/sh
# Rebuild the standalone mockup page from src/ (Tailwind classes scanned from src/).
set -e
cd "$(dirname "$0")"
npx tailwindcss -i src/mockup.css -o src/mockup.built.css --minify
python3 -c "
import pathlib
src = pathlib.Path('src/ai-passes.html').read_text()
css = pathlib.Path('src/mockup.built.css').read_text()
pathlib.Path('ai-passes.html').write_text(src.replace('/*INLINE_CSS*/', css))
print('wrote ai-passes.html')
"
