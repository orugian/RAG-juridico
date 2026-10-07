"""
Detecção local de quase-duplicatas por conteúdo (sem dependências externas, sem sair da máquina).

Usada na curadoria para distinguir, entre documentos de MESMO título no mesmo escopo:
  - versões do mesmo instrumento (texto quase idêntico)  -> família de versões (revisão);
  - instrumentos distintos com título genérico (ex.: "Contrato de Mútuo" para partes diferentes).

Escopo atual: .docx (texto de word/document.xml). Outros formatos retornam None: o documento fica
com flags.near_dup_checked=false e é listado em summary.near_dup_unchecked_includes, e a Etapa 2 é
OBRIGADA a completar a comparação sobre o texto parseado antes de indexar.
"""

import html
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from app.identifiers import extract_identifier_candidates

_MAX_XML_BYTES = 20 * 1024 * 1024  # proteção contra zip bomb
_SHINGLE = 5


def docx_text(path: Path) -> Optional[str]:
    try:
        with zipfile.ZipFile(path) as zf:
            info = zf.getinfo("word/document.xml")
            if info.file_size > _MAX_XML_BYTES:
                return None
            xml = zf.read(info).decode("utf-8", errors="ignore")
    except (KeyError, OSError, zipfile.BadZipFile):
        return None
    xml = re.sub(r"</w:p>", "\n", xml)
    return html.unescape(re.sub(r"<[^>]+>", " ", xml))


def shingles(text: str, size: int = _SHINGLE) -> frozenset[str]:
    words = re.findall(r"\w+", text.casefold())
    if len(words) < size:
        return frozenset({" ".join(words)}) if words else frozenset()
    return frozenset(" ".join(words[i:i + size]) for i in range(len(words) - size + 1))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def party_ids(text: str) -> frozenset[str]:
    """Candidatos CPF/CNPJ canônicos; forma não comprova identidade das partes."""
    return frozenset(extract_identifier_candidates(text))


@dataclass(frozen=True)
class Fingerprint:
    shingles: frozenset[str]
    parties: frozenset[str]


def fingerprint(path: Path) -> Optional[Fingerprint]:
    text = docx_text(path)
    return None if text is None else Fingerprint(shingles(text), party_ids(text))


def same_instrument(a: Fingerprint, b: Fingerprint, threshold: float, empty_parties_threshold: float = 0.95) -> bool:
    """
    Versões do mesmo instrumento: texto quase idêntico E mesmas partes (ou partes ainda não preenchidas).
    Texto quase idêntico com CPFs/CNPJs diferentes = o mesmo modelo usado para partes distintas.
    Se ambas as partes estão ausentes (not a.parties and not b.parties), exige similaridade mais estrita
    (empty_parties_threshold) para evitar falsos positivos entre minutas não preenchidas de clientes distintos.
    """
    sim = jaccard(a.shingles, b.shingles)
    if sim < threshold:
        return False
    if not a.parties and not b.parties:
        return sim >= empty_parties_threshold
    return a.parties == b.parties or not a.parties or not b.parties


__all__ = ["docx_text", "shingles", "jaccard", "party_ids", "Fingerprint", "fingerprint", "same_instrument"]
