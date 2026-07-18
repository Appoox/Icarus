/* ─────────────────────────────────────────────────────────────────────────
   flipbook_viewer.js
   ─────────────────────────────────────────────────────────────────────────
   Renders a page-flip book from server pre-rendered page images. Used by:

       the_librarian/templates/the_librarian/viewer.html

   which is shared by all three PDF-viewing flows:
       • the_librarian:viewer          (search results → ArchiveDocument)
       • the_librarian:magazine_viewer (archive grid    → ArchiveIssue)
       • the_librarian:archive_viewer  (raw filesystem PDFs)

   Pipeline:
       1. Fetch page_meta_url — {"page_count": N} for this document.
       2. Build one wrapper <div> per page, each containing an <img> whose
          src is built from page_image_url_template (a URL containing a
          literal "{page}" placeholder — see views.py's
          _page_image_url_template) — the page images themselves are
          rendered and disk-cached server-side (see page_cache.py), so
          this is just normal image loading, no client-side rasterisation.
       3. StPageFlip (the `St.PageFlip` global from page-flip.browser.js)
          takes those elements and renders the flip-book UI, including its
          own drag/click-to-turn interactions and shadows.

   This replaced an earlier version that rasterised every page from the
   raw PDF via PDF.js + <canvas> in the browser, with its own worker pool
   and a Cache Storage layer to avoid re-rendering on repeat visits. None
   of that is needed anymore: the server renders each page once (ever,
   across all readers) and images are cached like any other static asset
   via a normal Cache-Control header — so this file is just responsible
   for building the page elements and handing them to StPageFlip.

   Expects a container element:
       <div id="flipbook-root"
            data-page-meta-url="…"
            data-page-image-url-template="…/{page}.webp"
            data-start-page="0-indexed integer"></div>

   Optional DOM hooks (all safe to omit):
       #flipbook-loading            — overlay shown while the book opens
       #flipbook-loading__text      — status text inside the overlay
       #flipbook-loading__bar-fill  — progress bar fill (0–100% width)
       #flipbook-prev / #flipbook-next — nav buttons
       #flipbook-page-indicator     — "Page X of Y" text, kept in sync

   Responsive sizing
   ─────────────────
   StPageFlip's own "stretch" size mode already handles WIDTH-driven
   responsiveness natively — including automatically collapsing a
   two-page spread down to a single page on narrow/mobile screens — as
   long as it's given a *stable* minWidth (the width below which two
   pages can no longer fit side by side) rather than one that's
   re-derived from the current container width. What it can't do on its
   own is react to the viewport's HEIGHT, because the height it measures
   internally is just its own width scaled by the page's aspect ratio,
   not the actual visible space — so on a short/wide screen it can grow
   tall enough to spill past the bottom of the stage. To fix that we:
     • size the stage from window.innerHeight rather than trusting the
       stylesheet (sizeStageToViewport), which also sidesteps the
       chicken-and-egg problem of an intrinsically-sized container
       measuring as 0 before the book has ever been built;
     • cap the page's max width by how much of that stage height is
       actually available, converted through the aspect ratio
       (buildBook), re-solving this on window resize / orientation
       change since the library won't do it on its own;
     • center the book within the stage ourselves (centerBookInStage),
       since StPageFlip doesn't do that either.
   ───────────────────────────────────────────────────────────────────── */
(function () {
    'use strict';

    // Comfortable single-page width range used for on-screen layout.
    // MIN_PAGE_WIDTH is deliberately a fixed number rather than something
    // derived from the current screen size — StPageFlip compares it
    // against the container's actual width (is there room for
    // 2 * MIN_PAGE_WIDTH?) to decide whether to show a two-page spread or
    // fall back to one page at a time, so it needs to stay constant for
    // that comparison to mean anything.
    var MIN_PAGE_WIDTH         = 240;
    var DESIRED_MAX_PAGE_WIDTH = 640; // cap so pages don't get oversized on huge screens

    // ── Responsive sizing constants ──────────────────────────────────────
    var STAGE_BOTTOM_MARGIN  = 24;  // px of breathing room reserved below the stage
    var STAGE_MIN_HEIGHT     = 320; // never let the stage collapse smaller than this
    var PAGE_GAP             = 20;  // px reserved below the book within the stage
    var RESIZE_DEBOUNCE_MS   = 200; // wait for resizing/rotation to settle before rebuilding

    document.addEventListener('DOMContentLoaded', init);

    function init() {
        var root = document.getElementById('flipbook-root');
        if (!root) return;
        var t = root.dataset;

        var pageMetaUrl          = root.dataset.pageMetaUrl;
        var pageImageUrlTemplate = root.dataset.pageImageUrlTemplate;
        var startPage = parseInt(root.dataset.startPage, 10);
        if (isNaN(startPage) || startPage < 0) startPage = 0;

        if (!pageMetaUrl || !pageImageUrlTemplate) {
            showError(root, t.i18nErrNoData);
            return;
        }
        if (typeof St === 'undefined' || !St.PageFlip) {
            showError(root, t.i18nErrLoad);
            return;
        }

        var loadingEl   = document.getElementById('flipbook-loading');
        var loadingText = document.getElementById('flipbook-loading__text');
        var loadingBar  = document.getElementById('flipbook-loading__bar-fill');
        var prevBtn     = document.getElementById('flipbook-prev');
        var nextBtn     = document.getElementById('flipbook-next');
        var indicator   = document.getElementById('flipbook-page-indicator');

        var pageFlip  = null;
        var pageCount = 0;

        // The stage is the flip-book's positioning parent (.flipbook-stage).
        // Hoisted here (rather than computed inline in buildBook) so the
        // resize handler can also reach it.
        var stage = root.parentElement;

        // Populated once page_meta_url resolves; kept at this scope
        // (rather than local to that fetch's .then()) so a later
        // window-resize rebuild can reuse the already-built page
        // elements and aspect ratio without touching the network again.
        var pageElements = [];
        var firstAspect  = 1;

        function pageImageUrl(pageNumber) {
            return pageImageUrlTemplate.replace('{page}', String(pageNumber));
        }

        // ── Size the stage from the viewport, not from CSS ───────────────
        // Using window.innerHeight (minus whatever space the toolbar above
        // the stage is currently taking up) sidesteps the chicken-and-egg
        // problem of an intrinsically-sized container: before the book
        // exists, the stage has no content to size itself from, so a
        // CSS height of "auto" can measure as 0 the first time we need it.
        // Measuring from the viewport instead is reliable on first load
        // and stays correct after window resizes or orientation changes.
        function sizeStageToViewport() {
            if (!stage) return;
            var top = stage.getBoundingClientRect().top;
            var available = window.innerHeight - top - STAGE_BOTTOM_MARGIN;
            stage.style.height = Math.max(STAGE_MIN_HEIGHT, available) + 'px';
        }

        // ── Center the book within the stage ──────────────────────────────
        // StPageFlip sizes #flipbook-root but never centers it, so once the
        // book is capped at a comfortable reading width (rather than
        // stretching edge-to-edge) it would otherwise sit flush against the
        // top-left corner, leaving the rest of the stage empty. Positioning
        // root at 50%/50% with a counter-transform centers it regardless of
        // its (dynamic, aspect-ratio-driven) size, and only needs to run
        // once — the percentages stay correct as the stage resizes.
        function centerBookInStage() {
            if (!stage || !root) return;
            // Give the stage a positioning context if it doesn't already
            // have one. This only affects children that use
            // position:absolute (which is what we're about to make root),
            // so it won't disturb the loading overlay or nav buttons.
            if (getComputedStyle(stage).position === 'static') {
                stage.style.position = 'relative';
            }
            root.style.position  = 'absolute';
            root.style.left      = '50%';
            root.style.top       = '50%';
            root.style.transform = 'translate(-50%, -50%)';
        }

        function setStatus(text) {
            if (loadingText) loadingText.textContent = text;
        }

        function setProgress(done, total) {
            if (loadingBar && total) {
                loadingBar.style.width = Math.round((done / total) * 100) + '%';
            }
        }

        function updateIndicator(currentIndex) {
            if (!indicator) return;
            var shown = currentIndex + 1;
            indicator.textContent = pageCount
                ? pageCount + ' ' + t.i18nPageOf + ' ' + t.i18nPage + ' ' + shown
                : t.i18nPage + ' ' + shown;
        }

        function updateNavButtons(currentIndex) {
            if (prevBtn) prevBtn.disabled = currentIndex <= 0;
            if (nextBtn) nextBtn.disabled = pageCount ? currentIndex >= pageCount - 1 : false;
        }

        if (prevBtn) prevBtn.addEventListener('click', function () {
            if (pageFlip) pageFlip.flipPrev();
        });
        if (nextBtn) nextBtn.addEventListener('click', function () {
            if (pageFlip) pageFlip.flipNext();
        });
        document.addEventListener('keydown', function (e) {
            if (!pageFlip) return;
            if (e.key === 'ArrowLeft')  pageFlip.flipPrev();
            if (e.key === 'ArrowRight') pageFlip.flipNext();
        });

        // ── Responsive resize handling ────────────────────────────────────
        // StPageFlip keeps its own internal pixel rendering in sync with
        // CSS width changes automatically, but it has no idea about our
        // height budget or min/max bounds, so a big resize (rotating a
        // phone, resizing the browser window) can still leave the book
        // sized for the *old* viewport. On resize we re-solve the size and
        // rebuild the flip-book from the pageElements we already have —
        // the images are already loaded (or loading) in the browser's
        // normal cache, so this is cheap.
        //
        // Note: pageFlip.destroy() removes #flipbook-root from the DOM
        // entirely (it's how the library tears itself down), so we have to
        // re-attach root to the stage before constructing a new instance.
        var resizeTimer = null;
        function handleResize() {
            sizeStageToViewport();
            if (!pageFlip) return; // book hasn't been built yet — nothing to rebuild
            var currentIndex = pageFlip.getCurrentPageIndex();
            pageFlip.destroy();
            stage.appendChild(root); // re-attach root; destroy() detaches it
            buildBook(pageElements, firstAspect, currentIndex);
        }
        function scheduleResize() {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(handleResize, RESIZE_DEBOUNCE_MS);
        }
        window.addEventListener('resize', scheduleResize);
        window.addEventListener('orientationchange', scheduleResize);

        // ── Build the flip-book once the page elements exist ─────────────
        // `startIndexOverride` is used on resize-triggered rebuilds to
        // reopen the book at whatever page the reader was already on,
        // instead of jumping back to the original startPage.
        function buildBook(pageElements, firstAspect, startIndexOverride) {
            // Re-measure the stage against the current viewport before
            // reading its size below — this is what actually prevents the
            // book from being clipped at the bottom (see header comment).
            sizeStageToViewport();

            var stageHeight = stage.clientHeight;

            // The single-page width StPageFlip is allowed to grow to.
            // Bounded above by our own comfortable-reading cap AND by how
            // tall the current viewport actually is (converted to an
            // equivalent width via the aspect ratio) — whichever is
            // smaller — so the book can never grow tall enough to spill
            // past the bottom of the stage. minWidth is deliberately left
            // as the fixed MIN_PAGE_WIDTH constant (not derived from the
            // stage width here) — see the constant's comment for why.
            var heightConstrainedWidth = Math.floor((stageHeight - PAGE_GAP) * firstAspect);
            var maxPageWidth = Math.max(MIN_PAGE_WIDTH, Math.min(DESIRED_MAX_PAGE_WIDTH, heightConstrainedWidth));
            var pageHeight   = Math.floor(maxPageWidth / firstAspect);

            var clampedStart = typeof startIndexOverride === 'number'
                ? Math.max(0, Math.min(startIndexOverride, pageElements.length - 1))
                : Math.max(0, Math.min(startPage, pageElements.length - 1));

            pageFlip = new St.PageFlip(root, {
                width: maxPageWidth,
                height: pageHeight,
                size: 'stretch',
                // minWidth is what StPageFlip compares against the actual
                // available width (is there room for 2 * minWidth?) to
                // decide whether to show a two-page spread or fall back to
                // a single page — keeping it fixed at MIN_PAGE_WIDTH is
                // what lets that switch happen automatically, instantly,
                // and entirely through the library's own resize handling
                // (no rebuild needed) as the window is resized down to a
                // mobile-sized viewport. maxWidth/maxHeight are pinned to
                // the height-aware size solved above, so on wide-but-short
                // screens the library can never grow the book past the
                // point where it would spill below the stage.
                minWidth: MIN_PAGE_WIDTH,
                maxWidth: maxPageWidth,
                minHeight: Math.floor(MIN_PAGE_WIDTH / firstAspect),
                maxHeight: pageHeight,
                showCover: true,
                maxShadowOpacity: 0.5,
                mobileScrollSupport: false,
                startPage: clampedStart,
                useMouseEvents: true,
            });

            // Use loadFromHTML instead of loadFromImages to enable injecting content on the fly
            pageFlip.loadFromHTML(pageElements);
            pageCount = pageFlip.getPageCount();

            pageFlip.on('flip', function (e) {
                updateIndicator(e.data);
                updateNavButtons(e.data);
            });

            updateIndicator(clampedStart);
            updateNavButtons(clampedStart);

            // Hide the loading overlay immediately so the user can begin reading right away
            if (loadingEl) loadingEl.classList.add('is-hidden');
        }

        // ── Wait for an <img> to know its natural size ────────────────────
        // We need the real page aspect ratio for the layout math above;
        // resolves even on error (falling back to a square default
        // elsewhere) so one bad image can't hang the whole book.
        function waitForImage(img) {
            if (img.complete && img.naturalWidth) return Promise.resolve();
            return new Promise(function (resolve) {
                img.addEventListener('load', resolve, { once: true });
                img.addEventListener('error', resolve, { once: true });
            });
        }

        // ── Kick everything off ──────────────────────────────────────────
        // Size and center the stage immediately, before we've even fetched
        // the page count, so the loading overlay sits in the right spot
        // instead of jumping once the book is built.
        sizeStageToViewport();
        centerBookInStage();
        setStatus(t.i18nLoadingDoc);

        fetch(pageMetaUrl).then(function (response) {
            if (!response.ok) throw new Error('Failed to fetch page metadata (' + response.status + ')');
            return response.json();
        }).then(function (meta) {
            var total = meta.page_count;
            if (!total || total < 1) throw new Error('Document has no pages');

            pageCount = total;
            updateIndicator(startPage);

            var initialPageNumber = Math.max(1, Math.min(startPage + 1, total));
            var loadedCount = 0;

            pageElements = [];
            for (var i = 0; i < total; i++) {
                var pageNumber = i + 1;

                var pageDiv = document.createElement('div');
                pageDiv.className = 'flipbook-page-wrapper';
                // White background so a page whose image hasn't finished
                // loading yet looks like blank paper instead of being
                // transparent.
                pageDiv.style.backgroundColor = '#ffffff';

                var img = document.createElement('img');
                img.style.width = '100%';
                img.style.height = '100%';
                img.style.display = 'block'; // prevents nasty inline baseline gaps
                img.src = pageImageUrl(pageNumber);
                // Hint the browser to fetch the opening page first; the
                // rest can wait their turn. Pre-rendered pages are small
                // (tens of KB), so — unlike the old client-rendering
                // pipeline — there's no need for a custom concurrency-
                // limited worker queue here; the browser's own network
                // scheduling handles this well on its own.
                img.fetchPriority = (pageNumber === initialPageNumber) ? 'high' : 'low';
                img.addEventListener('load', onImageSettled);
                img.addEventListener('error', onImageSettled);
                pageDiv.appendChild(img);

                pageElements.push(pageDiv);
                root.appendChild(pageDiv);
            }

            function onImageSettled() {
                loadedCount++;
                setStatus(t.i18nLoadingPages + ' (' + loadedCount + ' / ' + total + ')');
                setProgress(loadedCount, total);
            }

            // We need the *real* aspect ratio for the layout math in
            // buildBook() — wait for the opening page's image specifically
            // (it's already fetchPriority "high" above, so this shouldn't
            // add a meaningful extra wait), falling back to a sane default
            // if it errors so a single bad image can't break the book.
            var initialImg = pageElements[initialPageNumber - 1].querySelector('img');
            return waitForImage(initialImg).then(function () {
                firstAspect = (initialImg.naturalWidth && initialImg.naturalHeight)
                    ? initialImg.naturalWidth / initialImg.naturalHeight
                    : 0.75; // fallback: a typical portrait page proportion
                buildBook(pageElements, firstAspect);
            });
        }).catch(function (err) {
            console.error('Flipbook viewer error:', err);
            showError(root, t.i18nErrDoc);
        });
    }

    function showError(root, message) {
        var loadingEl = document.getElementById('flipbook-loading');
        if (loadingEl) loadingEl.remove();
        var errDiv = document.createElement('div');
        errDiv.className = 'flipbook-error';
        var p = document.createElement('p');
        p.textContent = message;
        errDiv.appendChild(p);
        root.innerHTML = '';
        root.appendChild(errDiv);
    }
}());