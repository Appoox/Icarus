document.addEventListener('DOMContentLoaded', function() {
    const pills = document.querySelectorAll('.trending-pill');
    const panes = document.querySelectorAll('.articles-preview-pane');
    const defaultPane = document.getElementById('pane-default');
    const layoutGrid = document.querySelector('.trending-layout-grid');

    pills.forEach(pill => {
      pill.addEventListener('mouseenter', function() {
        const targetId = this.getAttribute('data-target');
        
        // Deactivate all active preview states cleanly
        panes.forEach(pane => pane.classList.remove('active'));
        
        // Target and activate corresponding list container item
        const activePane = document.getElementById(targetId);
        if (activePane) {
          activePane.classList.add('active');
          
          // Select list child items and programmatically trigger CSS staggered fade-ins
          const items = activePane.querySelectorAll('.preview-article-item');
          items.forEach((item, index) => {
            item.style.animation = 'none';
            void item.offsetHeight; // Triggers browser layout reflow cleanly to clear state
            item.style.animation = `articleItemFadeIn 0.35s cubic-bezier(0.215, 0.610, 0.355, 1) forwards ${index * 0.04}s`;
          });
        }
      });
    });

    // Tracks boundaries on the outer grid layout workspace instead of the left column list.
    // This makes the active preview pane fully navigable so users can mouse into it and click links safely.
    if (layoutGrid) {
      layoutGrid.addEventListener('mouseleave', function() {
        panes.forEach(pane => pane.classList.remove('active'));
        if (defaultPane) {
          defaultPane.classList.add('active');
        }
      });
    }
  });