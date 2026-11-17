# Fonts for article PDFs

`Article.serve_pdf` renders PDFs with WeasyPrint, which can only use fonts it
can find on the server. The Docker image (Debian slim) has no Malayalam,
Hindi or Tamil fonts, so without these files every letter in those scripts
came out as an empty box. `articles/pdf.py` loads them directly from this
folder, so PDFs look the same on any machine.

| File | Used for |
| --- | --- |
| `NotoSerifMalayalam-Regular.ttf`, `-Bold.ttf` | Malayalam (and English) text and headings |
| `AnekMalayalam-Regular.ttf`, `-Bold.ttf` | small labels: topic, byline, captions, footer |
| `NotoSerifDevanagari-Regular.ttf`, `-Bold.ttf` | Hindi versions of articles |
| `NotoSerifTamil-Regular.ttf`, `-Bold.ttf` | Tamil versions of articles |

The same families as the print edition design (Noto Serif Malayalam, Anek
Malayalam), plus Noto Serif for Hindi and Tamil.

## Licence

All four families are under the SIL Open Font License 1.1, with no Reserved
Font Names; the licence for each is in the matching `OFL-*.txt` file. They
may be bundled, modified and redistributed with this software.

## How these files were made

From the variable fonts in Google Fonts' repository
(`https://github.com/google/fonts`, folders `ofl/notoserifmalayalam`,
`ofl/anekmalayalam`, `ofl/notoserifdevanagari`, `ofl/notoseriftamil`):
fixed Regular (400) and Bold (700) weights at normal width, trimmed to
Malayalam, Devanagari, Tamil, Latin and punctuation, with every
letter-joining rule kept. `build_pdf_fonts.py` does this:

    python -I articles/pdf_fonts/build_pdf_fonts.py <folder with the downloads> <output folder>

It needs `fontTools` and `brotli` (`pip install fonttools brotli`), which the
site itself does not.
