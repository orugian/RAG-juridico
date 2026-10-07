"""Conservative local content diagnostics; never synthesize human approval."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

_PLACEHOLDERS = re.compile(r"\[(?:NOME|CLIENTE|CPF|CNPJ|VALOR|DATA|PREENCHER)[^\]]*\]|\bX{3,}\b|_{4,}|<\s*(?:NOME|CLIENTE|PREENCHER)[^>]*>", re.IGNORECASE)


class ContentReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    eligibility: Literal["pending_review", "quarantined", "failed"]
    risks: list[str]


def validate_content(document) -> ContentReport:
    if document.status == "failed":
        return ContentReport(eligibility="failed", risks=["parsing_failed"])
    risks = set()
    if document.status != "success":
        risks.add("metadata_mismatch")
    for block in document.blocks:
        risks.update(flag.value for flag in block.uncertainty_flags)
        if _PLACEHOLDERS.search(block.text_raw):
            risks.add("placeholder")
    if not document.metadata.parties:
        risks.add("missing_parties")
    if document.metadata.instrument_type in {"Aditivo", "Distrato"}:
        risks.add("linked_instrument_pending")
    # Physical provenance is qualified in P2B; offsets alone do not prove origin.
    if any(not block.source_locator or block.source_locator.get("verification") != "approved" for block in document.blocks):
        risks.add("provenance_unqualified")
    return ContentReport(eligibility="quarantined" if risks else "pending_review", risks=sorted(risks))
