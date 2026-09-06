import re

import frappe
from frappe import _
from frappe.utils import cint, now_datetime, strip_html

from helpdesk.api.governance import KNOWLEDGE_MANAGER_ROLE, has_helpdesk_role
from helpdesk.utils import agent_only, is_admin

# Words that say nothing about what a mail is about. A search that scores them
# answers every question with every article, which is the failure mode this
# list exists to prevent: a grounded but wrong answer is worse than silence.
# Question words belong here above all: "vilken", "varför" and "hur" are what
# a question is made of, never what it is about.
STOP_WORDS = {
    # Swedish: pronouns, articles, prepositions, conjunctions
    "och", "att", "det", "den", "som", "för", "med", "har", "vi", "ni", "jag",
    "till", "från", "här", "där", "detta", "denna", "dessa", "min", "mitt",
    "mina", "din", "ditt", "dina", "vår", "vårt", "våra", "er", "ert", "era",
    "hej", "tack", "men", "eller", "inte", "inga", "ingen", "inget", "är",
    "vara", "blir", "bli", "man", "sig", "sin", "sitt", "sina", "deras",
    "hans", "hennes", "mig", "dig", "oss", "dem", "hela", "mycket", "lång",
    "långt", "många", "något", "någon", "några", "andra", "efter", "innan",
    "under", "över", "utan", "samt", "ännu", "redan", "gärna", "behöver",
    "behöva", "får", "fick", "finns", "göra", "gör", "vet", "tar", "ta",
    "om", "på", "av", "en", "ett", "de", "du", "så", "vid", "sedan", "förra",
    "nästa", "igen", "just", "ju", "väl", "nog", "hos", "mot", "genom",
    "mellan", "enligt", "angående", "gällande", "mvh", "hälsningar",
    "vänliga", "vänligen", "hälsning",
    # Swedish: interrogatives and adverbs
    "hur", "vad", "när", "var", "vart", "vem", "vems", "vilken", "vilket",
    "vilka", "varför", "varifrån", "kanske", "bara", "också", "samma",
    "olika", "endast", "eftersom", "alltså", "därför", "ändå", "även",
    "dock", "kan", "ska", "skall", "vill", "skulle", "kunde", "måste",
    "borde", "brukar", "både", "annars", "aldrig", "alltid", "ofta",
    "ibland", "nu", "då", "idag", "imorgon", "igår", "snart", "helst",
    "ganska", "väldigt", "mest", "mer", "mindre", "minst", "själv", "själva",
    # English
    "the", "and", "for", "with", "that", "this", "have", "has", "are", "was",
    "you", "your", "our", "from", "will", "can", "what", "how", "when",
    "where", "there", "here", "not", "but", "all", "any", "does", "did",
    "would", "should", "could", "please", "hello", "thanks", "about",
    "which", "who", "why", "maybe", "only", "also", "same", "other",
    "because", "regards", "kind", "best",
}

# A long mail must not turn into a hundred LIKE terms. The first twelve
# content words, in the order the writer used them, carry the topic: a mail
# says what it is about before it says who wrote it, and the longest words in
# a real mail are the signature and the company name, not the topic.
MAX_QUERY_WORDS = 12
MIN_WORD_LENGTH = 3

# Swedish inflects by ending. "leveranstiden" and "leveranstid" are one word
# to the customer and must be one word to the search, in both directions, so
# the query's words and the article's words are cut back to the same crude
# stem before they are compared. Longest ending first, so "arna" is taken
# before "na" before "a"... and a word is never cut below MIN_STEM_LENGTH,
# because "ten" cut to "t" would match everything.
INFLECTION_ENDINGS = (
    "arna", "erna", "orna", "ens", "ets",
    "en", "et", "er", "ar", "na", "ns", "ts",
    "n", "t",
)
MIN_STEM_LENGTH = 4

# A word in the title says more about what an article is about than the same
# word somewhere in its body.
TITLE_WEIGHT = 3
BODY_WEIGHT = 1
# The whole query as a substring is still the strongest possible signal, and
# everything that matched before this wave must keep matching.
EXACT_WEIGHT = 100
# ...but only for a query that is a phrase. A single space or "ok" is a
# substring of every article; a phrase has content and some length to it.
MIN_PHRASE_LENGTH = 12

# The coverage rule. An article answers a question when it covers it, and one
# shared ordinary word is not coverage: "kontakta" in a mail about an invoice
# does not make the returns article an answer. An article covers a question
# when it shares at least this many distinct content words with it in the
# body, or one content word in the title (the title is what the article is
# about), or contains the question as a phrase.
MIN_SHARED_BODY_WORDS = 2

# Candidates are ranked in python, so more of them than are returned have to
# be read. The prefilter already demands a shared word, so what comes back is
# the articles that could possibly answer; the cap only keeps a very large
# library bounded, and is deliberately generous so that an older article on
# exactly the right topic is scored rather than evicted by newer ones.
MAX_CANDIDATES = 300


def _stem(word: str) -> str:
    """Return the crude stem an inflected Swedish word shares with its base form."""
    for ending in INFLECTION_ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= MIN_STEM_LENGTH:
            return word[: -len(ending)]
    return word


def _words(text: str) -> list:
    return re.findall(r"\w+", text.lower(), flags=re.UNICODE)


def _query_words(query: str) -> list:
    """Return the distinctive, case-folded stems a query should be searched by.

    Document order, first come first kept: the topic word is usually short
    and early, and the longest words are the signature.
    """
    stems = []
    for word in _words(query):
        if len(word) < MIN_WORD_LENGTH or word in STOP_WORDS:
            continue
        stem = _stem(word)
        if stem not in stems:
            stems.append(stem)
    return stems[:MAX_QUERY_WORDS]


def _like(term: str) -> str:
    """Return the LIKE pattern for one term, its own wildcards escaped."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _stems_of(text: str) -> set:
    """Return the stems of every word in a text."""
    return {_stem(word) for word in _words(text)}


def _same(a: str, b: str) -> bool:
    """True when two stems are one word: equal, or one the other's prefix past the stem floor."""
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    return len(short) >= MIN_STEM_LENGTH and long.startswith(short)


def _shared(stems: list, text_stems: set) -> list:
    """Return the query stems the text uses, inflected endings included."""
    return [stem for stem in stems if any(_same(stem, c) for c in text_stems)]


def _is_phrase(query: str, stems: list) -> bool:
    """True when the whole query is long enough, and has content, to be a phrase."""
    return bool(stems) and len(query.strip()) >= MIN_PHRASE_LENGTH


def _score(article, stems: list, query: str) -> tuple:
    """Return (score, relevance) for one article, or (0, 0) when it does not cover the query.

    The score ranks; the relevance is the share of the question's content
    words the article covers, 0 to 1, which is what a caller weighing whether
    the answer may be sent unread needs to know.
    """
    title = (article.get("title") or "").lower()
    body = strip_html(article.get("content") or "").lower()
    in_title = _shared(stems, _stems_of(title))
    in_body = [stem for stem in _shared(stems, _stems_of(body)) if stem not in in_title]
    lowered = query.strip().lower()
    exact = _is_phrase(query, stems) and (lowered in title or lowered in body)
    covers = exact or in_title or len(in_body) >= MIN_SHARED_BODY_WORDS
    if not covers:
        return 0, 0
    score = TITLE_WEIGHT * len(in_title) + BODY_WEIGHT * len(in_body)
    if exact:
        score += EXACT_WEIGHT
    relevance = 1.0 if exact else (len(in_title) + len(in_body)) / len(stems)
    return score, round(min(relevance, 1.0), 2)


@frappe.whitelist()
@agent_only
def search_knowledge(
    query: str, limit: int | None = 5, category: str | None = None
) -> list:
    """Return knowledge-library articles matching the query, best match first.

    The AI reply layer retrieves candidate source material through this
    contract; Helpdesk stays the authority over the library itself.

    The match is by word, not by the whole query as one substring: a real
    customer mail is never a substring of an article, so the old search could
    only answer questions someone had already pasted into the library. An
    article that does not cover the query — see MIN_SHARED_BODY_WORDS — is not
    returned at all, and a query without content words returns nothing:
    silence is the right answer to a question the library does not cover,
    because a grounded answer that is wrong reads as one that was checked.

    Each row carries `relevance`, the share of the query's content words the
    article covers, for callers that must weigh the answer.
    """
    stems = _query_words(query or "")
    if not stems:
        return []
    filters = {"ai_approved": 1}
    if category:
        filters["category"] = category
    page_length = cint(limit) or 5
    or_filters = [
        [field, "like", _like(stem)]
        for stem in stems
        for field in ("title", "content")
    ]
    if _is_phrase(query, stems):
        or_filters.append(["title", "like", _like(query.strip())])
        or_filters.append(["content", "like", _like(query.strip())])
    candidates = frappe.get_all(
        "HD Article",
        filters=filters,
        or_filters=or_filters,
        fields=[
            "name", "title", "content", "category", "version",
            "ai_approved_version", "modified",
        ],
        order_by="modified desc, name asc",
        limit_page_length=MAX_CANDIDATES,
    )
    ranked = []
    for article in candidates:
        score, relevance = _score(article, stems, query)
        if score:
            ranked.append((score, relevance, article))
    # Best score first. The sort is stable and the rows arrive in
    # "modified desc, name asc", so ties fall to the newest article and two
    # articles with the same modified stamp cannot swap places between runs.
    ranked.sort(key=lambda row: -row[0])
    results = []
    for _ranked_score, relevance, article in ranked[:page_length]:
        # Callers see the shape they always have, plus the relevance; the
        # candidate rows themselves are left as read.
        row = frappe._dict(
            {key: value for key, value in article.items() if key != "modified"}
        )
        row["relevance"] = relevance
        results.append(row)
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
