/* ─────────────────────────────────────────────
   Article Page Interactions
   Consolidated JS for Language Switcher, Tracking, and Favorites
───────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', function() {
    
    // ── LANGUAGE SLIDER ──
    const slider = document.querySelector('.lang-slider');
    if (slider) {
        const items = slider.querySelectorAll('.lang-slider__item');
        const indicator = slider.querySelector('.lang-slider__indicator');
        const langContents = document.querySelectorAll('.lang-content');

        function updateSlider(activeItem) {
            indicator.style.width = activeItem.offsetWidth + 'px';
            indicator.style.left = activeItem.offsetLeft + 'px';

            items.forEach(item => item.classList.remove('active'));
            activeItem.classList.add('active');

            const target = activeItem.getAttribute('data-lang-target');
            langContents.forEach(content => {
                if (content.getAttribute('data-lang') === target) {
                    content.classList.add('active');
                } else {
                    content.classList.remove('active');
                }
            });
        }

        const initialActive = slider.querySelector('.lang-slider__item.active');
        if (initialActive) {
            setTimeout(() => updateSlider(initialActive), 50);
        }

        items.forEach(item => {
            item.addEventListener('click', function() {
                updateSlider(this);
                const header = document.querySelector('.article-header');
                if (window.scrollY > header.offsetTop + 100) {
                    header.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            });
        });

        window.addEventListener('resize', () => {
            const currentActive = slider.querySelector('.lang-slider__item.active');
            if (currentActive) updateSlider(currentActive);
        });
    }

    // ── SCROLL TRACKING ──
    let readTracked = false;
    const articleMeta = document.querySelector('meta[name="article-id"]');
    const articleId = articleMeta ? articleMeta.content : null;
    const trackUrl = "/articles/track-read-fully/";
    const csrfInput = document.querySelector('[name=csrfmiddlewaretoken]');
    const csrfToken = csrfInput ? csrfInput.value : '';

    if (articleId) {
        window.addEventListener('scroll', function() {
            if (readTracked) return;

            const scrollHeight = document.documentElement.scrollHeight;
            const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
            const clientHeight = document.documentElement.clientHeight;
            const scrollPct = (scrollTop + clientHeight) / scrollHeight;

            if (scrollPct > 0.9) {
                readTracked = true;
                const formData = new FormData();
                formData.append('article_id', articleId);
                
                fetch(trackUrl, {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': csrfToken,
                        'X-Requested-With': 'XMLHttpRequest'
                    },
                    body: formData
                })
                .then(response => response.json())
                .then(data => { console.log('Read tracking:', data.message); })
                .catch(error => { console.error('Error tracking read:', error); });
            }
        }, { passive: true });
    }

    // ── FAVORITE TOGGLE ──
    document.addEventListener('click', function(e) {
        const btn = e.target.closest('.favorite-btn');
        if (!btn) return;
        
        e.preventDefault();
        
        const id = btn.getAttribute('data-id');
        const type = btn.getAttribute('data-type');
        const url = type === 'article' ? `/reader/favorite/article/${id}/` : `/reader/favorite/issue/${id}/`;
        const token = csrfInput ? csrfInput.value : '';
        
        fetch(url, {
            method: 'POST',
            headers: {
                'X-CSRFToken': token,
                'X-Requested-With': 'XMLHttpRequest'
            }
        })
        .then(response => {
            if (!response.ok) throw new Error('Server returned ' + response.status);
            return response.json();
        })
        .then(data => {
            if (data.favorited !== undefined) {
                btn.setAttribute('data-favorited', data.favorited);
                btn.title = data.favorited ? 'Remove from Favorites' : 'Add to Favorites';
            } else if (data.error) {
                alert(data.error);
            }
        })
        .catch(error => {
            console.error('Error toggling favorite:', error);
            alert('Failed to update favorite: ' + error.message);
        });
    });

    // ── MEDIA PLAYERS (AUTO-INIT) ──
    function initAllPlayers() {
        // Audio Players
        document.querySelectorAll('.modern-audio-player').forEach(playerContainer => {
            const audio = playerContainer.querySelector('audio');
            const playBtn = playerContainer.querySelector('.play-btn');
            const slider = playerContainer.querySelector('.progress-slider');
            const fill = playerContainer.querySelector('.progress-bar-fill');
            const current = playerContainer.querySelector('.time-current') || playerContainer.querySelector('[id^="current-"]');
            const duration = playerContainer.querySelector('.time-duration') || playerContainer.querySelector('[id^="duration-"]');
            const muteBtn = playerContainer.querySelector('.mute-btn');
            
            if (!audio || !playBtn) return;
            
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
                    if (playIcon) playIcon.style.display = 'none';
                    if (pauseIcon) pauseIcon.style.display = 'block';
                } else {
                    audio.pause();
                    if (playIcon) playIcon.style.display = 'block';
                    if (pauseIcon) pauseIcon.style.display = 'none';
                }
            });

            audio.addEventListener('loadedmetadata', () => {
                if (duration) duration.textContent = formatTime(audio.duration);
            });

            audio.addEventListener('timeupdate', () => {
                const pct = (audio.currentTime / audio.duration) * 100;
                if (slider) slider.value = pct;
                if (fill) fill.style.width = pct + '%';
                if (current) current.textContent = formatTime(audio.currentTime);
            });

            if (slider) {
                slider.addEventListener('input', () => {
                    const time = (slider.value / 100) * audio.duration;
                    audio.currentTime = time;
                    if (fill) fill.style.width = slider.value + '%';
                });
            }

            if (muteBtn) {
                muteBtn.addEventListener('click', () => {
                    audio.muted = !audio.muted;
                    muteBtn.style.opacity = audio.muted ? '0.5' : '1';
                });
            }
        });

        // Video Players
        document.querySelectorAll('.modern-video-player').forEach(vPlayer => {
            const video = vPlayer.querySelector('video');
            const playBtn = vPlayer.querySelector('.v-play-btn');
            const bigPlayBtn = vPlayer.querySelector('.big-play-btn');
            const overlay = vPlayer.querySelector('.video-overlay');
            const slider = vPlayer.querySelector('.v-progress-slider');
            const fill = vPlayer.querySelector('.v-progress-fill');
            
            if (!video) return;

            function togglePlay() {
                if (video.paused) {
                    video.play();
                    if (overlay) overlay.style.opacity = '0';
                    const pIcon = playBtn?.querySelector('.v-play-icon');
                    const paIcon = playBtn?.querySelector('.v-pause-icon');
                    if (pIcon) pIcon.style.display = 'none';
                    if (paIcon) paIcon.style.display = 'block';
                } else {
                    video.pause();
                    if (overlay) overlay.style.opacity = '1';
                    const pIcon = playBtn?.querySelector('.v-play-icon');
                    const paIcon = playBtn?.querySelector('.v-pause-icon');
                    if (pIcon) pIcon.style.display = 'block';
                    if (paIcon) paIcon.style.display = 'none';
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
        });
    }

    initAllPlayers();

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

    // ── RELATED TOPIC CLICKS ──
    document.querySelectorAll('.related-card__tag').forEach(tag => {
        tag.addEventListener('click', function(e) {
            const url = this.getAttribute('data-topic-url');
            if (url) {
                e.preventDefault();
                e.stopPropagation();
                window.location.href = url;
            }
        });
    });
});

