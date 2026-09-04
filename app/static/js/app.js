// Search box behaviour.
//
// Pages are rendered on the server, so this file only has one job: turn the
// search field into a live autocomplete against /api/suggest. Everything it
// does is an enhancement — with JavaScript off, the form still submits to
// /search and the site works.

const MAX_SUGGESTIONS = 8;
const DEBOUNCE_MS = 120;

const el = {
  form: document.getElementById('search-form'),
  input: document.getElementById('q'),
  clear: document.getElementById('clear'),
  list: document.getElementById('suggestions'),
};

if (el.form && el.input && el.list) {
  init();
}

function init() {
  let suggestions = [];
  let active = -1;
  let timer = null;
  let sequence = 0;                 // drops responses that arrive out of order

  const escapeHtml = (s) =>
    String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const wordPath = (word) => `/word/${encodeURIComponent(word.replace(/ /g, '-'))}`;

  function close() {
    el.list.hidden = true;
    el.list.innerHTML = '';
    el.input.setAttribute('aria-expanded', 'false');
    suggestions = [];
    active = -1;
  }

  function render() {
    if (!suggestions.length) {
      close();
      return;
    }
    const query = el.input.value.trim().toLowerCase();
    el.list.innerHTML = suggestions
      .map((item, i) => {
        const display = escapeHtml(item.display);
        const marked = query && item.word.startsWith(query)
          ? `<mark>${escapeHtml(item.display.slice(0, query.length))}</mark>${escapeHtml(item.display.slice(query.length))}`
          : display;
        return `<li role="option" id="sug-${i}" aria-selected="${i === active}" data-index="${i}">
            <a href="${wordPath(item.word)}" tabindex="-1">
              <span class="s-word">${marked}</span>
              ${item.pos ? `<span class="s-pos">${escapeHtml(item.pos)}</span>` : ''}
              <span class="s-gloss">${escapeHtml(item.gloss || '')}</span>
            </a>
          </li>`;
      })
      .join('');
    el.list.hidden = false;
    el.input.setAttribute('aria-expanded', 'true');
    highlight();
  }

  function highlight() {
    [...el.list.children].forEach((li, i) => {
      li.setAttribute('aria-selected', String(i === active));
      li.classList.toggle('active', i === active);
    });
    el.input.setAttribute('aria-activedescendant', active >= 0 ? `sug-${active}` : '');
  }

  async function fetchSuggestions(query) {
    const mine = ++sequence;
    try {
      const res = await fetch(`/api/suggest?q=${encodeURIComponent(query)}&limit=${MAX_SUGGESTIONS}`);
      if (!res.ok) throw new Error(`suggest responded ${res.status}`);
      const payload = await res.json();
      if (mine !== sequence) return;          // a newer keystroke already won
      suggestions = payload.results ?? [];
      active = -1;
      render();
    } catch {
      if (mine === sequence) close();          // offline or a bad response: stay quiet
    }
  }

  el.input.addEventListener('input', () => {
    const query = el.input.value.trim();
    el.clear.hidden = query.length === 0;
    clearTimeout(timer);
    if (!query) {
      close();
      return;
    }
    timer = setTimeout(() => fetchSuggestions(query), DEBOUNCE_MS);
  });

  el.input.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      close();
      return;
    }
    if (!suggestions.length) return;

    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      active = (active + step + suggestions.length + 1) % (suggestions.length + 1) - 1;
      if (active < -1) active = suggestions.length - 1;
      highlight();
    } else if (event.key === 'Enter' && active >= 0) {
      event.preventDefault();
      window.location.href = wordPath(suggestions[active].word);
    }
  });

  el.list.addEventListener('mousedown', (event) => {
    // mousedown, not click: blur would close the list first.
    const li = event.target.closest('li[data-index]');
    if (!li) return;
    event.preventDefault();
    window.location.href = wordPath(suggestions[Number(li.dataset.index)].word);
  });

  el.clear.addEventListener('click', () => {
    el.input.value = '';
    el.clear.hidden = true;
    close();
    el.input.focus();
  });

  el.input.addEventListener('blur', () => setTimeout(close, 120));
  el.input.addEventListener('focus', () => {
    if (el.input.value.trim()) fetchSuggestions(el.input.value.trim());
  });

  // "/" focuses the search box, the way every site with a search box does.
  document.addEventListener('keydown', (event) => {
    if (event.key !== '/' || event.metaKey || event.ctrlKey) return;
    const tag = document.activeElement?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA') return;
    event.preventDefault();
    el.input.focus();
    el.input.select();
  });
}
