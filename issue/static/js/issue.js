/* ─────────────────────────────────────────────
   Issue Page Interactions
   Consolidated JS for Players, YouTube, and Favorites
───────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', function() {

    // ── AUTOMATIC HEADER CONTRAST ANALYSIS ──
    // This function analyzes the issue cover image to determine if it is predominantly dark.
    // If the image is dark, it applies a contrast-boosting dark class to the parent header container.
    // By manipulating classes and using specific properties, it avoids wiping out background-image rules.
    function initHeaderContrastAnalysis() {
        const header = document.querySelector('.issue-page-header');
        const coverImg = document.querySelector('.issue-page-header__cover img');

        // Exit early if the elements do not exist on the current page
        if (!header || !coverImg) return;

        // Core analysis routine that reads pixel data via canvas
        function analyzeLuminance() {
            try {
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                if (!ctx) return;

                // Scale down dimensions significantly to keep processing fast and efficient
                canvas.width = 40;
                canvas.height = 60;

                // Render the cover image into the miniature context bounds
                ctx.drawImage(coverImg, 0, 0, canvas.width, canvas.height);

                // Fetch raw RGBA pixel arrays from the canvas environment
                const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
                const data = imageData.data;

                let totalLuminance = 0;
                let validPixels = 0;

                // Iterate through pixel data chunks (each pixel takes 4 indices: R, G, B, A)
                for (let i = 0; i < data.length; i += 4) {
                    const r = data[i];
                    const g = data[i + 1];
                    const b = data[i + 2];
                    const a = data[i + 3];

                    // Ignore fully or heavily transparent pixels to ensure color accuracy
                    if (a < 128) continue;

                    // Apply standard YUV/HSP weights to calculate perceived brightness
                    const luminance = 0.299 * r + 0.587 * g + 0.114 * b;
                    totalLuminance += luminance;
                    validPixels++;
                }

                // Fallback exit if no opaque pixels were analyzed
                if (validPixels === 0) return;

                const averageLuminance = totalLuminance / validPixels;

                // If average brightness drops below a mid-range threshold (125), trigger dark styling
                if (averageLuminance < 125) {
                    header.classList.add('header-contrast--dark-bg');
                } else {
                    header.classList.remove('header-contrast--dark-bg');
                }
            } catch (error) {
                // Gracefully log exceptions if images are served across distinct domains violating CORS controls
                console.warn('Header contrast analysis skipped due to cross-origin security constraints:', error);
            }
        }

        // Execute immediately if the image is cached and complete, otherwise listen for its load completion
        if (coverImg.complete) {
            analyzeLuminance();
        } else {
            coverImg.addEventListener('load', analyzeLuminance);
        }
    }

    // Invoke the analysis configuration sequence
    initHeaderContrastAnalysis();
    
    // ── AUDIO PLAYERS ──
    function initAudioPlayer(playerId) {
        const player = document.getElementById('player-' + playerId);
        if (!player) return;

        const audio = document.getElementById('audio-' + playerId);
        const playBtn = document.getElementById('play-btn-' + playerId);
        const slider = document.getElementById('slider-' + playerId);
        const fill = document.getElementById('fill-' + playerId);
        const current = document.getElementById('current-' + playerId);
        const duration = document.getElementById('duration-' + playerId);
        const playIcon = playBtn.querySelector('.play-icon');
        const pauseIcon = playBtn.querySelector('.pause-icon');

        function formatTime(seconds) {
            const mins = Math.floor(seconds / 60);
            const secs = Math.floor(seconds % 60);
            return mins + ':' + (secs < 10 ? '0' : '') + secs;
        }

        playBtn.addEventListener('click', () => {
            if (audio.paused) {
                audio.play();
                playIcon.style.display = 'none';
                pauseIcon.style.display = 'block';
            } else {
                audio.pause();
                playIcon.style.display = 'block';
                pauseIcon.style.display = 'none';
            }
        });

        audio.addEventListener('loadedmetadata', () => {
            duration.textContent = formatTime(audio.duration);
        });

        audio.addEventListener('timeupdate', () => {
            const pct = (audio.currentTime / audio.duration) * 100;
            slider.value = pct;
            fill.style.width = pct + '%';
            current.textContent = formatTime(audio.currentTime);
        });

        slider.addEventListener('input', () => {
            const time = (slider.value / 100) * audio.duration;
            audio.currentTime = time;
            fill.style.width = slider.value + '%';
        });
    }

    initAudioPlayer('main');
    initAudioPlayer('embed');

    // ── VIDEO PLAYERS ──
    function initVideoPlayer(playerId) {
        const video = document.getElementById('video-' + playerId);
        if (!video) return;

        const playBtn = document.getElementById('v-play-' + playerId);
        const bigPlayBtn = document.getElementById('big-play-' + playerId);
        const overlay = document.getElementById('overlay-' + playerId);
        const slider = document.getElementById('v-slider-' + playerId);
        const fill = document.getElementById('v-fill-' + playerId);
        
        let playIcon, pauseIcon;
        if (playBtn) {
            playIcon = playBtn.querySelector('.v-play-icon');
            pauseIcon = playBtn.querySelector('.v-pause-icon');
        }

        function togglePlay() {
            if (video.paused) {
                video.play();
                if (playIcon) playIcon.style.display = 'none';
                if (pauseIcon) pauseIcon.style.display = 'block';
                if (overlay) overlay.style.opacity = '0';
            } else {
                video.pause();
                if (playIcon) playIcon.style.display = 'block';
                if (pauseIcon) pauseIcon.style.display = 'none';
                if (overlay) overlay.style.opacity = '1';
            }
        }

        if (playBtn) playBtn.addEventListener('click', togglePlay);
        if (bigPlayBtn) bigPlayBtn.addEventListener('click', togglePlay);
        video.addEventListener('click', togglePlay);

        video.addEventListener('timeupdate', () => {
            const pct = (video.currentTime / video.duration) * 100;
            if (slider) slider.value = pct;
            if (fill) fill.style.width = pct + '%';
        });

        if (slider) {
            slider.addEventListener('input', () => {
                const time = (slider.value / 100) * video.duration;
                video.currentTime = time;
                if (fill) fill.style.width = slider.value + '%';
            });
        }
    }

    initVideoPlayer('main');
    initVideoPlayer('embed');

    // ── YOUTUBE FALLBACK ──
    document.querySelectorAll('.youtube-fallback').forEach(container => {
        const url = container.dataset.url;
        const regExp = /^.*(youtu.be\/|v\/|u\/\w\/|embed\/|watch\?v=|\&v=)([^#\&\?]*).*/;
        const match = url.match(regExp);
        const id = (match && match[2].length == 11) ? match[2] : null;

        if (id) {
            container.innerHTML = `<iframe style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; border: 0;" src="https://www.youtube.com/embed/${id}" title="YouTube video player" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>`;
        }
    });

    // ── FAVORITE BUTTON ──
    const favoriteBtn = document.querySelector('.favorite-btn');
    if (favoriteBtn) {
        favoriteBtn.addEventListener('click', function() {
            const id = this.getAttribute('data-id');
            const type = this.getAttribute('data-type');
            const url = type === 'article' ? `/reader/favorite/article/${id}/` : `/reader/favorite/issue/${id}/`;
            const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]') ? document.querySelector('[name=csrfmiddlewaretoken]').value : '';
            
            fetch(url, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': csrfToken,
                    'X-Requested-With': 'XMLHttpRequest'
                }
            })
            .then(response => {
                if (!response.ok) throw new Error('Server returned ' + response.status);
                return response.json();
            })
            .then(data => {
                if (data.favorited !== undefined) {
                    this.setAttribute('data-favorited', data.favorited);
                    this.title = data.favorited ? 'Remove from Favorites' : 'Add to Favorites';
                } else if (data.error) {
                    alert(data.error);
                }
            })
            .catch(error => {
                console.error('Error toggling favorite:', error);
                alert('Failed to update favorite. Please check if you are logged in.');
            });
        });
    }

    // ── EDITORIAL BOARD MODAL TOGGLE ──
    const boardToggle = document.querySelector('.editorial-board-btn');
    const boardContent = document.getElementById('issue-editorial-board-content');
    if (boardToggle && boardContent) {
        const closeBtn = document.querySelector('.issue-editorial-board-close-btn');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => {
                boardContent.close();
            });
        }

        boardContent.addEventListener('click', function(e) {
            const dialogBox = boardContent.getBoundingClientRect();
            const clickedInDialogBox = (
                e.clientY >= dialogBox.top &&
                e.clientY <= dialogBox.top + dialogBox.height &&
                e.clientX >= dialogBox.left &&
                e.clientX <= dialogBox.left + dialogBox.width
            );
            if (!clickedInDialogBox) {
                boardContent.close();
            }
        });

        boardContent.addEventListener('close', () => {
            boardToggle.setAttribute('aria-expanded', 'false');
        });

        boardToggle.addEventListener('click', () => {
            boardToggle.setAttribute('aria-expanded', 'true');
            boardContent.showModal();
        });
    }

    // ── EXPANDABLE SECTIONS ──
    document.querySelectorAll('.expandable-wrapper').forEach(wrapper => {
        const content = wrapper.querySelector('.expandable-content');
        const banner = wrapper.querySelector('.show-more-banner');
        const expandHeight = parseInt(wrapper.dataset.expandHeight) || 400;

        // Set initial max-height
        content.style.maxHeight = expandHeight + 'px';

        // Check if content actually overflows
        function checkOverflow() {
            if (content.scrollHeight <= expandHeight + 20) { // small buffer
                banner.style.display = 'none';
                content.style.maxHeight = 'none';
            } else {
                banner.style.display = 'flex';
            }
        }

        // Run on load and on resize
        setTimeout(checkOverflow, 200);
        window.addEventListener('resize', checkOverflow);

        banner.addEventListener('click', () => {
            wrapper.classList.add('expanded');
            content.style.maxHeight = content.scrollHeight + 'px';
            setTimeout(() => { content.style.maxHeight = 'none'; }, 650);
        });
    });

    // ── SYNC EDITORIAL FADE HEIGHT TO TABLE OF CONTENTS ──
    let syncTocDebounce;

    function syncEditorialToToc() {
        const tocCol  = document.querySelector('.editorial-toc-col');
        const wrapper = document.querySelector('.editorial-content-col .expandable-wrapper');
        if (!tocCol || !wrapper) return;
        if (wrapper.classList.contains('expanded')) return;

        const content = wrapper.querySelector('.expandable-content');
        const banner  = wrapper.querySelector('.show-more-banner');
        if (!content || !banner) return;

        const isDesktop = window.innerWidth > 900;

        if (!isDesktop) {
            const fallback = parseInt(wrapper.dataset.expandHeight) || 400;
            content.style.maxHeight = fallback + 'px';
            banner.style.display    = content.scrollHeight > fallback + 20 ? 'flex' : 'none';
            return;
        }

        const h = tocCol.offsetHeight;
        if (h < 80) return;

        content.style.maxHeight = h + 'px';

        if (content.scrollHeight <= h + 20) {
            banner.style.display    = 'none';
            content.style.maxHeight = 'none';
        } else {
            banner.style.display = 'flex';
        }
    }

    setTimeout(syncEditorialToToc, 350);

    window.addEventListener('resize', () => {
        clearTimeout(syncTocDebounce);
        syncTocDebounce = setTimeout(syncEditorialToToc, 180);
    });

});