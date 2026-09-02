# IPA Dictionary

A static dictionary web app. Every entry shows the British and American
pronunciation in the International Phonetic Alphabet, a definition per sense,
and example sentences. No audio, no back end, no tracking.

## Run it locally

```sh
python3 -m http.server 8000     # or: npx serve .
open http://localhost:8000
```

There is no build step for the app itself — it is plain HTML, CSS, and one ES
module. Only the data is generated.

## Adding words

Edit or add a file under `data/source/`. Each file is an array of entries:

```json
{
  "word": "schedule",
  "display": "schedule",
  "uk": "ˈʃed.juːl",
  "us": "ˈskedʒ.uːl",
  "note": "Optional. Shown in a callout under the pronunciations.",
  "senses": [
    {
      "pos": "noun",
      "definition": "A plan listing what will happen and when it will happen.",
      "examples": ["The project is running two weeks behind schedule.", "Her schedule is full until the end of the month."]
    }
  ]
}
```

Write the IPA without surrounding slashes — the app adds them. `display` is only
needed when the headword should render differently from its lookup key (e.g.
`tuesday` → `Tuesday`).

Then rebuild:

```sh
node tools/build.mjs
```

The build validates as it goes and refuses to write anything if an entry is
missing a pronunciation, a definition, or has fewer than two examples. It writes:

- `data/index.json` — every headword plus a one-line gloss. Loaded once, drives the search box.
- `data/entries/<letter>.json` — full entries, one shard per first letter, fetched only when a word in that letter is opened.

## Deploying to GitHub Pages

Yes, this works on GitHub Pages, and search works fully. See "Why search works
without a server" below.

1. Create a repo on your personal account and push this directory to `main`.
2. Repo **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. Push. `.github/workflows/deploy.yml` rebuilds the data (so a malformed source
   file fails the deploy instead of shipping) and publishes the site.

It lands at `https://<user>.github.io/<repo>/`. Nothing in the app hardcodes a
path — every URL is relative and the data files are resolved from
`import.meta.url` — so it works from a subpath or a custom domain unchanged.

Two details that matter on Pages:

- **`.nojekyll`** is committed. Without it, Pages runs the files through Jekyll,
  which silently refuses to serve anything whose name starts with `_` — including
  the `_.json` shard the build creates for headwords that do not start with a
  letter.
- **Routing uses the hash** (`#schedule`), not clean paths. Pages has no rewrite
  rules, so `/schedule` would 404 on a hard refresh. With the hash, deep links,
  bookmarks, refreshes, and the back button all work with no server config.

## Why search works without a server

Search here is not a query sent somewhere — it is a scan over an array already in
memory. `index.json` is fetched once on load; typing filters it in a few
milliseconds. What you lose by having no server is nothing this app needs.

Matching runs in tiers: exact, prefix, word-internal, gloss text, then a bounded
Levenshtein pass so `tomatoe` still finds `tomato` and `wather` finds `water`.
No search library is needed at this scale — adding one would cost more bytes than
the index itself.

## Scaling the data

The index costs roughly **95 bytes per word** as built (headword, parts of speech,
truncated gloss); the shards cost roughly 460 bytes per word. GitHub Pages gzips
text automatically, so divide transferred size by about 3.5.

| Words | index.json (gzipped) | What to do |
|---|---|---|
| up to ~2,000 | ~55 KB | Nothing. Current design is comfortable. |
| 2,000–20,000 | ~150–550 KB | Drop the gloss from the index (`tools/build.mjs`, the `index` map) — that alone cuts it to ~15 bytes per word. |
| 20,000+ | too big to ship whole | Bucket the index by two-letter prefix and fetch `index/sc.json` as the user types, or precompute a trie. Consider [MiniSearch](https://github.com/lucaong/minisearch) or [Fuse.js](https://fusejs.io) only if you also want full-text ranking over definitions. |

Shard size never becomes the problem — a shard is only fetched when a word in it
is opened, and it is cached for the session. Splitting on two letters instead of
one (`sc.json`) keeps shards small well past 100,000 entries.

Pages' own limits are generous for this: 1 GB repo, 100 GB/month bandwidth,
100 MB per file. A 100,000-word dictionary at this shape is roughly 50 MB — it
fits, though the repo gets unwieldy long before Pages complains.

### Why not an external API

Considered and rejected for this project:

- **Any API with a key** (Merriam-Webster, Oxford) is a bad fit for a static site.
  The key ships in your JavaScript where anyone can read it and burn your quota.
  Hiding it needs a proxy, which needs a server, which is the thing you are
  avoiding.
- **`dictionaryapi.dev`** is free and needs no key, but its phonetic data is
  inconsistent — many words have no IPA at all, and it rarely labels which accent
  a transcription represents. The whole point of this app is a reliable UK/US
  pair, so that is fatal.
- **Bundled data always renders.** No rate limits, no CORS surprises, no outage,
  no request per lookup, and it works offline once loaded.

The tradeoff is honest: coverage is whatever you curate. `data/source/` currently
holds a starter set of about 50 words, weighted toward words whose British and
American pronunciations actually differ.

## Limitations

- Coverage is the curated word list, not a full lexicon. Unknown words say so.
- One transcription per accent. Real dictionaries list variants; the `note` field
  is the escape hatch for the important ones.
- IPA rendering depends on the reader's fonts. The stack in `--ipa` starts with
  fonts with good phonetic coverage and falls back to the system UI font, which
  handles common symbols but can render rarer ones (`t̬`, `ɚ`) unevenly.
- No audio, by design.
