"""Literal reference candidates and reviewable structural bindings.

Exact labels only propose links. Materiality and legal effects are never inferred;
the scoped unit decision must review the complete references and dependencies.
"""
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from hashlib import sha256
import json
import re
import unicodedata

from app.contracts import CitationUnit, UnitReference
from app.ingestion.schemas import DocumentBlock


_ORDINALS = {}
for _value, _stem in enumerate(("primeir", "segund", "terceir", "quart", "quint",
                                "sext", "setim", "oitav", "non"), 1):
    for _gender in ("a", "o"):
        _ORDINALS[_stem + _gender] = _value
for _value, _stem in ((10, "decim"), (20, "vigesim"), (30, "trigesim"),
                      (40, "quadragesim"), (50, "quinquagesim"), (60, "sexagesim"),
                      (70, "septuagesim"), (80, "octogesim"), (90, "nonagesim"),
                      (100, "centesim")):
    for _gender in ("a", "o"):
        _ORDINALS[_stem + _gender] = _value
_ORDINALS["unico"] = "unico"
_ORDINALS["unica"] = "unico"
_ONES = "|".join(word for word, value in _ORDINALS.items() if isinstance(value, int) and value < 10)
_TENS = "|".join(word for word, value in _ORDINALS.items()
                 if isinstance(value, int) and 10 <= value <= 90)
_ORDINAL_PATTERN = rf"(?:(?:{_TENS})(?:\s+(?:{_ONES}))?|" + "|".join(_ORDINALS) + ")"
_LABEL_PATTERN = rf"(?:[0-9]+(?:\.[0-9]+)*(?:\s*[ªº°])?|{_ORDINAL_PATTERN}|[ivxlcdm]+|[a-z])(?![\w])"
_RELATIVE_PATTERN = r"(?:anterior|seguinte|precedente|subsequente|acima|abaixo|supra|presente|mesma|a\s+seguir)(?!\w)"
_REFERENCE_PATTERN = re.compile(
    rf"(?<!\w)(?P<kind>clausulas?(?!\w)|cl\.|paragrafos?(?!\w)|§§?|anexos?(?!\w)|"
    rf"itens?(?!\w)|item(?!\w)|artigos?(?!\w)|art\.)"
    rf"(?:\s*(?:(?P<relative>{_RELATIVE_PATTERN})|(?P<label>{_LABEL_PATTERN})))?"
)
_BARE_LABEL_PATTERN = re.compile(_LABEL_PATTERN)
_RANGE_TAIL = re.compile(rf"\s*(?:a(?!\w)|ate(?!\w)|[–—-])\s*(?P<label>{_LABEL_PATTERN})")
_CONJUNCTION_PATTERN = r"(?:e\s*/\s*ou|ou\s*/\s*e|bem\s+como|e|ou)"
_LIST_CONNECTOR_PATTERN = rf"(?:\s*[,;]\s*(?:{_CONJUNCTION_PATTERN}\s+)?|\s+{_CONJUNCTION_PATTERN}\s+)"
_LIST_TAIL = re.compile(rf"{_LIST_CONNECTOR_PATTERN}(?P<label>{_LABEL_PATTERN})")
_LIST_CONNECTOR = re.compile(
    rf"(?:\s*[,;]\s*(?:{_CONJUNCTION_PATTERN}(?:\s+|$))?|\s+{_CONJUNCTION_PATTERN}(?:\s+|$))")
_KIND_BY_WORD = {
    "clausula": "clause", "clausulas": "clause", "cl.": "clause",
    "paragrafo": "paragraph", "paragrafos": "paragraph", "§": "paragraph", "§§": "paragraph",
    "anexo": "annex", "anexos": "annex", "item": "item", "itens": "item",
    "artigo": "article", "artigos": "article", "art.": "article",
}
_STRUCTURAL_KIND = {"clause": "clause", "paragraph": "paragraph", "annex": "annex",
                    "item": "item", "subitem": "item"}
_ROMAN = re.compile(r"m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})$")


def _fold_literal(text: str) -> tuple[str, list[int]]:
    """Accent-insensitive projection with a map back to original codepoint offsets."""
    folded, origins = [], []
    for index, char in enumerate(text):
        for part in unicodedata.normalize("NFD", char).casefold():
            if unicodedata.category(part) != "Mn":
                folded.append(part)
                origins.append(index)
    return "".join(folded), origins


def _normalized_number(label: str) -> str:
    value = label.strip()
    if value in _ORDINALS:
        return str(_ORDINALS[value])
    words = value.split()
    if len(words) == 2 and all(word in _ORDINALS for word in words):
        tens, ones = (_ORDINALS[word] for word in words)
        if isinstance(tens, int) and isinstance(ones, int) and 10 <= tens <= 90 and 1 <= ones < 10:
            return str(tens + ones)
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)*(?:\s*[ªº°])?", value):
        digits = re.sub(r"\s*[ªº°]$", "", value)
        return ".".join(str(int(component)) for component in digits.split("."))
    if value and _ROMAN.fullmatch(value):
        numbers = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
        total, previous = 0, 0
        for letter in reversed(value):
            number = numbers[letter]
            total += -number if number < previous else number
            previous = number
        return str(total)
    # Alphabetic labels and invalid Roman tokens stay distinct; they cannot
    # accidentally acquire a normalized numeral or a legal interpretation.
    return value


def _literal_matches(text: str):
    folded, origins = _fold_literal(text)

    def physical_match(start, end, kind, label):
        start, end = origins[start], origins[end - 1] + 1
        # Include combining marks belonging to the final character as well.
        while end < len(text) and unicodedata.category(text[end]) == "Mn":
            end += 1
        return start, end, kind, f"{kind}:{label}"

    for match in _REFERENCE_PATTERN.finditer(folded):
        kind = _KIND_BY_WORD[match.group("kind")]
        if match.group("relative"):
            label = "relative:" + "_".join(match.group("relative").split())
        elif match.group("label"):
            label = _normalized_number(match.group("label"))
        else:
            label = "unidentified"
        start, end = match.start(), match.end()
        if not match.group("label"):
            yield physical_match(start, end, kind, label)
            continue
        while True:
            interval = _RANGE_TAIL.match(folded, end)
            if interval:
                label = f"range:{label}:{_normalized_number(interval.group('label'))}"
                end = interval.end()
            yield physical_match(start, end, kind, label)
            following = _LIST_TAIL.match(folded, end)
            if not following:
                # An explicit continuation is not evidence of a complete list
                # merely because its next label lies outside this grammar (or
                # in a later physical block). Require reviewed mapping instead.
                incomplete = _LIST_CONNECTOR.match(folded, end)
                if incomplete:
                    yield physical_match(incomplete.start(), incomplete.end(), kind, "unidentified")
                break
            start, end = following.span("label")
            label = _normalized_number(following.group("label"))


def reference_candidates(unit: CitationUnit) -> list[UnitReference]:
    """Re-extract every reference from source-backed text, never synthetic context."""
    unit = CitationUnit.model_validate(unit.model_dump())
    if not unit.text_map:
        raise ValueError("reference_text_map_missing")
    candidates = []
    for entry in unit.text_map:
        span = entry.source_span
        if span is None:
            continue
        text = unit.verbatim_text[entry.unit_start:entry.unit_end]
        for start, end, kind, label in _literal_matches(text):
            physical_start, physical_end = span.start + start, span.start + end
            literal = text[start:end]
            identity = [unit.unit_id, span.source_id, span.block_id, physical_start,
                        physical_end, literal, kind, label]
            digest = sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
            candidates.append(UnitReference(reference_id="ref:" + digest, block_id=span.block_id,
                                            start=physical_start, end=physical_end,
                                            literal=literal, kind=kind, normalized_label=label))
    return candidates


def reference_labels_for_blocks(blocks: Iterable[DocumentBlock]) -> list[str]:
    """Index only declared structural labels; body mentions never create targets."""
    labels = set()
    for block in blocks:
        block_kind = _STRUCTURAL_KIND.get(block.block_type.value)
        if not block_kind or not block.hierarchy_label:
            continue
        label = block.hierarchy_label.strip()
        folded, _ = _fold_literal(label)
        match = _REFERENCE_PATTERN.match(folded)
        if match and match.group("label"):
            kind = _KIND_BY_WORD[match.group("kind")]
            labels.add(f"{kind}:{_normalized_number(match.group('label'))}")
        elif _BARE_LABEL_PATTERN.fullmatch(folded.rstrip(".:)")):
            labels.add(f"{block_kind}:{_normalized_number(folded.rstrip('.:)'))}")
    return sorted(labels)


def _units_by_id(units: Iterable[CitationUnit] | Mapping[str, CitationUnit]) -> dict[str, CitationUnit]:
    values = list(units.values() if isinstance(units, Mapping) else units)
    by_id = {}
    for unit in values:
        if unit.unit_id in by_id:
            raise ValueError("reference_duplicate_unit_id")
        by_id[unit.unit_id] = unit
    if isinstance(units, Mapping) and set(units) != set(by_id):
        raise ValueError("reference_unit_mapping_keys_mismatch")
    return by_id


def _scope(unit: CitationUnit):
    # Bare references name local document structure. Cross-document links need
    # explicit mapping and scoped review, rather than accidental label equality.
    return unit.instrument_id, frozenset(unit.source_ids)


def _label_index(units: Iterable[CitationUnit]):
    index = defaultdict(list)
    for unit in units:
        for label in set(unit.reference_labels):
            index[(_scope(unit), label)].append(unit.unit_id)
    return index


def bind_references(
    units: Iterable[CitationUnit] | Mapping[str, CitationUnit],
    reference_bindings: Mapping[str, Sequence[str]] | None = None,
) -> list[CitationUnit]:
    """Return new candidates with proposed exact or explicitly mapped bindings."""
    by_id = _units_by_id(units)
    index = _label_index(by_id.values())
    extracted = {unit.unit_id: reference_candidates(unit) for unit in by_id.values()}
    bindings = reference_bindings or {}
    reference_ids = {ref.reference_id for refs in extracted.values() for ref in refs}
    if set(bindings) - reference_ids:
        raise ValueError("reference_mapping_unknown_reference")
    result = []
    for unit in by_id.values():
        bound = []
        dependencies = list(unit.closure_unit_ids)
        for candidate in extracted[unit.unit_id]:
            targets, origin = [], "candidate"
            if candidate.reference_id in bindings:
                supplied = bindings[candidate.reference_id]
                if isinstance(supplied, (str, bytes)) or not supplied:
                    raise ValueError("reference_mapping_targets_invalid")
                targets = list(supplied)
                if len(set(targets)) != len(targets) or any(target not in by_id for target in targets):
                    raise ValueError("reference_mapping_target_missing_or_duplicate")
                if any(by_id[target].instrument_id != unit.instrument_id for target in targets):
                    raise ValueError("reference_mapping_crossinstrument_requires_relation")
                origin = "review_mapping"
            else:
                matches = index.get((_scope(unit), candidate.normalized_label), [])
                if (len(matches) == 1 and ":relative:" not in candidate.normalized_label
                        and ":range:" not in candidate.normalized_label
                        and not candidate.normalized_label.endswith(":unidentified")):
                    targets, origin = list(matches), "exact_label"
            candidate = candidate.model_copy(update={"state": "bound" if targets else "pending",
                                                      "target_unit_ids": targets,
                                                      "binding_origin": origin})
            bound.append(candidate)
            for target in targets:
                if target != unit.unit_id and target not in dependencies:
                    dependencies.append(target)
        changes = {"references": bound, "closure_unit_ids": dependencies}
        if unit.references != bound or unit.closure_unit_ids != dependencies:
            changes.update(approval_state="pending_review", review_record_id=None)
        result.append(CitationUnit.model_validate(unit.model_copy(update=changes).model_dump()))
    return result


def validate_references(
    unit: CitationUnit,
    all_units: Iterable[CitationUnit] | Mapping[str, CitationUnit] | None = None,
) -> None:
    """Fail closed for omitted, pending, forged or structurally incomplete links."""
    expected = reference_candidates(unit)
    supplied = [UnitReference.model_validate(ref.model_dump()) for ref in unit.references]
    base_fields = {"reference_id", "block_id", "start", "end", "literal", "kind", "normalized_label"}
    if ([ref.model_dump(include=base_fields) for ref in supplied]
            != [ref.model_dump(include=base_fields) for ref in expected]):
        raise ValueError("reference_candidates_incomplete_or_changed")
    by_id = _units_by_id(all_units) if all_units is not None else None
    index = _label_index(by_id.values()) if by_id is not None else None
    for ref in supplied:
        if ref.state != "bound" or ref.binding_origin not in {"exact_label", "review_mapping"}:
            raise ValueError("reference_binding_unresolved")
        if any(target != unit.unit_id and target not in unit.closure_unit_ids
               for target in ref.target_unit_ids):
            raise ValueError("reference_target_outside_closure")
        if by_id is not None:
            if any(target not in by_id for target in ref.target_unit_ids):
                raise ValueError("reference_target_missing")
            if any(by_id[target].instrument_id != unit.instrument_id for target in ref.target_unit_ids):
                raise ValueError("reference_mapping_crossinstrument_requires_relation")
            if ref.binding_origin == "exact_label":
                matches = index.get((_scope(unit), ref.normalized_label), [])
                if (len(matches) != 1 or ref.target_unit_ids != matches
                        or ":relative:" in ref.normalized_label
                        or ":range:" in ref.normalized_label
                        or ref.normalized_label.endswith(":unidentified")):
                    raise ValueError("reference_exact_label_not_unique")
