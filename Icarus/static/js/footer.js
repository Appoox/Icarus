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

    const boardToggle  = document.querySelector('.site-footer__board-btn');
    const boardContent = document.getElementById('editorial-board-content');

    // Guard: both elements must exist (footer may be absent on some pages)
    if (!boardToggle || !boardContent) return;

    // NEW: Attach listener to the close button inside the modal
    const closeBtn = document.querySelector('.editorial-board-close-btn');
    if (closeBtn) {
        closeBtn.addEventListener('click', function() {
            toggleBoard(false);
        });
    }

    // NEW: Close the modal if the user clicks the darkened ::backdrop
    // Since clicks on the backdrop register as clicks on the dialog itself,
    // we calculate if the click coordinates fell outside the dialog box
    boardContent.addEventListener('click', function(e) {
        const dialogBox = boardContent.getBoundingClientRect();
        const clickedInDialogBox = (
            e.clientY >= dialogBox.top &&
            e.clientY <= dialogBox.top + dialogBox.height &&
            e.clientX >= dialogBox.left &&
            e.clientX <= dialogBox.left + dialogBox.width
        );
        
        if (!clickedInDialogBox) {
            toggleBoard(false);
        }
    });

    boardToggle.addEventListener('click', function () {
        toggleBoard();
    });

    // Escape key closes the board if it is open
    // NOTE: Native <dialog> handles Escape automatically, but keeping this
    // event listener ensures our button's ARIA state is synchronized 
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && boardToggle.getAttribute('aria-expanded') === 'true') {
            toggleBoard(false);  // force-close
            boardToggle.focus(); // return focus to the trigger
        }
    });

    // NEW: Also sync aria states if the dialog is closed via native methods (like default Escape behavior)
    boardContent.addEventListener('close', function() {
        boardToggle.setAttribute('aria-expanded', 'false');
    });

    function toggleBoard(forceState) {
        // Determine next state
        const isCurrentlyExpanded = boardToggle.getAttribute('aria-expanded') === 'true';
        const expand = (forceState !== undefined) ? forceState : !isCurrentlyExpanded;

        // Update ARIA state on the toggle button
        boardToggle.setAttribute('aria-expanded', String(expand));

        if (expand) {
            
            // MODIFIED: Utilize native HTML dialog method to open a modal overlay
            boardContent.showModal();

            /* --- PRESERVED COMMENTS FROM FORMER ACCORDION LOGIC ---
               // Remove the HTML `hidden` attribute on first open so the CSS
               // grid-template-rows transition can animate (hidden blocks animation)
               // Small rAF delay so the browser registers the un-hidden state
               // before the transition class is added
            */
            
        } else {
            
            // MODIFIED: Utilize native HTML dialog method to safely close the modal overlay
            boardContent.close();

            /* --- PRESERVED COMMENTS FROM FORMER ACCORDION LOGIC ---
               // Re-add `hidden` after the CSS transition finishes so the
               // collapsed content is invisible to assistive technologies
               // Only re-hide if still collapsed (user didn't re-open mid-transition)
            */
        }
    }
});