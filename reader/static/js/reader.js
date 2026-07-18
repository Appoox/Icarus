/* ─────────────────────────────────────────────
   Icarus Reader Interactions
   ───────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', function() {

    document.querySelectorAll('form[data-confirm]').forEach(function(form) {
        form.addEventListener('submit', function(e) {
            if (!window.confirm(form.dataset.confirm)) {
                e.preventDefault();
            }
        });
    });

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

    // Avatar live preview (edit_profile)
    var avatarInput = document.getElementById('id_avatar_upload');
    var avatarPreview = document.getElementById('avatar-preview-img');
    if (avatarInput && avatarPreview) {
        avatarInput.addEventListener('change', function() {
            var file = avatarInput.files && avatarInput.files[0];
            if (!file || !file.type || file.type.indexOf('image/') !== 0) {
                return;
            }

            var url = URL.createObjectURL(file);

            // With no existing avatar the preview slot is an initials
            // placeholder <div>; swap it for an <img> so preview works
            // in both states.
            if (avatarPreview.tagName !== 'IMG') {
                var img = document.createElement('img');
                img.id = 'avatar-preview-img';
                img.className = 'profile-avatar';
                img.alt = '';
                avatarPreview.replaceWith(img);
                avatarPreview = img;
            }

            avatarPreview.addEventListener('load', function() {
                URL.revokeObjectURL(url);
            }, { once: true });
            avatarPreview.src = url;

            // Choosing a new file supersedes "remove current image"
            var removeBox = document.getElementById('id_remove_avatar');
            if (removeBox) {
                removeBox.checked = false;
            }
        });
    }

});