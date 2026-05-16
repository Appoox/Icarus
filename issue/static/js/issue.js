/* ─────────────────────────────────────────────
   Issue Page Interactions
   Consolidated JS for Players, YouTube, and Favorites
───────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', function() {
    
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
            
            // After transition, set to none to handle dynamic content or resizing
            setTimeout(() => {
                content.style.maxHeight = 'none';
            }, 650);
        });
    });
});
