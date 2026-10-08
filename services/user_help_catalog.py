"""Prepared FAQs and local documentation lookup, with no model dependency."""

import re

from pricing.pricing_controller import PricingController
from services.docs_search import DocsSearchIndex, docs_search_index


def normalize_question(question: str) -> str:
    return re.sub(r"[^\w]+", " ", question.casefold()).strip()


_FAQ_ENTRIES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("What is Narratron?", "Tell me about Narratron", "What can Narratron do?", "What is Narratron Buddy?"),
     "Narratron is an interactive AI narration theater. It brings together story narration, "
     "dynamic visuals, music, and a shared canvas where people can follow and participate in a story. "
     "You can join a live theater, explore adventures, or host your own.\n\n"
     "Learn more in [About Narratron](/docs/about) and explore [the demos](/demos)."),
    (("How do I join a theater?", "How do I join Narratron?", "How do I use a join key?", "Where do I enter my join key?"),
     "Ask your theater host for the join key, enter it in **Enter Theater Join Key** on the front page, "
     "and click **Join**.\n\nSee the [Virtual Tabletop Guide](/docs/virtual-tabletop) for canvas controls and participation."),
    (("How do I make a theater?", "How do I create a theater?", "How do I host a theater?", "How do I deploy a theater?", "How can I make a theater?"),
     "1. Sign in and open [Create Theater](/deploy).\n"
     "2. Choose **Blank Canvas** for a quick start, **Adventure Mode** for an adventure, "
     "or **Folder Package** to upload a prepared theater. **Build with AI** opens the theater editor.\n"
     "3. Enter a **Theater Name**, configure your chosen path, and click **Deploy Theater**. "
     "Share the join key with your viewers.\n\n"
     "For custom adventures and configuration, see [Writing Adventures](/docs/writing-adventures) "
     "and the [theater.yaml Reference](/docs/theater-yaml)."),
    (("How do I make an account?", "How do I create an account?", "How do I sign up?", "How do I register?", "How can I make an account?"),
     "1. Click **Sign Up** in the navigation bar.\n"
     "2. Enter your **Username**, **Email**, and **Password**.\n"
     "3. Confirm that you are at least 13 years old, then click **Sign Up** to create your account.\n\n"
     "Signing up means agreeing to the [Terms of Service](/terms) and [Privacy Policy](/privacy)."),
    (("What is Adventure Mode?", "How do I start an adventure?", "Where can I find adventures?"),
     "Adventure Mode lets you run interactive adventures with Narratron. Start with "
     "[Explore Adventures](/adventures), or use **Deploy Custom** to create your own.\n\n"
     "For authoring your own adventure, see [Writing Adventures](/docs/writing-adventures)."),
    (("Where are the docs?", "Where is the documentation?", "How do I get help?"),
     "Browse [Narratron Documentation](/docs) for guides, controls, and configuration. "
     "Basic help provides prepared answers and relevant documentation links for free. "
     "Sign in to request personalized research for the price shown on its button."),
    (("How do I report a bug?", "How do I send feedback?", "How do I report a theater?"),
     "Use **Feedback/Report Theater** to report theater activity, file a bug, or suggest an improvement. "
     "See [Feedback and Reporting](/docs/feedback-and-reporting) for the steps."),
)

_FAQ_INDEX = {normalize_question(alias): answer for aliases, answer in _FAQ_ENTRIES for alias in aliases}
_PRICE_QUESTIONS = frozenset(normalize_question(question) for question in (
    "What are the prices?", "How much does it cost?", "How much does Narratron cost?",
    "What does Narratron cost?", "What is the pricing?", "How does pricing work?",
    "Is Narratron free?",
))


class UserHelpCatalog:
    def __init__(self, index: DocsSearchIndex = docs_search_index, pricing: PricingController | None = None) -> None:
        self.index = index
        self.pricing = pricing

    def answer(self, question: str) -> str:
        """Return a known FAQ, or links to the best matching public guides."""
        normalized = normalize_question(question)
        if normalized in _PRICE_QUESTIONS:
            return self._pricing_answer()
        prepared = _FAQ_INDEX.get(normalized)
        if prepared:
            return prepared
        results = self.index.search(question, limit=8)
        links: list[str] = []
        pages: set[str] = set()
        for result in results:
            page = result["href"].split("#", 1)[0]
            if page in pages:
                continue
            pages.add(page)
            links.append(f"- [{result['page_title']} — {result['title']}]({result['href']})")
            if len(links) == 3:
                break
        if links:
            return "These documentation sections are the closest matches to your question:\n\n" + "\n".join(links)
        return ("I couldn't find a close documentation match. Try a specific control name or topic, "
                "or browse [Narratron Documentation](/docs).")

    def _pricing_answer(self) -> str:
        pricing = self.pricing or PricingController.from_env()
        rates = pricing.get_rates()
        return (
            "Narratron uses prepaid credits for paid features; the total depends on what you use. "
            "Basic help and documentation lookup are free. Current usage rates include:\n\n"
            f"- Live-agent tool calls: **{rates['live_agent_tool_call_credit_rate']:g} credits per call**.\n"
            f"- Generated images: **{rates['image_credit_rate']:g} credits per image**.\n"
            f"- Generated music: **{rates['music_credit_rate']:g} credits per track**.\n"
            f"- Adventure/story planning: **{rates['story_planning_credit_rate']:g} credits per action**.\n"
            f"- Personalized help: **{rates['live_agent_tool_call_credit_rate']:g} credits per successful answer**.\n\n"
            "Feature charges can apply together, so these are individual rates rather than a fixed session price. "
            "Open [Create Theater](/deploy) and click **Pricing** for the full rates and calculator; "
            "use the credit balance to see available credit packages."
        )
