(function () {
  'use strict';

  /**
   * Initializes password field visibility toggles.
   * Scans for wrapper layouts, binds interactive listeners to inner SVG nodes, 
   * and preserves programmatic accessibility tags for assistive screens.
   */
  function initPasswordToggles() {
    document.querySelectorAll('.password-wrapper').forEach(function (wrapper) {
      var input     = wrapper.querySelector('input');
      var toggle    = wrapper.querySelector('.password-toggle');
      var eyeOpen   = wrapper.querySelector('.eye-open');
      var eyeClosed = wrapper.querySelector('.eye-closed');

      // Abort safely if structural dependencies do not match within the scoped element
      if (!input || !toggle) return;

      toggle.addEventListener('click', function () {
        var isHidden = input.type === 'password';

        // Flip field mask context between plain text and secret state
        input.type = isHidden ? 'text' : 'password';

        // Synchronize ARIA states dynamically for accessibility compliance
        toggle.setAttribute(
          'aria-label',
          isHidden ? 'Hide password' : 'Show password'
        );

        // Update active visibility layouts across inline SVG vector nodes
        eyeOpen.style.display   = isHidden ? 'none' : '';
        eyeClosed.style.display = isHidden ? ''     : 'none';
      });
    });
  }

  // Defer initialization cleanly according to browser state variables
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPasswordToggles);
  } else {
    initPasswordToggles();
  }
}());