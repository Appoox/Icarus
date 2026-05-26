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

    // Renew Subscription Options Toggle
    const renewToggleBtn = document.getElementById('btn-renew-toggle');
    const renewPlansContainer = document.getElementById('renew-plans-container');
    if (renewToggleBtn && renewPlansContainer) {
        renewToggleBtn.addEventListener('click', function() {
            if (renewPlansContainer.style.display === 'none') {
                renewPlansContainer.style.display = 'block';
            } else {
                renewPlansContainer.style.display = 'none';
            }
        });
    }

    // Comment body expand / collapse
    document.querySelectorAll('[data-comment-toggle]').forEach(function(btn) {
        var bodyEl = btn.closest('.comment-item__body-wrap').querySelector('.comment-item__body');
        var lessLabel = btn.querySelector('.toggle-less');
        var moreLabel = btn.querySelector('.toggle-more');

        btn.addEventListener('click', function() {
            var expanded = bodyEl.classList.toggle('is-expanded');
            lessLabel.classList.toggle('hidden', !expanded);
            moreLabel.classList.toggle('hidden', expanded);
        });
    });

});