#!/usr/bin/env python3
import sys
import cairosvg
from pathlib import Path

def is_svg(path):
    with open(path, 'rb') as f:
        head = f.read(1024)
    return b'<svg' in head

def main(dir_path='.'):
    src = Path(dir_path).resolve()
    for p in sorted(src.iterdir()):
        if p.suffix.lower() in ('.pdf', '.py') or not p.is_file():
            continue
        if not is_svg(p):
            continue
        out = p.with_suffix('.pdf')
        print(f'{p.name} -> {out.name}')
        cairosvg.svg2pdf(url=str(p), write_to=str(out))

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '.')
