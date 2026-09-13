/* Wide tables must be reachable and scrollable without a pointing device. */
(() => {
  let observer;
  const prepare = () => {
    if (observer) observer.disconnect();
    observer = new ResizeObserver(entries => {
      for (const {target} of entries) {
        const scrollable = target.scrollWidth > target.clientWidth;
        if (scrollable) {
          target.tabIndex = 0;
          target.setAttribute('role', 'region');
          target.setAttribute('aria-label', 'Scrollable table');
        } else {
          target.removeAttribute('tabindex');
          target.removeAttribute('role');
          target.removeAttribute('aria-label');
        }
      }
    });
    document.querySelectorAll('.md-typeset__scrollwrap').forEach(table => observer.observe(table));
  };
  // Material emits after each instant-navigation document replacement.
  if (typeof document$ !== 'undefined') document$.subscribe(prepare);
  else if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', prepare);
  else prepare();
})();
