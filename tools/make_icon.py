"""Export Snap Ninja's Windows icons from the project logo.

Development helper: install Pillow to run it. Runtime does not require Pillow.
"""
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent

if __name__ == '__main__':
    source = Image.open(ROOT / 'static' / 'snap-ninja-source.png').convert('RGBA')
    source.resize((192, 192), Image.Resampling.LANCZOS).save(ROOT / 'static' / 'snap-ninja.png')
    icon = source.resize((256, 256), Image.Resampling.LANCZOS)
    sizes = [(n, n) for n in (16, 24, 32, 48, 64, 128, 256)]
    for name in ('snap-ninja.ico', 'snap-ninja-v1.ico'):
        icon.save(ROOT / 'static' / name, sizes=sizes)
    print('Updated Snap Ninja favicon and Windows icons.')
