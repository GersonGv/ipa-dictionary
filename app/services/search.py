"""Lookup and search.

The old static build did all of this over an in-memory array. With a full
lexicon the array no longer fits in a browser, so the same tiers now run as
SQL, in this order, stopping as soon as enough results are found:

    1. exact headword
    2. exact inflected form  ("ran" -> "run")
    3. prefix, most common word first
    4. word-internal substring
    5. bounded edit distance over a small candidate pool  ("wather" -> "water")
    6. full-text over the one-line glosses

Tiers 5 and 6 trade places for a multi-word query: see `search`.

Tiers 1-4 are index lookups. The full-text tier needs the FULLTEXT index on
`words`. Edit distance is the only tier that does work in Python, and it only
ever sees a few hundred candidates because the SQL that feeds it is anchored
on the first character and on SOUNDEX.
"""

import re
import unicodedata

from sqlalchemy import func, or_, select, text

from app.db import db_session
from app.models import Word, WordForm

MAX_RESULTS = 25
FUZZY_CANDIDATE_LIMIT = 400
FUZZY_MAX_DISTANCE = 2


def normalise(value):
    """Lowercase, strip accents and collapse whitespace — the lookup key form."""
    if not value:
        return ""
    value = unicodedata.normalize("NFD", value.strip().lower())
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", value)


def edit_distance(a, b, max_distance):
    """Levenshtein, abandoned as soon as it cannot come in under the cap."""
    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1
    previous = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        current = [i]
        best = i
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            )
            best = min(best, current[j])
        if best > max_distance:
            return max_distance + 1
        previous = current
    return previous[len(b)]


def lookup(word):
    """Resolve a headword, following an inflection to its lemma if need be.

    Returns (Word|None, redirected_from|None) so the page can say "run — you
    searched for ran" instead of silently swapping the word out.
    """
    key = normalise(word)
    if not key:
        return None, None

    entry = db_session.execute(
        select(Word).where(Word.word == key)
    ).scalar_one_or_none()
    if entry:
        return entry, None

    form = db_session.execute(
        select(WordForm).where(WordForm.form == key).limit(1)
    ).scalar_one_or_none()
    if form and form.word:
        return form.word, key

    # URLs spell the space in a multi-word entry as a hyphen, and a real
    # hyphenated headword is spelled the same way. Try the other reading
    # before giving up, so /word/give-up and /word/well-being both resolve.
    if "-" in key:
        alternative = key.replace("-", " ")
        entry = db_session.execute(
            select(Word).where(Word.word == alternative)
        ).scalar_one_or_none()
        if entry:
            return entry, None

    return None, None


def _rows(stmt):
    return list(db_session.execute(stmt).scalars())


def search(query, limit=MAX_RESULTS):
    """Ranked results for a query string. Never raises on a weird query."""
    q = normalise(query)
    if not q:
        return []

    seen = set()
    results = []

    def take(entries):
        for entry in entries:
            if entry.word in seen:
                continue
            seen.add(entry.word)
            results.append(entry)
            if len(results) >= limit:
                return True
        return False

    # 1. exact
    if take(_rows(select(Word).where(Word.word == q))):
        return results

    # 2. an inflection of something
    if take(
        _rows(
            select(Word)
            .join(WordForm, WordForm.word_id == Word.id)
            .where(WordForm.form == q)
            .limit(limit)
        )
    ):
        return results

    escaped = _escape_like(q)

    # 3. prefix, commonest first
    if take(
        _rows(
            select(Word)
            .where(Word.word.like(f"{escaped}%", escape="\\"))
            .order_by(Word.freq_rank.is_(None), Word.freq_rank, Word.word)
            .limit(limit)
        )
    ):
        return results

    # 4. substring anywhere in the headword
    if len(q) >= 3 and take(
        _rows(
            select(Word)
            .where(Word.word.like(f"%{escaped}%", escape="\\"))
            .order_by(Word.freq_rank.is_(None), Word.freq_rank, Word.word)
            .limit(limit)
        )
    ):
        return results

    # Tiers 5 and 6 swap places depending on what was typed. One word is
    # someone looking up a word, and a misspelling of it should beat a
    # definition that happens to mention it — "tomatoe" wants "tomato", not
    # "lycopene", which is what the full-text pass returns because MySQL
    # expands `tomatoe*` onto glosses containing "tomatoes". Several words is
    # someone describing a meaning, and edit distance against single
    # headwords has nothing to say about that.
    single_word = " " not in q

    if single_word and len(q) >= 4 and take(_fuzzy(q, limit)):
        return results

    # inside the definitions
    if len(q) >= 3 and take(_fulltext(q, limit)):
        return results

    if not single_word and len(q) >= 4:
        take(_fuzzy(q, limit))

    return results


def _escape_like(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _fulltext(q, limit):
    """MATCH ... AGAINST over the denormalised glosses.

    Falls back to a LIKE scan if the FULLTEXT index is missing (a database
    imported before it existed, or a MySQL build without it) rather than
    failing the whole search.
    """
    tokens = [t for t in re.findall(r"[a-z0-9]+", q) if len(t) > 2]
    if not tokens:
        return []
    expression = " ".join(f"+{t}*" for t in tokens)
    try:
        stmt = (
            select(Word)
            .where(
                text(
                    "MATCH (words.word, words.gloss) "
                    "AGAINST (:ft IN BOOLEAN MODE)"
                ).bindparams(ft=expression)
            )
            .order_by(Word.freq_rank.is_(None), Word.freq_rank)
            .limit(limit)
        )
        return _rows(stmt)
    except Exception:
        db_session.rollback()
        return _rows(
            select(Word)
            .where(Word.gloss.like(f"%{_escape_like(q)}%", escape="\\"))
            .order_by(Word.freq_rank.is_(None), Word.freq_rank)
            .limit(limit)
        )


def _fuzzy(q, limit):
    """Words within a small edit distance of the query.

    The candidate pool is deliberately narrow: same first letter, or the same
    SOUNDEX code. That covers the two ways people actually mistype — a wrong
    vowel and a wrong consonant cluster — without pulling the lexicon into
    memory.
    """
    base = select(Word).order_by(Word.freq_rank.is_(None), Word.freq_rank).limit(
        FUZZY_CANDIDATE_LIMIT
    )
    prefix = Word.word.like(f"{_escape_like(q[:1])}%", escape="\\")
    try:
        candidates = _rows(
            base.where(or_(prefix, func.soundex(Word.word) == func.soundex(q)))
        )
    except Exception:
        # SOUNDEX is a MySQL builtin; on any other backend (a SQLite dev
        # database, say) fall back to the first-letter pool alone.
        db_session.rollback()
        candidates = _rows(base.where(prefix))

    scored = []
    for entry in candidates:
        distance = edit_distance(q, entry.word, FUZZY_MAX_DISTANCE)
        if distance <= FUZZY_MAX_DISTANCE:
            scored.append((distance, entry.freq_rank or 10**9, entry))
    scored.sort(key=lambda row: (row[0], row[1]))
    return [row[2] for row in scored[:limit]]


def suggest(query, limit=8):
    """Autocomplete: prefix first, then substring. Never fuzzy — too slow to
    run on every keystroke, and wrong-looking while someone is still typing."""
    q = normalise(query)
    if not q:
        return []

    escaped = _escape_like(q)
    rows = _rows(
        select(Word)
        .where(Word.word.like(f"{escaped}%", escape="\\"))
        .order_by(Word.freq_rank.is_(None), Word.freq_rank, Word.word)
        .limit(limit)
    )
    if len(rows) < limit and len(q) >= 3:
        seen = {r.word for r in rows}
        extra = _rows(
            select(Word)
            .where(Word.word.like(f"%{escaped}%", escape="\\"))
            .order_by(Word.freq_rank.is_(None), Word.freq_rank, Word.word)
            .limit(limit * 2)
        )
        rows.extend(e for e in extra if e.word not in seen)
    return rows[:limit]


def browse(initial, page=1, per_page=100):
    """One page of headwords starting with `initial`, alphabetically."""
    initial = (initial or "a")[:1].lower()
    if not initial.isalpha():
        initial = "_"
    offset = max(0, (page - 1) * per_page)
    total = db_session.execute(
        select(func.count(Word.id)).where(Word.initial == initial)
    ).scalar_one()
    rows = _rows(
        select(Word)
        .where(Word.initial == initial)
        .order_by(Word.word)
        .offset(offset)
        .limit(per_page)
    )
    return rows, total


def total_words():
    return db_session.execute(select(func.count(Word.id))).scalar_one()


def featured(words):
    """The hand-picked front-page words, in the order given, skipping any that
    are not in the database."""
    if not words:
        return []
    rows = _rows(select(Word).where(Word.word.in_(words)))
    by_word = {r.word: r for r in rows}
    return [by_word[w] for w in words if w in by_word]
