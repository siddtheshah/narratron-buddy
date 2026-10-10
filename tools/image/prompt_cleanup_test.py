import pytest

from tools.image.prompt_cleanup import ReplacementFamily, build_replacements, clean_image_prompt


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("A duel, no graphic content, subdued violence, sparks fly.", "A duel, graphic content, explicit violence, sparks fly."),
        ("NO GRAPHIC CONTENT; A duel; Subdued Violence", "graphic content; A duel; explicit violence"),
        ("A duel. No graphic content. Subdued violence.", "A duel. graphic content. explicit violence."),
        ("A duel\nno graphic content\nStyle: oil painting", "A duel\ngraphic content\nStyle: oil painting"),
        ("no graphic content, subdued violence", "graphic content, explicit violence"),
        ("restrained violence", "explicit violence"),
        ("avoiding gore", "including gore"),
        ("without gore", "with gore"),
        ("without explicit gore", "with explicit gore"),
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
        ("A duel,  without gore;\n\nStyle: painting", "A duel,  with gore;\n\nStyle: painting"),
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
        ("avoiding blood; without wounds", "including blood; with wounds"),
        ("rather than explicit gore", "and explicit gore"),
        ("violence is restrained; implied violence", "violence is explicit; explicit violence"),
        ("severance is symbolic; impact is symbolic", "severance is literal; impact is explicit"),
        ("violence is not graphic", "violence is graphic"),
        ("without bloodshed; without explicit violence", "including bloodshed; explicit violence"),
        ('<Avoiding Blood> beside "implied violence", avoiding blood.',
         '<Avoiding Blood> beside "implied violence", including blood.'),
        ("without explicit gore and without gore", "with explicit gore and with gore"),
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


def test_engine_rejects_conflicting_families() -> None:
    first = ReplacementFamily((("fog", "mist"),), (("no {term}", "{term}"),))
    second = ReplacementFamily((("fog", "clouds"),), (("no {term}", "{term}"),))
    with pytest.raises(ValueError, match="Conflicting replacements for 'no fog'"):
        build_replacements((first, second), {})
