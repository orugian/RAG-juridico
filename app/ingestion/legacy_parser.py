"""
Motor de conversão de formatos legados (.doc binário OLE, .rtf e WordPerfect) via LibreOffice headless.

Executa a conversão para DOCX em processo isolado com perfil de usuário efêmero
(-env:UserInstallation=file:///...), prevenindo colisões concorrentes, deadlocks e poluição
do ambiente do host. Encaminha o documento convertido para a extração canônica do docx_parser.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from app.ingestion.docx_parser import extract_docx_blocks
from app.ingestion.schemas import DocumentBlock


class LegacyParserError(Exception):
    """Exceção base para erros de processamento e conversão de formatos legados."""
    pass


class LibreOfficeNotFoundError(LegacyParserError, FileNotFoundError, RuntimeError):
    """Exceção levantada quando o binário executável do LibreOffice não é localizado."""
    pass


class LibreOfficeConversionError(LegacyParserError, RuntimeError):
    """Exceção levantada quando a conversão via LibreOffice falha ou gera saída inválida."""
    pass


class LibreOfficeTimeoutError(LibreOfficeConversionError, TimeoutError):
    """Exceção levantada quando o tempo limite de execução do LibreOffice expira."""
    pass


def find_libreoffice_binary() -> Optional[str]:
    """
    Localiza o executável do LibreOffice no ambiente atual.

    Ordem de busca:
    1. Variáveis de ambiente LIBREOFFICE_PATH e SOFFICE_PATH (arquivo direto ou pasta contendo binário).
    2. Resolução no PATH do sistema operacional (soffice, libreoffice, soffice.exe, libreoffice.exe).
    3. Caminhos padrão de instalação no Windows (Program Files e Program Files (x86)).

    Retorna:
        str: Caminho absoluto ou comando do executável se localizado, senão None.
    """
    # 1. Variáveis de ambiente explícitas
    for env_var in ("LIBREOFFICE_PATH", "SOFFICE_PATH"):
        val = os.environ.get(env_var)
        if val:
            p = Path(val)
            if p.is_file():
                return str(p)
            if p.is_dir():
                for candidate in ("soffice.exe", "soffice", "libreoffice.exe", "libreoffice"):
                    candidate_path = p / candidate
                    if candidate_path.is_file():
                        return str(candidate_path)
            found = shutil.which(val)
            if found:
                return found

    # 2. Resolução no PATH
    for cmd in ("soffice", "libreoffice", "soffice.exe", "libreoffice.exe"):
        found = shutil.which(cmd)
        if found:
            return found

    # 3. Caminhos padrão do Windows
    windows_candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "LibreOffice" / "program" / "soffice.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "LibreOffice" / "program" / "soffice.exe",
        Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
        Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
    ]
    for cand in windows_candidates:
        try:
            if cand.is_file():
                return str(cand)
        except OSError:
            continue

    return None


def convert_legacy_to_docx(
    input_path: Path | str,
    output_dir: Path | str,
    timeout_sec: int = 30,
    libreoffice_cmd: Optional[str] = None,
) -> Path:
    """
    Converte um arquivo de formato legado (.doc, .rtf, .wpd) para .docx usando LibreOffice headless.

    Executa em ambiente sandbox com perfil efêmero através de `-env:UserInstallation`
    para garantir segurança, concorrência segura e isolamento total de estado.

    Args:
        input_path: Caminho do arquivo de entrada.
        output_dir: Diretório de destino para o arquivo .docx convertido.
        timeout_sec: Limite de tempo em segundos para o término da conversão.
        libreoffice_cmd: Caminho/comando customizado do LibreOffice. Se None, realiza autodetecção.

    Returns:
        Path: Caminho do arquivo .docx gerado.

    Raises:
        FileNotFoundError: Se o arquivo de entrada não existir.
        LibreOfficeNotFoundError: Se o executável do LibreOffice não for localizado.
        LibreOfficeTimeoutError: Se a conversão exceder o timeout.
        LibreOfficeConversionError: Se o LibreOffice retornar erro ou o arquivo .docx não for gerado.
    """
    in_file = Path(input_path)
    if not in_file.exists():
        raise FileNotFoundError(f"Arquivo de entrada não encontrado: {input_path}")
    if not in_file.is_file():
        raise ValueError(f"Caminho de entrada não é um arquivo regular: {input_path}")

    if libreoffice_cmd is None:
        cmd = find_libreoffice_binary()
        if not cmd:
            raise LibreOfficeNotFoundError(
                "Binário do LibreOffice não encontrado no sistema ou variáveis de ambiente."
            )
    else:
        cmd = str(libreoffice_cmd)

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # Diretório temporário efêmero para o perfil de usuário do LibreOffice
    profile_dir = tempfile.mkdtemp(prefix="soffice_profile_")
    try:
        profile_uri = Path(profile_dir).resolve().as_uri()

        # Flags obrigatórias de segurança, headless e isolamento
        cmd_args = [
            cmd,
            "--headless",
            "--invisible",
            "--nocrashreport",
            "--nodefault",
            "--nofirststartwizard",
            "--nologo",
            "--norestore",
            f"-env:UserInstallation={profile_uri}",
            "--convert-to",
            "docx",
            "--outdir",
            str(out_path.resolve()),
            str(in_file.resolve()),
        ]

        expected_docx = out_path / f"{in_file.stem}.docx"
        if expected_docx.exists():
            try:
                expected_docx.unlink()
            except OSError:
                pass

        try:
            proc = subprocess.run(
                cmd_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_sec,
                check=False,
                text=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise LibreOfficeTimeoutError(
                f"Tempo limite ({timeout_sec}s) excedido ao converter {in_file.name} com LibreOffice."
            ) from exc

        if proc.returncode != 0:
            err_msg = (proc.stderr or proc.stdout or "").strip()
            raise LibreOfficeConversionError(
                f"Falha na conversão com LibreOffice (código {proc.returncode}): {err_msg}"
            )

        if not expected_docx.exists():
            raise LibreOfficeConversionError(
                f"Arquivo convertido não encontrado no destino esperado: {expected_docx}"
            )

        return expected_docx
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)


def extract_legacy_blocks(
    legacy_path: Path | str,
    doc_id: int,
    doc_version: int = 1,
    staging_dir: Optional[Path | str] = None,
    timeout_sec: int = 30,
    libreoffice_cmd: Optional[str] = None,
) -> List[DocumentBlock]:
    """
    Executa a extração canônica de blocos de um arquivo em formato legado.

    1. Cria/utiliza diretório de staging para conversão para .docx.
    2. Converte via LibreOffice com perfil isolado.
    3. Extrai blocos estruturados via `extract_docx_blocks`.
    4. Limpa arquivos intermediários quando executado em diretório de staging efêmero.

    Args:
        legacy_path: Caminho do arquivo legado (.doc, .rtf, WordPerfect).
        doc_id: Identificador único do documento.
        doc_version: Versão do documento.
        staging_dir: Diretório de staging intermediário. Se None, cria diretório efêmero.
        timeout_sec: Limite de tempo em segundos para a conversão.
        libreoffice_cmd: Caminho ou comando customizado do LibreOffice. Se None, autodetecta.

    Returns:
        List[DocumentBlock]: Lista de blocos extraídos e normalizados.
    """
    legacy_file = Path(legacy_path)
    if not legacy_file.exists():
        raise FileNotFoundError(f"Arquivo legado não encontrado: {legacy_path}")

    ephemeral = staging_dir is None
    target_staging = (
        Path(tempfile.mkdtemp(prefix="legacy_staging_"))
        if ephemeral
        else Path(staging_dir)
    )
    target_staging.mkdir(parents=True, exist_ok=True)

    try:
        converted_docx = convert_legacy_to_docx(
            input_path=legacy_file,
            output_dir=target_staging,
            timeout_sec=timeout_sec,
            libreoffice_cmd=libreoffice_cmd,
        )
        blocks = extract_docx_blocks(
            converted_docx,
            doc_id,
            doc_version,
        )
        # The locator addresses the conversion, never the original legacy file.
        for block in blocks:
            if block.source_locator:
                block.source_locator = {**block.source_locator, "format": "converted_docx", "original_format": legacy_file.suffix.lower(), "conversion_artifact_retained": False}
        return blocks
    finally:
        if ephemeral:
            shutil.rmtree(target_staging, ignore_errors=True)


__all__ = [
    "find_libreoffice_binary",
    "convert_legacy_to_docx",
    "extract_legacy_blocks",
    "LegacyParserError",
    "LibreOfficeNotFoundError",
    "LibreOfficeConversionError",
    "LibreOfficeTimeoutError",
]
