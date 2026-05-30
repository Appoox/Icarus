/* ─────────────────────────────────────────────────────────────
   footer.js  —  load this globally in base.html
   Handles the editorial board accordion in the site footer.
   Kept separate from issue.js so it works on every page.

   Changes from v1:
   - Uses removeAttribute('hidden') on first open so the element
     transitions correctly from the HTML `hidden` state.
   - Keyboard: Space / Enter on the button already work because it
     is a <button> element, but we also handle Escape to close.
───────────────────────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', function () {

    const boardToggle  = document.querySelector('.editorial-board-btn');
    const boardContent = document.getElementById('editorial-board-content');

    // Guard: both elements must exist (footer may be absent on some pages)
    if (!boardToggle || !boardContent) return;

    boardToggle.addEventListener('click', function () {
        toggleBoard();
    });

    // Escape key closes the board if it is open
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && boardToggle.getAttribute('aria-expanded') === 'true') {
            toggleBoard(false);  // force-close
            boardToggle.focus(); // return focus to the trigger
        }
    });

    function toggleBoard(forceState) {
        // Determine next state
        const isCurrentlyExpanded = boardToggle.getAttribute('aria-expanded') === 'true';
        const expand = (forceState !== undefined) ? forceState : !isCurrentlyExpanded;

        // Update ARIA state on the toggle button
        boardToggle.setAttribute('aria-expanded', String(expand));

        if (expand) {
            // Remove the HTML `hidden` attribute on first open so the CSS
            // grid-template-rows transition can animate (hidden blocks animation)
            boardContent.removeAttribute('hidden');
            // Small rAF delay so the browser registers the un-hidden state
            // before the transition class is added
            requestAnimationFrame(function () {
                boardContent.classList.add('is-expanded');
            });
        } else {
            boardContent.classList.remove('is-expanded');
            // Re-add `hidden` after the CSS transition finishes so the
            // collapsed content is invisible to assistive technologies
            boardContent.addEventListener('transitionend', function onEnd() {
                // Only re-hide if still collapsed (user didn't re-open mid-transition)
                if (boardToggle.getAttribute('aria-expanded') === 'false') {
                    boardContent.setAttribute('hidden', '');
                }
                boardContent.removeEventListener('transitionend', onEnd);
            });
        }
    }
});