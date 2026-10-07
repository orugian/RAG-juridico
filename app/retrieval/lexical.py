"""Versioned Portuguese lexical lookup, separate from semantic embeddings.

The same function processes documents and queries. Normalization creates lookup
keys only: it does not change citations, qualify parties, or validate legal dates.
No stop-word removal: negations and every numeric word remain searchable.
"""
import re
import unicodedata
import hashlib
from importlib.metadata import version
from pathlib import Path

from app.identifiers import extract_identifier_candidates
from app import identifiers
from app.ingestion.temporal import iter_date_candidates
from app.ingestion import temporal

LEXICAL_PROFILE_VERSION = "pt-contract-lexical-v1"
BM25_PARAMETERS = {"k1": 1.5, "b": 0.75, "epsilon": 0.25}


def lexical_profile() -> dict:
    """Bind code/parser/Unicode/runtime versions; changes require a rebuild."""
    return {
        "profile_version": LEXICAL_PROFILE_VERSION,
        "bm25_parameters": BM25_PARAMETERS.copy(),
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "identifiers_code_sha256": hashlib.sha256(Path(identifiers.__file__).read_bytes()).hexdigest(),
        "temporal_code_sha256": hashlib.sha256(Path(temporal.__file__).read_bytes()).hexdigest(),
        "temporal_extractor_version": temporal.TEMPORAL_EXTRACTOR_VERSION,
        "unicode_version": unicodedata.unidata_version,
        "packages": {name: version(name) for name in ("rank-bm25", "langchain-community", "langchain-core", "joblib")},
    }


def _words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(char for char in folded if not unicodedata.combining(char))
    return re.findall(r"[^\W_]+", plain, flags=re.UNICODE)


def normalize_party_name(name: str) -> str:
    """A search key, never a unique identity or access-control decision."""
    return " ".join(_words(name))


def lexical_tokens(text: str) -> list[str]:
    """Words plus exact, calendar-validated dates and identifier shape aliases.

    Identifier aliases only normalize masks (no check-digit/legal validation).
    Date aliases match the same literal date across BR, Portuguese and ISO
    formats; they cannot infer signature, effectiveness or temporal precedence.
    """
    tokens = _words(text)
    tokens.extend("identifier:" + value.casefold() for value in extract_identifier_candidates(text))
    tokens.extend("date:" + candidate.normalized_date
                  for candidate in iter_date_candidates(text)
                  if candidate.normalized_date is not None)
    return tokens
