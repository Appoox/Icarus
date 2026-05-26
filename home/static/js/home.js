document.addEventListener('DOMContentLoaded', () => {
    const wrapper = document.querySelector('.sg-issue-articles__scroll-wrapper');
    const grid    = document.querySelector('.sg-issue-articles__grid');
    if (!wrapper || !grid) return;

    const cards      = Array.from(grid.querySelectorAll('.sg-issue-card'));
    const thumbs     = Array.from(document.querySelectorAll('.sg-issue-thumbnail'));
    const totalCards = cards.length;

    // Need the three-part clone layout (clones | originals | clones)
    if (totalCards < 3) return;

    const numArticles = totalCards / 3; // cards per section

    // ── Tunables ────────────────────────────────────────────
    const PAUSE_MS  = 4000; // how long to rest on each card
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
});