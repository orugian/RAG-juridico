"""
Unit tests for app.ingestion.legacy_parser:
LibreOffice binary detection, isolated profile conversion, timeout and error handling,
and end-to-end legacy extraction pipeline.
"""

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

from app.ingestion.legacy_parser import (
    LibreOfficeConversionError,
    LibreOfficeNotFoundError,
    LibreOfficeTimeoutError,
    convert_legacy_to_docx,
    extract_legacy_blocks,
    find_libreoffice_binary,
)
from app.ingestion.schemas import BlockType, DocumentBlock, HierarchyLevel


# ============================================================================
# 1. Binary Detection Tests
# ============================================================================

def test_find_libreoffice_binary_from_libreoffice_path_env(monkeypatch, tmp_path):
    """Detects binary when LIBREOFFICE_PATH environment variable points to a valid file."""
    fake_soffice = tmp_path / "soffice.exe"
    fake_soffice.write_text("fake binary")

    monkeypatch.setenv("LIBREOFFICE_PATH", str(fake_soffice))
    monkeypatch.delenv("SOFFICE_PATH", raising=False)

    found = find_libreoffice_binary()
    assert found == str(fake_soffice)


def test_find_libreoffice_binary_from_soffice_path_env(monkeypatch, tmp_path):
    """Detects binary when SOFFICE_PATH environment variable points to a valid file."""
    fake_soffice = tmp_path / "soffice"
    fake_soffice.write_text("fake binary")

    monkeypatch.delenv("LIBREOFFICE_PATH", raising=False)
    monkeypatch.setenv("SOFFICE_PATH", str(fake_soffice))

    found = find_libreoffice_binary()
    assert found == str(fake_soffice)


def test_find_libreoffice_binary_from_env_dir(monkeypatch, tmp_path):
    """Detects binary when LIBREOFFICE_PATH points to a directory containing soffice.exe."""
    fake_soffice = tmp_path / "soffice.exe"
    fake_soffice.write_text("fake binary")

    monkeypatch.setenv("LIBREOFFICE_PATH", str(tmp_path))
    monkeypatch.delenv("SOFFICE_PATH", raising=False)

    found = find_libreoffice_binary()
    assert found == str(fake_soffice)


def test_find_libreoffice_binary_from_path(monkeypatch, tmp_path):
    """Detects binary via PATH (shutil.which)."""
    fake_soffice = tmp_path / "soffice"
    fake_soffice.write_text("fake binary")

    monkeypatch.delenv("LIBREOFFICE_PATH", raising=False)
    monkeypatch.delenv("SOFFICE_PATH", raising=False)

    def mock_which(cmd):
        if cmd in ("soffice", "soffice.exe"):
            return str(fake_soffice)
        return None

    monkeypatch.setattr("shutil.which", mock_which)

    found = find_libreoffice_binary()
    assert found == str(fake_soffice)


def test_find_libreoffice_binary_windows_standard_path(monkeypatch, tmp_path):
    """Falls back to Windows default installation paths if they exist on disk."""
    fake_prog = tmp_path / "LibreOffice" / "program"
    fake_prog.mkdir(parents=True)
    fake_soffice = fake_prog / "soffice.exe"
    fake_soffice.write_text("fake binary")

    monkeypatch.delenv("LIBREOFFICE_PATH", raising=False)
    monkeypatch.delenv("SOFFICE_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))

    found = find_libreoffice_binary()
    assert found == str(fake_soffice)


def test_find_libreoffice_binary_not_found(monkeypatch):
    """Returns None when LibreOffice is not found in env, PATH, or standard paths."""
    monkeypatch.delenv("LIBREOFFICE_PATH", raising=False)
    monkeypatch.delenv("SOFFICE_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr(Path, "is_file", lambda self: False)

    found = find_libreoffice_binary()
    assert found is None


# ============================================================================
# 2. Conversion & Isolation Flags Tests
# ============================================================================

def test_convert_legacy_to_docx_input_file_missing(tmp_path):
    """Raises FileNotFoundError if input file does not exist."""
    missing = tmp_path / "nonexistent.doc"
    out_dir = tmp_path / "out"

    with pytest.raises(FileNotFoundError, match="não encontrado"):
        convert_legacy_to_docx(missing, output_dir=out_dir)


def test_convert_legacy_to_docx_binary_not_found(tmp_path, monkeypatch):
    """Raises LibreOfficeNotFoundError if binary is not located and not provided."""
    dummy_input = tmp_path / "document.doc"
    dummy_input.write_text("OLE legacy content")
    out_dir = tmp_path / "out"

    monkeypatch.setattr("app.ingestion.legacy_parser.find_libreoffice_binary", lambda: None)

    with pytest.raises(LibreOfficeNotFoundError, match="Binário do LibreOffice não encontrado"):
        convert_legacy_to_docx(dummy_input, output_dir=out_dir)


def test_convert_legacy_to_docx_flags_and_profile_isolation(tmp_path, monkeypatch):
    """
    Verifies that convert_legacy_to_docx executes subprocess.run with all required
    isolation, security, and headless flags, including -env:UserInstallation with file:/// URI.
    """
    dummy_input = tmp_path / "contrato.doc"
    dummy_input.write_text("OLE legacy content")
    out_dir = tmp_path / "out"

    captured_cmd = []
    profile_dir_captured = []

    def mock_run(args, **kwargs):
        captured_cmd.extend(args)
        # Find -env:UserInstallation flag and extract directory path
        for arg in args:
            if arg.startswith("-env:UserInstallation="):
                uri = arg.split("=", 1)[1]
                assert uri.startswith("file://")
                parsed = urlparse(uri)
                path_str = url2pathname(parsed.path)
                # On Windows url2pathname might keep leading slash before drive (e.g. \C:\...)
                if path_str.startswith("\\") and len(path_str) > 2 and path_str[2] == ":":
                    path_str = path_str[1:]
                profile_p = Path(path_str)
                assert profile_p.exists(), f"Profile dir {profile_p} should exist during execution"
                profile_dir_captured.append(profile_p)

        # Simulate LibreOffice producing the output docx
        out_docx = out_dir / "contrato.docx"
        out_docx.write_text("generated docx content")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    result_path = convert_legacy_to_docx(
        input_path=dummy_input,
        output_dir=out_dir,
        timeout_sec=25,
        libreoffice_cmd="soffice",
    )

    assert result_path == out_dir / "contrato.docx"
    assert result_path.exists()

    # Verify all mandatory safety and headless flags
    expected_flags = [
        "--headless",
        "--invisible",
        "--nocrashreport",
        "--nodefault",
        "--nofirststartwizard",
        "--nologo",
        "--norestore",
    ]
    for flag in expected_flags:
        assert flag in captured_cmd, f"Missing flag: {flag}"

    # Verify -env:UserInstallation is present
    user_install_args = [a for a in captured_cmd if a.startswith("-env:UserInstallation=")]
    assert len(user_install_args) == 1
    assert "soffice_profile_" in user_install_args[0]

    # Verify conversion args
    assert "--convert-to" in captured_cmd
    conv_idx = captured_cmd.index("--convert-to")
    assert captured_cmd[conv_idx + 1] == "docx"

    assert "--outdir" in captured_cmd
    out_idx = captured_cmd.index("--outdir")
    assert Path(captured_cmd[out_idx + 1]).resolve() == out_dir.resolve()

    # Verify profile directory cleanup after execution
    assert len(profile_dir_captured) == 1
    assert not profile_dir_captured[0].exists(), "Profile dir must be cleaned up in finally block"


def test_convert_legacy_to_docx_cleans_profile_on_failure(tmp_path, monkeypatch):
    """Ensures profile directory is deleted even if subprocess fails with non-zero exit code."""
    dummy_input = tmp_path / "corrupt.doc"
    dummy_input.write_text("corrupt content")
    out_dir = tmp_path / "out"

    profile_dir_captured = []

    def mock_run(args, **kwargs):
        for arg in args:
            if arg.startswith("-env:UserInstallation="):
                uri = arg.split("=", 1)[1]
                parsed = urlparse(uri)
                path_str = url2pathname(parsed.path)
                if path_str.startswith("\\") and len(path_str) > 2 and path_str[2] == ":":
                    path_str = path_str[1:]
                profile_dir_captured.append(Path(path_str))
        return subprocess.CompletedProcess(
            args=args, returncode=1, stdout="", stderr="Error: cannot open file"
        )

    monkeypatch.setattr(subprocess, "run", mock_run)

    with pytest.raises(LibreOfficeConversionError, match="Falha na conversão com LibreOffice"):
        convert_legacy_to_docx(
            input_path=dummy_input,
            output_dir=out_dir,
            libreoffice_cmd="soffice",
        )

    assert len(profile_dir_captured) == 1
    assert not profile_dir_captured[0].exists(), "Profile dir must be cleaned up after failure"


def test_convert_legacy_to_docx_timeout(tmp_path, monkeypatch):
    """Ensures subprocess.TimeoutExpired is translated into LibreOfficeTimeoutError and cleans up."""
    dummy_input = tmp_path / "heavy.rtf"
    dummy_input.write_text("large rtf content")
    out_dir = tmp_path / "out"

    profile_dir_captured = []

    def mock_run(args, **kwargs):
        for arg in args:
            if arg.startswith("-env:UserInstallation="):
                uri = arg.split("=", 1)[1]
                parsed = urlparse(uri)
                path_str = url2pathname(parsed.path)
                if path_str.startswith("\\") and len(path_str) > 2 and path_str[2] == ":":
                    path_str = path_str[1:]
                profile_dir_captured.append(Path(path_str))
        raise subprocess.TimeoutExpired(cmd=args, timeout=10)

    monkeypatch.setattr(subprocess, "run", mock_run)

    with pytest.raises(LibreOfficeTimeoutError, match="Tempo limite"):
        convert_legacy_to_docx(
            input_path=dummy_input,
            output_dir=out_dir,
            timeout_sec=10,
            libreoffice_cmd="soffice",
        )

    assert len(profile_dir_captured) == 1
    assert not profile_dir_captured[0].exists(), "Profile dir must be cleaned up after timeout"


def test_convert_legacy_to_docx_output_file_missing_after_zero_exit(tmp_path, monkeypatch):
    """Raises LibreOfficeConversionError if LibreOffice exits with 0 but docx file was not created."""
    dummy_input = tmp_path / "silent_fail.doc"
    dummy_input.write_text("content")
    out_dir = tmp_path / "out"

    def mock_run(args, **kwargs):
        # Return 0 but don't create output docx
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    with pytest.raises(LibreOfficeConversionError, match="não encontrado no destino"):
        convert_legacy_to_docx(
            input_path=dummy_input,
            output_dir=out_dir,
            libreoffice_cmd="soffice",
        )


# ============================================================================
# 3. Complete Extraction Pipeline Tests
# ============================================================================

def test_extract_legacy_blocks_ephemeral_staging(tmp_path, monkeypatch):
    """
    Extracts blocks end-to-end with ephemeral staging directory:
    converts to docx, invokes extract_docx_blocks, and cleans up staging directory.
    """
    legacy_file = tmp_path / "minuta.doc"
    legacy_file.write_text("legacy binary content")

    staging_captured = []

    def mock_convert(input_path, output_dir, timeout_sec=30, libreoffice_cmd=None):
        out_p = Path(output_dir)
        staging_captured.append(out_p)
        created = out_p / f"{Path(input_path).stem}.docx"
        created.write_text("mock docx")
        return created

    dummy_block = DocumentBlock(
        block_id="doc_100_blk_0",
        doc_id=100,
        doc_version=1,
        block_type=BlockType.CLAUSE,
        hierarchy_level=HierarchyLevel.CLAUSE,
        hierarchy_label="Cláusula 1ª",
        parent_clause_id=None,
        order_index=0,
        text_raw="Cláusula 1ª - Do Objeto",
        text_search="clausula 1a do objeto",
        spans=[{"start": 0, "end": 22}],
        uncertainty_flags=[],
    )

    mock_extract_docx = MagicMock(return_value=[dummy_block])

    monkeypatch.setattr("app.ingestion.legacy_parser.convert_legacy_to_docx", mock_convert)
    monkeypatch.setattr("app.ingestion.legacy_parser.extract_docx_blocks", mock_extract_docx)

    blocks = extract_legacy_blocks(legacy_file, doc_id=100, doc_version=1)

    assert len(blocks) == 1
    assert blocks[0].block_id == "doc_100_blk_0"
    assert blocks[0].hierarchy_label == "Cláusula 1ª"

    mock_extract_docx.assert_called_once()
    called_docx_arg = mock_extract_docx.call_args[0][0]
    assert called_docx_arg.name == "minuta.docx"

    # Ephemeral staging directory must have been removed
    assert len(staging_captured) == 1
    assert not staging_captured[0].exists(), "Ephemeral staging directory must be cleaned up"


def test_extract_legacy_blocks_custom_staging_preserved(tmp_path, monkeypatch):
    """If user provides explicit staging_dir, it is preserved after extraction."""
    legacy_file = tmp_path / "peticao.rtf"
    legacy_file.write_text("rtf content")
    custom_staging = tmp_path / "my_custom_staging"

    def mock_convert(input_path, output_dir, timeout_sec=30, libreoffice_cmd=None):
        out_p = Path(output_dir)
        created = out_p / f"{Path(input_path).stem}.docx"
        created.write_text("mock docx")
        return created

    mock_extract_docx = MagicMock(return_value=[])

    monkeypatch.setattr("app.ingestion.legacy_parser.convert_legacy_to_docx", mock_convert)
    monkeypatch.setattr("app.ingestion.legacy_parser.extract_docx_blocks", mock_extract_docx)

    blocks = extract_legacy_blocks(
        legacy_file, doc_id=200, doc_version=2, staging_dir=custom_staging
    )

    assert blocks == []
    assert custom_staging.exists(), "Explicit staging dir should be retained"


def test_extract_legacy_blocks_cleans_ephemeral_on_docx_parser_exception(tmp_path, monkeypatch):
    """Ensures ephemeral staging directory is cleaned up even if extract_docx_blocks fails."""
    legacy_file = tmp_path / "quebrado.doc"
    legacy_file.write_text("legacy doc")

    staging_captured = []

    def mock_convert(input_path, output_dir, timeout_sec=30, libreoffice_cmd=None):
        out_p = Path(output_dir)
        staging_captured.append(out_p)
        created = out_p / f"{Path(input_path).stem}.docx"
        created.write_text("mock docx")
        return created

    def mock_extract_docx(path, doc_id, doc_version):
        raise ValueError("Corrupt OOXML structure inside converted document")

    monkeypatch.setattr("app.ingestion.legacy_parser.convert_legacy_to_docx", mock_convert)
    monkeypatch.setattr("app.ingestion.legacy_parser.extract_docx_blocks", mock_extract_docx)

    with pytest.raises(ValueError, match="Corrupt OOXML"):
        extract_legacy_blocks(legacy_file, doc_id=300)

    assert len(staging_captured) == 1
    assert not staging_captured[0].exists(), "Ephemeral staging must be removed even on failure"


def test_extract_legacy_blocks_missing_file(tmp_path):
    """Raises FileNotFoundError if legacy file is missing."""
    missing = tmp_path / "inexistente.doc"
    with pytest.raises(FileNotFoundError, match="não encontrado"):
        extract_legacy_blocks(missing, doc_id=1)


def test_convert_legacy_to_docx_input_is_directory(tmp_path):
    """Raises ValueError if input_path points to a directory rather than a file."""
    sub_dir = tmp_path / "somedir.doc"
    sub_dir.mkdir()
    out_dir = tmp_path / "out"

    with pytest.raises(ValueError, match="não é um arquivo"):
        convert_legacy_to_docx(sub_dir, output_dir=out_dir)


def test_find_libreoffice_binary_windows_x86_standard_path(monkeypatch, tmp_path):
    """Falls back to ProgramFiles(x86) candidate when ProgramFiles is empty."""
    fake_prog_x86 = tmp_path / "x86" / "LibreOffice" / "program"
    fake_prog_x86.mkdir(parents=True)
    fake_soffice = fake_prog_x86 / "soffice.exe"
    fake_soffice.write_text("fake binary")

    monkeypatch.delenv("LIBREOFFICE_PATH", raising=False)
    monkeypatch.delenv("SOFFICE_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "nonexistent_prog"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "x86"))

    found = find_libreoffice_binary()
    assert found == str(fake_soffice)


def test_find_libreoffice_binary_from_dir_with_soffice_no_exe(monkeypatch, tmp_path):
    """Detects binary when SOFFICE_PATH points to a directory containing 'soffice' (POSIX name)."""
    fake_soffice = tmp_path / "soffice"
    fake_soffice.write_text("fake binary")

    monkeypatch.delenv("LIBREOFFICE_PATH", raising=False)
    monkeypatch.setenv("SOFFICE_PATH", str(tmp_path))

    found = find_libreoffice_binary()
    assert found == str(fake_soffice)


def test_convert_legacy_to_docx_cleans_stale_target_file(tmp_path, monkeypatch):
    """Removes pre-existing stale .docx before running conversion to avoid false positives."""
    dummy_input = tmp_path / "documento.doc"
    dummy_input.write_text("content")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    stale_docx = out_dir / "documento.docx"
    stale_docx.write_text("stale docx that should be overwritten")

    def mock_run(args, **kwargs):
        # By the time mock_run is invoked, stale_docx should have been unlinked
        assert not stale_docx.exists(), "Stale docx must be removed prior to running LibreOffice"
        stale_docx.write_text("newly converted docx")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    res = convert_legacy_to_docx(dummy_input, output_dir=out_dir, libreoffice_cmd="soffice")
    assert res == stale_docx
    assert res.read_text() == "newly converted docx"


def test_extract_legacy_blocks_real_docx_integration(tmp_path, monkeypatch):
    """
    End-to-end integration test connecting convert_legacy_to_docx and extract_docx_blocks:
    mock_run generates a genuine minimal OOXML docx, and extract_legacy_blocks returns parsed blocks.
    """
    import zipfile

    legacy_doc = tmp_path / "procuracao.doc"
    legacy_doc.write_text("legacy doc bytes")

    def mock_run(args, **kwargs):
        # Extract output dir and create genuine minimal OOXML docx
        out_idx = args.index("--outdir")
        out_dir = Path(args[out_idx + 1])
        out_docx = out_dir / "procuracao.docx"

        doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
            <w:body>
                <w:p><w:r><w:t>PROCURAÇÃO AD JUDICIA</w:t></w:r></w:p>
                <w:p><w:r><w:t>OUTORGANTE: Empresa Alfa Ltda.</w:t></w:r></w:p>
            </w:body>
        </w:document>"""

        with zipfile.ZipFile(out_docx, "w") as zf:
            zf.writestr("word/document.xml", doc_xml)
            zf.writestr(
                "[Content_Types].xml",
                """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
                <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
                    <Default Extension="xml" ContentType="application/xml"/>
                </Types>""",
            )

        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    blocks = extract_legacy_blocks(
        legacy_path=legacy_doc,
        doc_id=555,
        doc_version=1,
        libreoffice_cmd="soffice",
    )

    assert len(blocks) == 2
    assert blocks[0].block_type == BlockType.TITLE
    assert blocks[0].text_raw == "PROCURAÇÃO AD JUDICIA"
    assert blocks[1].text_raw == "OUTORGANTE: Empresa Alfa Ltda."

