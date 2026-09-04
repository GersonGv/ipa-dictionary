#!/usr/bin/env python3
"""Turn the normalised lexicon into an importable MySQL dump.

    python3 tools/build_sql.py \
        --lexicon build/lexicon.jsonl \
        --curated data/curated \
        --out build/dictionary.sql.gz

The schema is not written out by hand here — it is compiled from the
SQLAlchemy models with the MySQL dialect, so the dump and the running app can
never drift apart. (`DATABASE_URL` is forced to a throwaway SQLite URL first
purely so importing the models does not require a MySQL driver on the machine
doing the build; no connection is ever opened.)

Hand-written entries in `--curated` win over the generated ones. That is the
escape hatch for words where the automatic parse is thin and for the
editorial notes about UK/US differences, which Wiktionary has nowhere to put.
"""

import argparse
import gzip
import json
import os
import pathlib
import sys

# Run as a script from anywhere: the repo root has to be importable for
# `app.models` to resolve.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy.dialects import mysql  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

from app.db import Base  # noqa: E402,F401
from app.models import Example, Sense, Word, WordForm  # noqa: E402,F401

ROWS_PER_INSERT = 400
GLOSS_CHARS = 250
DIALECT = mysql.dialect()

ESCAPES = {
    "\\": "\\\\",
    "'": "\\'",
    "\n": "\\n",
    "\r": "\\r",
    "\x00": "\\0",
    "\x1a": "\\Z",
}


def sql_value(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    return "'" + "".join(ESCAPES.get(c, c) for c in str(value)) + "'"


def schema_sql():
    """DROP + CREATE TABLE for every model, in dependency order."""
    statements = []
    tables = Base.metadata.sorted_tables
    for table in reversed(tables):
        statements.append(f"DROP TABLE IF EXISTS `{table.name}`;")
    for table in tables:
        ddl = str(CreateTable(table).compile(dialect=DIALECT)).strip()
        statements.append(ddl.rstrip() + " ENGINE=InnoDB;")
    return statements


def index_sql():
    """Secondary indexes, emitted after the data.

    Building them as rows arrive costs several times more than building them
    once over a finished table — and the FULLTEXT index in particular is the
    difference between an import that takes minutes and one that takes hours.
    """
    return [
        str(CreateIndex(index).compile(dialect=DIALECT)).strip() + ";"
        for table in Base.metadata.sorted_tables
        for index in table.indexes
    ]


class Writer:
    """Batches rows into multi-row INSERTs.

    One INSERT per row would make this dump several times larger and take
    minutes longer to import over a shared-hosting MySQL.
    """

    def __init__(self, handle):
        self.handle = handle
        self.table = None
        self.columns = ()
        self.buffer = []

    def write(self, line):
        self.handle.write(line + "\n")

    def row(self, table, columns, values):
        if table != self.table or columns != self.columns:
            self.flush()
            self.table, self.columns = table, columns
        self.buffer.append("(" + ",".join(sql_value(v) for v in values) + ")")
        if len(self.buffer) >= ROWS_PER_INSERT:
            self.flush()

    def flush(self):
        if not self.buffer:
            return
        cols = ",".join(f"`{c}`" for c in self.columns)
        self.write(
            f"INSERT INTO `{self.table}` ({cols}) VALUES\n"
            + ",\n".join(self.buffer)
            + ";"
        )
        self.buffer = []


def load_curated(directory):
    """Hand-written entries, keyed by headword."""
    entries = {}
    path = pathlib.Path(directory)
    if not path.is_dir():
        return entries
    for file in sorted(path.glob("*.json")):
        payload = json.loads(file.read_text(encoding="utf-8"))
        for entry in payload:
            word = entry["word"].strip().lower()
            entries[word] = {
                "word": word,
                "display": entry.get("display") or entry["word"],
                "uk": entry.get("uk"),
                "us": entry.get("us"),
                "note": entry.get("note"),
                "senses": [
                    {
                        "pos": sense.get("pos", ""),
                        "definition": sense["definition"],
                        "tags": sense.get("tags", []),
                        "examples": sense.get("examples", []),
                    }
                    for sense in entry.get("senses", [])
                ],
                "forms": entry.get("forms", []),
                "freq_rank": entry.get("freq_rank"),
                "curated": True,
            }
    return entries


def initial_of(word):
    return word[0] if word[:1].isalpha() else "_"


def gloss_of(senses):
    if not senses:
        return ""
    text = senses[0]["definition"]
    if len(text) <= GLOSS_CHARS:
        return text
    return text[: GLOSS_CHARS - 1].rsplit(" ", 1)[0] + "…"


def pos_list_of(senses):
    seen = []
    for sense in senses:
        pos = sense.get("pos") or ""
        if pos and pos not in seen:
            seen.append(pos)
    return ", ".join(seen)[:120]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lexicon", required=True)
    parser.add_argument("--curated", default="data/curated")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    curated = load_curated(args.curated)
    print(f"curated overrides: {len(curated)}", file=sys.stderr)

    opener = gzip.open if args.out.endswith(".gz") else open
    with opener(args.out, "wt", encoding="utf-8") as handle:
        writer = Writer(handle)
        writer.write("SET NAMES utf8mb4;")
        writer.write("SET FOREIGN_KEY_CHECKS=0;")
        writer.write("SET UNIQUE_CHECKS=0;")
        writer.write("SET AUTOCOMMIT=0;")
        for statement in schema_sql():
            writer.write(statement)

        word_id = sense_id = form_id = example_id = 0
        counts = {"words": 0, "senses": 0, "examples": 0, "forms": 0}
        emitted = set()

        for entry in stream_entries(args.lexicon, curated):
            if entry["word"] in emitted:
                continue
            emitted.add(entry["word"])
            word_id += 1
            counts["words"] += 1
            senses = entry["senses"]
            writer.row(
                "words",
                ("id", "word", "display", "ipa_uk", "ipa_us", "note",
                 "pos_list", "gloss", "freq_rank", "initial", "curated"),
                (
                    word_id,
                    entry["word"],
                    entry["display"],
                    entry.get("uk"),
                    entry.get("us"),
                    entry.get("note"),
                    pos_list_of(senses),
                    gloss_of(senses),
                    entry.get("freq_rank"),
                    initial_of(entry["word"]),
                    entry.get("curated", False),
                ),
            )
            for ordinal, sense in enumerate(senses):
                sense_id += 1
                counts["senses"] += 1
                writer.row(
                    "senses",
                    ("id", "word_id", "pos", "definition", "tags", "ordinal"),
                    (
                        sense_id,
                        word_id,
                        sense.get("pos", ""),
                        sense["definition"],
                        ",".join(sense.get("tags") or []) or None,
                        ordinal,
                    ),
                )
                for index, example in enumerate(sense.get("examples") or []):
                    example_id += 1
                    counts["examples"] += 1
                    writer.row(
                        "examples",
                        ("id", "sense_id", "text", "ordinal"),
                        (example_id, sense_id, example, index),
                    )
            for form in entry.get("forms") or []:
                form_id += 1
                counts["forms"] += 1
                writer.row(
                    "word_forms", ("id", "form", "word_id", "tags"),
                    (form_id, form, word_id, None),
                )

        writer.flush()
        for statement in index_sql():
            writer.write(statement)
        writer.write("SET UNIQUE_CHECKS=1;")
        writer.write("SET FOREIGN_KEY_CHECKS=1;")
        writer.write("COMMIT;")

    print(
        "wrote {words:,} words, {senses:,} senses, {examples:,} examples, "
        "{forms:,} forms".format(**counts),
        file=sys.stderr,
    )
    print(f"-> {args.out}", file=sys.stderr)


def stream_entries(lexicon_path, curated):
    """Generated entries, with a curated entry substituted where one exists."""
    used = set()
    with open(lexicon_path, encoding="utf-8") as handle:
        for line in handle:
            entry = json.loads(line)
            override = curated.get(entry["word"])
            if override:
                used.add(entry["word"])
                # Keep the generated frequency rank; the curated file has no
                # reason to know one.
                override = dict(override, freq_rank=entry.get("freq_rank"))
                yield override
            else:
                yield entry
    for word, entry in curated.items():
        if word not in used:
            yield entry


if __name__ == "__main__":
    main()
