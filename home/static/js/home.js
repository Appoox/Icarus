/* ═══════════════════════════════════════════════════════════════════
   ISSUE CAROUSEL — auto-advancing, infinitely looping article strip.

   Scoped per instance.  Sections are placeable now, so an editor can
   put two carousels on the homepage; the old code used
   document.querySelector() and would have driven only the first while
   the second sat dead.  Everything below resolves against `root`, and
   each instance keeps its own timer, index and listeners.

   Per-instance settings ride in on data-* attributes written by
   blocks/issue_carousel_block.html, so autoplay and pause are editor
   controls rather than constants.
═══════════════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-sg-carousel]').forEach(initCarousel);
});

function initCarousel(root) {
    const wrapper = root.querySelector('.sg-issue-articles__scroll-wrapper');
    const grid    = root.querySelector('.sg-issue-articles__grid');
    if (!wrapper || !grid) return;

    const cards      = Array.from(grid.querySelectorAll('.sg-issue-card'));
    const thumbs     = Array.from(root.querySelectorAll('.sg-issue-thumbnail'));
    const totalCards = cards.length;

    // Need the three-part clone layout (clones | originals | clones)
    if (totalCards < 3) return;

    const numArticles = totalCards / 3; // cards per section

    // ── Tunables ────────────────────────────────────────────
    // Editor-controlled: see the data-* attributes on the block template.
    const AUTOPLAY  = root.dataset.autoplay !== '0';
    const PAUSE_MS  = parseInt(root.dataset.pause, 10) || 4000;
    const SLIDE_MS  = 600;  // smooth-scroll budget (matches CSS ease)
    // ────────────────────────────────────────────────────────

    let currentIndex    = 0;     // 0-based position within the originals
    let isAnimating     = false;
    let userInteracting = false;
    let autoTimer       = null;

    const vert = () => window.innerWidth > 900;

    // Remove any CSS snap so programmatic scrolls feel consistent
    wrapper.style.scrollSnapType = 'none';

    // ── Scroll helpers ───────────────────────────────────────

    /** Pixel offset to centre cardIndex inside the wrapper. */
    const centrePos = (cardIndex) => {
        const card = cards[cardIndex];
        return vert()
            ? card.offsetTop  - (wrapper.offsetHeight - card.offsetHeight) / 2
            : card.offsetLeft - (wrapper.offsetWidth  - card.offsetWidth)  / 2;
    };

    /** Instant (no animation) scroll — used for the infinite-loop jump. */
    const jumpTo = (cardIndex) => {
        const pos = centrePos(cardIndex);
        if (vert()) wrapper.scrollTop  = pos;
        else        wrapper.scrollLeft = pos;
    };

    /** Animated scroll — returns a Promise that resolves after SLIDE_MS. */
    const slideTo = (cardIndex) =>
        new Promise(resolve => {
            const pos = centrePos(cardIndex);
            wrapper.scrollTo(vert()
                ? { top:  pos, behavior: 'smooth' }
                : { left: pos, behavior: 'smooth' }
            );
            setTimeout(resolve, SLIDE_MS);
        });

    // ── Thumbnail bar ────────────────────────────────────────

    const updateThumbs = (index) =>
        thumbs.forEach((t, i) => t.classList.toggle('is-active', i === index));

    // ── Step logic ───────────────────────────────────────────

    /**
     * Advance one step.
     *
     * Normal step:  slide from originals[currentIndex] → originals[nextIndex]
     * Wrap-around:  slide to clones[0] (third section), then instantly jump
     *               to originals[0] so the loop is seamless.
     */
    const step = async () => {
        if (isAnimating || userInteracting) return;
        isAnimating = true;

        const nextIndex = (currentIndex + 1) % numArticles;

        if (nextIndex === 0) {
            // Animate into the first card of the THIRD (trailing) clone set …
            await slideTo(numArticles * 2);
            // … then silently snap back to the matching ORIGINAL card.
            jumpTo(numArticles);
        } else {
            await slideTo(numArticles + nextIndex);
        }

        currentIndex = nextIndex;
        updateThumbs(currentIndex);
        isAnimating = false;
    };

    // ── Auto-advance timer ───────────────────────────────────

    const startAuto = () => {
        if (!AUTOPLAY) return;
        clearInterval(autoTimer);
        autoTimer = setInterval(step, PAUSE_MS);
    };

    const stopAuto = () => clearInterval(autoTimer);

    // ── Thumbnail click navigation ───────────────────────────

    thumbs.forEach((thumb, i) => {
        thumb.addEventListener('click', async () => {
            if (isAnimating) return;
            stopAuto();
            isAnimating = true;

            await slideTo(numArticles + i);
            currentIndex = i;
            updateThumbs(currentIndex);
            isAnimating = false;

            startAuto();
        });
    });

    // ── Pause / resume on user interaction ───────────────────

    const onStart = () => {
        userInteracting = true;
        stopAuto();
    };

    const onEnd = () => {
        userInteracting = false;
        // Brief delay so any in-flight snap can settle before we resume
        setTimeout(startAuto, 800);
    };

    wrapper.addEventListener('touchstart', onStart, { passive: true });
    wrapper.addEventListener('touchend',   onEnd,   { passive: true });
    wrapper.addEventListener('mousedown',  onStart);
    wrapper.addEventListener('mouseup',    onEnd);

    // ── Re-centre on resize ──────────────────────────────────

    let resizeDebounce;
    window.addEventListener('resize', () => {
        clearTimeout(resizeDebounce);
        resizeDebounce = setTimeout(() => {
            jumpTo(numArticles + currentIndex);
        }, 150);
    });

    // ── Boot ─────────────────────────────────────────────────

    // Small delay so layout has settled before we measure offsetTop/Left
    setTimeout(() => {
        jumpTo(numArticles);   // start on first original card
        updateThumbs(0);
        startAuto();
    }, 80);
}
/* ═══════════════════════════════════════════════════════════════════
   FLOATING SPECKS — background dots that are always in slow motion:
   each speck wanders on its own drift path (sin/cos with per-speck
   period, phase and amplitude), and additionally reacts to the
   pointer with individual parallax plus a local "push away from the
   cursor" force. Mouse and touch. Static under prefers-reduced-motion.
   Self-contained: registers its own listener rather than sharing the
   carousel's, so it runs on every homepage arrangement — including one
   with no carousel section on it at all.
═══════════════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
    const layer = document.querySelector('.sg-specks');
    if (!layer) return;

    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
    const COLORS = ['#0b1f3a', '#c8102e', '#0072ce', '#2e7d32', '#e65100', '#6b6659'];
    // The layer spans the whole document, so scale the population with
    // the page length (roughly N per screenful, capped for performance).
    const perScreen = window.innerWidth < 900 ? 26 : 46;
    const screens = Math.max(1, document.documentElement.scrollHeight / window.innerHeight);
    const COUNT = Math.min(120, Math.round(perScreen * screens));
    const TWO_PI = Math.PI * 2;

    const specks = [];
    for (let i = 0; i < COUNT; i++) {
        const el = document.createElement('span');
        el.className = 'sg-speck';
        const size = 4 + Math.random() * 5;
        el.style.width = el.style.height = size.toFixed(1) + 'px';
        el.style.left = (Math.random() * 100).toFixed(2) + '%';
        el.style.top = (Math.random() * 100).toFixed(2) + '%';
        el.style.background = COLORS[i % COLORS.length];
        el.style.opacity = (0.22 + Math.random() * 0.20).toFixed(2);
        layer.appendChild(el);
        specks.push({
            el,
            bx: 0, by: 0,                              // base position (px)
            x: 0, y: 0,                                // lerped pointer response
            depth: 0.15 + Math.random() * 0.85,        // parallax strength
            dirX: Math.random() < 0.5 ? -1 : 1,        // parallax direction
            dirY: Math.random() < 0.5 ? -1 : 1,
            // Autonomous wander: slow per-speck drift orbits
            wf1: TWO_PI / (22 + Math.random() * 26),   // rad/s
            wf2: TWO_PI / (28 + Math.random() * 34),
            wp1: Math.random() * TWO_PI,               // phase
            wp2: Math.random() * TWO_PI,
            wax: 22 + Math.random() * 42,              // amplitude (px)
            way: 22 + Math.random() * 42,
        });
    }

    // Cache base pixel positions (document coordinates — the layer spans
    // the full page) so the animation loop never reads the DOM.
    const measure = () => {
        for (const s of specks) {
            s.bx = (parseFloat(s.el.style.left) / 100) * layer.clientWidth;
            s.by = (parseFloat(s.el.style.top) / 100) * layer.clientHeight;
        }
    };
    measure();
    // Images loading can grow the document — re-measure once settled.
    window.addEventListener('load', measure);
    let mt;
    window.addEventListener('resize', () => { clearTimeout(mt); mt = setTimeout(measure, 150); });

    if (reduce.matches) return; // static specks only

    // ── Pointer tracking (mouse + touch) ──────────────────────────
    const AMP = 26;    // max parallax travel (px)
    const R = 150;     // repulsion radius around the cursor (px)
    const PUSH = 46;   // max repulsion distance (px)
    let px = -1e4, py = -1e4;  // pointer position (far away until first move)
    let nx = 0, ny = 0;        // pointer offset from viewport centre, -1..1
    const onPoint = (cx, cy) => {
        px = cx; py = cy;
        nx = (cx / window.innerWidth - 0.5) * 2;
        ny = (cy / window.innerHeight - 0.5) * 2;
    };
    window.addEventListener('mousemove', (e) => onPoint(e.clientX, e.clientY), { passive: true });
    window.addEventListener('touchmove', (e) => {
        if (e.touches[0]) onPoint(e.touches[0].clientX, e.touches[0].clientY);
    }, { passive: true });
    window.addEventListener('touchstart', (e) => {
        if (e.touches[0]) onPoint(e.touches[0].clientX, e.touches[0].clientY);
    }, { passive: true });

    // ── Animation loop ────────────────────────────────────────────
    const tick = (now) => {
        const t = now / 1000;
        // Pointer in document coordinates (specks live in document space).
        const pdx = px + window.scrollX;
        const pdy = py + window.scrollY;
        for (const s of specks) {
            // Always-on slow wander — visible even with no pointer around.
            const wx = Math.sin(t * s.wf1 + s.wp1) * s.wax;
            const wy = Math.cos(t * s.wf2 + s.wp2) * s.way;

            // Individual parallax: per-speck depth and direction.
            let tx = nx * AMP * s.depth * s.dirX;
            let ty = ny * AMP * s.depth * s.dirY;

            // Local repulsion: specks near the cursor get pushed away.
            const dx = (s.bx + wx + s.x) - pdx;
            const dy = (s.by + wy + s.y) - pdy;
            const d = Math.hypot(dx, dy);
            if (d < R && d > 0.5) {
                const f = (1 - d / R) * PUSH;
                tx += (dx / d) * f;
                ty += (dy / d) * f;
            }

            s.x += (tx - s.x) * 0.07;
            s.y += (ty - s.y) * 0.07;
            s.el.style.transform =
                'translate(' + (wx + s.x).toFixed(1) + 'px,' + (wy + s.y).toFixed(1) + 'px)';
        }
        requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
});

/* ═══════════════════════════════════════════════════════════════════
   HOVER FLOAT — every card lifts and tilts subtly toward the cursor
   for a light 3D effect. Fine-pointer devices only; skipped under
   prefers-reduced-motion. The inline transform overrides the CSS
   :hover lifts while hovering and clears on leave; the cards' existing
   `transition: transform` keeps the motion smooth.
═══════════════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
    const fine = window.matchMedia('(hover: hover) and (pointer: fine)');
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
    if (!fine.matches || reduce.matches) return;

    const SELECTOR = [
        '.sg-issue-cover',
        '.sg-issue-card',
        '.sg-issue-small-card',
        '.sg-issue-card-simple',
        '.sg-article-card',
        '.sg-author-card',
        '.sg-custom-card',
    ].join(', ');
    const MAX_TILT = 3.5; // deg
    const LIFT = 6;       // px

    document.querySelectorAll(SELECTOR).forEach((card) => {
        card.addEventListener('mousemove', (e) => {
            const r = card.getBoundingClientRect();
            const rx = ((e.clientY - r.top) / r.height - 0.5) * -2 * MAX_TILT;
            const ry = ((e.clientX - r.left) / r.width - 0.5) * 2 * MAX_TILT;
            card.style.transform =
                'perspective(700px) rotateX(' + rx.toFixed(2) + 'deg)' +
                ' rotateY(' + ry.toFixed(2) + 'deg) translateY(-' + LIFT + 'px)';
            card.classList.add('is-floating');
        });
        card.addEventListener('mouseleave', () => {
            card.style.transform = '';
            card.classList.remove('is-floating');
        });
    });
});
