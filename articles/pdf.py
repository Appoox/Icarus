"""
Article PDFs (Article.serve_pdf), rendered with WeasyPrint.

Fonts
    WeasyPrint can only draw a letter if some font it can find has it.
    The server's Docker image (Debian slim) has no Malayalam, Hindi or
    Tamil font at all, so every letter in those scripts came out as an
    empty box.  The PDF therefore brings its own fonts from
    articles/pdf_fonts/ (see the README there) as @font-face rules, and
    the PDF template names them in its font-family lists.

Files from this site
    Images in an article are on this site (/media/…).  They are read
    straight from disk instead of being fetched back over HTTP from the
    site's own public address, which is slower and fails wherever a
    server cannot reach its own domain.  file: URLs are only allowed
    inside the fonts and media folders, so article text cannot point the
    renderer at other files on the server.
"""
from pathlib import Path
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from weasyprint import CSS, HTML
from weasyprint.text.fonts import FontConfiguration
from weasyprint.urls import URLFetcher

FONT_DIR = Path(__file__).resolve().parent / 'pdf_fonts'

# (family, weight, file): the names used in templates/articles/article_pdf.html
PDF_FONTS = [
    ('Noto Serif Malayalam', 400, 'NotoSerifMalayalam-Regular.ttf'),
    ('Noto Serif Malayalam', 700, 'NotoSerifMalayalam-Bold.ttf'),
    ('Anek Malayalam', 400, 'AnekMalayalam-Regular.ttf'),
    ('Anek Malayalam', 700, 'AnekMalayalam-Bold.ttf'),
    ('Noto Serif Devanagari', 400, 'NotoSerifDevanagari-Regular.ttf'),
    ('Noto Serif Devanagari', 700, 'NotoSerifDevanagari-Bold.ttf'),
    ('Noto Serif Tamil', 400, 'NotoSerifTamil-Regular.ttf'),
    ('Noto Serif Tamil', 700, 'NotoSerifTamil-Bold.ttf'),
]


def font_face_css():
    """@font-face rules for the bundled fonts."""
    return '\n'.join(
        f"@font-face {{ font-family: '{family}'; font-weight: {weight}; "
        f"font-style: normal; src: url('{(FONT_DIR / filename).as_uri()}'); }}"
        for family, weight, filename in PDF_FONTS
    )


class LocalFileFetcher(URLFetcher):
    """
    Serves this site's /media/ and /static/ URLs from disk, and keeps
    file: URLs to the font and media folders.  Anything else (an image
    embedded from another site) is fetched as WeasyPrint normally would.
    """

    def __init__(self, site_root, **kwargs):
        super().__init__(**kwargs)
        self.site_root = urlsplit(site_root)
        self.allowed_dirs = [FONT_DIR.resolve()]
        if settings.MEDIA_ROOT:
            self.allowed_dirs.append(Path(settings.MEDIA_ROOT).resolve())
        if settings.STATIC_ROOT:
            self.allowed_dirs.append(Path(settings.STATIC_ROOT).resolve())

    def _inside(self, path, root):
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _local_file(self, url_path):
        """The file behind a /media/… or /static/… path on this site, if any."""
        for prefix, root in ((settings.MEDIA_URL, settings.MEDIA_ROOT),
                             (settings.STATIC_URL, settings.STATIC_ROOT)):
            if not prefix or not prefix.startswith('/') or not url_path.startswith(prefix):
                continue
            relative = url_path[len(prefix):]
            if root:
                base = Path(root).resolve()
                candidate = (base / relative).resolve()
                if self._inside(candidate, base) and candidate.is_file():
                    return candidate
            if prefix == settings.STATIC_URL:
                found = finders.find(relative)
                if found:
                    return Path(found).resolve()
        return None

    def fetch(self, url, headers=None):
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme in ('http', 'https') and parts.netloc == self.site_root.netloc:
            local = self._local_file(unquote(parts.path))
            if local is not None:
                return super().fetch(local.as_uri(), headers)
        if scheme == 'file':
            path = Path(unquote(parts.path)).resolve()
            if not any(self._inside(path, root) for root in self.allowed_dirs):
                raise ValueError(f'File not allowed in a PDF: {url}')
        return super().fetch(url, headers)


def render_article_pdf(article, request, context):
    """The PDF bytes for `article`, from templates/articles/article_pdf.html."""
    html_string = render_to_string('articles/article_pdf.html', context)
    site_root = request.build_absolute_uri('/')
    fetcher = LocalFileFetcher(site_root)
    font_config = FontConfiguration()
    fonts = CSS(string=font_face_css(), font_config=font_config, url_fetcher=fetcher)
    document = HTML(string=html_string, base_url=site_root, url_fetcher=fetcher)
    return document.write_pdf(stylesheets=[fonts], font_config=font_config)
