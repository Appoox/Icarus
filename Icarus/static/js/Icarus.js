/* Icarus — Global Interactions */

document.addEventListener('DOMContentLoaded', function() {
    
    // ── Django Messages — Floating Toasts ──
    function dismissToast(msg) {
        msg.classList.add('is-dismissing');
        msg.addEventListener('animationend', () => msg.remove(), { once: true });
    }

    document.querySelectorAll('.message').forEach(msg => {
        const btn = msg.querySelector('.message__close');
        if (btn) btn.addEventListener('click', () => dismissToast(msg));

        // Warnings linger longer
        const delay = msg.classList.contains('message--warning') ? 9000 : 6000;
        let timer = setTimeout(() => dismissToast(msg), delay);

        msg.addEventListener('mouseenter', () => clearTimeout(timer));
        msg.addEventListener('mouseleave', () => {
            timer = setTimeout(() => dismissToast(msg), 3000);
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
            homeBtn.title = floatingContainer.dataset.homeLabel || 'Home';
            homeBtn.innerHTML = '<i class="fas fa-home"></i>';
            homeBtn.style.textDecoration = 'none';
            homeBtn.style.justifyContent = 'center';
            floatingContainer.insertBefore(homeBtn, floatingContainer.firstChild);
        }

});