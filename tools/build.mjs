#!/usr/bin/env node
// Builds the shipped data files from data/source/*.json.
//
//   node tools/build.mjs
//
// Output:
//   data/index.json        small search index, loaded once at startup
//   data/entries/<x>.json  one shard per first letter, fetched on demand

import { readdir, readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const sourceDir = join(root, 'data', 'source');
const entriesDir = join(root, 'data', 'entries');

const problems = [];
const fail = (where, msg) => problems.push(`${where}: ${msg}`);

// Words are sharded on their first character. Anything outside a-z lands in "_".
const shardOf = (word) => (/^[a-z]/.test(word) ? word[0] : '_');

const normaliseWord = (s) =>
  String(s).toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').trim();

const files = (await readdir(sourceDir)).filter((f) => f.endsWith('.json')).sort();
const entries = new Map();

for (const file of files) {
  let parsed;
  try {
    parsed = JSON.parse(await readFile(join(sourceDir, file), 'utf8'));
  } catch (err) {
    fail(file, `not valid JSON — ${err.message}`);
    continue;
  }
  if (!Array.isArray(parsed)) {
    fail(file, 'top level must be an array of entries');
    continue;
  }

  for (const raw of parsed) {
    const where = `${file} › ${raw.word ?? '(no word)'}`;
    // Must match normalise() in assets/js/app.js, which strips accents from the
    // URL hash before lookup — otherwise "café" would be stored but never found.
    const word = normaliseWord(raw.word ?? '');
    if (!word) { fail(where, 'missing "word"'); continue; }
    if (entries.has(word)) { fail(where, 'duplicate headword'); continue; }
    if (!raw.uk) fail(where, 'missing UK pronunciation');
    if (!raw.us) fail(where, 'missing US pronunciation');

    const senses = Array.isArray(raw.senses) ? raw.senses : [];
    if (senses.length === 0) fail(where, 'needs at least one sense');
    // Hand-written entries must have two examples per sense. Imported entries
    // (those with a "source") are kept even when the source has no examples.
    const minExamples = raw.source ? 0 : 2;
    senses.forEach((sense, i) => {
      if (!sense.definition) fail(where, `sense ${i + 1} has no definition`);
      if (!Array.isArray(sense.examples) || sense.examples.length < minExamples) {
        fail(where, `sense ${i + 1} needs at least two example sentences`);
      }
    });

    entries.set(word, {
      word,
      display: raw.display ?? raw.word,
      uk: raw.uk,
      us: raw.us,
      ...(raw.note ? { note: raw.note } : {}),
      ...(raw.source ? { source: raw.source } : {}),
      senses: senses.map((s) => ({
        pos: s.pos ?? '',
        definition: s.definition,
        examples: s.examples ?? [],
      })),
    });
  }
}

if (problems.length) {
  console.error(`Build failed with ${problems.length} problem(s):`);
  for (const p of problems) console.error('  • ' + p);
  process.exit(1);
}

const sorted = [...entries.keys()].sort();

// Shards: one file per letter, keyed by headword.
const shards = new Map();
for (const word of sorted) {
  const key = shardOf(word);
  if (!shards.has(key)) shards.set(key, {});
  shards.get(key)[word] = entries.get(word);
}

// Index: [headword, display-if-different, parts of speech, one-line gloss].
// Small enough to load up front, rich enough to render suggestions without a fetch.
const index = sorted.map((word) => {
  const e = entries.get(word);
  const pos = [...new Set(e.senses.map((s) => s.pos).filter(Boolean))].join(', ');
  const gloss = e.senses[0].definition;
  return [
    word,
    e.display === word ? '' : e.display,
    pos,
    gloss.length > 84 ? gloss.slice(0, 83).trimEnd() + '…' : gloss,
  ];
});

const indexPath = join(root, 'data', 'index.json');
const shardList = [...shards.keys()].sort();

// Keep the previous "generated" date unless the data actually changed, so a
// rebuild with no edits leaves every output file byte-identical. This reads the
// old output, so it has to run before the shards are rewritten below.
let generated = new Date().toISOString().slice(0, 10);
try {
  const prev = JSON.parse(await readFile(indexPath, 'utf8'));
  let unchanged =
    Boolean(prev.generated) &&
    JSON.stringify(prev.shards) === JSON.stringify(shardList) &&
    JSON.stringify(prev.words) === JSON.stringify(index);
  for (const key of shardList) {
    if (!unchanged) break;
    const old = await readFile(join(entriesDir, `${key}.json`), 'utf8').catch(() => null);
    unchanged = old === JSON.stringify(shards.get(key));
  }
  if (unchanged) generated = prev.generated;
} catch {
  /* no previous index — first build */
}

await rm(entriesDir, { recursive: true, force: true });
await mkdir(entriesDir, { recursive: true });
for (const [key, payload] of shards) {
  await writeFile(join(entriesDir, `${key}.json`), JSON.stringify(payload));
}

await writeFile(
  indexPath,
  JSON.stringify({
    version: 1,
    generated,
    count: index.length,
    shards: shardList,
    words: index,
  })
);

const bytes = async (p) => (await readFile(p)).length;
const indexBytes = await bytes(join(root, 'data', 'index.json'));
let shardBytes = 0;
for (const key of shards.keys()) shardBytes += await bytes(join(entriesDir, `${key}.json`));

console.log(`Built ${index.length} entries across ${shards.size} shards.`);
console.log(`  index.json  ${(indexBytes / 1024).toFixed(1)} KB (loaded on every visit)`);
console.log(`  entries/    ${(shardBytes / 1024).toFixed(1)} KB total, ` +
            `${(shardBytes / shards.size / 1024).toFixed(1)} KB average per shard`);
