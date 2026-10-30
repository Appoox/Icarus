"""
Builds the fonts in this folder (see README.md): static, trimmed copies of
Google Fonts' variable Noto Serif Malayalam, Anek Malayalam, Noto Serif
Devanagari and Noto Serif Tamil, for WeasyPrint.

    python -I build_pdf_fonts.py <folder with the downloads> <output folder>

The download folder holds the variable fonts from github.com/google/fonts,
saved as <family folder>__<file name>, e.g.
notoserifmalayalam__NotoSerifMalayalam[wght].ttf.  Not used by the site at
run time.
"""
import sys
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools import subset

src, out = Path(sys.argv[1]), Path(sys.argv[2])
JOBS = [
    ('notoserifmalayalam__NotoSerifMalayalam[wght].ttf', 'NotoSerifMalayalam', {'Regular': {'wght': 400}, 'Bold': {'wght': 700}}),
    ('anekmalayalam__AnekMalayalam[wdth,wght].ttf', 'AnekMalayalam', {'Regular': {'wdth': 100, 'wght': 400}, 'Bold': {'wdth': 100, 'wght': 700}}),
    ('notoserifdevanagari__NotoSerifDevanagari[wdth,wght].ttf', 'NotoSerifDevanagari', {'Regular': {'wdth': 100, 'wght': 400}, 'Bold': {'wdth': 100, 'wght': 700}}),
    ('notoseriftamil__NotoSerifTamil[wdth,wght].ttf', 'NotoSerifTamil', {'Regular': {'wdth': 100, 'wght': 400}, 'Bold': {'wdth': 100, 'wght': 700}}),
]
UNICODES = (
    list(range(0x0D00, 0x0D80))        # Malayalam
    + list(range(0x0900, 0x0980))      # Devanagari (Hindi)
    + list(range(0x0B80, 0x0C00))      # Tamil
    + list(range(0xA8E0, 0xA900))      # Devanagari Extended
    + list(range(0x0020, 0x007F))      # Basic Latin
    + list(range(0x00A0, 0x0100))      # Latin-1 (·, ©, «»…)
    + list(range(0x2000, 0x2070))      # General Punctuation (– — ‘ ’ “ ” • … ZWNJ ZWJ)
    + [0x20B9, 0x2192, 0x2190, 0x25CC, 0x25A0, 0x2122]  # ₹ → ← ◌ ■ ™
)
for filename, family, styles in JOBS:
    for style, axes in styles.items():
        font = TTFont(src / filename)
        font = instantiateVariableFont(font, axes, updateFontNames=True)
        options = subset.Options()
        options.layout_features = ['*']          # keep every joining rule (akhn, pres, …)
        options.name_IDs = ['*']
        options.name_languages = ['*']
        options.notdef_outline = True
        options.hinting = False                   # PDFs don't use TrueType hinting
        options.desubroutinize = True
        sub = subset.Subsetter(options)
        sub.populate(unicodes=UNICODES)
        sub.subset(font)
        target = out / f'{family}-{style}.ttf'
        font.save(target)
        print(target.name, target.stat().st_size, 'bytes', 'weight', font['OS/2'].usWeightClass)
