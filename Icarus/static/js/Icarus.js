/* Icarus — Global Interactions */

document.addEventListener('DOMContentLoaded', function() {
    
    // ── Django Messages Close Button ──
    const messageCloseButtons = document.querySelectorAll('.message__close');
    messageCloseButtons.forEach(button => {
        button.addEventListener('click', function() {
            const message = this.closest('.message');
            if (message) {
                message.style.opacity = '0';
                message.style.transform = 'translateY(-10px)';
                message.style.transition = 'all 0.3s ease';
                setTimeout(() => {
                    message.remove();
                }, 300);
            }
        });
    });

    // ── Floating Action Buttons ──
    const goBackButton = document.getElementById('goBackButton');
    const goToTopButton = document.getElementById('goToTopButton');

    if (goBackButton) {
        goBackButton.addEventListener('click', function(e) {
            e.preventDefault();
            if (document.referrer && document.referrer.includes(window.location.host)) {
                window.history.back();
            } else {
                window.location.href = '/';
            }
        });

        let mouseTimeout;
        const resetMouseTimeout = () => {
            goBackButton.classList.remove('idle-hidden');
            clearTimeout(mouseTimeout);
            mouseTimeout = setTimeout(() => {
                goBackButton.classList.add('idle-hidden');
            }, 2000); // 2 seconds of inactivity
        };

        // Listen for mouse and touch movements
        document.addEventListener('mousemove', resetMouseTimeout);
        document.addEventListener('touchstart', resetMouseTimeout);
        document.addEventListener('touchmove', resetMouseTimeout);

        // Initialize the timer
        resetMouseTimeout();
    }

    if (goToTopButton) {
        window.addEventListener('scroll', function() {
            if (window.scrollY > 300) {
                goToTopButton.classList.add('visible');
            } else {
                goToTopButton.classList.remove('visible');
            }
        });

        goToTopButton.addEventListener('click', function() {
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });
    }

        const floatingContainer = document.querySelector('.floating-action-buttons');
        if (floatingContainer && window.location.pathname !== '/') {
            const homeBtn = document.createElement('a');
            homeBtn.href = '/';
            homeBtn.className = 'floating-btn go-home-btn';
            homeBtn.title = 'Home';
            homeBtn.innerHTML = '<i class="fas fa-home"></i>';
            homeBtn.style.textDecoration = 'none';
            homeBtn.style.justifyContent = 'center';
            floatingContainer.insertBefore(homeBtn, floatingContainer.firstChild);
        }

});
