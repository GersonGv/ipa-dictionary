#!/usr/bin/env python3
"""Turn the English Wiktextract dump into a normalised dictionary file.

    python3 tools/extract_wiktionary.py \
        --dump build/en-wiktextract.jsonl \
        --frequencies build/count_1w.txt \
        --out build/lexicon.jsonl

The dump is one JSON object per (word, part of speech, etymology), roughly
3 GB of it, and most of each object is etymology trees and translations we do
not want. This script streams it once and keeps four things: the accent-tagged
IPA, the glosses, a couple of example sentences per sense, and the inflected
forms.

Two filters do the real work of turning "everything Wiktionary has" into
"what a dictionary would print":

  * a frequency cut, so the 200k obscure taxonomic and dialectal entries do
    not bury the words people actually look up;
  * a sense filter, dropping obsolete/archaic/misspelling senses and the
    "plural of X" stubs — those become inflected forms of X instead, so
    searching for them still lands somewhere useful.
"""

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict

POS_KEEP = {
    "noun": "noun",
    "verb": "verb",
    "adj": "adjective",
    "adv": "adverb",
    "prep": "preposition",
    "conj": "conjunction",
    "pron": "pronoun",
    "det": "determiner",
    "article": "article",
    "num": "number",
    "intj": "exclamation",
    "phrase": "phrase",
    "prep_phrase": "phrase",
    "proverb": "phrase",
    "contraction": "contraction",
    "prefix": "prefix",
    "suffix": "suffix",
}

# Accent labels Wiktionary uses, collapsed to the two columns this dictionary
# shows. A sound entry can carry several (one transcription that happens to be
# shared), in which case it is used for both.
UK_TAGS = {
    "Received-Pronunciation", "RP", "UK", "British", "England",
    "Southern-England", "General-British", "Anglic", "Estuary-English",
}
US_TAGS = {
    "General-American", "GenAm", "GA", "US", "America", "American",
    "United-States",
}

# A sense carrying any of these is not what someone looking the word up wants
# to read first; if every sense of a word is dropped, the word goes too.
DROP_SENSE_TAGS = {
    "obsolete", "archaic", "rare", "nonstandard", "misspelling", "eye-dialect",
    "pronunciation-spelling", "alt-of", "form-of", "abbreviation", "initialism",
    "romanization", "no-gloss",
}

# A sense that is only a pointer at another word ("plural of dog") is recorded
# as an inflected form instead of as a definition.
FORM_OF_KEYS = ("form_of", "alt_of")

# Inflection tables carry bookkeeping rows that are not real word forms.
FORM_TAG_SKIP = {"table-tags", "inflection-template", "class", "error-unknown-tag"}

# Wiktionary's quotations run from 1590s printings to yesterday's news. The
# old ones arrive with long-s, doubled-v for w, verse slashes and bracketed
# editorial insertions, and they are worse than no example at all.
UNQUOTABLE_RE = re.compile(r"[\[\]/ſﬀﬁ]|vv|\bthe{1,2}e\b", re.IGNORECASE)

# "(noun sense 1.1)" and friends: cross-references to a numbering this app
# does not reproduce.
CROSSREF_RE = re.compile(
    r"\s*\((?:see |cf\.? |compare )?(?:noun |verb |adjective |adverb )?"
    r"senses? [\d.,\s–-]+\)",
    re.IGNORECASE,
)

MAX_SENSES = 8
MAX_EXAMPLES = 3
MAX_EXAMPLE_CHARS = 220
MAX_DEFINITION_CHARS = 400

WORD_RE = re.compile(r"^[a-z][a-z'’.\- ]*$")

# Wiktionary files lexical relations in the same list as example sentences.
# They are useful data, but they are not sentences, and printing them under
# "Examples" reads as a bug.
RELATION_RE = re.compile(
    r"^(synonym|antonym|near-synonym|near-antonym|coordinate term|hypernym|"
    r"hyponym|meronym|holonym|troponym|see also|usage|compare|cf\.)s?\b[:,]?",
    re.IGNORECASE,
)

# A definition that only points at another entry, whether or not the sense was
# tagged as such. The tags catch most of these; the wording catches the rest.
POINTER_RE = re.compile(
    r"^(alternative|obsolete|archaic|nonstandard|informal|common|eye)?\s*"
    r"(spelling|form|misspelling|pronunciation|rendering)\s+of\b"
    r"|^(plural|singular|past tense|past participle|present participle|"
    r"comparative|superlative|gerund|inflection|initialism|abbreviation|"
    r"acronym|clipping|contraction|diminutive|feminine|masculine)\b.{0,30}\bof\b"
    r"|^used other than (figuratively|as an idiom)"
    r"|^synonym of\b|^see \w+$",
    re.IGNORECASE,
)
BRACKET_RE = re.compile(r"[\[\]/⟨⟩]")
WHITESPACE_RE = re.compile(r"\s+")


def normalise(value):
    value = unicodedata.normalize("NFD", (value or "").strip().lower())
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    return WHITESPACE_RE.sub(" ", value).replace("’", "'")


def load_frequencies(path, limit):
    """word -> rank, 1 = most common. Ranks beyond `limit` are not loaded."""
    ranks = {}
    with open(path, encoding="utf-8") as handle:
        for rank, line in enumerate(handle, start=1):
            if rank > limit:
                break
            word = line.split("\t", 1)[0].strip().lower()
            if word:
                ranks.setdefault(word, rank)
    return ranks


def clean_ipa(raw):
    """Strip the slashes or brackets Wiktionary wraps a transcription in.

    The app adds its own slashes, and a mix of /phonemic/ and [phonetic]
    notation across entries looks like a bug to a reader.
    """
    if not raw:
        return None
    text = BRACKET_RE.sub("", raw).strip()
    text = WHITESPACE_RE.sub(" ", text)
    if not text or len(text) > 120:
        return None
    # A comma-separated pair is two variants; keep the first.
    text = text.split(",")[0].strip()
    return text or None


def pick_pronunciations(sounds):
    """(uk, us) from the accent-tagged `sounds` array."""
    uk = us = fallback = None
    for sound in sounds or []:
        ipa = clean_ipa(sound.get("ipa"))
        if not ipa:
            continue
        tags = set(sound.get("tags") or [])
        if not uk and tags & UK_TAGS:
            uk = ipa
        if not us and tags & US_TAGS:
            us = ipa
        if not fallback and not tags:
            fallback = ipa
        if not fallback:
            fallback = ipa
    return uk or fallback, us or fallback


def clean_text(value, limit):
    if not value:
        return None
    text = WHITESPACE_RE.sub(" ", value.replace("\n", " ")).strip()
    text = CROSSREF_RE.sub("", text)
    text = text.strip("—–-• ")
    if len(text) < 2 or len(text) > limit:
        return None
    return text


def pick_examples(sense):
    """Prefer usage examples over dated literary quotations."""
    plain, quotes = [], []
    for example in sense.get("examples") or []:
        text = clean_text(example.get("text"), MAX_EXAMPLE_CHARS)
        if not text or RELATION_RE.match(text):
            continue
        # A bare compound ("A-bomb") is a derived term, not a usage example.
        if " " not in text:
            continue
        # Quotations arrive with an ellipsis where the citation was trimmed;
        # they read badly as a first example.
        if example.get("type") == "quotation" or example.get("ref"):
            if UNQUOTABLE_RE.search(text) or len(text.split()) < 5:
                continue
            quotes.append(text)
        else:
            plain.append(text)
    return (plain + quotes)[:MAX_EXAMPLES]


def sense_tags(sense):
    """The register labels worth printing next to a definition."""
    keep = {
        "informal", "formal", "slang", "vulgar", "offensive", "humorous",
        "dated", "literary", "figurative", "transitive", "intransitive",
        "countable", "uncountable", "British", "American", "Australian",
        "medicine", "law", "music", "computing", "mathematics", "grammar",
    }
    return [t for t in (sense.get("tags") or []) if t in keep][:3]


def parse_entry(record):
    """One dump record -> (senses, forms, uk, us), or None if it is not usable."""
    pos = POS_KEEP.get(record.get("pos"))
    if not pos:
        return None

    senses, form_targets = [], []
    for sense in record.get("senses") or []:
        tags = set(sense.get("tags") or [])

        target = None
        for key in FORM_OF_KEYS:
            for item in sense.get(key) or []:
                target = normalise(item.get("word"))
                if target:
                    break
            if target:
                break
        if target:
            form_targets.append(target)
            continue

        if tags & DROP_SENSE_TAGS:
            continue

        glosses = sense.get("glosses") or []
        # The last gloss is the most specific one in a nested sense.
        definition = clean_text(glosses[-1] if glosses else None, MAX_DEFINITION_CHARS)
        if not definition or POINTER_RE.match(definition):
            continue

        senses.append(
            {
                "pos": pos,
                "definition": definition,
                "tags": sense_tags(sense),
                "examples": pick_examples(sense),
            }
        )

    forms = []
    for form in record.get("forms") or []:
        tags = set(form.get("tags") or [])
        if tags & FORM_TAG_SKIP:
            continue
        text = normalise(form.get("form"))
        if text and WORD_RE.match(text):
            forms.append(text)

    uk, us = pick_pronunciations(record.get("sounds"))
    return {
        "senses": senses,
        "forms": forms,
        "form_of": form_targets,
        "uk": uk,
        "us": us,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", required=True)
    parser.add_argument("--frequencies", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--max-rank",
        type=int,
        default=200000,
        help="keep a word only if it is this common or commoner",
    )
    parser.add_argument(
        "--phrase-max-rank",
        type=int,
        default=30000,
        help="every word of a multi-word entry must be at least this common",
    )
    args = parser.parse_args()

    ranks = load_frequencies(args.frequencies, args.max_rank)
    print(f"frequency list: {len(ranks):,} words", file=sys.stderr)

    words = {}
    forms = defaultdict(set)          # form -> {lemma}
    read = kept = 0

    with open(args.dump, encoding="utf-8") as handle:
        for line in handle:
            read += 1
            if read % 250000 == 0:
                print(f"  read {read:,} records, kept {kept:,} words", file=sys.stderr)
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("lang_code") != "en":
                continue

            word = normalise(record.get("word"))
            if not word or not WORD_RE.match(word) or len(word) > 60:
                continue
            if not is_common(word, ranks, args.phrase_max_rank):
                continue

            parsed = parse_entry(record)
            if parsed is None:
                continue

            for target in parsed["form_of"]:
                if target != word:
                    forms[word].add(target)

            if not parsed["senses"]:
                continue

            entry = words.get(word)
            if entry is None:
                entry = words[word] = {
                    "word": word,
                    "display": record.get("word") or word,
                    "uk": None,
                    "us": None,
                    "senses": [],
                    "forms": set(),
                    "freq_rank": ranks.get(word),
                }
                kept += 1

            original = record.get("word") or word
            if original == word:
                entry["display"] = original
            entry["uk"] = entry["uk"] or parsed["uk"]
            entry["us"] = entry["us"] or parsed["us"]
            entry["senses"].extend(parsed["senses"])
            entry["forms"].update(parsed["forms"])

    # Inflections recorded on the *form's* own page ("ran: simple past of run")
    # only become useful once we know `run` survived the filters.
    for form, lemmas in forms.items():
        for lemma in lemmas:
            if lemma in words and form not in words:
                words[lemma]["forms"].add(form)

    written = 0
    with open(args.out, "w", encoding="utf-8") as out:
        for word in sorted(words):
            entry = words[word]
            entry["senses"] = order_senses(entry["senses"])[:MAX_SENSES]
            if not entry["senses"]:
                continue
            entry["forms"] = sorted(f for f in entry["forms"] if f != word)
            out.write(json.dumps(entry, ensure_ascii=False) + "\n")
            written += 1

    print(
        f"read {read:,} records -> {written:,} headwords "
        f"({sum(1 for w in words.values() if w['uk'] or w['us']):,} with a pronunciation)",
        file=sys.stderr,
    )


def is_common(word, ranks, phrase_max_rank):
    if " " in word or "-" in word:
        parts = [p for p in re.split(r"[ \-]", word) if p]
        return all(ranks.get(p, 10**9) <= phrase_max_rank for p in parts)
    return word in ranks


def order_senses(senses):
    """Wiktionary's own order, with duplicate definitions dropped.

    Promoting senses that happen to have an example was tried and was worse:
    the primary sense of a common word often has only archaic quotations
    attached, which this script discards, and "apple" ended up leading with
    "an imaginary diminutive unit of height".
    """
    seen = set()
    unique = []
    for sense in senses:
        key = sense["definition"].lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(sense)
    return unique


if __name__ == "__main__":
    main()
