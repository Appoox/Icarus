/* Icarus — Global Interactions */

document.addEventListener('DOMContentLoaded', function() {
    
    // ── Django Messages — Floating Toasts ──
    function dismissToast(msg) {
        msg.classList.add('is-dismissing');
        msg.addEventListener('animationend', () => msg.remove(), { once: true });
    }

    document.querySelectorAll('.message').forEach(msg => {
        const btn = msg.querySelector('.message__close');
        if (btn) btn.addEventListener('click', () => dismissToast(msg));

        // Warnings linger longer
        const delay = msg.classList.contains('message--warning') ? 9000 : 6000;
        let timer = setTimeout(() => dismissToast(msg), delay);

        msg.addEventListener('mouseenter', () => clearTimeout(timer));
        msg.addEventListener('mouseleave', () => {
            timer = setTimeout(() => dismissToast(msg), 3000);
        });
    });

    // ── Floating Action Buttons ──
    const goBackButton = document.getElementById('goBackButton');
    const goToTopButton = document.getElementById('goToTopButton');

    if (goBackButton) {
        goBackButton.addEventListener('click', function(e) {
            e.preventDefault();
            if (document.referrer && document.referrer.includes(window.location.host)) {
                window.history.back();
            } else {
                window.location.href = '/';
            }
        });

        let mouseTimeout;
        const resetMouseTimeout = () => {
            goBackButton.classList.remove('idle-hidden');
            clearTimeout(mouseTimeout);
            mouseTimeout = setTimeout(() => {
                goBackButton.classList.add('idle-hidden');
            }, 2000); // 2 seconds of inactivity
        };

        // Listen for mouse and touch movements
        document.addEventListener('mousemove', resetMouseTimeout);
        document.addEventListener('touchstart', resetMouseTimeout);
        document.addEventListener('touchmove', resetMouseTimeout);

        // Initialize the timer
        resetMouseTimeout();
    }

    if (goToTopButton) {
        window.addEventListener('scroll', function() {
            if (window.scrollY > 300) {
                goToTopButton.classList.add('visible');
            } else {
                goToTopButton.classList.remove('visible');
            }
        });

        goToTopButton.addEventListener('click', function() {
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });
    }

        const floatingContainer = document.querySelector('.floating-action-buttons');
        if (floatingContainer && window.location.pathname !== '/') {
            const homeBtn = document.createElement('a');
            homeBtn.href = '/';
            homeBtn.className = 'floating-btn go-home-btn';
            homeBtn.title = floatingContainer.dataset.homeLabel || 'Home';
            homeBtn.innerHTML = '<i class="fas fa-home"></i>';
            homeBtn.style.textDecoration = 'none';
            homeBtn.style.justifyContent = 'center';
            floatingContainer.insertBefore(homeBtn, floatingContainer.firstChild);
        }

});

/* ─────────────────────────────────────────────
   Lazy word-by-word reveal (shared helper)
   Wraps each on-screen word of `root` in <span class="lazy-word"> (hidden via
   CSS), then reveals them in ONE continuous stream in reading order — a single wave
   flowing top-to-bottom, not an independent cascade per paragraph. Used by
   article.js and issue.js on their body roots.

   Only text on screen is touched: the words there are wrapped and paced
   word-by-word, and everything else is left as plain text, which shows as soon
   as the body does. Wrapping a whole long article (thousands of spans, each
   with its own fade) froze phones for over a second right as reading began.

   Measured against the viewport only — NOT against clipping ancestors like the
   editorial's "read more" box. That clip starts at 400px and is expanded a
   moment later by the show-more logic; words it hides are still wrapped, and
   issue.js reveals any left waiting when the reader expands it.

   Tune the pace with STEP_MS below (smaller = faster); the per-word fade
   softness is the `word-in` duration in Icarus.css.
───────────────────────────────────────────── */
window.lazyRevealWords = function (root) {
    if (!root || root.dataset.lazyWords === 'done') return;
    root.dataset.lazyWords = 'done';

    // Hidden roots (e.g. an inactive language tab) and reduced-motion readers
    // get the text as it is — there's no wave to watch.
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce || !root.offsetParent) return;

    var STEP_MS = 24; // delay between consecutive words — the reveal speed knob

    // Never touch code/math/scripts or already-wrapped text.
    var SKIP = 'pre, code, .math, .code-block, script, style, svg, .lazy-word, .no-lazy';

    // On screen = overlapping the viewport. Words above it count as off screen
    // too, so a reader who scrolled before the reveal isn't kept waiting while
    // the wave works through text they've already passed.
    var viewBottom = window.innerHeight;
    var onScreen = function (rect) {
        return (rect.width || rect.height) && rect.bottom > 0 && rect.top < viewBottom;
    };

    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
        acceptNode: function (n) {
            if (!n.nodeValue || !n.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
            if (n.parentElement && n.parentElement.closest(SKIP)) return NodeFilter.FILTER_REJECT;
            return NodeFilter.FILTER_ACCEPT;
        }
    });

    // Read phase (no writes → one layout): keep only the text nodes on screen.
    var range = document.createRange();
    var textNodes = [], node;
    while ((node = walker.nextNode())) {
        range.selectNodeContents(node);
        if (onScreen(range.getBoundingClientRect())) textNodes.push(node);
    }
    if (range.detach) range.detach();
    if (!textNodes.length) return;

    textNodes.forEach(function (tn) {
        var frag = document.createDocumentFragment();
        tn.nodeValue.split(/(\s+)/).forEach(function (part) {
            if (part === '') return;
            if (/^\s+$/.test(part)) {
                frag.appendChild(document.createTextNode(part));
            } else {
                var span = document.createElement('span');
                span.className = 'lazy-word';
                span.textContent = part;
                frag.appendChild(span);
            }
        });
        tn.parentNode.replaceChild(frag, tn);
    });

    var words = Array.prototype.slice.call(root.getElementsByClassName('lazy-word'));
    var reveal = function (el) { el.classList.add('lazy-word--in'); };
    if (!words.length) return;

    // A paragraph that starts on screen can run past the fold: pace only its
    // words that are on screen and show the rest straight away.
    var paced = [], rest = [];
    words.forEach(function (w) {
        (onScreen(w.getBoundingClientRect()) ? paced : rest).push(w);
    });
    rest.forEach(reveal);
    if (!paced.length) return;

    // Pace the on-screen words as one continuous wave, capped so a tall screen
    // still finishes promptly instead of taking many seconds.
    var MAX_WAVE_MS = 3500;
    var duration = Math.min(paced.length * STEP_MS, MAX_WAVE_MS);
    var shown = 0, start = 0;
    (function frame(now) {
        if (!start) start = now;
        var target = Math.min(paced.length, Math.ceil((now - start) / duration * paced.length));
        for (; shown < target; shown++) reveal(paced[shown]);
        if (shown < paced.length) requestAnimationFrame(frame);
    })(0);
};