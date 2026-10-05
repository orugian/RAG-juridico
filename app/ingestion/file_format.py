"""
Detecção do formato REAL de um arquivo pelo conteúdo (magic bytes), independente da extensão.

No acervo M-Files há arquivos ".doc" que são RTF ou WordPerfect: a rota de parsing da Etapa 2
deve ser decidida aqui, pelo conteúdo, e nunca pela extensão declarada.
Leituras são limitadas (cabeçalho/trailer), para não carregar arquivos grandes inteiros em memória.
"""

import zipfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class DetectedFormat(StrEnum):
    DOCX = "docx"
    XLSX = "xlsx"
    OOXML_OTHER = "ooxml_other"
    OLE_DOC = "ole_doc"
    OLE_OTHER = "ole_other"
    RTF = "rtf"
    WORDPERFECT = "wordperfect"
    PDF = "pdf"
    IMAGE = "image"
    EMPTY = "empty"
    UNKNOWN = "unknown"


class ParseRoute(StrEnum):
    DOCX = "docx"                # docx2python direto
    LIBREOFFICE = "libreoffice"  # conversão headless para .docx, depois rota DOCX
    PDF = "pdf"                  # camada de texto nativa + OCR local nas páginas sem texto
    UNSUPPORTED = "unsupported"  # não textual ou ilegível: fora do índice


_ROUTES = {
    DetectedFormat.DOCX: ParseRoute.DOCX,
    DetectedFormat.OLE_DOC: ParseRoute.LIBREOFFICE,
    DetectedFormat.RTF: ParseRoute.LIBREOFFICE,
    DetectedFormat.WORDPERFECT: ParseRoute.LIBREOFFICE,
    DetectedFormat.PDF: ParseRoute.PDF,
}

_OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
_IMAGE_MAGICS = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"II*\x00", b"MM\x00*")
_OLE_SCAN_BYTES = 8 * 1024 * 1024   # diretório OLE fica no início em arquivos Word reais
_PDF_SCAN_BYTES = 64 * 1024         # dicionário /Encrypt fica no trailer (ou no início, se linearizado)
_MAX_MAIN_PART_BYTES = 200 * 1024 * 1024  # document.xml descompactado acima disto é anômalo


@dataclass(frozen=True)
class FormatInfo:
    detected: DetectedFormat
    route: ParseRoute
    extension_mismatch: bool
    corrupt: bool = False
    encrypted: bool = False

    @property
    def parseable(self) -> bool:
        return self.route is not ParseRoute.UNSUPPORTED and not self.corrupt


def _expected_formats(extension: str) -> set[DetectedFormat]:
    return {
        "docx": {DetectedFormat.DOCX},
        "doc": {DetectedFormat.OLE_DOC},
        "rtf": {DetectedFormat.RTF},
        "pdf": {DetectedFormat.PDF},
        "xlsx": {DetectedFormat.XLSX},
        "png": {DetectedFormat.IMAGE},
        "jpg": {DetectedFormat.IMAGE},
        "jpeg": {DetectedFormat.IMAGE},
    }.get(extension.lower().lstrip("."), set())


def _read_head(path: Path, size: int) -> bytes:
    with path.open("rb") as fh:
        return fh.read(size)


def _read_tail(path: Path, size: int) -> bytes:
    with path.open("rb") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - size))
        return fh.read()


def _inspect_zip(path: Path) -> tuple[DetectedFormat, bool]:
    """Valida a estrutura e o CRC apenas da parte principal (evita descompactar o pacote inteiro)."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            main = "word/document.xml" if "word/document.xml" in names else (
                "xl/workbook.xml" if "xl/workbook.xml" in names else None)
            if main is None:
                return DetectedFormat.OOXML_OTHER, False
            if zf.getinfo(main).file_size > _MAX_MAIN_PART_BYTES:  # zip bomb / arquivo anômalo
                return DetectedFormat.OOXML_OTHER, True
            with zf.open(main) as fh:
                while fh.read(1024 * 1024):
                    pass
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError):
        return DetectedFormat.OOXML_OTHER, True
    return (DetectedFormat.DOCX if main == "word/document.xml" else DetectedFormat.XLSX), False


def _inspect_ole(path: Path) -> DetectedFormat:
    marker = "WordDocument".encode("utf-16-le")
    return DetectedFormat.OLE_DOC if marker in _read_head(path, _OLE_SCAN_BYTES) else DetectedFormat.OLE_OTHER


def _pdf_is_encrypted(path: Path) -> bool:
    return b"/Encrypt" in _read_tail(path, _PDF_SCAN_BYTES) or b"/Encrypt" in _read_head(path, _PDF_SCAN_BYTES)


def detect_format(path: Path, declared_extension: str = "") -> FormatInfo:
    expected = _expected_formats(declared_extension)
    try:
        head = _read_head(path, 16)
        corrupt = encrypted = False
        if not head:
            detected = DetectedFormat.EMPTY
        elif head.startswith(b"PK\x03\x04"):
            detected, corrupt = _inspect_zip(path)
        elif head.startswith(_OLE_MAGIC):
            detected = _inspect_ole(path)
        elif head.startswith(b"{\\rtf"):
            detected = DetectedFormat.RTF
        elif head.startswith(b"\xffWPC"):
            detected = DetectedFormat.WORDPERFECT
        elif head.startswith(b"%PDF"):
            detected = DetectedFormat.PDF
            encrypted = _pdf_is_encrypted(path)
        elif head.startswith(_IMAGE_MAGICS):
            detected = DetectedFormat.IMAGE
        else:
            detected = DetectedFormat.UNKNOWN
    except OSError:
        # Arquivo ausente/ilegível (ex.: sync concorrente): tratado como corrompido, nunca derruba a curadoria.
        return FormatInfo(DetectedFormat.UNKNOWN, ParseRoute.UNSUPPORTED, bool(expected), corrupt=True)

    return FormatInfo(
        detected=detected,
        route=_ROUTES.get(detected, ParseRoute.UNSUPPORTED),
        extension_mismatch=bool(expected) and detected not in expected,
        corrupt=corrupt,
        encrypted=encrypted,
    )


__all__ = ["DetectedFormat", "ParseRoute", "FormatInfo", "detect_format"]
