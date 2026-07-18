(function () {
    const toggle   = document.getElementById('searchToggle');
    const overlay  = document.getElementById('searchOverlay');
    const closeBtn = document.getElementById('searchClose');
    const input    = document.getElementById('searchOverlayInput');
    const resultsList = document.getElementById('resultsList');
    const status      = document.getElementById('searchStatus');
    const spinner     = document.getElementById('searchSpinner');
    const results     = document.getElementById('searchResults');
    const t           = results ? results.dataset : {};

    let debounceTimer;
    // Tracks the in-flight request so a newer keystroke can cancel it —
    // otherwise fast typing stacks up overlapping embed+DB calls that all
    // run to completion even though only the last one's result matters.
    let currentSearchController = null;

    function openSearch() {
        overlay.classList.add('is-open');
        overlay.setAttribute('aria-hidden', 'false');
        document.body.style.overflow = 'hidden'; // Prevent scroll
        setTimeout(() => input.focus(), 300);
    }

    function closeSearch() {
        overlay.classList.remove('is-open');
        overlay.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = '';
    }

    function performSearch(query) {
        if (!query || query.length < 2) {
            if (currentSearchController) {
                currentSearchController.abort();
                currentSearchController = null;
            }
            resultsList.innerHTML = '';
            status.textContent = t.i18nMinChars;
            spinner.style.display = 'none';
            return;
        }

        // Cancel any in-flight search before starting a new one — its result
        // is stale the moment a newer keystroke fires.
        if (currentSearchController) {
            currentSearchController.abort();
        }
        const controller = new AbortController();
        currentSearchController = controller;

        spinner.style.display = 'block';
        status.textContent = t.i18nSearching;

        const url = `/librarian/api/search/?q=${encodeURIComponent(query)}&top_k=10&mode=hybrid`;

        fetch(url, { signal: controller.signal })
            .then(res => res.json())
            .then(data => {
                if (controller.signal.aborted) return;
                spinner.style.display = 'none';
                renderResults(data.results, query);
            })
            .catch(err => {
                if (err.name === 'AbortError') return; // superseded by a newer keystroke
                spinner.style.display = 'none';
                status.textContent = t.i18nError;
                console.error('Search error:', err);
            });
    }

    function renderResults(results, query) {
        if (!results || results.length === 0) {
            resultsList.innerHTML = '';
            status.textContent = `${t.i18nNoResults}: ${query}`;
            return;
        }

        status.textContent = `${t.i18nFound}: ${results.length} — ${query}`;

        resultsList.innerHTML = results.map(res => {
            const icon = getIcon(res.type);
            const title = highlightMatch(res.title, query);
            const snippet = highlightMatch(res.chunk_text, query);
            const url = getUrl(res);
            const typeLabel = typeLabelFor(res.type);

            return `
                <a href="${url}" class="search-result-item">
                    <div class="search-result-item__icon">
                        <i class="fas ${icon}"></i>
                    </div>
                    <div class="search-result-item__content">
                        <span class="search-result-item__title">${title}</span>
                        <span class="search-result-item__snippet">${snippet}</span>
                        <div class="search-result-item__meta">
                            <span class="search-result-item__badge">${typeLabel}</span>
                            ${res.page_number ? `<span class="search-result-item__page">${escapeHtml(t.i18nPage)} ${res.page_number}</span>` : ''}
                        </div>
                    </div>
                </a>
            `;
        }).join('');
    }

    // NOTE: original icon mapping was lost in the nesting accident —
    // reconstructed below; adjust the Font Awesome classes if yours differed.
    function getIcon(type) {
        switch (type) {
            case 'pdf':     return 'fa-file-pdf';
            case 'article': return 'fa-newspaper';
            case 'author':  return 'fa-user-pen';
            default:        return 'fa-file-lines';
        }
    }

    function typeLabelFor(type) {
        switch (type) {
            case 'pdf':     return t.i18nPdf;
            case 'article': return t.i18nArticle;
            case 'author':  return t.i18nAuthor;
            default:        return type;
        }
    }

    function getUrl(res) {
        if (res.type === 'pdf') {
            return `/librarian/viewer/${res.document_id}/?page=${res.page_number || 1}`;
        }
        return res.url || '#';
    }

    function highlightMatch(text, query) {
        if (!text || !query) return text;
        text = escapeHtml(text);
        const words = query.trim().split(/\s+/);
        let highlighted = text;

        words.forEach(word => {
            if (word.length < 2) return;
            const regex = new RegExp(`(${escapeRegex(word)})`, 'gi');
            highlighted = highlighted.replace(regex, '<mark>$1</mark>');
        });

        return highlighted;
    }
    function escapeHtml(s) {
        const d = document.createElement('div');
        d.textContent = s == null ? '' : s;
        return d.innerHTML;
    }
    function escapeRegex(s) {
        return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }

    input.addEventListener('input', (e) => {
        clearTimeout(debounceTimer);
        const query = e.target.value.trim();
        debounceTimer = setTimeout(() => performSearch(query), 300);
    });

    toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        openSearch();
    });

    closeBtn.addEventListener('click', closeSearch);

    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) closeSearch();
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeSearch();
    });
})();