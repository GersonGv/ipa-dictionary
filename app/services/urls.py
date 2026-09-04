"""How a headword becomes a URL path.

Spaces become hyphens, which means a phrase ("give up") and a hyphenated word
("well-being") produce the same-looking path. That collision is resolved on the
way back in by `search.lookup`, which tries the literal spelling first. Keeping
the mapping lossy but readable beats percent-encoded spaces in a URL people are
expected to share.
"""

from urllib.parse import quote


def word_path(word):
    return "/word/" + quote(word.replace(" ", "-"), safe="-'")
