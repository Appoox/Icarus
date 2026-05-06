/* ─────────────────────────────────────────────
   Icarus Admin Utilities
   ───────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', function() {
    
    // Print functionality for Print Subscribers page
    const printBtn = document.getElementById('print-list-btn');
    if (printBtn) {
        printBtn.addEventListener('click', function() {
            window.print();
        });
    }

    // Close window functionality
    const closeBtn = document.getElementById('close-window-btn');
    if (closeBtn) {
        closeBtn.addEventListener('click', function() {
            window.close();
        });
    }
});
