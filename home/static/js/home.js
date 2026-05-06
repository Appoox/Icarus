document.addEventListener('DOMContentLoaded', () => {
    const wrapper = document.querySelector('.sg-issue-articles__scroll-wrapper');
    const grid = document.querySelector('.sg-issue-articles__grid');
    const cards = grid ? grid.querySelectorAll('.sg-issue-card') : [];
    const thumbs = document.querySelectorAll('.sg-issue-thumbnail');
    
    const totalCards = cards.length;
    if (wrapper && grid && totalCards >= 3) {
        const numArticles = totalCards / 3;
        
        let isVertical = window.innerWidth > 900;
        
        const updateOrientation = () => {
            isVertical = window.innerWidth > 900;
        };

        const getSectionSize = () => {
            if (cards.length >= numArticles * 2) {
                if (isVertical) {
                    return cards[numArticles].offsetTop - cards[0].offsetTop;
                } else {
                    return cards[numArticles].offsetLeft - cards[0].offsetLeft;
                }
            }
            return isVertical ? grid.scrollHeight / 3 : grid.scrollWidth / 3;
        };

        let sectionSize = getSectionSize();
        
        // Recalculate on resize
        window.addEventListener('resize', () => {
            updateOrientation();
            sectionSize = getSectionSize();
            initialCenter();
        });

        // Center on the middle (Original) section
        const initialCenter = () => {
            if (isVertical) {
                wrapper.scrollTop = sectionSize;
            } else {
                wrapper.scrollLeft = sectionSize;
            }
        };
        setTimeout(initialCenter, 50);

        // --- Thumbnail highlight logic (CENTERED) ---
        const updateActiveThumb = () => {
            if (!thumbs.length) return;
            const scrollPos = isVertical ? wrapper.scrollTop : wrapper.scrollLeft;
            const containerDim = isVertical ? wrapper.offsetHeight : wrapper.offsetWidth;
            
            // Calculate which card is currently in the center of the carousel
            const centerPos = scrollPos + containerDim / 2;
            
            // Normalize relative to the middle section (Original)
            const relativeCenter = (centerPos - sectionSize + sectionSize) % sectionSize;
            
            // Index calculation
            const activeIndex = Math.floor((relativeCenter / sectionSize) * numArticles);
            const safeIndex = (activeIndex + numArticles) % numArticles;
            
            thumbs.forEach((t, i) => {
                t.classList.toggle('is-active', i === safeIndex);
            });
        };

        // --- Consolidated Scroll Listener ---
        wrapper.addEventListener('scroll', () => {
            const scrollPos = isVertical ? wrapper.scrollTop : wrapper.scrollLeft;
            
            // Infinite loop jump-sync
            if (scrollPos < sectionSize) {
                // Scrolled into first clone set -> Jump forward
                if (isVertical) wrapper.scrollTop += sectionSize;
                else wrapper.scrollLeft += sectionSize;
            } 
            else if (scrollPos >= sectionSize * 2) {
                // Scrolled into third clone set -> Jump backward
                if (isVertical) wrapper.scrollTop -= sectionSize;
                else wrapper.scrollLeft -= sectionSize;
            }
            
            updateActiveThumb();
        }, { passive: true });

        // Auto-scroll logic
        let isPaused = false;
        let pauseTimeout;
        const scrollSpeed = 0.6; // Adjust speed

        const autoScroll = () => {
            if (!isPaused) {
                if (isVertical) {
                    wrapper.scrollTop += scrollSpeed;
                } else {
                    wrapper.scrollLeft += scrollSpeed;
                }
            }
            requestAnimationFrame(autoScroll);
        };

        const pauseAutoScroll = () => {
            isPaused = true;
            wrapper.style.scrollSnapType = isVertical ? 'y mandatory' : 'x mandatory';
            clearTimeout(pauseTimeout);
        };

        const resumeAutoScroll = () => {
            clearTimeout(pauseTimeout);
            pauseTimeout = setTimeout(() => {
                isPaused = false;
                wrapper.style.scrollSnapType = 'none';
            }, 3000); 
        };

        // Thumbnail Click Interaction
        thumbs.forEach((thumb, i) => {
            thumb.addEventListener('click', () => {
                pauseAutoScroll();
                const targetCard = cards[numArticles + i];
                let targetScroll;
                
                if (isVertical) {
                    targetScroll = targetCard.offsetTop - (wrapper.offsetHeight - targetCard.offsetHeight) / 2;
                    wrapper.scrollTo({ top: targetScroll, behavior: 'smooth' });
                } else {
                    targetScroll = targetCard.offsetLeft - (wrapper.offsetWidth - targetCard.offsetWidth) / 2;
                    wrapper.scrollTo({ left: targetScroll, behavior: 'smooth' });
                }
                
                resumeAutoScroll();
            });
        });

        // Interaction Listeners
        wrapper.addEventListener('touchstart', pauseAutoScroll, { passive: true });
        wrapper.addEventListener('touchend', resumeAutoScroll, { passive: true });
        wrapper.addEventListener('mousedown', pauseAutoScroll);
        wrapper.addEventListener('mouseup', resumeAutoScroll);

        // Start auto-scroll
        wrapper.style.scrollSnapType = 'none';
        requestAnimationFrame(autoScroll);

        // Initialize thumbnails
        updateActiveThumb();
    }
});
