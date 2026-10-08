"""Verify prepared answers and safe fallback to local documentation links."""

from unittest.mock import patch

import pytest

from services.docs_search import DocsSearchIndex, DocsSearchPage
from services.user_help_catalog import UserHelpCatalog
from pricing.pricing_controller import PricingController


@pytest.mark.parametrize("question", ["What is Narratron?", "  WHAT IS NARRATRON!!!  ", "Tell me about Narratron"])
def test_prepared_answer_needs_no_docs_lookup(question: str) -> None:
    index = DocsSearchIndex()
    with patch.object(index, "search") as search:
        answer = UserHelpCatalog(index).answer(question)
    assert "interactive AI narration theater" in answer
    assert "[About Narratron](/docs/about)" in answer
    search.assert_not_called()


def test_complex_question_gets_ranked_documentation_links() -> None:
    index = DocsSearchIndex()
    index.build([
        DocsSearchPage("Beyond20", "/docs/beyond20", '<main><h2 id="setup">Extension setup</h2><p>Forward dice rolls from D&amp;D Beyond using the Beyond20 extension.</p></main>'),
        DocsSearchPage("Adventure writing", "/docs/writing-adventures", '<main><h2>Lore</h2><p>Author world lore and adventure scenes.</p></main>'),
    ])
    answer = UserHelpCatalog(index).answer("How do I forward dice rolls from the Beyond20 extension?")
    assert "closest matches" in answer
    assert "/docs/beyond20#setup" in answer


def test_extended_question_is_not_mistaken_for_a_simple_faq() -> None:
    index = DocsSearchIndex()
    with patch.object(index, "search", return_value=[]) as search:
        answer = UserHelpCatalog(index).answer("What is Narratron and how do I fix missing token images?")
    search.assert_called_once()
    assert "couldn't find a close" in answer
    assert "[Narratron Documentation](/docs)" in answer


@pytest.mark.parametrize("question,expected", [
    ("How do I make a theater?", "[Create Theater](/deploy)"),
    ("How do I create a theater?", "**Deploy Theater**"),
    ("How do I make an account?", "**Sign Up**"),
    ("How do I register?", "**Username**, **Email**, and **Password**"),
])
def test_creation_and_signup_answers_are_prepared(question: str, expected: str) -> None:
    index = DocsSearchIndex()
    with patch.object(index, "search") as search:
        answer = UserHelpCatalog(index).answer(question)
    assert expected in answer
    search.assert_not_called()


@pytest.mark.parametrize("question", ["What are the prices?", "How much does it cost?", "How much does Narratron cost?", "Is Narratron free?"])
def test_pricing_answer_uses_configured_rates(question: str) -> None:
    pricing = PricingController(image_credit_rate=2.5, live_agent_tool_call_credit_rate=0.3)
    index = DocsSearchIndex()
    with patch.object(index, "search") as search:
        catalog = UserHelpCatalog(index, pricing=pricing)
        answer = catalog.answer(question)
        assert "2.5 credits per image" in answer
        assert "0.3 credits per successful answer" in answer
        pricing.image_credit_rate = 4.0
        assert "4 credits per image" in catalog.answer(question)
    assert "[Create Theater](/deploy)" in answer
    assert "Basic help and documentation lookup are free" in answer
    search.assert_not_called()
