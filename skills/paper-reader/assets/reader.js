(() => {
  'use strict';
  const rows = [...document.querySelectorAll('.reading-row')];
  const search = document.querySelector('#reader-search');
  const status = document.querySelector('#search-status');
  const corpus = rows.map(row => row.textContent.toLocaleLowerCase());
  search.addEventListener('input', () => {
    const query = search.value.trim().toLocaleLowerCase();
    let count = 0;
    rows.forEach((row, i) => {
      row.hidden = !!query && !corpus[i].includes(query);
      if (!row.hidden) count++;
    });
    document.querySelectorAll('.paper-section').forEach(section => {
      section.classList.toggle('empty-section', ![...section.querySelectorAll('.reading-row')].some(row => !row.hidden));
    });
    status.hidden = !query;
    status.textContent = query ? `找到 ${count} / ${rows.length} 个阅读单元` : '';
  });
  document.querySelector('#reading-mode').addEventListener('change', event => {
    document.body.classList.remove('mode-en', 'mode-zh');
    if (event.target.value !== 'bi') document.body.classList.add(`mode-${event.target.value}`);
  });
  document.querySelectorAll('.legend-button').forEach(button => {
    button.addEventListener('click', () => {
      const next = button.getAttribute('aria-pressed') !== 'true';
      button.setAttribute('aria-pressed', String(next));
      document.querySelectorAll(`mark[data-kind="${button.dataset.kind}"]`).forEach(mark => mark.classList.toggle('dimmed', !next));
    });
  });
  const progress = document.querySelector('.reading-progress');
  function updateProgress() {
    const range = document.documentElement.scrollHeight - innerHeight;
    progress.style.width = `${range > 0 ? Math.min(100, scrollY / range * 100) : 100}%`;
  }
  addEventListener('scroll', updateProgress, {passive: true});
  addEventListener('resize', updateProgress);
  updateProgress();
  document.querySelector('#download-pdf').addEventListener('click', () => {
    const data = document.querySelector('#pdf-data');
    if (!data) return;
    const bytes = Uint8Array.from(atob(data.textContent.trim()), char => char.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], {type: 'application/pdf'}));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'original-paper.pdf';
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  });
})();
