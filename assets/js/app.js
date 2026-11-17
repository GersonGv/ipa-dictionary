// IPA Dictionary — a static, client-side lookup.
//
// Data flow: index.json (all headwords + a one-line gloss) is fetched once and
// drives the search box. The full entry for a word lives in a per-letter shard
// that is only fetched when that word is actually opened, and then cached.

const dataUrl = (path) => new URL(`../../data/${path}`, import.meta.url);

const MAX_SUGGESTIONS = 8;
const RECENT_KEY = 'ipa-dict:recent';
const RECENT_LIMIT = 8;

const FEATURED = ['schedule', 'tomato', 'privacy', 'herb', 'advertisement', 'either', 'vitamin', 'clerk', 'garage', 'zebra'];

const el = {
  form: document.getElementById('search-form'),
  input: document.getElementById('q'),
  clear: document.getElementById('clear'),
  suggestions: document.getElementById('suggestions'),
  status: document.getElementById('status'),
  entry: document.getElementById('entry'),
  home: document.getElementById('home'),
  recentPanel: document.getElementById('recent-panel'),
  recent: document.getElementById('recent'),
  featured: document.getElementById('featured'),
  countNote: document.getElementById('count-note'),
};

/** @type {{word:string, display:string, pos:string, gloss:string}[]} */
let index = [];
const shardCache = new Map();   // letter -> Promise<Record<string, entry>>
let active = -1;                // highlighted suggestion
let suggestions = [];

/* ---- small helpers ----------------------------------------------------- */

const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const normalise = (s) => s.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim();

const shardOf = (word) => (/^[a-z]/.test(word) ? word[0] : '_');

/**
 * Edit distance where swapping two adjacent letters counts as one edit
 * ("shcool" -> "school" is 1, not 2). Abandoned as soon as it exceeds `max`.
 */
function editDistance(a, b, max) {
  if (Math.abs(a.length - b.length) > max) return max + 1;
  let before = null;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const curr = [i];
    let best = i;
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      curr[j] = Math.min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost);
      if (before && i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
        curr[j] = Math.min(curr[j], before[j - 2] + 1);
      }
      best = Math.min(best, curr[j]);
    }
    if (best > max) return max + 1;
    before = prev;
    prev = curr;
  }
  return prev[b.length];
}

/* ---- search ------------------------------------------------------------ */

// Ranked in tiers: exact, prefix, word-internal, gloss text, then near-misses.
function search(rawQuery) {
  const q = normalise(rawQuery);
  if (!q) return [];

  const hits = [];
  for (const item of index) {
    const word = item.word;
    let score = null;

    if (word === q) score = 0;
    else if (word.startsWith(q)) score = 100 + (word.length - q.length);
    else if (word.includes(q)) score = 300 + word.indexOf(q);
    else if (q.length >= 3 && item.gloss.toLowerCase().includes(q)) score = 600 + word.length;
    else if (q.length >= 4) {
      const d = editDistance(q, word, 2);
      if (d <= 2) score = 800 + d * 100 + word.length;
    }

    if (score !== null) hits.push({ item, score });
  }

  hits.sort((a, b) => a.score - b.score || a.item.word.localeCompare(b.item.word));
  return hits.slice(0, MAX_SUGGESTIONS).map((h) => h.item);
}

/* ---- data -------------------------------------------------------------- */

async function loadIndex() {
  const res = await fetch(dataUrl('index.json'));
  if (!res.ok) throw new Error(`index.json responded ${res.status}`);
  const payload = await res.json();
  index = payload.words.map(([word, display, pos, gloss]) => ({
    word,
    display: display || word,
    pos,
    gloss,
  }));
  el.countNote.textContent = `${payload.count} words, updated ${payload.generated}.`;
}

function loadShard(letter) {
  if (!shardCache.has(letter)) {
    shardCache.set(
      letter,
      fetch(dataUrl(`entries/${letter}.json`)).then((res) => {
        if (!res.ok) throw new Error(`entries/${letter}.json responded ${res.status}`);
        return res.json();
      }).catch((err) => {
        shardCache.delete(letter);   // let a later attempt retry
        throw err;
      })
    );
  }
  return shardCache.get(letter);
}

async function getEntry(word) {
  const shard = await loadShard(shardOf(word));
  return shard[word] ?? null;
}

/* ---- recent lookups ---------------------------------------------------- */

function readRecent() {
  try {
    const parsed = JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]');
    return Array.isArray(parsed) ? parsed.filter((w) => typeof w === 'string') : [];
  } catch {
    return [];
  }
}

function rememberWord(word) {
  try {
    const next = [word, ...readRecent().filter((w) => w !== word)].slice(0, RECENT_LIMIT);
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    /* private browsing, storage disabled — the app works fine without it */
  }
  renderRecent();
}

/* ---- rendering --------------------------------------------------------- */

function renderChips(container, words, { warnMissing = false } = {}) {
  container.innerHTML = words
    .map((w) => {
      const item = index.find((i) => i.word === w);
      if (!item) {
        if (warnMissing) console.warn(`Featured word "${w}" is not in the dictionary data; it was skipped.`);
        return '';
      }
      return `<li><button type="button" data-word="${escapeHtml(w)}">${escapeHtml(item.display)}</button></li>`;
    })
    .join('');
}

function renderRecent() {
  const words = readRecent().filter((w) => index.some((i) => i.word === w));
  el.recentPanel.hidden = words.length === 0;
  renderChips(el.recent, words);
}

function showHome() {
  el.entry.hidden = true;
  el.entry.innerHTML = '';
  el.status.hidden = true;
  el.home.hidden = false;
  document.title = 'IPA Dictionary — UK & US pronunciations';
}

function showStatus(message, isError = false) {
  el.entry.hidden = true;
  el.home.hidden = true;
  el.status.hidden = false;
  el.status.textContent = message;
  el.status.classList.toggle('error', isError);
}

// Bolds the headword where it appears in an example sentence.
function highlightExample(sentence, word) {
  const pattern = new RegExp(`\\b(${word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\w*)\\b`, 'gi');
  return escapeHtml(sentence).replace(pattern, '<b>$1</b>');
}

function renderEntry(entry) {
  const senses = entry.senses
    .map((sense, i) => `
      <li class="sense">
        <div class="sense-head">
          <span class="sense-num" aria-hidden="true">${i + 1}</span>
          ${sense.pos ? `<span class="pos">${escapeHtml(sense.pos)}</span>` : ''}
        </div>
        <p class="definition">${escapeHtml(sense.definition)}</p>
        ${sense.examples.length ? `<ul class="examples">
          ${sense.examples.map((ex) => `<li>${highlightExample(ex, entry.word)}</li>`).join('')}
        </ul>` : ''}
      </li>`)
    .join('');

  // Wiktionary content is CC BY-SA, which requires attribution on the entry.
  const source = entry.source === 'wiktionary'
    ? `<p class="source">From <a href="https://en.wiktionary.org/wiki/${encodeURIComponent(entry.word)}#English" rel="noopener">Wiktionary</a>,
        available under <a href="https://creativecommons.org/licenses/by-sa/4.0/" rel="license noopener">CC BY-SA 4.0</a>.</p>`
    : '';

  el.entry.innerHTML = `
    <h1 class="headword">${escapeHtml(entry.display)}</h1>
    <ul class="pron">
      <li class="uk"><span class="tag">UK</span><span class="ipa">/${escapeHtml(entry.uk)}/</span></li>
      <li class="us"><span class="tag">US</span><span class="ipa">/${escapeHtml(entry.us)}/</span></li>
    </ul>
    ${entry.note ? `<p class="note"><b>Note.</b> ${escapeHtml(entry.note)}</p>` : ''}
    <ol class="senses">${senses}</ol>
    ${source}`;

  el.status.hidden = true;
  el.home.hidden = true;
  el.entry.hidden = false;
  document.title = `${entry.display} — IPA Dictionary`;
}

/* ---- suggestions ------------------------------------------------------- */

function renderSuggestions(query) {
  suggestions = search(query);
  active = -1;

  if (!query.trim()) {
    closeSuggestions();
    return;
  }

  if (suggestions.length === 0) {
    el.suggestions.innerHTML = `<li class="s-empty" role="option" aria-disabled="true">No match for “${escapeHtml(query)}”. This dictionary is a curated set, not a full lexicon.</li>`;
  } else {
    const q = normalise(query);
    el.suggestions.innerHTML = suggestions
      .map((item, i) => {
        const at = item.word.indexOf(q);
        const display = escapeHtml(item.display);
        const marked = at === -1
          ? display
          : escapeHtml(item.display.slice(0, at)) +
            '<mark>' + escapeHtml(item.display.slice(at, at + q.length)) + '</mark>' +
            escapeHtml(item.display.slice(at + q.length));
        return `<li role="option" id="sug-${i}" aria-selected="false" data-word="${escapeHtml(item.word)}">
          <span class="s-word">${marked}</span>
          ${item.pos ? `<span class="s-pos">${escapeHtml(item.pos)}</span>` : ''}
          <span class="s-gloss">${escapeHtml(item.gloss)}</span>
        </li>`;
      })
      .join('');
  }

  el.suggestions.hidden = false;
  el.input.setAttribute('aria-expanded', 'true');
}

function closeSuggestions() {
  el.suggestions.hidden = true;
  el.suggestions.innerHTML = '';
  el.input.setAttribute('aria-expanded', 'false');
  el.input.removeAttribute('aria-activedescendant');
  suggestions = [];
  active = -1;
}

function moveActive(delta) {
  if (suggestions.length === 0) return;
  const n = suggestions.length;
  active += delta;
  if (active >= n) active = -1;
  if (active < -1) active = n - 1;

  [...el.suggestions.children].forEach((li, i) => li.setAttribute('aria-selected', String(i === active)));
  if (active >= 0) {
    el.input.setAttribute('aria-activedescendant', `sug-${active}`);
    el.suggestions.children[active].scrollIntoView({ block: 'nearest' });
  } else {
    el.input.removeAttribute('aria-activedescendant');
  }
}

/* ---- navigation -------------------------------------------------------- */

function go(word) {
  closeSuggestions();
  const target = `#${encodeURIComponent(word)}`;
  if (location.hash === target) route();
  else location.hash = target;
}

async function route() {
  // A hash change can come from anywhere — a chip, the back button, a pasted
  // link — so drop any dropdown left open from a previous query.
  closeSuggestions();
  const word = normalise(decodeURIComponent(location.hash.slice(1)));

  if (!word) {
    el.input.value = '';
    el.clear.hidden = true;
    showHome();
    return;
  }

  el.input.value = index.find((i) => i.word === word)?.display ?? word;
  el.clear.hidden = false;
  showStatus('Looking up…');

  try {
    const entry = await getEntry(word);
    if (!entry) {
      showStatus(`“${word}” is not in this dictionary yet.`, true);
      return;
    }
    renderEntry(entry);
    rememberWord(word);
    window.scrollTo(0, 0);
  } catch (err) {
    showStatus(`Could not load that entry (${err.message}).`, true);
  }
}

/* ---- events ------------------------------------------------------------ */

el.input.addEventListener('input', () => {
  el.clear.hidden = el.input.value === '';
  renderSuggestions(el.input.value);
});

el.input.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowDown') { e.preventDefault(); moveActive(1); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); moveActive(-1); }
  else if (e.key === 'Escape') { closeSuggestions(); }
  else if (e.key === 'Enter' && active >= 0) {
    e.preventDefault();
    go(suggestions[active].word);
  }
});

el.input.addEventListener('focus', () => {
  if (el.input.value.trim()) renderSuggestions(el.input.value);
});

el.form.addEventListener('submit', (e) => {
  e.preventDefault();
  const picked = active >= 0 ? suggestions[active] : suggestions[0] ?? search(el.input.value)[0];
  if (picked) go(picked.word);
  else showStatus(`“${el.input.value.trim()}” is not in this dictionary yet.`, true);
});

el.suggestions.addEventListener('mousedown', (e) => {
  const li = e.target.closest('li[data-word]');
  if (li) { e.preventDefault(); go(li.dataset.word); }
});

el.clear.addEventListener('click', () => {
  el.input.value = '';
  el.clear.hidden = true;
  closeSuggestions();
  el.input.focus();
  if (location.hash) location.hash = '';
  else showHome();
});

document.addEventListener('click', (e) => {
  const chip = e.target.closest('.chips button[data-word]');
  if (chip) { go(chip.dataset.word); return; }
  if (!el.form.contains(e.target)) closeSuggestions();
});

window.addEventListener('hashchange', route);

/* ---- start ------------------------------------------------------------- */

(async function start() {
  try {
    await loadIndex();
  } catch (err) {
    showStatus(`Could not load the dictionary data (${err.message}).`, true);
    return;
  }
  renderChips(el.featured, FEATURED, { warnMissing: true });
  renderRecent();
  // Focus before routing: route() fills the input, and focusing afterwards
  // would pop the suggestion list open on top of a freshly opened entry.
  el.input.focus({ preventScroll: true });
  await route();
})();
