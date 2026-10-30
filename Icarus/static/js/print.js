/* ─────────────────────────────────────────────────────────────
   print.js — interactions for the print edition design.
   Loaded by base.html only when that design is active. Each part
   looks for its own markup and does nothing when it isn't there.
───────────────────────────────────────────────────────────── */
(function () {
    'use strict';

    function ready(fn) {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', fn);
        } else {
            fn();
        }
    }

    function store(key, value) {
        try {
            if (value === undefined) return localStorage.getItem(key);
            localStorage.setItem(key, value);
        } catch (e) { /* private mode: just don't remember */ }
        return null;
    }

    ready(function () {

        /* ── Mobile menu ─────────────────────────────── */
        var header = document.getElementById('ptHeader');
        var menuBtn = document.getElementById('ptMenuToggle');
        if (header && menuBtn) {
            menuBtn.addEventListener('click', function () {
                var open = header.classList.toggle('is-menu-open');
                menuBtn.setAttribute('aria-expanded', String(open));
            });
        }

        /* ── Modals: [data-pt-modal-open="id"] opens <dialog id> ── */
        document.addEventListener('click', function (event) {
            var opener = event.target.closest('[data-pt-modal-open]');
            if (opener) {
                var dialog = document.getElementById(opener.getAttribute('data-pt-modal-open'));
                if (dialog && typeof dialog.showModal === 'function') {
                    event.preventDefault();
                    dialog._ptOpener = opener;
                    dialog.showModal();
                }
                return;
            }
            if (event.target.closest('[data-pt-modal-close]')) {
                var open = event.target.closest('dialog');
                if (open) open.close();
                return;
            }
            // A click on the backdrop lands on the <dialog> itself, outside its panel.
            if (event.target.tagName === 'DIALOG' && event.target.classList.contains('pt-modal')) {
                event.target.close();
            }
        });
        document.querySelectorAll('dialog.pt-modal').forEach(function (dialog) {
            dialog.addEventListener('close', function () {
                if (dialog._ptOpener) dialog._ptOpener.focus();
            });
        });

        /* ── Fold long text (the issue's editorial) ─────── */
        document.querySelectorAll('[data-pt-fold]').forEach(function (text) {
            var toggle = text.parentNode.querySelector('[data-pt-fold-toggle]');
            if (!toggle) return;
            text.classList.add('is-folded');
            if (text.scrollHeight <= text.clientHeight + 40 && text.scrollWidth <= text.clientWidth + 4) {
                text.classList.remove('is-folded');  // short enough to show whole
                return;
            }
            toggle.hidden = false;
            toggle.setAttribute('aria-expanded', 'false');
            toggle.addEventListener('click', function () {
                var folded = text.classList.toggle('is-folded');
                toggle.setAttribute('aria-expanded', String(!folded));
                toggle.textContent = folded ? toggle.dataset.labelMore : toggle.dataset.labelLess;
                if (folded) text.scrollIntoView({ block: 'nearest' });
            });
        });

        /* ── Article language switch (മലയാളം | English) ── */
        document.querySelectorAll('[data-pt-lang-switch]').forEach(function (group) {
            var buttons = group.querySelectorAll('[data-lang-target]');
            buttons.forEach(function (button) {
                button.addEventListener('click', function () {
                    var lang = button.getAttribute('data-lang-target');
                    buttons.forEach(function (b) {
                        var on = b === button;
                        b.classList.toggle('is-active', on);
                        b.setAttribute('aria-pressed', String(on));
                    });
                    document.querySelectorAll('[data-pt-lang]').forEach(function (el) {
                        var langs = el.getAttribute('data-pt-lang').split(' ');
                        el.hidden = langs.indexOf(lang) === -1;
                    });
                    // Every PDF link on the page (by the share icons and at the end).
                    document.querySelectorAll('.article-pdf-download-btn').forEach(function (pdf) {
                        pdf.setAttribute('href', pdf.getAttribute('href').split('?')[0] + '?lang=' + lang);
                    });
                });
            });
        });

        /* ── Text size: three steps, remembered per reader ── */
        var article = document.querySelector('[data-pt-text-size]');
        if (article) {
            var sizeButtons = document.querySelectorAll('[data-pt-size]');
            var setSize = function (size) {
                article.setAttribute('data-pt-text-size', size);
                sizeButtons.forEach(function (b) {
                    var on = b.getAttribute('data-pt-size') === size;
                    b.classList.toggle('is-active', on);
                    b.setAttribute('aria-pressed', String(on));
                });
            };
            var saved = store('pt-text-size');
            if (saved === '0' || saved === '1' || saved === '2') setSize(saved);
            sizeButtons.forEach(function (b) {
                b.addEventListener('click', function () {
                    var size = b.getAttribute('data-pt-size');
                    setSize(size);
                    store('pt-text-size', size);
                });
            });
        }

        /* ── Share: copy link ───────────────────────── */
        document.querySelectorAll('[data-pt-copy-link]').forEach(function (button) {
            button.addEventListener('click', function () {
                var url = button.getAttribute('data-pt-copy-link') || window.location.href;
                var done = function () {
                    button.classList.add('is-copied');
                    var label = button.getAttribute('data-copied-label');
                    if (label) button.setAttribute('title', label);
                    setTimeout(function () { button.classList.remove('is-copied'); }, 1600);
                };
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(url).then(done, function () {});
                }
            });
        });

        /* ── Listen bar ─────────────────────────────── */
        var SPEEDS = [1, 1.25, 1.5, 2, 0.75];
        function clock(seconds) {
            if (!isFinite(seconds) || seconds < 0) seconds = 0;
            var m = Math.floor(seconds / 60);
            var s = Math.floor(seconds % 60);
            return m + ':' + (s < 10 ? '0' : '') + s;
        }
        document.querySelectorAll('[data-pt-listen]').forEach(function (bar) {
            var audio = bar.querySelector('audio');
            var play = bar.querySelector('.pt-listen__play');
            var icon = play && play.querySelector('i');
            var track = bar.querySelector('.pt-listen__track');
            var fill = bar.querySelector('.pt-listen__fill');
            var elapsed = bar.querySelector('[data-pt-elapsed]');
            var total = bar.querySelector('[data-pt-total]');
            var speed = bar.querySelector('.pt-listen__speed');
            if (!audio || !play) return;

            var showState = function () {
                var playing = !audio.paused;
                if (icon) icon.className = playing ? 'fas fa-pause' : 'fas fa-play';
                play.setAttribute('aria-label', playing ? play.dataset.labelPause : play.dataset.labelPlay);
            };
            play.addEventListener('click', function () {
                if (audio.paused) { audio.play(); } else { audio.pause(); }
            });
            audio.addEventListener('play', showState);
            audio.addEventListener('pause', showState);
            audio.addEventListener('ended', showState);
            audio.addEventListener('loadedmetadata', function () {
                if (total) total.textContent = clock(audio.duration);
            });
            audio.addEventListener('timeupdate', function () {
                if (elapsed) elapsed.textContent = clock(audio.currentTime);
                if (fill && audio.duration) fill.style.width = (audio.currentTime / audio.duration * 100) + '%';
                if (track && audio.duration) track.setAttribute('aria-valuenow', String(Math.round(audio.currentTime / audio.duration * 100)));
            });
            if (track) {
                var seek = function (fraction) {
                    if (audio.duration) audio.currentTime = Math.max(0, Math.min(1, fraction)) * audio.duration;
                };
                track.addEventListener('click', function (e) {
                    var rect = track.getBoundingClientRect();
                    seek((e.clientX - rect.left) / rect.width);
                });
                track.addEventListener('keydown', function (e) {
                    if (!audio.duration) return;
                    if (e.key === 'ArrowRight') { audio.currentTime = Math.min(audio.duration, audio.currentTime + 10); e.preventDefault(); }
                    if (e.key === 'ArrowLeft') { audio.currentTime = Math.max(0, audio.currentTime - 10); e.preventDefault(); }
                });
            }
            if (speed) {
                speed.addEventListener('click', function () {
                    var next = SPEEDS[(SPEEDS.indexOf(audio.playbackRate) + 1) % SPEEDS.length];
                    audio.playbackRate = next;
                    speed.textContent = next + '×';
                });
            }
        });
    });
})();
