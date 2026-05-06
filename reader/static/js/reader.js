/* ─────────────────────────────────────────────
   Icarus Reader Interactions
   ───────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', function() {
    
    // Subscription Cancellation Confirmation
    const cancelForm = document.getElementById('cancel-sub-form');
    if (cancelForm) {
        cancelForm.addEventListener('submit', function(e) {
            if (!confirm('Are you sure you want to cancel your subscription?')) {
                e.preventDefault();
            }
        });
    }

    // Account Deactivation Confirmation
    const deactivateForm = document.getElementById('deactivate-account-form');
    if (deactivateForm) {
        deactivateForm.addEventListener('submit', function(e) {
            if (!confirm('Are you sure you want to DEACTIVATE your account? This action will log you out immediately and disable your access.')) {
                e.preventDefault();
            }
        });
    }
});
