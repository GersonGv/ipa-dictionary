"""Schema.org markup for a word page.

Google shows a definition snippet for a `DefinedTerm` in a `DefinedTermSet`,
which is the closest thing the vocabulary has to a dictionary entry. The
pronunciations go in `description` because there is no IPA property.
"""

import json


def entry_jsonld(entry):
    definitions = [s.definition for s in entry.senses][:5]
    pronunciation = " · ".join(
        part
        for part in (
            f"UK /{entry.ipa_uk}/" if entry.ipa_uk else None,
            f"US /{entry.ipa_us}/" if entry.ipa_us else None,
        )
        if part
    )
    data = {
        "@context": "https://schema.org",
        "@type": "DefinedTerm",
        "name": entry.display,
        "description": " ".join(filter(None, [pronunciation, definitions[0] if definitions else ""])).strip(),
        "inDefinedTermSet": {
            "@type": "DefinedTermSet",
            "name": "IPA Dictionary",
        },
    }
    if len(definitions) > 1:
        data["subjectOf"] = [
            {"@type": "CreativeWork", "text": d} for d in definitions[1:]
        ]
    return json.dumps(data, ensure_ascii=False)
