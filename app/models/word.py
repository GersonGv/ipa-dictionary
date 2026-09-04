"""The lexicon.

One row per headword, its senses beneath it, examples beneath those. `WordForm`
holds inflections ("ran" -> "run") so a lookup for a form that is not itself a
headword still lands on the right entry.

Denormalised columns on `words` (`pos_list`, `gloss`, `freq_rank`) exist so the
search box can render a result list from a single-table query. Building them at
import time is what keeps autocomplete off the joins.
"""

from sqlalchemy import (
    CHAR,
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.db import Base


class Word(Base):
    __tablename__ = "words"

    id = Column(Integer, primary_key=True)
    # 191 chars is the longest utf8mb4 column MySQL will accept in a unique
    # index under the older 767-byte key limit still common on shared hosting.
    word = Column(String(191), nullable=False, unique=True)
    display = Column(String(191), nullable=False)
    ipa_uk = Column(String(191))
    ipa_us = Column(String(191))
    note = Column(Text)
    pos_list = Column(String(120), nullable=False, default="")
    gloss = Column(String(255), nullable=False, default="")
    freq_rank = Column(Integer)
    initial = Column(CHAR(1), nullable=False, default="_")
    curated = Column(Boolean, nullable=False, default=False)

    senses = relationship(
        "Sense",
        back_populates="word",
        order_by="Sense.ordinal",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    forms = relationship(
        "WordForm", back_populates="word", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_words_initial", "initial", "freq_rank"),
        Index("idx_words_freq", "freq_rank"),
        # Powers the "no headword matched, look inside the definitions" tier.
        # Without it that query is a full scan of every row.
        Index("ft_words_gloss", "word", "gloss", mysql_prefix="FULLTEXT"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    def summary(self):
        """The shape the search box consumes."""
        return {
            "word": self.word,
            "display": self.display,
            "pos": self.pos_list,
            "gloss": self.gloss,
            "uk": self.ipa_uk,
            "us": self.ipa_us,
        }

    def detail(self):
        return {
            "word": self.word,
            "display": self.display,
            "uk": self.ipa_uk,
            "us": self.ipa_us,
            "note": self.note,
            "senses": [s.to_dict() for s in self.senses],
        }


class Sense(Base):
    __tablename__ = "senses"

    id = Column(Integer, primary_key=True)
    word_id = Column(
        Integer, ForeignKey("words.id", ondelete="CASCADE"), nullable=False
    )
    pos = Column(String(32), nullable=False, default="")
    definition = Column(Text, nullable=False)
    tags = Column(String(191))
    ordinal = Column(SmallInteger, nullable=False, default=0)

    word = relationship("Word", back_populates="senses")
    examples = relationship(
        "Example",
        back_populates="sense",
        order_by="Example.ordinal",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("idx_senses_word", "word_id", "ordinal"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    def to_dict(self):
        return {
            "pos": self.pos,
            "definition": self.definition,
            "tags": [t for t in (self.tags or "").split(",") if t],
            "examples": [e.text for e in self.examples],
        }


class Example(Base):
    __tablename__ = "examples"

    id = Column(Integer, primary_key=True)
    sense_id = Column(
        Integer, ForeignKey("senses.id", ondelete="CASCADE"), nullable=False
    )
    text = Column(Text, nullable=False)
    ordinal = Column(SmallInteger, nullable=False, default=0)

    sense = relationship("Sense", back_populates="examples")

    __table_args__ = (
        Index("idx_examples_sense", "sense_id", "ordinal"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )


class WordForm(Base):
    """An inflected form pointing back at its lemma."""

    __tablename__ = "word_forms"

    id = Column(Integer, primary_key=True)
    form = Column(String(191), nullable=False)
    word_id = Column(
        Integer, ForeignKey("words.id", ondelete="CASCADE"), nullable=False
    )
    tags = Column(String(120))

    word = relationship("Word", back_populates="forms")

    __table_args__ = (
        Index("idx_forms_form", "form"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )
