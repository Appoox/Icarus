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
        
        // This function will be defined later to allow dynamic re-sorting of the render queue
        var resortRenderQueue = null; 

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
        function buildBook(pageElements, firstAspect) {
            var stage = root.parentElement;
            var stageWidth  = stage ? stage.clientWidth  : 900;
            var stageHeight = stage ? stage.clientHeight : 640;

            var pageWidth  = Math.max(240, Math.min(560, Math.floor(stageWidth / 2) - 20));
            var pageHeight = Math.floor(pageWidth / firstAspect);
            if (pageHeight > stageHeight - 20) {
                pageHeight = stageHeight - 20;
                pageWidth = Math.floor(pageHeight * firstAspect);
            }

            var clampedStart = Math.max(0, Math.min(startPage, pageElements.length - 1));

            pageFlip = new St.PageFlip(root, {
                width: pageWidth,
                height: pageHeight,
                size: 'stretch',
                minWidth: 240,
                maxWidth: 900,
                minHeight: 320,
                maxHeight: 1200,
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
        setStatus('Loading document…');

        pdfjsLib.getDocument(pdfUrl).promise.then(function (pdfDoc) {
            var total = pdfDoc.numPages;
            pageCount = total;
            updateIndicator(startPage);

            var initialPageToRender = Math.max(1, Math.min(startPage + 1, total));

            // Block the UI only for the first requested page to determine dimensions and unblock reading
            return renderPageToImage(pdfDoc, initialPageToRender).then(function (result) {
                var firstAspect = result.aspect; 
                var pageElements = [];

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