import re

import frappe
from frappe import _
from frappe.utils import cint, now_datetime, strip_html

from helpdesk.api.governance import KNOWLEDGE_MANAGER_ROLE, has_helpdesk_role
from helpdesk.utils import agent_only, is_admin

# Words that say nothing about what a mail is about. A search that scores them
# answers every question with every article, which is the failure mode this
# list exists to prevent: a grounded but wrong answer is worse than silence.
STOP_WORDS = {
    # Swedish
    "och", "att", "det", "den", "som", "för", "med", "har", "vi", "ni", "jag",
    "hur", "vad", "när", "var", "kan", "ska", "skall", "vill", "till", "från",
    "här", "där", "detta", "denna", "dessa", "min", "vår", "våra", "era",
    "hej", "tack", "men", "eller", "inte", "inga", "ingen", "inget", "är",
    "vara", "blir", "bli", "man", "sig", "sin", "sitt", "deras", "hela",
    "mycket", "lång", "långt", "många", "något", "några", "andra", "efter",
    "innan", "under", "över", "utan", "samt", "ännu", "redan", "gärna",
    "behöver", "behöva", "får", "fick", "finns", "göra", "gör", "vet",
    "tar", "ta", "om", "på", "av", "en", "ett", "de", "du", "så", "vid",
    # English
    "the", "and", "for", "with", "that", "this", "have", "has", "are", "was",
    "you", "your", "our", "from", "will", "can", "what", "how", "when",
    "where", "there", "here", "not", "but", "all", "any", "does", "did",
    "would", "should", "could", "please", "hello", "thanks", "about",
}

# A long mail must not turn into a hundred LIKE terms; the twelve longest
# content words carry the topic well enough and keep the query bounded.
MAX_QUERY_WORDS = 12
MIN_WORD_LENGTH = 3

# A word in the title says more about what an article is about than the same
# word somewhere in its body.
TITLE_WEIGHT = 3
BODY_WEIGHT = 1
# The whole query as a substring is still the strongest possible signal, and
# everything that matched before this wave must keep matching.
EXACT_WEIGHT = 100


def _query_words(query: str) -> list:
    """Return the distinctive, case-folded words a query should be searched by."""
    words = []
    for word in re.findall(r"\w+", query.lower(), flags=re.UNICODE):
        if len(word) < MIN_WORD_LENGTH or word in STOP_WORDS or word in words:
            continue
        words.append(word)
    # Longer words are the rarer ones, so they are what a long mail is kept to.
    return sorted(sorted(words), key=len, reverse=True)[:MAX_QUERY_WORDS]


def _matches(word: str, text: str) -> bool:
    """True when the text uses the word, inflected endings included."""
    return re.search(rf"\b{re.escape(word)}", text) is not None


def _score(article, words: list, query: str) -> int:
    """Rank one article by how many distinct query words it uses, title first."""
    title = (article.get("title") or "").lower()
    body = strip_html(article.get("content") or "").lower()
    score = 0
    for word in words:
        if _matches(word, title):
            score += TITLE_WEIGHT
        elif _matches(word, body):
            score += BODY_WEIGHT
    lowered = query.lower()
    if lowered in title or lowered in body:
        score += EXACT_WEIGHT
    return score


@frappe.whitelist()
@agent_only
def search_knowledge(
    query: str, limit: int | None = 5, category: str | None = None
) -> list:
    """Return knowledge-library articles matching the query.

    The AI reply layer retrieves candidate source material through this
    contract; Helpdesk stays the authority over the library itself.

    The match is by word, not by the whole query as one substring: a real
    customer mail is never a substring of an article, so the old search could
    only answer questions someone had already pasted into the library. An
    article that shares no query word is not returned at all — silence is the
    right answer to a question the library does not cover, because a grounded
    answer that is wrong reads as one that was checked.
    """
    if not query:
        return []
    words = _query_words(query)
    filters = {"ai_approved": 1}
    if category:
        filters["category"] = category
    page_length = cint(limit) or 5
    or_filters = [
        [field, "like", f"%{word}%"]
        for word in words or [query]
        for field in ("title", "content")
    ]
    or_filters.append(["title", "like", f"%{query}%"])
    or_filters.append(["content", "like", f"%{query}%"])
    candidates = frappe.get_all(
        "HD Article",
        filters=filters,
        or_filters=or_filters,
        fields=[
            "name", "title", "content", "category", "version",
            "ai_approved_version", "modified",
        ],
        order_by="modified desc",
        # Candidates are ranked in python, so more of them than are returned
        # have to be read; the ceiling keeps a large library bounded.
        limit_page_length=max(page_length * 10, 50),
    )
    ranked = []
    for article in candidates:
        score = _score(article, words, query)
        if score:
            ranked.append((score, article))
    # Stable sort on a list already in modified desc order, so ties keep the
    # recency order this search has always broken ties on.
    ranked.sort(key=lambda row: row[0], reverse=True)
    results = []
    for _ranked_score, article in ranked[:page_length]:
        # The sort key is an implementation detail; callers see the same shape
        # as before, and read fields as attributes.
        article.pop("modified", None)
        results.append(article)
    return results


def _require_knowledge_admin():
    """Curating the library is an administrative act, not an agent action."""
    if is_admin() or has_helpdesk_role(KNOWLEDGE_MANAGER_ROLE):
        return
    frappe.throw(
        _("You are not permitted to administer the knowledge library."),
        frappe.PermissionError,
    )


@frappe.whitelist(methods=["POST"])
@agent_only
def upsert_knowledge_article(
    title: str,
    content: str,
    category: str | None = None,
    article: str | None = None,
) -> dict:
    """Create or update a knowledge-library article as an authorized user."""
    _require_knowledge_admin()
    doc = (
        frappe.get_doc("HD Article", article) if article else frappe.new_doc("HD Article")
    )
    doc.title = title
    doc.content = content
    if category:
        doc.category = category
    doc.save(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist(methods=["POST"])
@agent_only
def approve_knowledge_article(article: str) -> dict:
    """Approve the current version of an article for use in AI replies."""
    _require_knowledge_admin()
    doc = frappe.get_doc("HD Article", article)
    doc.ai_approved = 1
    doc.ai_approved_by = frappe.session.user
    doc.ai_approved_on = now_datetime()
    doc.save(ignore_permissions=True)
    return doc.as_dict()
