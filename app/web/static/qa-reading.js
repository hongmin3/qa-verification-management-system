/* Progressive enhancement: without JS all evidence and history remain readable. */
(() => {
  const nav = document.querySelector('.qa-reading-tabs');
  if (!nav) return;
  const tabs = [...nav.querySelectorAll('a')];
  const panels = tabs.map(tab => document.querySelector(tab.getAttribute('href')));
  nav.setAttribute('role', 'tablist');
  function show(index) {
    tabs.forEach((tab, i) => {
      tab.setAttribute('aria-selected', String(i === index)); tab.tabIndex = i === index ? 0 : -1;
      panels[i].hidden = i !== index;
    });
  }
  tabs.forEach((tab, i) => {
    tab.id = 'qa-reading-tab-' + i;
    tab.setAttribute('role', 'tab'); tab.setAttribute('aria-controls', panels[i].id);
    panels[i].setAttribute('role', 'tabpanel'); panels[i].setAttribute('aria-labelledby', tab.id);
    tab.addEventListener('click', () => show(i));
    tab.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 :
        (i + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      tabs[next].focus(); tabs[next].click();
    });
  });
  function fromHash() { show(Math.max(0, panels.findIndex(panel => '#' + panel.id === location.hash))); }
  window.addEventListener('hashchange', fromHash); fromHash();
})();
