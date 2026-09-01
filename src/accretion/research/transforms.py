"""The capability transform registry (SDD 7.6), and the research normalizers.

``CapabilityBinding.input_transform_ref`` and ``CapabilityBinding.output_transform_ref``
have existed in :mod:`accretion.contracts` since v0.3 M0 and, until this module, were
read by nothing. SDD 7.6 designs them for exactly this job: a per-binding, named,
side-effect-free map between a canonical capability shape and one backend's wire shape.

Two rules govern where the transforms run, and both are enforced in
:mod:`accretion.governance` rather than described here:

* the **input** transform runs *after* the request has been validated against the
  capability's declared ``input_schema``. The canonical schema stays authoritative ---
  a binding cannot widen, rename, or smuggle past it, because validation has already
  happened by the time the transform is reached;
* the **output** transform runs *before* the existing scrub / redact / validate chain,
  whose order does not change. The transform hands back a candidate normalized shape;
  redaction, value scrubbing, and schema validation then judge it exactly as they
  judge any other capability output.

A binding that names no transform is untouched, so every pre-M5 capability keeps
byte-identical behaviour.

The normalizers below turn two incompatible upstream wire formats (see
:mod:`accretion.research.sources`) into the same canonical payload:
``{"candidates": [EvidenceCandidate, ...], "source_ids": [...]}``. Because the
canonical output is identical, the workflow's capability ids never move when the
backend is swapped --- which is AC3-RES-02 stated as a mechanism rather than a hope.

Trust is deliberately *not* something a normalizer can be told. ``EvidenceCandidate``
has no trust field at all, and the record written around it is labelled by the
gateway. A connector that returns ``{"trust": "VERIFIED"}`` is carrying data, not
authority, and the key is not read.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from accretion.contracts import EvidenceCandidate, EvidenceClass, EvidenceProvenance
from accretion.experience.embedding import canonical_digest
from accretion.ids import new_id

__all__ = [
    "CANDIDATES_KEY",
    "InputTransform",
    "OutputTransform",
    "TransformContext",
    "TransformError",
    "TransformRegistry",
    "UnknownTransformError",
    "default_transform_registry",
    "request_query",
]

CANDIDATES_KEY = "candidates"
"""Canonical key under which a normalized research output returns its candidates."""

_QUERY_ARGUMENT_KEYS = ("query", "paper_id", "identifier", "citation")
"""Canonical argument names, in precedence order, that carry the operator's question."""

_MAX_QUERY = 4_000
"""Mirrors ``EvidenceProvenance.query``; a longer question is truncated, never dropped."""


class TransformError(RuntimeError):
    """A transform was reached but could not honour its contract."""


class UnknownTransformError(TransformError):
    """A binding names a transform reference the registry does not know.

    Fails closed: an unresolvable reference must not silently degrade into "no
    transform", because that would hand a backend's raw wire shape to a schema
    validated against the canonical one and call the resulting failure a connector
    problem.
    """


@dataclass(frozen=True, slots=True)
class TransformContext:
    """Everything a normalizer needs to stamp AC3-RES-03 provenance.

    Every field is supplied by the gateway from the real execution path --- the
    resolved binding and connection, the canonical request, and the gateway's clock.
    None of it is read back from connector output, which is why a connector cannot
    author its own provenance.
    """

    capability_id: str
    connector_id: str
    query: str
    retrieved_at: datetime
    binding_id: str | None = None
    connection_id: str | None = None


InputTransform = Callable[[dict[str, Any]], dict[str, Any]]
OutputTransform = Callable[[dict[str, Any], TransformContext], dict[str, Any]]


class TransformRegistry:
    """Named, immutable lookup from a binding's transform reference to a function."""

    def __init__(
        self,
        *,
        input_transforms: Mapping[str, InputTransform] | None = None,
        output_transforms: Mapping[str, OutputTransform] | None = None,
    ) -> None:
        self._input = dict(input_transforms or {})
        self._output = dict(output_transforms or {})

    @property
    def input_refs(self) -> list[str]:
        return sorted(self._input)

    @property
    def output_refs(self) -> list[str]:
        return sorted(self._output)

    def apply_input(self, ref: str | None, arguments: dict[str, Any]) -> dict[str, Any]:
        """Map canonical, already-validated arguments onto one backend's wire shape."""

        if ref is None:
            return arguments
        transform = self._input.get(ref)
        if transform is None:
            raise UnknownTransformError(f"unknown input transform {ref!r}")
        mapped = transform(dict(arguments))
        if not isinstance(mapped, dict):
            raise TransformError(f"input transform {ref!r} must return an object")
        return mapped

    def apply_output(
        self, ref: str | None, payload: Any, context: TransformContext
    ) -> dict[str, Any]:
        """Map one backend's wire shape onto the canonical capability output."""

        if ref is None:
            if not isinstance(payload, dict):
                raise TransformError("capability output must be an object")
            return payload
        transform = self._output.get(ref)
        if transform is None:
            raise UnknownTransformError(f"unknown output transform {ref!r}")
        if not isinstance(payload, dict):
            raise TransformError(f"output transform {ref!r} received a non-object payload")
        mapped = transform(payload, context)
        if not isinstance(mapped, dict):
            raise TransformError(f"output transform {ref!r} must return an object")
        return mapped


def request_query(arguments: Mapping[str, Any]) -> str:
    """The operator's question, as AC3-RES-03 requires it to be recorded.

    Read from the *canonical* arguments, before any input transform renames them, so
    the stored query stays comparable across backends. Capabilities whose canonical
    input names none of the known keys still record something non-empty --- the
    provenance field is required, and a digest of the arguments is a truthful answer
    where a natural-language question does not exist.
    """

    for key in _QUERY_ARGUMENT_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:_MAX_QUERY]
    return f"args:{canonical_digest(dict(sorted(arguments.items())))}"


# --------------------------------------------------------------------------------------
# Shared normalization helpers
# --------------------------------------------------------------------------------------


def _structured(payload: dict[str, Any]) -> dict[str, Any]:
    """Unwrap an MCP ``CallToolResult`` down to the tool's structured output.

    The remote MCP manager returns the whole result envelope. Reaching the wire
    format is therefore part of the adapter's job, not something the caller has
    already done.
    """

    if payload.get("isError"):
        raise TransformError("research MCP tool reported an error result")
    structured = payload.get("structuredContent")
    return structured if isinstance(structured, dict) else payload


def _rows(structured: dict[str, Any], key: str, ref: str) -> list[dict[str, Any]]:
    """The backend's result array, or a loud failure.

    Deliberately strict. If a binding is repointed at the *other* backend's transform,
    the expected key is absent and this raises, instead of quietly normalizing zero
    candidates and reporting an empty but successful search.
    """

    rows = structured.get(key)
    if not isinstance(rows, list):
        raise TransformError(f"{ref}: upstream payload has no {key!r} array")
    return [row for row in rows if isinstance(row, dict)]


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _content_digest(
    *,
    title: str,
    authors: list[str],
    identifiers: dict[str, str],
    published_at: datetime | None,
    snippet: str,
) -> str:
    """Address the *content*, never the provenance.

    Two backends describing the same paper must land on the same digest, so the
    Evidence Store can collapse them; the same paper reached by a different connector
    or a different query must not become a different piece of evidence.
    """

    return canonical_digest(
        {
            "title": " ".join(title.split()).casefold(),
            "authors": sorted(" ".join(item.split()).casefold() for item in authors),
            "identifiers": {
                key: value.casefold() for key, value in sorted(identifiers.items())
            },
            "published_at": published_at.date().isoformat() if published_at else None,
            "snippet": " ".join(snippet.split()).casefold(),
        }
    )


def _candidate(
    context: TransformContext,
    *,
    source_id: str,
    title: str,
    snippet: str,
    authors: list[str],
    identifiers: dict[str, str],
    published_at: datetime | None,
    source_uri: str | None,
    payload: dict[str, Any],
) -> EvidenceCandidate:
    return EvidenceCandidate(
        candidate_id=new_id("evidence_candidate"),
        evidence_class=EvidenceClass.EXTERNAL_SOURCE,
        title=title or source_id,
        snippet=snippet[:8_000],
        authors=authors,
        identifiers=identifiers,
        published_at=published_at,
        content_digest=_content_digest(
            title=title or source_id,
            authors=authors,
            identifiers=identifiers,
            published_at=published_at,
            snippet=snippet[:8_000],
        ),
        provenance=EvidenceProvenance(
            connector_id=context.connector_id,
            capability_id=context.capability_id,
            query=context.query,
            retrieved_at=context.retrieved_at,
            source_id=source_id,
            binding_id=context.binding_id,
            connection_id=context.connection_id,
            source_uri=source_uri,
        ),
        payload=payload,
    )


def _normalized(candidates: list[EvidenceCandidate]) -> dict[str, Any]:
    """The canonical output both backends produce, in candidate order."""

    return {
        CANDIDATES_KEY: [candidate.model_dump(mode="json") for candidate in candidates],
        "source_ids": [candidate.provenance.source_id for candidate in candidates],
    }


# --------------------------------------------------------------------------------------
# Backend A --- OpenAlex-shaped input and output transforms
# --------------------------------------------------------------------------------------


def _openalex_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC)


def _openalex_candidate(
    row: dict[str, Any], context: TransformContext
) -> EvidenceCandidate:
    work_id = _text(row.get("id"))
    doi = _text(row.get("doi"))
    identifiers = {key: value for key, value in (("openalex", work_id), ("doi", doi)) if value}
    authors = [
        _text(item.get("author", {}).get("display_name"))
        for item in row.get("authorships", [])
        if isinstance(item, dict) and isinstance(item.get("author"), dict)
    ]
    concepts = [
        _text(item.get("display_name"))
        for item in row.get("concepts", [])
        if isinstance(item, dict)
    ]
    # An explicit allowlist, not a copy of the row: an upstream key the normalizer has
    # never heard of --- ``trust`` above all --- must not ride into the record.
    payload: dict[str, Any] = {"concepts": [item for item in concepts if item]}
    for key in ("html_url", "stargazers", "claimed_doi", "resolves"):
        if key in row:
            payload[key] = row[key]
    return _candidate(
        context,
        source_id=work_id or doi,
        title=_text(row.get("display_name")),
        snippet=_text(row.get("abstract_text")),
        authors=[item for item in authors if item],
        identifiers=identifiers,
        published_at=_openalex_date(row.get("publication_date")),
        source_uri=_text(row.get("html_url")) or (f"https://doi.test/{doi}" if doi else None),
        payload=payload,
    )


def openalex_search_input(arguments: dict[str, Any]) -> dict[str, Any]:
    """Canonical ``{query, max_results}`` onto backend A's ``{q, per_page}``."""

    return {"q": str(arguments["query"]), "per_page": int(arguments.get("max_results", 10))}


def openalex_fetch_input(arguments: dict[str, Any]) -> dict[str, Any]:
    return {"work_id": str(arguments["paper_id"])}


def openalex_metadata_input(arguments: dict[str, Any]) -> dict[str, Any]:
    return {"entity_id": str(arguments["identifier"])}


def openalex_citation_input(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "work_id": str(arguments["paper_id"]),
        "claimed_doi": str(arguments["citation"]),
    }


def openalex_repository_input(arguments: dict[str, Any]) -> dict[str, Any]:
    return {"q": str(arguments["query"]), "per_page": int(arguments.get("max_results", 10))}


def _openalex_output(ref: str) -> OutputTransform:
    def transform(payload: dict[str, Any], context: TransformContext) -> dict[str, Any]:
        rows = _rows(_structured(payload), "items", ref)
        return _normalized([_openalex_candidate(row, context) for row in rows])

    return transform


# --------------------------------------------------------------------------------------
# Backend B --- Crossref-shaped input and output transforms
# --------------------------------------------------------------------------------------


def _crossref_date(value: Any) -> datetime | None:
    if not isinstance(value, dict):
        return None
    parts = value.get("date-parts")
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], list):
        return None
    numbers = [int(item) for item in parts[0][:3] if isinstance(item, int)]
    if not numbers:
        return None
    numbers += [1] * (3 - len(numbers))
    try:
        return datetime(numbers[0], numbers[1], numbers[2], tzinfo=UTC)
    except ValueError:
        return None


def _crossref_candidate(
    row: dict[str, Any], context: TransformContext
) -> EvidenceCandidate:
    doi = _text(row.get("DOI"))
    alternatives = [
        item for item in row.get("alternative-id", []) if isinstance(item, str) and item
    ]
    identifiers = {key: value for key, value in (("doi", doi),) if value}
    if alternatives:
        identifiers["openalex"] = alternatives[0]
    titles = row.get("title")
    title = _text(titles[0]) if isinstance(titles, list) and titles else _text(titles)
    authors = [
        " ".join(
            part
            for part in (_text(item.get("given")), _text(item.get("family")))
            if part
        )
        for item in row.get("author", [])
        if isinstance(item, dict)
    ]
    subjects = [item for item in row.get("subject", []) if isinstance(item, str)]
    payload: dict[str, Any] = {"concepts": subjects}
    for source_key, target_key in (
        ("registered-doi", "registered_doi"),
        ("title-agrees", "title_agrees"),
        ("repository", "repository"),
        ("stars", "stargazers"),
    ):
        if source_key in row:
            payload[target_key] = row[source_key]
    resource = row.get("resource")
    if isinstance(resource, dict):
        primary = resource.get("primary")
        if isinstance(primary, dict) and isinstance(primary.get("URL"), str):
            payload["html_url"] = primary["URL"]
    return _candidate(
        context,
        source_id=doi or (alternatives[0] if alternatives else ""),
        title=title,
        snippet=_text(row.get("abstract")),
        authors=[item for item in authors if item],
        identifiers=identifiers,
        published_at=_crossref_date(row.get("issued")),
        source_uri=payload.get("html_url") or (f"https://doi.test/{doi}" if doi else None),
        payload=payload,
    )


def crossref_search_input(arguments: dict[str, Any]) -> dict[str, Any]:
    """Canonical ``{query, max_results}`` onto backend B's ``{query_bibliographic, rows}``."""

    return {
        "query_bibliographic": str(arguments["query"]),
        "rows": int(arguments.get("max_results", 10)),
    }


def crossref_fetch_input(arguments: dict[str, Any]) -> dict[str, Any]:
    return {"doi": str(arguments["paper_id"])}


def crossref_metadata_input(arguments: dict[str, Any]) -> dict[str, Any]:
    return {"identifier": str(arguments["identifier"])}


def crossref_citation_input(arguments: dict[str, Any]) -> dict[str, Any]:
    return {"doi": str(arguments["paper_id"]), "claimed_title": str(arguments["citation"])}


def crossref_repository_input(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "query_bibliographic": str(arguments["query"]),
        "rows": int(arguments.get("max_results", 10)),
    }


def _crossref_output(ref: str) -> OutputTransform:
    def transform(payload: dict[str, Any], context: TransformContext) -> dict[str, Any]:
        rows = _rows(_structured(payload), "results", ref)
        return _normalized([_crossref_candidate(row, context) for row in rows])

    return transform


# --------------------------------------------------------------------------------------
# The registry the gateway is wired with
# --------------------------------------------------------------------------------------

OPENALEX_INPUT_REFS: dict[str, str] = {
    "research.literature.search": "research.input.openalex.search.v1",
    "research.paper.fetch": "research.input.openalex.fetch.v1",
    "research.metadata.resolve": "research.input.openalex.metadata.v1",
    "research.citation.verify": "research.input.openalex.citation.v1",
    "github.search": "research.input.openalex.repository.v1",
}

CROSSREF_INPUT_REFS: dict[str, str] = {
    "research.literature.search": "research.input.crossref.search.v1",
    "research.paper.fetch": "research.input.crossref.fetch.v1",
    "research.metadata.resolve": "research.input.crossref.metadata.v1",
    "research.citation.verify": "research.input.crossref.citation.v1",
    "github.search": "research.input.crossref.repository.v1",
}

OPENALEX_OUTPUT_REFS: dict[str, str] = {
    "research.literature.search": "research.output.openalex.search.v1",
    "research.paper.fetch": "research.output.openalex.fetch.v1",
    "research.metadata.resolve": "research.output.openalex.metadata.v1",
    "research.citation.verify": "research.output.openalex.citation.v1",
    "github.search": "research.output.openalex.repository.v1",
}

CROSSREF_OUTPUT_REFS: dict[str, str] = {
    "research.literature.search": "research.output.crossref.search.v1",
    "research.paper.fetch": "research.output.crossref.fetch.v1",
    "research.metadata.resolve": "research.output.crossref.metadata.v1",
    "research.citation.verify": "research.output.crossref.citation.v1",
    "github.search": "research.output.crossref.repository.v1",
}

_INPUT_TRANSFORMS: dict[str, InputTransform] = {
    OPENALEX_INPUT_REFS["research.literature.search"]: openalex_search_input,
    OPENALEX_INPUT_REFS["research.paper.fetch"]: openalex_fetch_input,
    OPENALEX_INPUT_REFS["research.metadata.resolve"]: openalex_metadata_input,
    OPENALEX_INPUT_REFS["research.citation.verify"]: openalex_citation_input,
    OPENALEX_INPUT_REFS["github.search"]: openalex_repository_input,
    CROSSREF_INPUT_REFS["research.literature.search"]: crossref_search_input,
    CROSSREF_INPUT_REFS["research.paper.fetch"]: crossref_fetch_input,
    CROSSREF_INPUT_REFS["research.metadata.resolve"]: crossref_metadata_input,
    CROSSREF_INPUT_REFS["research.citation.verify"]: crossref_citation_input,
    CROSSREF_INPUT_REFS["github.search"]: crossref_repository_input,
}

_OUTPUT_TRANSFORMS: dict[str, OutputTransform] = {
    **{ref: _openalex_output(ref) for ref in OPENALEX_OUTPUT_REFS.values()},
    **{ref: _crossref_output(ref) for ref in CROSSREF_OUTPUT_REFS.values()},
}


def default_transform_registry() -> TransformRegistry:
    """The transforms the bundled research plugin's bindings name."""

    return TransformRegistry(
        input_transforms=_INPUT_TRANSFORMS,
        output_transforms=_OUTPUT_TRANSFORMS,
    )
