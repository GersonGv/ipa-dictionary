# IPA Dictionary

A full English dictionary with the British and American pronunciation of every
headword written out in the International Phonetic Alphabet, a definition per
sense, and example sentences.

Flask + MySQL, server-rendered, running as a single Phusion Passenger
application on cPanel shared hosting. Every word has its own URL.

```
dictionary.example.com
├── /                     home, search box, A–Z
├── /word/schedule        one rendered page per headword
├── /browse/s             every word beginning with S
├── /search?q=…           ranked results
├── /sitemap.xml          a sitemap index over every word
└── /api/…                JSON: suggest, search, word, health
```

## Why it is shaped this way

**Server-rendered, not a client-side app.** A dictionary is worth having only
if search engines can index it, which means a real HTML page per word rather
than a hash route. That is also why the API and the pages are one Flask app on
one subdomain: nothing here needs a second runtime, and a deploy is `git pull`
plus a restart.

**The data ships as a database, not as files.** The earlier version of this
project shipped JSON shards and did everything in the browser. That works up to
a couple of thousand words and falls over well before a hundred thousand — the
index alone would be megabytes before a reader has typed anything.

**The lexicon is built offline.** `tools/build_data.sh` parses a 3 GB
Wiktionary extract on your machine and emits one gzipped `.sql` file to import.
Shared hosting has neither the memory nor the disk to do that itself.

## Running it locally

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # point DATABASE_URL at a local MySQL
.venv/bin/flask --app passenger_wsgi:application run --debug
```

SQLite works for poking at the templates (`DATABASE_URL=sqlite:///dev.db`), but
two search tiers — the full-text pass over definitions and the SOUNDEX
candidate pool behind "did you mean" — are MySQL features. Both degrade to
something weaker instead of failing, so the app runs; it just finds less.

## The data

Built from [English Wiktionary](https://en.wiktionary.org) via
[wiktextract](https://github.com/tatuylonen/wiktextract), filtered down to the
words a general-purpose dictionary would actually print:

- a **frequency cut** against Norvig's `count_1w` list, so ~200,000 taxonomic,
  dialectal and obsolete entries do not bury the words people look up
  (`MAX_RANK` in `tools/build_data.sh` moves this line);
- a **sense filter** dropping obsolete, archaic, rare and misspelling senses;
- **inflections become forms, not entries** — "ran" is recorded as a form of
  "run", so looking it up lands on the entry rather than on a stub. Wiktionary
  gives many inflections a second, marginal life of their own ("ran" is also a
  nautical noun meaning "yarns coiled on a spun-yarn winch"), which is enough
  to make them survive the filters and then render as *only* that. An
  inflection whose own senses carry no example sentence is folded into its
  lemma; one that survives keeps its entry but is told what it is first
  ("simple past of see" above the woodworking tool) before its own meanings.

  Getting that last part right took several passes, and the failures are
  instructive: leading with the relationship unconditionally turned "bad" into
  "past participle of bid", because Wiktionary's inflection tables carry no
  register labels and "bid" still lists a form that died out around 1500.
  Reconstructed relationships are now only printed when the lemma is the
  commoner word; relationships Wiktionary states outright on the form's own
  page are trusted as-is.

The default build is about **64,000 headwords** — roughly 54,000 single words
and 10,000 idioms — in a 60 MB database. 89% of the commonest 20,000 words have
a transcription; the rarer tail is thinner, because Wiktionary itself is
thinner there, and those entries say so rather than showing a blank.

Pronunciations keep Wiktionary's own accent labels: Received Pronunciation and
its neighbours become the UK column, General American and its neighbours the
US one. An untagged transcription is used for both.

Wiktionary is CC BY-SA 3.0, so the definitions and examples here are too.

### Hand-written entries

`data/curated/*.json` overrides the generated data word by word. That is where
the editorial notes about UK/US differences live — Wiktionary has nowhere to
put "British English stresses the second syllable, American English the third",
and it is the most useful sentence on that page.

```json
{
  "word": "schedule",
  "uk": "ˈʃed.juːl",
  "us": "ˈskedʒ.uːl",
  "note": "Shown in a callout under the pronunciations.",
  "senses": [
    {
      "pos": "noun",
      "definition": "A plan listing what will happen and when it will happen.",
      "examples": ["The project is running two weeks behind schedule."]
    }
  ]
}
```

IPA is written without the surrounding slashes — the templates add them. Add a
file, rebuild, re-import.

## How search works

Tiers, stopping at the first that fills the result list:

1. exact headword
2. exact inflected form — "ran" → "run"
3. prefix, commonest word first
4. substring anywhere in the headword
5. full text over the one-line glosses
6. bounded edit distance — "wather" → "water"

Tiers 1–4 are index lookups. Tier 5 needs the FULLTEXT index on `words`. Tier 6
is the only one doing work in Python, and the SQL feeding it is anchored on the
first letter and on SOUNDEX so it never sees more than a few hundred
candidates.

## Layout

```
passenger_wsgi.py           Passenger entry point
app/
  __init__.py               app factory
  config.py                 .env, loaded by absolute path
  db.py                     engine + scoped session
  models/word.py            words, senses, examples, forms
  routes/pages.py           rendered pages, sitemap, robots
  routes/api.py             JSON endpoints
  services/search.py        the tiers above
  templates/                Jinja
  static/                   CSS and the search-box script
data/curated/               hand-written entries that win over the generated ones
tools/
  build_data.sh             download → filter → dictionary.sql.gz
  extract_wiktionary.py     the dump parser
  build_sql.py              lexicon → MySQL dump (schema compiled from the models)
  import_remote.sh          upload + import over SSH
  deploy.sh                 git pull + restart
docs/DEPLOY.md              cPanel setup, start to finish
```

## Deploying

See [docs/DEPLOY.md](docs/DEPLOY.md).

## Limitations

- One transcription per accent. Real dictionaries list variants; `note` is the
  escape hatch for the important ones.
- Not every word has both a UK and a US transcription — Wiktionary's coverage
  is uneven.
- Definitions are Wiktionary's, and Wiktionary writes for a reader who already
  knows the word. The curated overrides exist to fix that where it matters.
- No audio, by design.
