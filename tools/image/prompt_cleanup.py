"""Generate image-prompt substitutions from explicit phrase families."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ReplacementFamily:
    """Expand term pairs through source/replacement templates using ``{term}``.

    Keep related subjects in separate families so adding a qualifier does not
    accidentally rewrite unrelated scene content.
    """

    terms: tuple[tuple[str, str], ...]
    templates: tuple[tuple[str, str], ...]


_FAMILIES: tuple[ReplacementFamily, ...] = (
    ReplacementFamily(
        terms=tuple((term, term) for term in (
            "gore", "explicit gore", "blood", "bloodshed", "wounds",
            "graphic content", "explicit violence", "gory detail",
        )),
        templates=(
            ("no {term}", "{term}"),
            ("without {term}", "with {term}"),
            ("avoid {term}", "include {term}"),
            ("avoiding {term}", "including {term}"),
            ("rather than {term}", "and {term}"),
        ),
    ),
    ReplacementFamily(
        terms=(("violence", "violence"),),
        templates=tuple(
            template
            for qualifier in ("subdued", "restrained", "symbolic", "implied")
            for template in (
                (f"{qualifier} {{term}}", "explicit {term}"),
                (f"{{term}} is {qualifier}", "{term} is explicit"),
            )
        ),
    ),
    ReplacementFamily(
        terms=tuple((term, term) for term in ("severance", "severing")),
        templates=(
            ("symbolic {term}", "literal {term}"),
            ("{term} is symbolic", "{term} is literal"),
        ),
    ),
    ReplacementFamily(
        terms=tuple((term, term) for term in ("impact", "strike")),
        templates=(
            ("symbolic {term}", "physical {term}"),
            ("{term} is symbolic", "{term} is explicit"),
        ),
    ),
    ReplacementFamily(
        terms=(("wounds", "visible wounds"), ("death", "actual death")),
        templates=(("implied {term}", "{term}"),),
    ),
    ReplacementFamily(
        terms=(("graphic", "graphic"),),
        templates=(
            ("non-{term}", "{term}"),
            ("not {term}", "{term}"),
            ("rather than {term}", "and {term}"),
        ),
    ),
)

# Idiomatic exceptions take precedence over generated combinations.
_OVERRIDES: dict[str, str] = {
    "suggested rather than shown": "shown explicitly",
    "without explicit violence": "explicit violence",
    "without bloodshed": "including bloodshed",
}


def build_replacements(
    families: tuple[ReplacementFamily, ...],
    overrides: dict[str, str],
) -> dict[str, str]:
    """Generate a phrase map, rejecting conflicting families before overrides."""
    replacements: dict[str, str] = {}
    for family in families:
        for source_term, target_term in family.terms:
            for source_template, target_template in family.templates:
                source = source_template.format(term=source_term).casefold()
                target = target_template.format(term=target_term)
                if source in replacements and replacements[source] != target:
                    raise ValueError(f"Conflicting replacements for {source!r}")
                replacements[source] = target
    replacements.update({source.casefold(): target for source, target in overrides.items()})
    return replacements


_SUBSTITUTION_PHRASES = build_replacements(_FAMILIES, _OVERRIDES)
_PHRASES = "|".join(
    re.escape(phrase)
    for phrase in sorted(_SUBSTITUTION_PHRASES, key=len, reverse=True)
)
_PATTERN = re.compile(r"(?<![\w-])(?:" + _PHRASES + r")(?![\w-])", re.IGNORECASE)


def clean_image_prompt(prompt: str) -> str:
    """Apply longest matches once, preserving character tags and quoted text."""
    def substitute(match: re.Match[str]) -> str:
        return _SUBSTITUTION_PHRASES[match.group().casefold()]

    # Tags and explicit quoted text are scene content, not prompt qualifiers.
    segments = re.split(r'(<[^>]*>|"[^"\n]*")', prompt)
    for index in range(0, len(segments), 2):
        segments[index] = _PATTERN.sub(substitute, segments[index])
    return "".join(segments)
