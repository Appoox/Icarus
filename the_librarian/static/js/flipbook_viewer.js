/* ─────────────────────────────────────────────────────────────────────────
   flipbook_viewer.js
   ─────────────────────────────────────────────────────────────────────────
   Renders a PDF as an interactive page-flip book instead of the old
   ViewerJS iframe embed. Used by:

       the_librarian/templates/the_librarian/viewer.html

   which is shared by all three PDF-viewing flows:
       • the_librarian:viewer          (search results → ArchiveDocument)
       • the_librarian:magazine_viewer (archive grid    → ArchiveIssue)
       • the_librarian:archive_viewer  (raw filesystem PDFs)

   Pipeline:
       1. PDF.js loads the document and rasterises every page onto an
          off-screen <canvas>, which is then exported as a JPEG data URL.
       2. StPageFlip (the `St.PageFlip` global from page-flip.browser.js)
          takes that array of image URLs and renders the flip-book UI,
          including its own drag/click-to-turn interactions and shadows.

   Pages are rendered with a small worker pool (not fully sequential, not
   all-at-once) so long issues don't block the main thread for too long in
   one go. A loading overlay with a progress bar covers the wait.

   Expects a container element:
       <div id="flipbook-root"
            data-pdf-url="…"
            data-start-page="0-indexed integer"></div>

   Optional DOM hooks (all safe to omit):
       #flipbook-loading            — overlay shown while pages render
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

    // Pin the pdf.js worker to the same version as the main script tag in
    // viewer.html. If you swap the CDN version there, update it here too.
    var PDFJS_WORKER_SRC = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';

    // Rendered width (px) per page canvas before JPEG export. StPageFlip
    // displays pages smaller than this on typical screens, so this is
    // mostly about keeping text/line-art crisp when the book is enlarged.
    var RENDER_WIDTH = 900;
    var JPEG_QUALITY  = 0.85;

    // How many pages to rasterise in parallel. Higher = faster overall
    // render, but more canvases alive briefly at once.
    var RENDER_CONCURRENCY = 4;

    // ── Responsive sizing constants ──────────────────────────────────────
    var STAGE_BOTTOM_MARGIN  = 24;  // px of breathing room reserved below the stage
    var STAGE_MIN_HEIGHT     = 320; // never let the stage collapse smaller than this
    var PAGE_GAP             = 20;  // px reserved below the book within the stage
    var RESIZE_DEBOUNCE_MS   = 200; // wait for resizing/rotation to settle before rebuilding

    // Comfortable single-page width range. MIN_PAGE_WIDTH is deliberately
    // a fixed number rather than something derived from the current
    // screen size — StPageFlip compares it against the container's actual
    // width (is there room for 2 * MIN_PAGE_WIDTH?) to decide whether to
    // show a two-page spread or fall back to one page at a time, so it
    // needs to stay constant for that comparison to mean anything.
    var MIN_PAGE_WIDTH        = 240;
    var DESIRED_MAX_PAGE_WIDTH = 640; // cap so pages don't get oversized on huge screens

    document.addEventListener('DOMContentLoaded', init);

    function init() {
        var root = document.getElementById('flipbook-root');
        if (!root) return;

        var pdfUrl    = root.dataset.pdfUrl;
        var startPage = parseInt(root.dataset.startPage, 10);
        if (isNaN(startPage) || startPage < 0) startPage = 0;

        if (!pdfUrl) {
            showError(root, 'No PDF URL was provided.');
            return;
        }
        if (typeof pdfjsLib === 'undefined') {
            showError(root, 'The PDF engine failed to load. Please refresh the page.');
            return;
        }
        if (typeof St === 'undefined' || !St.PageFlip) {
            showError(root, 'The page-flip viewer failed to load. Please refresh the page.');
            return;
        }

        pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_WORKER_SRC;

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

        // Populated once the PDF's first page has rendered; kept at this
        // scope (rather than local to the loading .then()) so a later
        // window-resize rebuild can reuse the already-rendered page
        // elements and aspect ratio without re-rasterising anything.
        var pageElements = [];
        var firstAspect  = 1;

        // This function will be defined later to allow dynamic re-sorting of the render queue
        var resortRenderQueue = null; 

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
                ? 'Page ' + shown + ' of ' + pageCount
                : 'Page ' + shown;
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
        // no PDF re-rendering, so this is cheap.
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

        // ── Rasterise a single PDF page to a JPEG data URL ──────────────
        function renderPageToImage(pdfDoc, pageNumber) {
            return pdfDoc.getPage(pageNumber).then(function (pdfPage) {
                var baseViewport = pdfPage.getViewport({ scale: 1 });
                var scale = RENDER_WIDTH / baseViewport.width;
                var viewport = pdfPage.getViewport({ scale: scale });

                var canvas = document.createElement('canvas');
                canvas.width = Math.round(viewport.width);
                canvas.height = Math.round(viewport.height);
                var ctx = canvas.getContext('2d');

                return pdfPage.render({ canvasContext: ctx, viewport: viewport }).promise
                    .then(function () {
                        var dataUrl = canvas.toDataURL('image/jpeg', JPEG_QUALITY);
                        var aspect = viewport.width / viewport.height;
                        // Release the canvas's backing store promptly —
                        // matters on long issues (60+ pages).
                        canvas.width = 0;
                        canvas.height = 0;
                        return { dataUrl: dataUrl, aspect: aspect };
                    });
            });
        }

        // ── Background Queue for rendering remaining pages ──────────────────
        // This replaces the old renderAllPages blocking mechanism.
        function startBackgroundRendering(pdfDoc, total, startPage, pageElements) {
            var queue = [];
            // Target the actual starting page (1-indexed for pdf.js)
            var initialRenderedPage = Math.max(1, Math.min(startPage + 1, total));
            
            // Queue all pages except the one we already rendered upfront
            for (var i = 1; i <= total; i++) {
                if (i !== initialRenderedPage) {
                    queue.push(i);
                }
            }

            // Expose a dynamic sorting function to prioritize pages closest to what the user is currently viewing
            resortRenderQueue = function(targetPage) {
                queue.sort(function(a, b) {
                    // targetPage from StPageFlip is 0-indexed, whereas our queue tracks 1-indexed pages.
                    return Math.abs((a - 1) - targetPage) - Math.abs((b - 1) - targetPage);
                });
            };

            // Perform initial sort based on where the flipbook opened
            resortRenderQueue(startPage);

            var completed = 1; // Start at 1 because we explicitly rendered the initial page
            setStatus('Rendering pages in background… (' + completed + ' / ' + total + ')');
            setProgress(completed, total);

            function pullNext() {
                if (queue.length === 0) return Promise.resolve();

                var pageNumber = queue.shift();
                return renderPageToImage(pdfDoc, pageNumber).then(function (result) {
                    var img = document.createElement('img');
                    img.src = result.dataUrl;
                    
                    // Ensure the image scales properly within the dynamically managed HTML containers
                    img.style.width = '100%';
                    img.style.height = '100%';
                    img.style.display = 'block'; // Prevents nasty inline baseline gaps

                    // Inject the rendered JPEG into our placeholder container
                    pageElements[pageNumber - 1].appendChild(img);

                    completed++;
                    // Update progress silently in the background
                    setStatus('Rendering pages in background… (' + completed + ' / ' + total + ')');
                    setProgress(completed, total);

                    return pullNext();
                }).catch(function (err) {
                    console.error('Failed to render page ' + pageNumber, err);
                    return pullNext(); // Ensure the queue doesn't completely halt on a single page error
                });
            }

            var workers = [];
            for (var j = 0; j < Math.min(RENDER_CONCURRENCY, queue.length); j++) {
                workers.push(pullNext());
            }

            return Promise.all(workers);
        }

        // ── Build the flip-book once every page image is ready ──────────
        // `startIndexOverride` is used on resize-triggered rebuilds to
        // reopen the book at whatever page the reader was already on,
        // instead of jumping back to the original startPage.
        function buildBook(pageElements, firstAspect, startIndexOverride) {
            // Re-measure the stage against the current viewport before
            // reading its size below — this is what actually fixes the
            // "bottom cut off" bug (see header comment for why).
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
                // mobile-sized viewport, matching the original StPageFlip
                // demo's behaviour. maxWidth/maxHeight are pinned to the
                // height-aware size solved above, so on wide-but-short
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
                // Re-prioritize the background rendering queue dynamically when the user flips pages!
                if (resortRenderQueue) {
                    resortRenderQueue(e.data);
                }
            });

            updateIndicator(clampedStart);
            updateNavButtons(clampedStart);

            // Hide the loading overlay immediately so the user can begin reading right away
            if (loadingEl) loadingEl.classList.add('is-hidden');
        }

        // ── Kick everything off ──────────────────────────────────────────
        // Size and center the stage immediately, before the PDF even starts
        // loading, so the loading overlay itself sits in the right spot
        // instead of jumping once the book is built.
        sizeStageToViewport();
        centerBookInStage();
        setStatus('Loading document…');

        pdfjsLib.getDocument(pdfUrl).promise.then(function (pdfDoc) {
            var total = pdfDoc.numPages;
            pageCount = total;
            updateIndicator(startPage);

            var initialPageToRender = Math.max(1, Math.min(startPage + 1, total));

            // Block the UI only for the first requested page to determine dimensions and unblock reading
            return renderPageToImage(pdfDoc, initialPageToRender).then(function (result) {
                // Assign (not re-declare) — these live at init()'s scope so
                // a later resize rebuild can reuse them.
                firstAspect = result.aspect;
                pageElements = [];

                // Create placeholder HTML wrappers for all pages
                for (var i = 0; i < total; i++) {
                    var pageDiv = document.createElement('div');
                    pageDiv.className = 'flipbook-page-wrapper';
                    // Add a white background so unrendered pages look like blank paper instead of being transparent
                    pageDiv.style.backgroundColor = '#ffffff';

                    // If this is the page we just rendered, populate its image immediately
                    if (i === initialPageToRender - 1) {
                        var img = document.createElement('img');
                        img.src = result.dataUrl;
                        img.style.width = '100%';
                        img.style.height = '100%';
                        img.style.display = 'block';
                        pageDiv.appendChild(img);
                    }

                    pageElements.push(pageDiv);
                    root.appendChild(pageDiv);
                }

                // Build the book instantly with the placeholders
                buildBook(pageElements, firstAspect);

                // Defer the remaining page processing to background workers
                startBackgroundRendering(pdfDoc, total, startPage, pageElements);
            });
        }).catch(function (err) {
            console.error('Flipbook viewer error:', err);
            showError(root, 'Could not load this PDF. It may be missing, corrupted, or blocked by your network.');
        });
    }

    function showError(root, message) {
        var loadingEl = document.getElementById('flipbook-loading');
        if (loadingEl) loadingEl.remove();
        root.innerHTML =
            '<div class="flipbook-error">' +
            '<p>' + message + '</p>' +
            '</div>';
    }
}());