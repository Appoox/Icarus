/* ─────────────────────────────────────────────────────────────
   footer.js  —  load this globally in base.html
   Handles the editorial board accordion in the site footer.
   Kept separate from issue.js so it works on every page.
───────────────────────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', function () {

    const boardToggle  = document.querySelector('.editorial-board-btn');
    const boardContent = document.getElementById('editorial-board-content');

    if (!boardToggle || !boardContent) return;

    boardToggle.addEventListener('click', function () {
        const isExpanded = boardToggle.getAttribute('aria-expanded') === 'true';

        // Toggle aria state
        boardToggle.setAttribute('aria-expanded', String(!isExpanded));

        // Toggle visible class (CSS handles the grid-template-rows animation)
        boardContent.classList.toggle('is-expanded', !isExpanded);
    });
});
