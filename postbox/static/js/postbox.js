document.addEventListener("DOMContentLoaded", function () {
    'use strict';

    /* ── Element references ──────────────────────────── */
    var typeInput   = document.getElementById('id_feedback_type');
    var ratingInput = document.getElementById('id_rating');
    var textarea    = document.querySelector('textarea[name="feedback"]');
    var charCount   = document.getElementById('fb-char-count');
    var starLabel   = document.getElementById('fb-star-label');

    document.getElementById('image-upload').addEventListener('change', function() {
    var fileName = this.files[0] ? this.files[0].name : 'Choose File';
    document.querySelector('.custom-browse-btn span').textContent = fileName;
    });

    /* ── Type selector ───────────────────────────────── */
    document.querySelectorAll('.fb-type-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            document.querySelectorAll('.fb-type-btn').forEach(function (b) {
                b.classList.remove('active');
                b.setAttribute('aria-pressed', 'false');
            });
            btn.classList.add('active');
            btn.setAttribute('aria-pressed', 'true');
            if (typeInput) typeInput.value = btn.getAttribute('data-type');
        });
    });

    /* ── Star rating ─────────────────────────────────── */
    var stars = Array.from(document.querySelectorAll('.fb-star'));
    var currentRating = 0;
    var LABELS = { 1: 'Poor', 2: 'Fair', 3: 'Good', 4: 'Very good', 5: 'Excellent' };

    function paintStars(n) {
        stars.forEach(function (s, i) { s.classList.toggle('active', i < n); });
        if (starLabel) starLabel.textContent = n ? LABELS[n] : '';
    }

    stars.forEach(function (star, idx) {
        star.addEventListener('click', function () {
            // Clicking the active star again deselects it
            if (currentRating === idx + 1) {
                currentRating = 0;
            } else {
                currentRating = idx + 1;
            }
            paintStars(currentRating);
            if (ratingInput) ratingInput.value = currentRating || '';
        });
        star.addEventListener('mouseenter', function () { paintStars(idx + 1); });
        star.addEventListener('mouseleave', function () { paintStars(currentRating); });
    });

    /* ── Character counter ───────────────────────────── */
    if (textarea && charCount) {
        function updateCount() {
            var len = textarea.value.length;
            charCount.textContent = len;
            charCount.closest('.fb-char-count').classList.toggle(
                'fb-char-count--warning', len > 1800
            );
        }
        textarea.addEventListener('input', updateCount);
        updateCount();
    }

});