(function () {
    const toggle   = document.getElementById('searchToggle');
    const overlay  = document.getElementById('searchOverlay');
    const closeBtn = document.getElementById('searchClose');
    const input    = document.getElementById('searchOverlayInput');
    const resultsList = document.getElementById('resultsList');
    const status      = document.getElementById('searchStatus');
    const spinner     = document.getElementById('searchSpinner');

    let debounceTimer;

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
            resultsList.innerHTML = '';
            status.textContent = 'Type at least 2 characters...';
            spinner.style.display = 'none';
            return;
        }

        spinner.style.display = 'block';
        status.textContent = 'Searching across archive, articles, and authors...';

        const url = `/librarian/api/search/?q=${encodeURIComponent(query)}&top_k=10&mode=hybrid`;

        fetch(url)
            .then(res => res.json())
            .then(data => {
                spinner.style.display = 'none';
                renderResults(data.results, query);
            })
            .catch(err => {
                spinner.style.display = 'none';
                status.textContent = 'Error fetching results. Please try again.';
                console.error('Search error:', err);
            });
    }

    function renderResults(results, query) {
        if (!results || results.length === 0) {
            resultsList.innerHTML = '';
            status.textContent = `No results found for "${query}"`;
            return;
        }

        status.textContent = `Found ${results.length} results for "${query}"`;
        
        resultsList.innerHTML = results.map(res => {
            const icon = getIcon(res.type);
            const title = highlightMatch(res.title, query);
            const snippet = highlightMatch(res.chunk_text, query);
            const url = getUrl(res);
            const typeLabel = res.type.charAt(0).toUpperCase() + res.type.slice(1);

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
                            ${res.page_number ? `<span class="search-result-item__page">Page ${res.page_number}</span>` : ''}
                        </div>
                    </div>
                </a>
            `;
        }).join('');
    }

    function getIcon(type) {
        switch(type) {
            case 'pdf': return 'fa-file-pdf';
            case 'article': return 'fa-newspaper';
            case 'author': return 'fa-user-circle';
            default: return 'fa-search';
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
        const words = query.trim().split(/\s+/);
        let highlighted = text;
        
        words.forEach(word => {
            if (word.length < 2) return;
            const regex = new RegExp(`(${word})`, 'gi');
            highlighted = highlighted.replace(regex, '<mark>$1</mark>');
        });
        
        return highlighted;
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
