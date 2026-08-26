"""Two upstream research sources with deliberately incompatible wire formats.

SDD 10.1 puts a *Research MCP / API adapter* between the MCP gateway and whatever
literature service an operator happens to have. This module is the "whatever
service" half: two upstream shapes over one shared body of facts.

They disagree on purpose, and the disagreement is the point. AC3-RES-02 asks that a
research workflow switch connector backend without any canonical workflow capability
id moving. A pair of backends that returned the same JSON would prove nothing --- the
normalizer could be the identity function and the criterion would still appear to
pass. So:

* the OpenAlex-shaped source returns ``{"items": [...], "meta": {...}}`` and names a
  record by an opaque work id, listing authors as nested ``authorship`` objects;
* the Crossref-shaped source returns ``{"results": [...], "total-results": n}`` and
  names a record by DOI, with a *list* for the title and split given/family names.

Every field that could be copied straight across is renamed, renested, or retyped, so
a second backend written by copy-and-paste from the first cannot normalize correctly.

Nothing here performs network I/O. The corpus is a fixed, deterministic body of
records: M5's connectors are faked on purpose (a benchmark over them would measure
the fakes), and ``enable_research_plugin`` plus ``research_allowed_hosts`` gate a real
upstream before any of this reaches the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "CORPUS",
    "Paper",
    "crossref_citation_check",
    "crossref_metadata",
    "crossref_repository_search",
    "crossref_search",
    "crossref_work",
    "openalex_citation_check",
    "openalex_metadata",
    "openalex_repository_search",
    "openalex_search",
    "openalex_work",
]


@dataclass(frozen=True, slots=True)
class Paper:
    """One record in the shared corpus, in neither backend's wire shape.

    ``registered_doi`` is what a DOI resolver would return for ``work_id``. It is
    normally equal to ``claimed_doi``; where it is not, the record is the fixture for
    a citation whose claimed identifier does not survive resolution.
    """

    work_id: str
    claimed_doi: str
    registered_doi: str
    title: str
    authors: tuple[tuple[str, str], ...]
    published: tuple[int, int, int]
    abstract: str
    keywords: tuple[str, ...]
    repository: str = ""
    stars: int = 0

    @property
    def identifier_matches(self) -> bool:
        return self.claimed_doi == self.registered_doi


CORPUS: tuple[Paper, ...] = (
    Paper(
        work_id="W2001",
        claimed_doi="10.1000/retrieval-verification",
        registered_doi="10.1000/retrieval-verification",
        title="Deterministic Verification of Retrieved Evidence",
        authors=(("Ada", "Ostrowski"), ("Kenji", "Mori")),
        published=(2026, 2, 11),
        abstract="A verifier-first account of when retrieved text may be trusted.",
        keywords=("retrieval", "verification", "evidence", "trust"),
        repository="accretion-labs/evidence-verifier",
        stars=412,
    ),
    Paper(
        work_id="W2002",
        # Two records share this DOI: the overlap that makes cross-backend
        # deduplication a real question rather than a decorative one.
        claimed_doi="10.1000/provenance-graphs",
        registered_doi="10.1000/provenance-graphs",
        title="Provenance Graphs for Machine-Gathered Citations",
        authors=(("Ada", "Ostrowski"),),
        published=(2025, 11, 3),
        abstract="Records the connector, capability, and query behind every citation.",
        keywords=("provenance", "citation", "graph", "evidence"),
        repository="accretion-labs/provenance-graphs",
        stars=188,
    ),
    Paper(
        work_id="W2003",
        claimed_doi="10.1000/provenance-graphs",
        registered_doi="10.1000/provenance-graphs",
        title="Provenance Graphs for Machine-Gathered Citations (Extended Report)",
        authors=(("Ada", "Ostrowski"), ("Rosa", "Villalobos")),
        published=(2026, 1, 20),
        abstract="The extended report, sharing a DOI with the conference version.",
        keywords=("provenance", "citation", "graph", "evidence", "report"),
    ),
    Paper(
        work_id="W2004",
        # Claimed and registered disagree: the citation-verification fixture.
        claimed_doi="10.1000/quarantine-unverified",
        registered_doi="10.1000/quarantine-unverified-v2",
        title="Quarantining Unverified External Text",
        authors=(("Kenji", "Mori"),),
        published=(2025, 6, 30),
        abstract="Argues that unverified text should be unrankable, not merely low-ranked.",
        keywords=("trust", "quarantine", "verification", "evidence"),
    ),
    Paper(
        work_id="W2005",
        claimed_doi="10.1000/connector-portability",
        registered_doi="10.1000/connector-portability",
        title="Connector Portability for Canonical Capability Identifiers",
        authors=(("Rosa", "Villalobos"), ("Ada", "Ostrowski")),
        published=(2026, 3, 14),
        abstract="Swapping the backend without moving the capability id.",
        keywords=("connector", "capability", "portability", "retrieval"),
        repository="accretion-labs/connector-portability",
        stars=77,
    ),
    Paper(
        work_id="W2006",
        claimed_doi="10.1000/schema-normalization",
        registered_doi="10.1000/schema-normalization",
        title="Schema Normalization at the Adapter Boundary",
        authors=(("Kenji", "Mori"), ("Rosa", "Villalobos")),
        published=(2024, 9, 8),
        abstract="Where to put the transform when two upstreams disagree on everything.",
        keywords=("schema", "normalization", "adapter", "retrieval"),
    ),
    Paper(
        work_id="W2007",
        claimed_doi="10.1000/content-addressed-evidence",
        registered_doi="10.1000/content-addressed-evidence",
        title="Content-Addressed Evidence Stores",
        authors=(("Ada", "Ostrowski"), ("Kenji", "Mori"), ("Rosa", "Villalobos")),
        published=(2026, 5, 2),
        abstract="Digesting the content, not the provenance, so duplicates collapse.",
        keywords=("evidence", "digest", "store", "provenance"),
        repository="accretion-labs/evidence-store",
        stars=931,
    ),
    Paper(
        work_id="W2008",
        claimed_doi="10.1000/citation-resolution",
        registered_doi="10.1000/citation-resolution",
        title="Citation Resolution Is Not Citation Verification",
        authors=(("Rosa", "Villalobos"),),
        published=(2025, 4, 17),
        abstract="Resolving an identifier says where it points, not that it agrees.",
        keywords=("citation", "verification", "resolution", "trust"),
    ),
)

_BY_WORK_ID = {paper.work_id: paper for paper in CORPUS}


def _matches(paper: Paper, query: str) -> bool:
    terms = [term for term in query.casefold().split() if term]
    if not terms:
        return False
    haystack = " ".join(
        (paper.title, paper.abstract, " ".join(paper.keywords), paper.claimed_doi)
    ).casefold()
    return any(term in haystack for term in terms)


def _ranked(query: str, limit: int) -> list[Paper]:
    """Deterministic relevance: term hits first, then the stable corpus order."""

    terms = [term for term in query.casefold().split() if term]
    scored: list[tuple[int, int, Paper]] = []
    for position, paper in enumerate(CORPUS):
        if not _matches(paper, query):
            continue
        haystack = " ".join(
            (paper.title, paper.abstract, " ".join(paper.keywords))
        ).casefold()
        hits = sum(haystack.count(term) for term in terms)
        scored.append((-hits, position, paper))
    return [paper for _, _, paper in sorted(scored, key=lambda item: item[:2])][:limit]


def _by_doi(doi: str) -> list[Paper]:
    return [paper for paper in CORPUS if paper.claimed_doi == doi]


# --------------------------------------------------------------------------------------
# Backend A --- OpenAlex-shaped: ``items``, opaque work ids, nested authorships
# --------------------------------------------------------------------------------------


def _openalex_item(paper: Paper) -> dict[str, Any]:
    year, month, day = paper.published
    return {
        "id": paper.work_id,
        "display_name": paper.title,
        "doi": paper.claimed_doi,
        "publication_date": f"{year:04d}-{month:02d}-{day:02d}",
        "abstract_text": paper.abstract,
        "authorships": [
            {"author": {"display_name": f"{given} {family}"}}
            for given, family in paper.authors
        ],
        "concepts": [{"display_name": keyword} for keyword in paper.keywords],
    }


def openalex_search(q: str, per_page: int) -> dict[str, Any]:
    items = [_openalex_item(paper) for paper in _ranked(q, per_page)]
    return {"items": items, "meta": {"count": len(items), "per_page": per_page}}


def openalex_work(work_id: str) -> dict[str, Any]:
    paper = _BY_WORK_ID.get(work_id)
    return {"items": [_openalex_item(paper)] if paper else [], "meta": {"count": 1 if paper else 0}}


def openalex_metadata(entity_id: str) -> dict[str, Any]:
    paper = _BY_WORK_ID.get(entity_id)
    if paper is None:
        return {"items": [], "meta": {"count": 0}}
    item = _openalex_item(paper)
    item["ids"] = {"openalex": paper.work_id, "doi": paper.claimed_doi}
    return {"items": [item], "meta": {"count": 1}}


def openalex_citation_check(work_id: str, claimed_doi: str) -> dict[str, Any]:
    paper = _BY_WORK_ID.get(work_id)
    if paper is None:
        return {"items": [], "meta": {"count": 0}}
    item = _openalex_item(paper)
    # The resolver answers with the *registered* identifier, which is not always the
    # one the citing record claimed. That gap is what verification exists to find.
    item["doi"] = paper.registered_doi
    item["claimed_doi"] = claimed_doi
    item["resolves"] = paper.registered_doi == claimed_doi
    return {"items": [item], "meta": {"count": 1}}


def openalex_repository_search(q: str, per_page: int) -> dict[str, Any]:
    items = [
        {
            "id": paper.work_id,
            "display_name": paper.repository,
            "doi": paper.claimed_doi,
            "publication_date": "{:04d}-{:02d}-{:02d}".format(*paper.published),
            "abstract_text": paper.abstract,
            "authorships": [],
            "concepts": [],
            "html_url": f"https://github.test/{paper.repository}",
            "stargazers": paper.stars,
        }
        for paper in _ranked(q, per_page)
        if paper.repository
    ]
    return {"items": items, "meta": {"count": len(items), "per_page": per_page}}


# --------------------------------------------------------------------------------------
# Backend B --- Crossref-shaped: ``results``, DOI identity, list titles, split names
# --------------------------------------------------------------------------------------


def _crossref_result(paper: Paper) -> dict[str, Any]:
    year, month, day = paper.published
    return {
        "DOI": paper.claimed_doi,
        "title": [paper.title],
        "author": [{"given": given, "family": family} for given, family in paper.authors],
        "issued": {"date-parts": [[year, month, day]]},
        "abstract": paper.abstract,
        "subject": list(paper.keywords),
        "alternative-id": [paper.work_id],
    }


def crossref_search(query_bibliographic: str, rows: int) -> dict[str, Any]:
    results = [_crossref_result(paper) for paper in _ranked(query_bibliographic, rows)]
    return {"results": results, "total-results": len(results), "rows": rows}


def crossref_work(doi: str) -> dict[str, Any]:
    results = [_crossref_result(paper) for paper in _by_doi(doi)]
    return {"results": results, "total-results": len(results), "rows": len(results)}


def crossref_metadata(identifier: str) -> dict[str, Any]:
    matched = _by_doi(identifier) or [
        paper for paper in CORPUS if paper.work_id == identifier
    ]
    results = []
    for paper in matched:
        result = _crossref_result(paper)
        result["resource"] = {"primary": {"URL": f"https://doi.test/{paper.claimed_doi}"}}
        results.append(result)
    return {"results": results, "total-results": len(results), "rows": len(results)}


def crossref_citation_check(doi: str, claimed_title: str) -> dict[str, Any]:
    results = []
    for paper in _by_doi(doi):
        result = _crossref_result(paper)
        result["registered-doi"] = paper.registered_doi
        result["title-agrees"] = paper.title.casefold() == claimed_title.casefold()
        results.append(result)
    return {"results": results, "total-results": len(results), "rows": len(results)}


def crossref_repository_search(query_bibliographic: str, rows: int) -> dict[str, Any]:
    results = []
    for paper in _ranked(query_bibliographic, rows):
        if not paper.repository:
            continue
        result = _crossref_result(paper)
        result["resource"] = {"primary": {"URL": f"https://github.test/{paper.repository}"}}
        result["repository"] = paper.repository
        result["stars"] = paper.stars
        results.append(result)
    return {"results": results, "total-results": len(results), "rows": rows}
