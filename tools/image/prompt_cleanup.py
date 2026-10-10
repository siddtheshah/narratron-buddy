"""Generate image-prompt substitutions from explicit phrase families."""

from dataclasses import dataclass
from functools import lru_cache
import re


@dataclass(frozen=True)
class ReplacementFamily:
    """Expand term pairs through source/replacement templates using ``{term}``.

    Keep related subjects in separate families so adding a qualifier does not
    accidentally rewrite unrelated scene content.
    """

    terms: tuple[tuple[str, str], ...]
    templates: tuple[tuple[str, str], ...]
    trigger_set: str | None = None
    trigger_words: tuple[str, ...] = ()

    def is_triggered(self, text: str) -> bool:
        """Unconditional families are always active."""
        return True


ROMANCE_TRIGGER_SET: tuple[str, ...] = (
    "romance", "romantic", "intimacy", "kiss", "kisses", "kissing",
    "embrace", "embraces", "embracing", "holding hands",
)

VIOLENCE_TRIGGER_SET: tuple[str, ...] = (
    "violence", "violent", "fight", "fights", "fighting", "combat",
    "battle", "battles", "duel", "duels", "gore", "gory", "blood",
    "bloodshed", "wound", "wounds", "severance", "severing",
)

TRIGGER_SETS: dict[str, tuple[str, ...]] = {
    "romance": ROMANCE_TRIGGER_SET,
    "violence": VIOLENCE_TRIGGER_SET,
}


@lru_cache(maxsize=32)
def _compile_trigger_pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    """Compile whole-word regex pattern for matching trigger cues."""
    alternatives = "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))
    return re.compile(r"(?<![\w-])(?:" + alternatives + r")(?![\w-])", re.IGNORECASE)


@dataclass(frozen=True)
class ConditionalReplacementFamily(ReplacementFamily):
    """Expand term pairs only when a trigger word from the trigger set is present in text."""

    def is_triggered(self, text: str) -> bool:
        """Check whether any trigger word from the trigger set is found in text."""
        if self.trigger_set is None and not self.trigger_words:
            return True
        words: tuple[str, ...]
        if self.trigger_set is not None:
            normalized = self.trigger_set.casefold()
            if normalized not in TRIGGER_SETS:
                raise ValueError(f"Unknown trigger set: {self.trigger_set!r}")
            words = TRIGGER_SETS[normalized] + self.trigger_words
        else:
            words = self.trigger_words
        pattern = _compile_trigger_pattern(words)
        return pattern.search(text) is not None


_FAMILIES: tuple[ReplacementFamily, ...] = (
    ReplacementFamily(
        terms=tuple((term, term) for term in (
            "gore", "explicit gore", "blood", "bloodshed", "wounds",
            "graphic content", "explicit violence", "gory detail",
            "graphic gore detail",
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
        terms=tuple((term, term) for term in (
            "romance", "romantic affection", "romantic intimacy",
        )),
        templates=tuple(
            template
            for qualifier in ("subdued", "restrained", "implied", "symbolic")
            for template in (
                (f"{qualifier} {{term}}", "expressive {term}"),
                (f"{{term}} is {qualifier}", "{term} is expressive"),
            )
        ),
    ),
    ReplacementFamily(
        terms=tuple((term, term) for term in (
            "kiss", "kisses", "embrace", "embraces", "romantic touch",
        )),
        templates=(
            ("implied {term}", "visible {term}"),
            ("symbolic {term}", "visible {term}"),
            ("{term} is implied", "{term} is visible"),
            ("{term} is symbolic", "{term} is visible"),
        ),
    ),
    ReplacementFamily(
        terms=tuple((term, term) for term in (
            "kissing", "embracing", "holding hands", "romantic affection", "explicit detail"
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


@lru_cache(maxsize=32)
def _compile_family_replacements(
    family: ReplacementFamily,
) -> tuple[dict[str, str], re.Pattern[str]]:
    """Compile replacements and regex pattern for a single family."""
    replacements = build_replacements((family,), {})
    phrases = "|".join(re.escape(p) for p in sorted(replacements, key=len, reverse=True))
    pattern = re.compile(r"(?<![\w-])(?:" + phrases + r")(?![\w-])", re.IGNORECASE)
    return replacements, pattern


_CONDITIONAL_FAMILIES: tuple[ConditionalReplacementFamily, ...] = (
    ConditionalReplacementFamily(
        terms=(
            ("touch", "deep kiss"),
            ("touches", "deep kiss"),
            ("touching", "deep kiss"),
            ("meet", "deep kiss"),
            ("meets", "deep kiss"),
            ("meeting", "deep kiss"),
        ),
        templates=(
            ("forehead {term}", "{term}"),
            ("foreheads {term}", "{term}"),
            ("foreheads are {term}", "{term}"),
            ("forehead is {term}", "{term}"),
            ("{term} foreheads", "{term}"),
            ("{term} forehead", "{term}"),
            ("{term} of foreheads", "{term}"),
            ("{term} of forehead", "{term}"),
        ),
        trigger_set="romance",
    ),
    ConditionalReplacementFamily(
        terms=(
            ("forehead to forehead", "deep kiss"),
            ("forehead-to-forehead", "deep kiss"),
            ("foreheads to foreheads", "deep kiss"),
            ("forehead against forehead", "deep kiss"),
            ("foreheads against each other", "deep kiss"),
            ("forehead meeting forehead", "deep kiss"),
            ("foreheads touching each other", "deep kiss"),
        ),
        templates=(
            ("{term}", "{term}"),
        ),
        trigger_set="romance",
    ),
)

_SUBSTITUTION_PHRASES = build_replacements(_FAMILIES, _OVERRIDES)
_PHRASES = "|".join(
    re.escape(phrase)
    for phrase in sorted(_SUBSTITUTION_PHRASES, key=len, reverse=True)
)
_PATTERN = re.compile(r"(?<![\w-])(?:" + _PHRASES + r")(?![\w-])", re.IGNORECASE)

# Intent is deliberately lexical and limited to unprotected scene text.
_INTENT_TERMS: tuple[str, ...] = ROMANCE_TRIGGER_SET + VIOLENCE_TRIGGER_SET
_INTENT_PATTERN = re.compile(
    r"(?<![\w-])(?:" + "|".join(re.escape(term) for term in _INTENT_TERMS) + r")(?![\w-])",
    re.IGNORECASE,
)
# Edit this list to configure conditional word removal in code.
CONDITIONALLY_DROPPED_WORDS: tuple[str, ...] = ("restrained", "without", "no", "almost", "nearly")
_RESTRAINED_PREDICATE = re.compile(r"[ \t]+is[ \t]+restrained(?![\w-])", re.IGNORECASE)


@lru_cache(maxsize=32)
def _drop_word_pattern(words: tuple[str, ...]) -> re.Pattern[str] | None:
    """Compile literal, whole-word matches; an empty list disables removal."""
    if not words:
        return None
    if any(not word or word.strip() != word or len(word.split()) != 1 for word in words):
        raise ValueError("Conditionally dropped words must be nonempty single words")
    alternatives = "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))
    return re.compile(r"(?<![\w-])(?:" + alternatives + r")(?![\w-])[ \t]*", re.IGNORECASE)


def clean_image_prompt(
    prompt: str,
    *,
    conditionally_dropped_words: tuple[str, ...] = CONDITIONALLY_DROPPED_WORDS,
    conditional_families: tuple[ConditionalReplacementFamily, ...] = _CONDITIONAL_FAMILIES,
) -> str:
    """Drop conditional qualifiers, then substitute phrases in unprotected text.

    Romantic or violent cues anywhere in unprotected text enable whole-word
    removal of CONDITIONALLY_DROPPED_WORDS throughout that text. Callers can
    override the list, including an empty tuple to disable this pass. Existing
    phrase substitutions still apply. Conditional families trigger on scene text.
    Line breaks survive.
    """
    def substitute(match: re.Match[str]) -> str:
        return _SUBSTITUTION_PHRASES[match.group().casefold()]

    # Tags and explicit quoted text are scene content, not prompt qualifiers.
    segments = re.split(r'(<[^>]*>|"[^"\n]*")', prompt)
    drop_pattern = _drop_word_pattern(conditionally_dropped_words)
    drop_qualifiers = any(_INTENT_PATTERN.search(segment) for segment in segments[::2])
    drop_restrained = "restrained" in {word.casefold() for word in conditionally_dropped_words}

    unprotected_text = " ".join(segments[::2])
    active_conditional_replacements: list[tuple[dict[str, str], re.Pattern[str]]] = []
    for family in conditional_families:
        if family.is_triggered(unprotected_text):
            active_conditional_replacements.append(_compile_family_replacements(family))

    for index in range(0, len(segments), 2):
        if drop_qualifiers and drop_pattern is not None:
            segment = segments[index]
            if drop_restrained:
                segment = _RESTRAINED_PREDICATE.sub("", segment)
            cleaned = drop_pattern.sub("", segment)
            if cleaned != segments[index]:
                cleaned = re.sub(r"[ \t]+([.,;?!])", r"\1", cleaned)
                segments[index] = cleaned.rstrip(" \t") if index == len(segments) - 1 else cleaned
        for replacements_map, pattern in active_conditional_replacements:
            def cond_substitute(m: re.Match[str], rmap: dict[str, str] = replacements_map) -> str:
                return rmap[m.group().casefold()]
            segments[index] = pattern.sub(cond_substitute, segments[index])
        segments[index] = _PATTERN.sub(substitute, segments[index])
    return "".join(segments)
