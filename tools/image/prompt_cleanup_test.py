import pytest

from tools.image.prompt_cleanup import (
    ConditionalReplacementFamily,
    ReplacementFamily,
    ROMANCE_TRIGGER_SET,
    TRIGGER_SETS,
    VIOLENCE_TRIGGER_SET,
    _SUBSTITUTION_PHRASES,
    build_replacements,
    clean_image_prompt,
)


@pytest.mark.parametrize(("phrase", "replacement"), _SUBSTITUTION_PHRASES.items())
def test_every_replacement_matches_case_insensitively(phrase: str, replacement: str) -> None:
    # Conditional word removal takes precedence over phrase substitutions.
    replacement = clean_image_prompt(phrase)
    for variant in (phrase.upper(), phrase.title(), phrase.title().swapcase()):
        prompt = f'<{variant}> beside "{variant}". Scene: {variant}.'
        expected = f'<{variant}> beside "{variant}". Scene: {replacement}.'
        cleaned = clean_image_prompt(prompt)
        assert cleaned.casefold() == expected.casefold()
        assert cleaned.startswith(f'<{variant}> beside "{variant}".')


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("sigil in the air. Avoid explicit gore;", "sigil in the air. include explicit gore;"),
        ("A duel, no graphic content, subdued violence, sparks fly.", "A duel, graphic content, explicit violence, sparks fly."),
        ("NO GRAPHIC CONTENT; A duel; Subdued Violence", "GRAPHIC CONTENT; A duel; explicit violence"),
        ("A duel. No graphic content. Subdued violence.", "A duel. graphic content. explicit violence."),
        ("A duel\nno graphic content\nStyle: oil painting", "A duel\ngraphic content\nStyle: oil painting"),
        ("no graphic content, subdued violence", "graphic content, explicit violence"),
        ("restrained violence", "violence"),
        ("avoiding gore", "including gore"),
        ("without gore", "gore"),
        ("without explicit gore", "explicit gore"),
        ("suggested rather than shown", "shown explicitly"),
        ("symbolic severance", "literal severance"),
        ("symbolic severing", "literal severing"),
        ("symbolic impact", "physical impact"),
        ("symbolic strike", "physical strike"),
        ("<No Graphic Content> faces <Subdued Violence>.", "<No Graphic Content> faces <Subdued Violence>."),
        ('A poster says "no graphic content", beside a knight.', 'A poster says "no graphic content", beside a knight.'),
        ("Keep the scene non-graphic, intense, and cinematic.", "Keep the scene graphic, intense, and cinematic."),
        ("A NON-GRAPHIC duel in a storm.", "A graphic duel in a storm."),
        ("<Non-Graphic> in armor, non-graphic, dramatic light.", "<Non-Graphic> in armor, graphic, dramatic light."),
        ("A non-graphical illustration.", "A non-graphical illustration."),
        ("A duel,  sparks fly.\n\nStyle: painting", "A duel,  sparks fly.\n\nStyle: painting"),
        ("A duel,  without gore;\n\nStyle: painting", "A duel,  gore;\n\nStyle: painting"),
        ("nonsymbolic strike; symbolic strikes", "nonsymbolic strike; symbolic strikes"),
    ],
)
def test_cleanup_preserves_scene_content(prompt: str, expected: str) -> None:
    assert clean_image_prompt(prompt) == expected


def test_cleanup_preserves_unknown_qualifiers() -> None:
    prompt = "A duel, no graphic content, cinematic restraint"
    assert clean_image_prompt(prompt) == "A duel, graphic content, cinematic restraint"


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("avoiding blood; without wounds", "including blood; wounds"),
        ("rather than explicit gore", "and explicit gore"),
        ("violence is restrained; implied violence", "violence; explicit violence"),
        ("severance is symbolic; impact is symbolic", "severance is literal; impact is explicit"),
        ("violence is not graphic", "violence is graphic"),
        ("without bloodshed; without explicit violence", "bloodshed; explicit violence"),
        ('<Avoiding Blood> beside "implied violence", avoiding blood.',
         '<Avoiding Blood> beside "implied violence", including blood.'),
        ("without explicit gore and without gore", "explicit gore and gore"),
        ("no bloodhound; restrained painter; nonsymbolic impact",
         "no bloodhound; restrained painter; nonsymbolic impact"),
    ],
)
def test_cleanup_expands_phrase_families(prompt: str, expected: str) -> None:
    cleaned = clean_image_prompt(prompt)
    assert cleaned == expected
    assert clean_image_prompt(cleaned) == expected


def test_engine_expands_terms_and_templates_with_explicit_overrides() -> None:
    family = ReplacementFamily(
        terms=(("fog", "mist"), ("rain", "showers")),
        templates=(("no {term}", "with {term}"), ("avoiding {term}", "including {term}")),
    )
    assert build_replacements((family,), {"NO FOG": "clear skies"}) == {
        "no fog": "clear skies",
        "avoiding fog": "including mist",
        "no rain": "with showers",
        "avoiding rain": "including showers",
    }


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("restrained romance; subdued romantic affection",
         "romance; expressive romantic affection"),
        ("romantic intimacy is implied", "romantic intimacy is expressive"),
        ("symbolic romantic intimacy", "expressive romantic intimacy"),
        ("Their implied kiss and symbolic embrace.", "Their visible kiss and visible embrace."),
        ("implied kisses; symbolic embraces; romantic touch is implied",
         "visible kisses; visible embraces; romantic touch is visible"),
        ("no kissing; without embracing; avoid holding hands",
         "kissing; embracing; include holding hands"),
        ("avoiding romantic affection, rather than kissing",
         "including romantic affection, and kissing"),
        ("IMPLIED KISS; ROMANCE IS RESTRAINED", "visible kiss; ROMANCE"),
        ('<Implied Kiss> holds a sign saying "no kissing". Their implied embrace.',
         '<Implied Kiss> holds a sign saying "no kissing". Their visible embrace.'),
        ("restrained romantic; no kissing-booth; implied kissable lips",
         "romantic; kissing-booth; implied kissable lips"),
        ("A tender kiss, gentle embrace, and platonic affection.",
         "A tender kiss, gentle embrace, and platonic affection."),
    ],
)
def test_cleanup_romantic_families(prompt: str, expected: str) -> None:
    cleaned = clean_image_prompt(prompt)
    assert cleaned == expected
    assert clean_image_prompt(cleaned) == expected


def test_engine_rejects_conflicting_families() -> None:
    first = ReplacementFamily((("fog", "mist"),), (("no {term}", "{term}"),))
    second = ReplacementFamily((("fog", "clouds"),), (("no {term}", "{term}"),))
    with pytest.raises(ValueError, match="Conflicting replacements for 'no fog'"):
        build_replacements((first, second), {})


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("A restrained romantic scene, without moonlight, no flowers.",
         "A romantic scene, moonlight, flowers."),
        ("A RESTRAINED battle WITHOUT smoke and NO banners.",
         "A battle smoke and banners."),
        ("restrained lighting, without shadows, no clouds. A duel.",
         "lighting, shadows, clouds. A duel."),
        ("A landscape, restrained palette, without shadows, no clouds.",
         "A landscape, restrained palette, without shadows, no clouds."),
        ('<Romantic Violence> beside "a kiss and a duel", no clouds, without shadows, restrained lighting.',
         '<Romantic Violence> beside "a kiss and a duel", no clouds, without shadows, restrained lighting.'),
        ('A duel beside <No Flowers> and "restrained without no". No clouds.',
         'A duel beside <No Flowers> and "restrained without no". clouds.'),
        ("A duel. Unrestrained movement, restraint, withoutness, nobody, no-one.",
         "A duel. Unrestrained movement, restraint, withoutness, nobody, no-one."),
        ("No\nrestrained romance\nwithout flowers", "\nromance\nflowers"),
        ("Romance is restrained.", "Romance."),
        ("A duel, restrained", "A duel,"),
    ],
)
def test_conditional_word_removal(prompt: str, expected: str) -> None:
    cleaned = clean_image_prompt(prompt)
    assert cleaned == expected
    assert clean_image_prompt(cleaned) == expected


def test_custom_conditionally_dropped_words() -> None:
    prompt = 'A duel, MUTED lighting, without clouds, no shadows, restrained palette. <Muted> says "muted".'
    assert clean_image_prompt(prompt, conditionally_dropped_words=("muted",)) == (
        'A duel, lighting, without clouds, no shadows, restrained palette. <Muted> says "muted".'
    )
    assert clean_image_prompt("A muted landscape.", conditionally_dropped_words=("muted",)) == "A muted landscape."


def test_empty_drop_list_disables_conditional_removal() -> None:
    prompt = "A duel, restrained lighting, without clouds, no shadows. Romance is restrained."
    assert clean_image_prompt(prompt, conditionally_dropped_words=()) == (
        "A duel, restrained lighting, without clouds, no shadows. romance is expressive."
    )


def test_configured_drop_words_are_literal() -> None:
    assert clean_image_prompt("A duel, n.o nxo.", conditionally_dropped_words=("n.o",)) == "A duel, nxo."


@pytest.mark.parametrize("word", ["", " ", " no", "no ", "no clouds"])
def test_invalid_drop_words_are_rejected(word: str) -> None:
    with pytest.raises(ValueError, match="nonempty single words"):
        clean_image_prompt("A duel.", conditionally_dropped_words=(word,))


@pytest.mark.parametrize(
    "phrase",
    [
        "forehead touch",
        "foreheads touch",
        "forehead touches",
        "foreheads touches",
        "forehead touching",
        "foreheads touching",
        "forehead meet",
        "foreheads meet",
        "forehead meets",
        "foreheads meets",
        "forehead meeting",
        "foreheads meeting",
        "forehead to forehead",
        "forehead-to-forehead",
        "forehead against forehead",
        "touching foreheads",
        "meeting foreheads",
        "foreheads are touching",
        "foreheads are meeting",
    ],
)
def test_romance_forehead_replacements(phrase: str) -> None:
    prompt = f"A romantic moment, their {phrase}."
    assert clean_image_prompt(prompt) == "A romantic moment, their deep kiss."


@pytest.mark.parametrize(
    "phrase",
    [
        "FOREHEAD TOUCH",
        "Foreheads Touching",
        "forehead MEETING",
        "Forehead To Forehead",
    ],
)
def test_romance_forehead_replacements_case_insensitive(phrase: str) -> None:
    prompt = f"Tender romance: their {phrase}."
    assert clean_image_prompt(prompt) == "Tender romance: their deep kiss."


def test_forehead_replacement_inactive_without_romance_trigger() -> None:
    prompt = "A duel, their foreheads touching in intense combat."
    assert clean_image_prompt(prompt) == "A duel, their foreheads touching in intense combat."


def test_forehead_replacement_ignores_protected_tags_and_quotes() -> None:
    prompt = '<Romantic Scene> beside "tender romance". Their foreheads touching.'
    assert clean_image_prompt(prompt) == '<Romantic Scene> beside "tender romance". Their foreheads touching.'


def test_conditional_replacement_family_trigger_sets() -> None:
    assert TRIGGER_SETS["romance"] == ROMANCE_TRIGGER_SET
    assert TRIGGER_SETS["violence"] == VIOLENCE_TRIGGER_SET
    assert "romance" in ROMANCE_TRIGGER_SET
    assert "violence" in VIOLENCE_TRIGGER_SET

    romance_fam = ConditionalReplacementFamily(
        terms=(("whisper", "shout"),),
        templates=(("{term}", "{term}"),),
        trigger_set="romance",
    )
    assert romance_fam.is_triggered("A romantic evening.") is True
    assert romance_fam.is_triggered("An intense duel.") is False

    violence_fam = ConditionalReplacementFamily(
        terms=(("shield", "armor"),),
        templates=(("{term}", "{term}"),),
        trigger_set="violence",
    )
    assert violence_fam.is_triggered("Combat erupted.") is True
    assert violence_fam.is_triggered("A tender embrace.") is False


def test_conditional_replacement_family_unconditional_when_no_trigger_set() -> None:
    fam = ConditionalReplacementFamily(
        terms=(("stone", "rock"),),
        templates=(("{term}", "{term}"),),
        trigger_set=None,
    )
    assert fam.is_triggered("Any scene text whatsoever") is True


def test_unconditional_family_always_triggered() -> None:
    base_fam = ReplacementFamily(
        terms=(("tree", "oak"),),
        templates=(("{term}", "{term}"),),
    )
    assert base_fam.is_triggered("Unrelated scene") is True


def test_conditional_replacement_family_rejects_unknown_trigger_set() -> None:
    fam = ConditionalReplacementFamily(
        terms=(("wand", "staff"),),
        templates=(("{term}", "{term}"),),
        trigger_set="fantasy",
    )
    with pytest.raises(ValueError, match="Unknown trigger set: 'fantasy'"):
        fam.is_triggered("Magic scene")


def test_conditional_replacement_family_custom_trigger_words() -> None:
    fam = ConditionalReplacementFamily(
        terms=(("breeze", "gale"),),
        templates=(("{term}", "{term}"),),
        trigger_words=("gusty", "tempest"),
    )
    assert fam.is_triggered("A gusty afternoon") is True
    assert fam.is_triggered("A calm sunny day") is False


def test_clean_image_prompt_with_custom_conditional_family() -> None:
    custom_fam = ConditionalReplacementFamily(
        terms=(("clashing swords", "blade sparks"),),
        templates=(("{term}", "{term}"),),
        trigger_set="violence",
    )
    prompt_violent = "A duel, clashing swords in the courtyard."
    assert clean_image_prompt(prompt_violent, conditional_families=(custom_fam,)) == (
        "A duel, blade sparks in the courtyard."
    )
    prompt_peaceful = "A peaceful courtyard, clashing swords on display."
    assert clean_image_prompt(prompt_peaceful, conditional_families=(custom_fam,)) == (
        "A peaceful courtyard, clashing swords on display."
    )

