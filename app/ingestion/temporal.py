"""Conservative absolute-date candidates; no inferred contractual legal effect.

Offsets refer exclusively to canonical ``DocumentBlock.text_raw``. Event labels
are deterministic, unreviewed candidates, not proof of execution or effectiveness.
"""
from dataclasses import dataclass
from datetime import date
import re
import unicodedata
from typing import Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from app.ingestion.schemas import DocumentBlock, TemporalMention

TEMPORAL_EXTRACTOR_VERSION = "temporal-candidates-v1"


_MONTHS = {name: number for number, name in enumerate(
    ("janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"), 1)}
# One combined pattern prevents the numeric matcher from matching an ISO suffix.
# Exact boundaries reject embedded identifiers and inconsistent separators.
_DATES = re.compile(
    r"(?<![\w/.-])(?:"
    r"(?P<iso_year>[0-9]{4})-(?P<iso_month>[0-9]{2})-(?P<iso_day>[0-9]{2})"
    r"|(?P<num_day>[0-9]{1,2})(?P<sep>[/.-])(?P<num_month>[0-9]{1,2})(?P=sep)(?P<num_year>[0-9]{4})"
    r"|(?:aos\s+)?(?P<text_day>[0-9]{1,2})(?:º)?(?:\s+dias)?\s+(?:do\s+mês\s+)?de\s+"
    r"(?P<text_month>janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\s+de\s+(?P<text_year>[0-9]{4})"
    r")(?!\w)(?![/.-]\w)", re.IGNORECASE,
)


@dataclass(frozen=True)
class DateCandidate:
    normalized_date: str | None
    start: int
    end: int
    raw_text: str


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.casefold()) if not unicodedata.combining(c))


def iter_date_candidates(text: str) -> Iterable[DateCandidate]:
    """Yield all bounded BR/textual/ISO dates, preserving invalid calendar tokens.

    No relative durations, inferred years, locale guessing or partial matches.
    A lexically recognizable but impossible date has ``normalized_date=None``.
    """
    for match in _DATES.finditer(text):
        if match.group("iso_year") is not None:
            year, month, day = (int(match.group("iso_" + field)) for field in ("year", "month", "day"))
        elif match.group("num_year") is not None:
            year, month, day = (int(match.group("num_" + field)) for field in ("year", "month", "day"))
        else:
            year = int(match.group("text_year"))
            month = _MONTHS[_fold(match.group("text_month"))]
            day = int(match.group("text_day"))
        try:
            normalized = date(year, month, day).isoformat()
        except ValueError:
            normalized = None
        yield DateCandidate(normalized, match.start(), match.end(), match.group())


_EVENT_CUES = {
    "signature": re.compile(r"\b(?:assinatura|assinado|assinada|assinam|firmado|firmada|celebrado|celebrada)\b"),
    "effective_start": re.compile(r"\b(?:inicio\s+(?:da\s+|de\s+)?vigencia|vigencia\s+(?:inicia|a\s+partir)|entra(?:ra)?\s+em\s+vigor|vigente\s+a\s+partir)\b"),
    "effective_end": re.compile(r"\b(?:fim|final|termino|encerramento)\s+(?:(?:da|de)\s+)?vigencia\b|\bvigencia\s+(?:ate|encerra|termina)\b"),
    "due": re.compile(r"\b(?:vencimento|vence|vencera|pagamento|pagar|pagavel|quitacao)\b"),
    "termination": re.compile(r"\b(?:rescisao|rescindido|rescindida|distrato)\b"),
    "renewal": re.compile(r"\b(?:renovacao|renovado|renovada|prorrogacao)\b"),
}
_HISTORICAL = re.compile(r"\b(?:anterior|anteriores|original|historica|historico|antigo|antiga|referencia|citado|citada|nascimento|nascido|nascida)\b")
_NONASSERTED = re.compile(
    r"\b(?:nao|nunca|jamais|tampouco|nem|sem|nenhum|nenhuma|ausencia"
    r"|previsto|prevista|previstos|previstas|previsao|estimado|estimada|hipotese"
    r"|condicionado|condicionada|confirmar|se|caso|sera|serao|podera|poderao|devera|deverao"
    r"|teria|teriam|haveria|haveriam|seria|seriam|poderia|poderiam|deveria|deveriam"
    r"|suposto|suposta|supostos|supostas|supostamente|suposicao|eventual|eventuais|eventualmente"
    r"|possivel|possivelmente|presumido|presumida|pretendido|pretendida|hipotetico|hipotetica|hipoteticamente|alegadamente|talvez)\b"
)
_CURRENT_SIGNATURE = re.compile(
    # Positive grammar binds the current subject directly to an affirmative
    # event. An arbitrary intervening phrase can name another instrument.
    r"^\s*(?:data\s+(?:de|da)\s+)?assinatura(?:\s+(?:deste|do\s+presente)\s+(?:contrato|instrumento|termo))?\s*(?::|em)?\s*$"
    r"|^\s*(?:o\s+)?(?:este|presente)\s+(?:contrato|instrumento|termo)\s+"
    r"(?:foi|e|esta|restou)\s+(?:devidamente\s+)?(?:assinado|firmado|celebrado)\s+"
    r"(?:em|aos|na\s+data\s+de)\s*$"
)
_CLOSURE = re.compile(r"^\s*[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'-]{1,80}(?:\s*/\s*[A-Z]{2})?\s*,\s*$")
_NOT_CITY = re.compile(r"\b(?:contrato|instrumento|pagamento|vencimento|vigencia|prazo|assinatura|rescisao|renovacao|referencia|data|historico|historica|nascimento|entregue|entrega|recebido|recebida|quitado|quitada|vencido|vencida|emitido|emitida|aprovado|aprovada)\b")
_VIGENCIA_RANGE = re.compile(
    r"^\s*(?:(?:prazo|periodo)\s+(?:de|da)\s+)?(?:prevista\s+)?"
    r"vigencia\s*(?::\s*)?(?:de|entre)\s*$"
)
_DATE_EVENT_PREFIXES = {
    "due": re.compile(
        r"^\s*(?:(?:o|a)\s+)?(?:data\s+(?:de|do|da)\s+)?(?:pagamento|vencimento|quitacao)"
        r"(?:\s+(?:previsto|prevista|devido|devida|agendado|agendada|vence|vencera))?\s*(?::|em|para)?\s*$"
    ),
    "effective_start": re.compile(
        r"^\s*(?:inicio\s+(?:da\s+|de\s+)?vigencia|vigencia\s+(?:inicia|a\s+partir)"
        r"|entra(?:ra)?\s+em\s+vigor|vigente\s+a\s+partir)\s*(?::|em|de)?\s*$"
    ),
    "effective_end": re.compile(
        r"^\s*(?:(?:fim|final|termino|encerramento)\s+(?:da\s+|de\s+)?vigencia"
        r"|vigencia\s+(?:ate|encerra|termina))\s*(?::|em|de)?\s*$"
    ),
    "termination": re.compile(r"^\s*(?:rescisao|distrato)\s*(?::|em|para)?\s*$"),
    "renewal": re.compile(r"^\s*(?:renovacao|prorrogacao)\s*(?::|em|para)?\s*$"),
}
_LEXICAL_QUALIFIER = re.compile(r"\w")
_CLAUSE_HEADING = re.compile(
    r"^\s*clausula\s+(?:[0-9]+[ºª°]?|[ivxlcdm]+|primeira|segunda|terceira|quarta"
    r"|quinta|sexta|setima|oitava|nona|decima)\s*[:–—-]\s*"
)


@dataclass(frozen=True)
class _EventBinding:
    kind: str
    closing_candidate: bool = False


def _recognize_prefix(prefix: str, signature_block: bool, allow_closing: bool) -> _EventBinding | None:
    """One complete positive grammar shared by frames and their boundaries."""
    # A narrowly recognized typographical clause heading is not an event or
    # an attribution qualifier. Do not trust arbitrary hierarchy metadata or
    # discard any other words: the remaining entire prefix must still bind.
    prefix = _CLAUSE_HEADING.sub("", prefix, count=1)
    if _CURRENT_SIGNATURE.fullmatch(prefix) or (signature_block and re.fullmatch(
        r"\s*(?:assinado|assinada|firmado|firmada)\s+em\s*", prefix
    )):
        return _EventBinding("signature")
    if _VIGENCIA_RANGE.fullmatch(prefix):
        return _EventBinding("validity_range")
    for kind, pattern in _DATE_EVENT_PREFIXES.items():
        if pattern.fullmatch(prefix):
            return _EventBinding(kind)
    if allow_closing and _CLOSURE.fullmatch(prefix) and not _NOT_CITY.search(prefix):
        return _EventBinding("signature", closing_candidate=True)
    return None


def _is_range(candidates: list[DateCandidate], index: int, text: str) -> bool:
    return index + 1 < len(candidates) and _fold(
        text[candidates[index].end:candidates[index + 1].start]
    ).strip() in {"a", "ate"}


def _independent_boundary(
    text: str, end: int, candidates: list[DateCandidate], next_index: int,
    signature_block: bool,
) -> int | None:
    """Propose only an independently bound next dated event, with no qualifier.

    The caller must also prove the preceding frame is qualified. A recognized
    token or a delimiter alone never advances the frame origin.
    """
    if next_index >= len(candidates) or candidates[next_index].normalized_date is None:
        return None
    between = text[end:candidates[next_index].start]
    for delimiter in re.finditer(r"[,;\n.]", between):
        if _LEXICAL_QUALIFIER.search(_fold(between[:delimiter.start()])):
            continue
        prefix_start = end + delimiter.end()
        binding = _recognize_prefix(_fold(text[prefix_start:candidates[next_index].start]), signature_block, False)
        if binding is None:
            continue
        if binding.kind == "validity_range" and not _is_range(candidates, next_index, text):
            continue
        return prefix_start
    return None


def extract_temporal_mentions(blocks: list["DocumentBlock"]) -> list["TemporalMention"]:
    """Preserve unresolved frames across dates until independence is qualified.

    Positive grammar is required on both sides of a boundary. Historical,
    hypothetical, invalid or otherwise unknown frames cannot erase their
    context by introducing another date or a convenient field label. No legal
    relation is inferred to recover a classification from an unresolved frame.
    """
    from app.ingestion.schemas import BlockType, TemporalMention

    mentions = []
    terminal_ids = {b.block_id for b in sorted(blocks, key=lambda b: b.order_index)[-5:]}
    for block in blocks:
        candidates = list(iter_date_candidates(block.text_raw))
        frame_start = 0
        index = 0
        signature_block = block.block_type == BlockType.SIGNATURE
        allow_closing = signature_block or block.block_id in terminal_ids
        while index < len(candidates):
            prefix = _fold(block.text_raw[frame_start:candidates[index].start])
            binding = _recognize_prefix(prefix, signature_block, allow_closing)
            is_range = binding is not None and binding.kind == "validity_range" and _is_range(candidates, index, block.text_raw)
            group = candidates[index:index + (2 if is_range else 1)]
            next_index = index + len(group)
            calendar_valid = all(candidate.normalized_date is not None for candidate in group)
            # A heuristic closing may describe a bare date, but cannot certify
            # independence for a later event in the same block.
            may_split = (
                binding is not None
                and (binding.kind != "validity_range" or is_range)
                and not binding.closing_candidate
                and calendar_valid
            )
            boundary = _independent_boundary(block.text_raw, group[-1].end, candidates, next_index, signature_block) if may_split else None
            suffix_end = boundary if boundary is not None else len(block.text_raw)
            suffix = _fold(block.text_raw[group[-1].end:suffix_end])
            context = prefix + " " + suffix
            flags = []
            if _HISTORICAL.search(context):
                flags.append("historical_reference")
            if _NONASSERTED.search(context):
                flags.append("nonasserted_event_context")
            if binding is None or (binding.kind == "validity_range" and not is_range):
                flags.append("unqualified_event_frame")
            if _LEXICAL_QUALIFIER.search(suffix):
                flags.append("unresolved_event_qualification")
            if is_range and not calendar_valid:
                flags.append("invalid_range_endpoint")
            qualified = not flags and calendar_valid
            signature_uncertain = not qualified and _EVENT_CUES["signature"].search(context) is not None
            for offset, candidate in enumerate(group):
                candidate_flags = flags.copy()
                kind = "unknown"
                if candidate.normalized_date is None:
                    candidate_flags.append("invalid_calendar_date")
                if signature_uncertain:
                    candidate_flags.append("signature_attribution_uncertain")
                if qualified:
                    kind = ("effective_start" if offset == 0 else "effective_end") if is_range else binding.kind
                    if binding.closing_candidate:
                        candidate_flags.append("closing_line_candidate")
                mentions.append(TemporalMention(
                    normalized_date=candidate.normalized_date, kind=kind,
                    block_id=block.block_id, start=candidate.start, end=candidate.end,
                    raw_text=candidate.raw_text, uncertainty_flags=candidate_flags,
                ))
            if qualified and boundary is not None:
                frame_start = boundary
            index = next_index
    return mentions


def summarize_execution_date(mentions: list["TemporalMention"]) -> str | None:
    """Return only an unambiguous signature candidate; never resolve conflicts."""
    if any("signature_attribution_uncertain" in mention.uncertainty_flags for mention in mentions):
        return None
    dates = {m.normalized_date for m in mentions if m.kind == "signature" and m.normalized_date
             and set(m.uncertainty_flags) <= {"closing_line_candidate"}}
    return next(iter(dates)) if len(dates) == 1 else None
