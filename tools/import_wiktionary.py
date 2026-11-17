#!/usr/bin/env python3
"""Imports frequent English words from Wiktionary into data/source/.

    pip install wordfreq
    curl -sS https://kaikki.org/dictionary/English/kaikki.org-dictionary-English.jsonl \
      | python3 tools/import_wiktionary.py --limit 5000
    node tools/build.mjs

Input is the Wiktextract dump published by kaikki.org (one JSON object per
line, ~3.4 GB). It is read from stdin, or from a path given with --input, so the
whole file never has to be kept on disk.

Output is data/source/90-wiktionary.json. Words already present in any other
file in data/source/ are skipped, so hand-curated entries always win. Every
imported entry is marked "source": "wiktionary"; the app shows the CC BY-SA
attribution that Wiktionary's licence requires on those entries.

A word is kept only if Wiktionary gives it both a British (RP or UK) and an
American (GA or US) IPA transcription and at least one usable sense.
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import wordfreq

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / 'data' / 'source'
OUT_FILE = SOURCE_DIR / '90-wiktionary.json'

UK_TAGS = {'Received-Pronunciation', 'UK', 'British', 'Southern-England'}
US_TAGS = {'General-American', 'US'}

# Wiktextract part-of-speech codes -> the labels the curated data uses.
POS = {
    'noun': 'noun', 'verb': 'verb', 'adj': 'adjective', 'adv': 'adverb',
    'prep': 'preposition', 'pron': 'pronoun', 'conj': 'conjunction',
    'intj': 'interjection', 'det': 'determiner', 'num': 'number',
    'article': 'determiner', 'postp': 'adverb',
}

# Senses carrying any of these tags are dropped.
SKIP_SENSE_TAGS = {
    'obsolete', 'archaic', 'rare', 'dated', 'historical', 'nonstandard',
    'misspelling', 'form-of', 'alt-of', 'abbreviation', 'initialism',
    'vulgar', 'offensive', 'derogatory', 'slur', 'ethnic', 'pejorative',
    'Internet', 'dialectal', 'obsolete-form', 'no-gloss',
}

WORD_RE = re.compile(r"^[a-z]+(?:['-][a-z]+)*$")
MAX_SENSES = 4
MAX_EXAMPLES = 3
MAX_EXAMPLE_LEN = 180


PHONEMIC_RE = re.compile(r'^\s*/([^/,]+)/')
PHONETIC_RE = re.compile(r'^\s*\[([^\],~]+)')


def clean_ipa(raw):
    """Returns (ipa, is_phonetic) or None.

    '/ˈsɪt.i/ [ˈsɪɾ.i]' -> ('ˈsɪt.i', False): the leading /phonemic/ part wins.
    '[ˈsiː.zn̩]' -> ('ˈsiː.zn̩', True): narrow [phonetic] transcriptions are a
    fallback, with aspiration and most diacritics stripped.
    """
    m = PHONEMIC_RE.match(raw)
    phonetic = False
    if not m:
        m = PHONETIC_RE.match(raw)
        phonetic = True
    if not m:
        return None
    ipa = m.group(1).strip()
    if phonetic:
        ipa = ipa.replace('ʰ', '').replace('ɫ', 'l')
        # Drop combining marks except the syllabic stroke (n̩) and nasal tilde.
        ipa = ''.join(c for c in unicodedata.normalize('NFD', ipa)
                      if not unicodedata.combining(c) or c in '̩̃')
        ipa = unicodedata.normalize('NFC', ipa)
    if not ipa:
        return None
    # Match the curated entries' style: r rather than ɹ, no non-syllabic marks
    # on diphthongs (haʊ̯s -> haʊs), no tie bars on affricates (t͡ʃ -> tʃ).
    return ipa.replace('ɹ', 'r').replace('̯', '').replace('͡', ''), phonetic


def pick_ipa(sounds, wanted):
    """Best transcription for one accent, or None.

    Preference order: tagged only with the accent's own tags; then untagged
    (Wiktionary's way of saying "same in every major accent"); then tagged with
    the accent plus a regional label. Within each tier, phonemic beats phonetic
    and full forms beat weak forms; otherwise Wiktionary's order is kept.
    Dated and obsolete pronunciations are never used.
    """
    best = None
    for order, s in enumerate(sounds):
        tags = set(s.get('tags') or [])
        if tags & {'dated', 'obsolete', 'archaic'}:
            continue
        if not tags:
            tier = 1
        elif wanted & tags:
            tier = 0 if tags <= wanted else 2
        else:
            continue
        cleaned = clean_ipa(s.get('ipa', ''))
        if not cleaned:
            continue
        ipa, phonetic = cleaned
        weak = 'weak' in (s.get('note') or '')
        key = (phonetic, tier, weak, order)
        if best is None or key < best[0]:
            best = (key, ipa)
    return best[1] if best else None


ARCHAIC_RE = re.compile(r"\b(thee|thou|thy|thine|hath|doth|ye|art thou|unto|shalt|wilt)\b", re.I)

# wordfreq's top list includes abbreviations and fragments; at two letters
# only these are real headwords.
TWO_LETTER = set('am an as at be by do go he hi if in is it me my no of oh ok on or so to up us we'.split())


def tidy_sentence(text):
    text = re.sub(r'\s+', ' ', text).strip().rstrip(':;,')
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return text if text[-1] in '.!?…' else text + '.'


def usable_senses(entry):
    pos = POS.get(entry.get('pos'))
    if not pos:
        return []
    out = []
    for s in entry.get('senses', []):
        if 'form_of' in s or 'alt_of' in s:
            continue
        if SKIP_SENSE_TAGS & set(s.get('tags', [])):
            continue
        glosses = s.get('glosses') or []
        if not glosses:
            continue
        definition = tidy_sentence(glosses[-1])
        if len(definition) < 4:
            continue
        examples = []
        for ex in s.get('examples', []):
            if ex.get('type') not in (None, 'example'):
                continue  # dated literary quotations are long and archaic
            text = tidy_sentence(ex.get('text', ''))
            if 8 <= len(text) <= MAX_EXAMPLE_LEN and not ARCHAIC_RE.search(text):
                examples.append(text)
            if len(examples) == MAX_EXAMPLES:
                break
        out.append({'pos': pos, 'definition': definition, 'examples': examples})
    return out


def curated_words():
    words = set()
    for path in SOURCE_DIR.glob('*.json'):
        if path == OUT_FILE:
            continue
        for e in json.loads(path.read_text(encoding='utf-8')):
            words.add(str(e.get('word', '')).strip().lower())
    return words


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--input', help='kaikki.org JSONL file (default: stdin)')
    ap.add_argument('--limit', type=int, default=5000, help='how many words to import (default 5000)')
    ap.add_argument('--candidates', type=int, default=30000,
                    help='consider only the N most frequent English words (default 30000)')
    args = ap.parse_args()

    ranked = [w for w in wordfreq.top_n_list('en', args.candidates) 
              if WORD_RE.match(w) and (len(w) > 2 or w in TWO_LETTER or w == 'a')]
    rank = {w: i for i, w in enumerate(ranked)}
    skip = curated_words()

    # word -> {'uk', 'us', 'senses'}; a word can appear in several entries
    # (one per part of speech or etymology), so senses are merged.
    found = {}
    stream = open(args.input, encoding='utf-8') if args.input else sys.stdin
    for line in stream:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        w = e.get('word')
        if w not in rank or w in skip or e.get('lang_code', 'en') != 'en':
            continue
        rec = found.setdefault(w, {'uk': None, 'us': None, 'senses': []})
        sounds = e.get('sounds', [])
        rec['uk'] = rec['uk'] or pick_ipa(sounds, UK_TAGS)
        rec['us'] = rec['us'] or pick_ipa(sounds, US_TAGS)
        rec['senses'].extend(usable_senses(e))

    entries = []
    for w in sorted(found, key=rank.get):
        rec = found[w]
        if not (rec['uk'] and rec['us'] and rec['senses']):
            continue
        # Wiktionary lists the main sense first; keep its order.
        senses = rec['senses'][:MAX_SENSES]
        entries.append({'word': w, 'uk': rec['uk'], 'us': rec['us'], 'source': 'wiktionary', 'senses': senses})
        if len(entries) == args.limit:
            break

    entries.sort(key=lambda e: e['word'])
    OUT_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    with_ex = sum(1 for e in entries if any(s['examples'] for s in e['senses']))
    print(f'Wrote {len(entries)} entries to {OUT_FILE.relative_to(ROOT)} '
          f'({with_ex} with at least one example; skipped {len(skip)} curated words).')


if __name__ == '__main__':
    main()
