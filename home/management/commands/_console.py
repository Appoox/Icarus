"""
Shared console helpers for the Icarus content export / import commands.

Nothing here touches the database — it is purely presentation:

    Console         verbosity-aware writer that also counts warnings/errors
    ProgressBar     dependency-free progress bar that degrades gracefully when
                    the stream is not a terminal (CI logs, `> file.log`, cron)
    human_bytes     1234567           → '1.2 MB'
    human_duration  93.4              → '1m 33s'

Modules whose name starts with '_' are ignored by Django's management-command
autodiscovery, so this file never shows up in `manage.py help`.
"""

import shutil
import time

# Extensions whose bytes are already compressed — re-deflating them in the ZIP
# burns CPU for ~0% gain, so they are stored verbatim instead.
PRECOMPRESSED_SUFFIXES = frozenset({
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif', '.heic',
    '.mp3', '.m4a', '.aac', '.ogg', '.opus',
    '.mp4', '.m4v', '.mov', '.webm', '.mkv',
    '.zip', '.gz', '.bz2', '.xz', '.7z', '.rar',
    '.woff', '.woff2',
})


def human_bytes(n) -> str:
    """1234567 → '1.2 MB'."""
    size = float(n or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if abs(size) < 1024:
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} TB'


def human_duration(seconds) -> str:
    """93.4 → '1m 33s'."""
    secs = max(float(seconds or 0), 0)
    if secs < 1:
        return f'{secs * 1000:.0f}ms'
    if secs < 60:
        return f'{secs:.1f}s'
    minutes, secs = divmod(int(secs), 60)
    if minutes < 60:
        return f'{minutes}m {secs:02d}s'
    hours, minutes = divmod(minutes, 60)
    return f'{hours}h {minutes:02d}m'


def _truncate(text, limit) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + '…'


def _is_tty(stream) -> bool:
    isatty = getattr(stream, 'isatty', None)
    if callable(isatty):
        try:
            return bool(isatty())
        except Exception:
            return False
    return False


def _emit(stream, msg, ending=''):
    """Write to a Django OutputWrapper *or* a plain stream, without a newline."""
    try:
        stream.write(msg, ending=ending)           # django OutputWrapper
    except TypeError:
        stream.write(msg + ending)                 # plain file object
    flush = getattr(stream, 'flush', None)
    if callable(flush):
        try:
            flush()
        except Exception:
            pass


class ProgressBar:
    """
    A single-line progress bar.

    On a terminal it redraws in place with a rate-limited `\\r`; when the output
    is redirected it prints one line on start and one on finish so log files
    stay readable instead of filling with escape noise.
    """

    def __init__(self, total, label, stream, enabled=True, width=26,
                 min_interval=0.08):
        self.total = max(int(total or 0), 0)
        self.label = label
        self.stream = stream
        self.width = width
        self.min_interval = min_interval
        self.n = 0
        self.enabled = bool(enabled) and self.total > 0
        self._tty = _is_tty(stream)
        self._started = time.monotonic()
        self._last_render = 0.0
        self._last_len = 0
        self._painted = False
        self._closed = False
        if self.enabled and not self._tty:
            _emit(self.stream, f'  {self.label}: {self.total} item(s)…', '\n')

    # ── drawing ──────────────────────────────────────────────────────────

    def _line(self, suffix=''):
        total = max(self.total, 1)
        frac = min(self.n / total, 1.0)
        filled = int(round(frac * self.width))
        bar = '█' * filled + '░' * (self.width - filled)
        eta = ''
        elapsed = time.monotonic() - self._started
        if 0 < frac < 1 and elapsed > 1.5:
            eta = f'  eta {human_duration(elapsed / frac - elapsed)}'
        text = (f'  {_truncate(self.label, 14):<14} [{bar}] '
                f'{self.n:>{len(str(self.total))}}/{self.total} '
                f'{frac * 100:3.0f}%{eta}')
        if suffix:
            text += f'  {_truncate(suffix, 30)}'
        # keep the whole thing on one physical line
        return text[: max(shutil.get_terminal_size((100, 24)).columns - 1, 40)]

    def _paint(self, suffix='', ending=''):
        """
        Draw the bar, padding to the previous line's width.

        Without the padding a shorter line leaves the tail of the longer one
        behind, so a short final slug renders as e.g. 'most-read-articlesaet'.
        """
        line = self._line(suffix)
        pad = ' ' * max(self._last_len - len(line), 0)
        self._last_len = len(line)
        _emit(self.stream, '\r' + line + pad, ending)
        self._painted = True

    def clear(self):
        """Wipe the in-place bar so other output can be written cleanly."""
        if self._tty and self._painted and not self._closed:
            _emit(self.stream, '\r' + ' ' * self._last_len + '\r')
            self._last_len = 0
            self._painted = False

    def advance(self, step=1, suffix=''):
        self.n += step
        if not self.enabled or not self._tty:
            return
        now = time.monotonic()
        if now - self._last_render < self.min_interval and self.n < self.total:
            return
        self._last_render = now
        self._paint(suffix)

    def finish(self, suffix=''):
        if self._closed:
            return
        self._closed = True
        if not self.enabled:
            return
        took = human_duration(time.monotonic() - self._started)
        if self._tty:
            self._paint(suffix, ending='\n')
        else:
            _emit(self.stream,
                  f'  {self.label}: done — {self.n}/{self.total} in {took}', '\n')
        self._painted = False

    # ── context manager ──────────────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.finish()
        return False


class _NullBar:
    """Stand-in used when progress output is disabled; same call surface."""

    n = 0

    def advance(self, step=1, suffix=''):
        self.n += step

    def clear(self):
        pass

    def finish(self, suffix=''):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Console:
    """
    Verbosity-aware output for management commands.

    verbosity 0 — silent except errors
              1 — headings, progress bars, summary          (default)
              2 — plus one line per record processed
    """

    def __init__(self, stdout, stderr, style, verbosity=1, progress=True):
        self.stdout = stdout
        self.stderr = stderr
        self.style = style
        self.verbosity = int(verbosity if verbosity is not None else 1)
        self.warnings = 0
        self.errors = 0
        self._progress = bool(progress)
        self._bar = None

    # ── plumbing ─────────────────────────────────────────────────────────

    def _clear_bar(self):
        if self._bar is not None:
            self._bar.clear()

    def bar(self, total, label):
        """Create a progress bar; it becomes the active bar for this console."""
        if not self._progress or self.verbosity < 1:
            self._bar = _NullBar()
        else:
            self._bar = ProgressBar(total, label, self.stdout)
        return self._bar

    # ── output ───────────────────────────────────────────────────────────

    def heading(self, msg):
        if self.verbosity >= 1:
            self._clear_bar()
            self.stdout.write(self.style.MIGRATE_HEADING(msg))

    def line(self, msg, level=1):
        if self.verbosity >= level:
            self._clear_bar()
            self.stdout.write(msg)

    def detail(self, msg):
        """One line per record — only shown at -v 2."""
        self.line(msg, level=2)

    def success(self, msg):
        if self.verbosity >= 1:
            self._clear_bar()
            self.stdout.write(self.style.SUCCESS(msg))

    def warn(self, msg):
        self.warnings += 1
        if self.verbosity >= 1:
            self._clear_bar()
            self.stderr.write(self.style.WARNING(f'  [!] {msg}'))

    def error(self, msg):
        self.errors += 1
        self._clear_bar()
        self.stderr.write(self.style.ERROR(f'  [x] {msg}'))
