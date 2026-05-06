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

});
