"""Canonical lookup keys, preserving alphanumeric CNPJ; never infer OCR corrections.

Shape validation is deliberately separate from check-digit verification and party
qualification. A normalized identifier does not establish identity or permission.
"""
import re


def normalize_identifier(value: str) -> str:
    value = value.strip()
    shapes = (r"[0-9]{11}", r"[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2}", r"[A-Za-z0-9]{12}[0-9]{2}", r"[A-Za-z0-9]{2}\.[A-Za-z0-9]{3}\.[A-Za-z0-9]{3}/[A-Za-z0-9]{4}-[0-9]{2}")
    if not any(re.fullmatch(shape, value, flags=re.ASCII) for shape in shapes):
        raise ValueError("Identificador ou máscara inválida")
    canonical = re.sub(r"[./-]", "", value).upper()
    if not (re.fullmatch(r"[0-9]{11}", canonical) or re.fullmatch(r"[A-Z0-9]{12}[0-9]{2}", canonical)):
        raise ValueError("Identificador deve ser CPF ou CNPJ canônico")
    return canonical


_IN_TEXT = re.compile(r"(?<![A-Z0-9])(?:[A-Z0-9]{2}\.[A-Z0-9]{3}\.[A-Z0-9]{3}/[A-Z0-9]{4}-[0-9]{2}|[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2}|[A-Z0-9]{12}[0-9]{2}|[0-9]{11})(?![A-Z0-9])", re.IGNORECASE | re.ASCII)


def extract_identifier_candidates(text: str) -> list[str]:
    """Shape candidates only; party qualification is a separate reviewed decision."""
    return list(dict.fromkeys(normalize_identifier(match.group()) for match in _IN_TEXT.finditer(text)))
