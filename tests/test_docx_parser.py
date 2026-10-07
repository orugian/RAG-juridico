"""
Unit tests for the native DOCX parsing engine.

Covers:
- Basic document structure (titles, preambles, clauses, paragraphs, signatures).
- Multilevel numbering reconstruction from numbering.xml (decimal, Roman, letters, ordinal).
- Counter resets across hierarchy levels (e.g. Clause 1 -> § 1º, § 2º -> Clause 2 -> § 1º).
- Track changes detection (w:ins included, w:del excluded, UncertaintyFlag.TRACK_CHANGES_PRESENT).
- Comment extraction and flags (UncertaintyFlag.HAS_COMMENTS).
- Structured table extraction with row/col and column header mapping.
- Unresolved numbering detection (UncertaintyFlag.UNRESOLVED_NUMBERING).
- Error handling on missing, empty, or corrupt files.
- Integration with ParsedDocument model.
"""

import io
import zipfile
from pathlib import Path
import pytest

from app.ingestion.docx_parser import extract_docx_blocks
from app.ingestion.schemas import (
    BlockType,
    ContractMetadata,
    HierarchyLevel,
    ParsedDocument,
    UncertaintyFlag,
)


def _build_docx(
    tmp_path: Path,
    filename: str,
    document_xml: str,
    numbering_xml: str | None = None,
    comments_xml: str | None = None,
) -> Path:
    """Helper to build a valid .docx zip archive in tmp_path with given XML parts."""
    docx_path = tmp_path / filename
    with zipfile.ZipFile(docx_path, "w") as zf:
        zf.writestr("word/document.xml", document_xml)
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
    <Default Extension="xml" ContentType="application/xml"/>
</Types>""",
        )
        if numbering_xml:
            zf.writestr("word/numbering.xml", numbering_xml)
        if comments_xml:
            zf.writestr("word/comments.xml", comments_xml)
    return docx_path


def test_physical_locator_counts_empty_paragraphs_and_distinguishes_table_header(tmp_path):
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p/><w:p><w:r><w:t>CLÁUSULA PRIMEIRA</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Valor</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:p><w:r><w:t>100</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'
    blocks = extract_docx_blocks(_build_docx(tmp_path, "locator.docx", xml), doc_id=1)
    assert blocks[0].order_index == 0
    assert blocks[0].source_locator["body_child_index"] == 1
    table = blocks[-1]
    assert table.text_raw == "Valor: 100"
    assert table.source_locator["source_text"] == "100"
    assert table.source_locator["header_text"] == "Valor"
    assert table.source_locator["body_child_index"] == 2
    assert table.source_locator["row_index"] == 1
    assert table.source_locator["header_row_index"] == 0
    assert table.source_locator["verification"] == "unreviewed"


def test_extract_docx_blocks_simple_document(tmp_path: Path):
    doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body>
            <w:p><w:r><w:t>CONTRATO DE HONORÁRIOS ADVOCATÍCIOS</w:t></w:r></w:p>
            <w:p><w:r><w:t>Pelo presente instrumento particular, de um lado Andrade Advogados...</w:t></w:r></w:p>
            <w:p><w:r><w:t>CLÁUSULA PRIMEIRA - DO OBJETO</w:t></w:r></w:p>
            <w:p><w:r><w:t>O presente contrato tem por objeto a prestação de serviços jurídicos.</w:t></w:r></w:p>
            <w:p><w:r><w:t>§ 1º - Os serviços incluem a fase recursal.</w:t></w:r></w:p>
            <w:p><w:r><w:t>E por estarem de pleno acordo, firmam o presente.</w:t></w:r></w:p>
            <w:p><w:r><w:t>TESTEMUNHAS:</w:t></w:r></w:p>
        </w:body>
    </w:document>"""

    docx_path = _build_docx(tmp_path, "contrato_simples.docx", doc_xml)
    blocks = extract_docx_blocks(docx_path, doc_id=101, doc_version=1)

    assert len(blocks) == 7

    # 1. Title
    assert blocks[0].block_type == BlockType.TITLE
    assert blocks[0].hierarchy_level == HierarchyLevel.TITLE
    assert "CONTRATO DE HONORÁRIOS ADVOCATÍCIOS" in blocks[0].text_raw
    assert blocks[0].parent_clause_id is None
    assert blocks[0].order_index == 0

    # 2. Preamble
    assert blocks[1].block_type == BlockType.PREAMBLE
    assert blocks[1].hierarchy_level == HierarchyLevel.PREAMBLE
    assert "Pelo presente" in blocks[1].text_raw
    assert blocks[1].parent_clause_id is None

    # 3. Clause
    assert blocks[2].block_type == BlockType.CLAUSE
    assert blocks[2].hierarchy_level == HierarchyLevel.CLAUSE
    assert blocks[2].hierarchy_label == "CLÁUSULA PRIMEIRA"
    assert blocks[2].parent_clause_id is None
    clause_id = blocks[2].block_id

    # 4. Paragraph subordinate to Clause
    assert blocks[3].block_type == BlockType.PARAGRAPH
    assert blocks[3].hierarchy_level == HierarchyLevel.PARAGRAPH
    assert blocks[3].parent_clause_id == clause_id

    # 5. § 1º subordinate to Clause
    assert blocks[4].block_type == BlockType.PARAGRAPH
    assert blocks[4].hierarchy_level == HierarchyLevel.PARAGRAPH
    assert blocks[4].hierarchy_label == "§ 1º"
    assert blocks[4].parent_clause_id == clause_id

    # 6 & 7. Signature
    assert blocks[5].block_type == BlockType.SIGNATURE
    assert blocks[5].hierarchy_level == HierarchyLevel.SIGNATURE
    assert blocks[6].block_type == BlockType.SIGNATURE
    assert blocks[6].hierarchy_level == HierarchyLevel.SIGNATURE


def test_extract_docx_blocks_with_multilevel_numbering(tmp_path: Path):
    doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body>
            <w:p><w:r><w:t>CONTRATO DE PRESTAÇÃO DE SERVIÇOS</w:t></w:r></w:p>
            <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>
                 <w:r><w:t>Do Objeto da Prestação de Serviços</w:t></w:r></w:p>
            <w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr></w:pPr>
                 <w:r><w:t>O objeto inclui assessoria consultiva contenciosa.</w:t></w:r></w:p>
            <w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr></w:pPr>
                 <w:r><w:t>Ficam ressalvados os honorários sucumbenciais.</w:t></w:r></w:p>
            <w:p><w:pPr><w:numPr><w:ilvl w:val="2"/><w:numId w:val="1"/></w:numPr></w:pPr>
                 <w:r><w:t>Acompanhamento de recursos nos tribunais superiores.</w:t></w:r></w:p>
            <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>
                 <w:r><w:t>Do Preço e Condições de Pagamento</w:t></w:r></w:p>
            <w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr></w:pPr>
                 <w:r><w:t>O pagamento será mensal e sucessivo.</w:t></w:r></w:p>
        </w:body>
    </w:document>"""

    num_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:abstractNum w:abstractNumId="0">
            <w:lvl w:ilvl="0">
                <w:start w:val="1"/>
                <w:numFmt w:val="decimal"/>
                <w:lvlText w:val="Cláusula %1ª - "/>
            </w:lvl>
            <w:lvl w:ilvl="1">
                <w:start w:val="1"/>
                <w:numFmt w:val="decimal"/>
                <w:lvlText w:val="§ %1º - "/>
            </w:lvl>
            <w:lvl w:ilvl="2">
                <w:start w:val="1"/>
                <w:numFmt w:val="lowerLetter"/>
                <w:lvlText w:val="%1) "/>
            </w:lvl>
        </w:abstractNum>
        <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
    </w:numbering>"""

    docx_path = _build_docx(tmp_path, "multilevel.docx", doc_xml, num_xml)
    blocks = extract_docx_blocks(docx_path, doc_id=501, doc_version=1)

    assert len(blocks) == 7

    # Block 1: Clause 1ª
    clause_1 = blocks[1]
    assert clause_1.block_type == BlockType.CLAUSE
    assert clause_1.hierarchy_level == HierarchyLevel.CLAUSE
    assert clause_1.hierarchy_label == "Cláusula 1ª"
    assert clause_1.text_raw.startswith("Cláusula 1ª - Do Objeto")
    assert clause_1.text_search.startswith("Cláusula 1ª - Do Objeto")

    # Block 2: § 1º under Clause 1
    p1 = blocks[2]
    assert p1.block_type == BlockType.PARAGRAPH
    assert p1.hierarchy_label == "§ 1º"
    assert p1.text_raw.startswith("§ 1º - O objeto inclui")
    assert p1.parent_clause_id == clause_1.block_id

    # Block 3: § 2º under Clause 1
    p2 = blocks[3]
    assert p2.block_type == BlockType.PARAGRAPH
    assert p2.hierarchy_label == "§ 2º"
    assert p2.text_raw.startswith("§ 2º - Ficam ressalvados")
    assert p2.parent_clause_id == clause_1.block_id

    # Block 4: Item a) under Clause 1
    item_a = blocks[4]
    assert item_a.block_type == BlockType.ITEM
    assert item_a.hierarchy_label == "a)"
    assert item_a.text_raw.startswith("a) Acompanhamento")
    assert item_a.parent_clause_id == clause_1.block_id

    # Block 5: Clause 2ª
    clause_2 = blocks[5]
    assert clause_2.block_type == BlockType.CLAUSE
    assert clause_2.hierarchy_label == "Cláusula 2ª"
    assert clause_2.text_raw.startswith("Cláusula 2ª - Do Preço")
    assert clause_2.parent_clause_id is None

    # Block 6: § 1º under Clause 2 (counter restarted!)
    p2_1 = blocks[6]
    assert p2_1.block_type == BlockType.PARAGRAPH
    assert p2_1.hierarchy_label == "§ 1º"
    assert p2_1.text_raw.startswith("§ 1º - O pagamento será")
    assert p2_1.parent_clause_id == clause_2.block_id


def test_extract_docx_blocks_with_track_changes(tmp_path: Path):
    doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body>
            <w:p><w:r><w:t>CLÁUSULA PRIMEIRA - DA VIGÊNCIA</w:t></w:r></w:p>
            <w:p>
                <w:r><w:t>O prazo de vigência será de </w:t></w:r>
                <w:del w:id="1" w:author="Usuario Antigo"><w:r><w:delText>12 meses</w:delText></w:r></w:del>
                <w:ins w:id="2" w:author="Revisor"><w:r><w:t>24 meses</w:t></w:r></w:ins>
                <w:r><w:t>, a contar da assinatura.</w:t></w:r>
            </w:p>
        </w:body>
    </w:document>"""

    docx_path = _build_docx(tmp_path, "revisoes.docx", doc_xml)
    blocks = extract_docx_blocks(docx_path, doc_id=601, doc_version=1)

    assert len(blocks) == 2
    clause_block, body_block = blocks[0], blocks[1]

    assert UncertaintyFlag.TRACK_CHANGES_PRESENT not in clause_block.uncertainty_flags
    assert UncertaintyFlag.TRACK_CHANGES_PRESENT in body_block.uncertainty_flags

    # Verification: deleted text MUST be ignored, inserted text MUST be included
    assert "24 meses" in body_block.text_raw
    assert "12 meses" not in body_block.text_raw
    assert body_block.text_raw == "O prazo de vigência será de 24 meses, a contar da assinatura."


def test_extract_docx_blocks_with_comments(tmp_path: Path):
    doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body>
            <w:p><w:r><w:t>CLÁUSULA SEGUNDA - DA CONFIDENCIALIDADE</w:t></w:r></w:p>
            <w:p>
                <w:commentRangeStart w:id="0"/>
                <w:r><w:t>As partes concordam em manter estrito sigilo.</w:t></w:r>
                <w:commentRangeEnd w:id="0"/>
                <w:r><w:commentReference w:id="0"/></w:r>
            </w:p>
        </w:body>
    </w:document>"""

    comments_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:comment w:id="0" w:author="Advogado"><w:p><w:r><w:t>Verificar prazo da NDA</w:t></w:r></w:p></w:comment>
    </w:comments>"""

    docx_path = _build_docx(tmp_path, "comments.docx", doc_xml, comments_xml=comments_xml)
    blocks = extract_docx_blocks(docx_path, doc_id=701, doc_version=1)

    assert len(blocks) == 2
    commented_block = blocks[1]
    assert UncertaintyFlag.HAS_COMMENTS in commented_block.uncertainty_flags
    assert "As partes concordam em manter estrito sigilo." in commented_block.text_raw


def test_extract_docx_blocks_comments_global_fallback(tmp_path: Path):
    """If comments.xml exists but body paragraphs don't have range anchors, all blocks receive HAS_COMMENTS."""
    doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body>
            <w:p><w:r><w:t>CLÁUSULA ÚNICA</w:t></w:r></w:p>
            <w:p><w:r><w:t>Texto sem âncora explícita.</w:t></w:r></w:p>
        </w:body>
    </w:document>"""

    comments_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:comment w:id="1" w:author="Revisor"><w:p><w:r><w:t>Comentário geral</w:t></w:r></w:p></w:comment>
    </w:comments>"""

    docx_path = _build_docx(tmp_path, "global_comments.docx", doc_xml, comments_xml=comments_xml)
    blocks = extract_docx_blocks(docx_path, doc_id=702, doc_version=1)

    assert len(blocks) == 2
    assert all(UncertaintyFlag.HAS_COMMENTS in b.uncertainty_flags for b in blocks)


def test_extract_docx_blocks_pure_deletion_paragraph_ignored(tmp_path: Path):
    """If an entire paragraph was deleted, it yields no text and is skipped."""
    doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body>
            <w:p><w:r><w:t>CLÁUSULA PRIMEIRA</w:t></w:r></w:p>
            <w:p><w:del w:id="1"><w:r><w:delText>Todo este parágrafo foi excluído.</w:delText></w:r></w:del></w:p>
            <w:p><w:r><w:t>CLÁUSULA SEGUNDA</w:t></w:r></w:p>
        </w:body>
    </w:document>"""

    docx_path = _build_docx(tmp_path, "deleted_p.docx", doc_xml)
    blocks = extract_docx_blocks(docx_path, doc_id=703, doc_version=1)

    assert len(blocks) == 2
    assert blocks[0].text_raw == "CLÁUSULA PRIMEIRA"
    assert blocks[1].text_raw == "CLÁUSULA SEGUNDA"


def test_extract_docx_blocks_with_table(tmp_path: Path):
    doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body>
            <w:p><w:r><w:t>CLÁUSULA TERCEIRA - DOS HONORÁRIOS</w:t></w:r></w:p>
            <w:tbl>
                <w:tr>
                    <w:tc><w:p><w:r><w:t>Fase</w:t></w:r></w:p></w:tc>
                    <w:tc><w:p><w:r><w:t>Prazo</w:t></w:r></w:p></w:tc>
                    <w:tc><w:p><w:r><w:t>Valor</w:t></w:r></w:p></w:tc>
                </w:tr>
                <w:tr>
                    <w:tc><w:p><w:r><w:t>Fase Inicial</w:t></w:r></w:p></w:tc>
                    <w:tc><w:p><w:r><w:t>15 dias</w:t></w:r></w:p></w:tc>
                    <w:tc><w:p><w:r><w:t>R$ 10.000,00</w:t></w:r></w:p></w:tc>
                </w:tr>
                <w:tr>
                    <w:tc><w:p><w:r><w:t>Fase Recursal</w:t></w:r></w:p></w:tc>
                    <w:tc><w:p><w:r><w:t>30 dias</w:t></w:r></w:p></w:tc>
                    <w:tc><w:p><w:r><w:t>R$ 15.000,00</w:t></w:r></w:p></w:tc>
                </w:tr>
            </w:tbl>
        </w:body>
    </w:document>"""

    docx_path = _build_docx(tmp_path, "tabela.docx", doc_xml)
    blocks = extract_docx_blocks(docx_path, doc_id=801, doc_version=1)

    # 1 clause block + 3 header cells + 3 data cells + 3 data cells = 10 blocks
    assert len(blocks) == 10

    clause = blocks[0]
    assert clause.block_type == BlockType.CLAUSE
    assert clause.block_id == "doc_801_blk_0"

    # Header cells
    header_fase = blocks[1]
    assert header_fase.block_type == BlockType.TABLE
    assert header_fase.hierarchy_level == HierarchyLevel.TABLE
    assert header_fase.table_metadata == {"row": 0, "col": 0, "header": "Fase"}
    assert header_fase.text_raw == "Fase"
    assert header_fase.parent_clause_id == clause.block_id

    # Data row 1 cells
    data_fase = blocks[4]
    assert data_fase.block_type == BlockType.TABLE
    assert data_fase.table_metadata == {"row": 1, "col": 0, "header": "Fase"}
    assert "Fase Inicial" in data_fase.text_raw
    assert data_fase.parent_clause_id == clause.block_id

    data_valor = blocks[6]
    assert data_valor.block_type == BlockType.TABLE
    assert data_valor.table_metadata == {"row": 1, "col": 2, "header": "Valor"}
    assert "R$ 10.000,00" in data_valor.text_raw
    assert "Valor:" in data_valor.text_raw
    assert data_valor.parent_clause_id == clause.block_id


def test_extract_docx_blocks_unresolved_numbering(tmp_path: Path):
    doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body>
            <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="999"/></w:numPr></w:pPr>
                 <w:r><w:t>Cláusula com numbering inexistente</w:t></w:r></w:p>
        </w:body>
    </w:document>"""

    docx_path = _build_docx(tmp_path, "unresolved.docx", doc_xml)
    blocks = extract_docx_blocks(docx_path, doc_id=901, doc_version=1)

    assert len(blocks) == 1
    assert UncertaintyFlag.UNRESOLVED_NUMBERING in blocks[0].uncertainty_flags


def test_extract_docx_blocks_invalid_and_corrupt_files(tmp_path: Path):
    # 1. Non-existent file
    with pytest.raises(ValueError, match="inexistente"):
        extract_docx_blocks(tmp_path / "nao_existe.docx", doc_id=1)

    # 2. Empty file
    empty_file = tmp_path / "vazio.docx"
    empty_file.write_bytes(b"")
    with pytest.raises(ValueError, match="vazio"):
        extract_docx_blocks(empty_file, doc_id=1)

    # 3. Corrupt non-zip file
    corrupt_file = tmp_path / "corrompido.docx"
    corrupt_file.write_bytes(b"NOT_A_ZIP_HEADER_DATA")
    with pytest.raises(ValueError, match="corrompido ou inválido"):
        extract_docx_blocks(corrupt_file, doc_id=1)

    # 4. Zip without word/document.xml
    invalid_zip = tmp_path / "no_doc_xml.docx"
    with zipfile.ZipFile(invalid_zip, "w") as zf:
        zf.writestr("test.txt", "hello")
    with pytest.raises(ValueError, match="sem word/document.xml"):
        extract_docx_blocks(invalid_zip, doc_id=1)


def test_extract_docx_blocks_roman_and_letters(tmp_path: Path):
    doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body>
            <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>
                 <w:r><w:t>Primeiro item romano</w:t></w:r></w:p>
            <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>
                 <w:r><w:t>Segundo item romano</w:t></w:r></w:p>
            <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>
                 <w:r><w:t>Terceiro item romano</w:t></w:r></w:p>
            <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>
                 <w:r><w:t>Quarto item romano</w:t></w:r></w:p>
        </w:body>
    </w:document>"""

    num_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:abstractNum w:abstractNumId="10">
            <w:lvl w:ilvl="0">
                <w:start w:val="1"/>
                <w:numFmt w:val="upperRoman"/>
                <w:lvlText w:val="%1 - "/>
            </w:lvl>
        </w:abstractNum>
        <w:num w:numId="1"><w:abstractNumId w:val="10"/></w:num>
    </w:numbering>"""

    docx_path = _build_docx(tmp_path, "roman.docx", doc_xml, num_xml)
    blocks = extract_docx_blocks(docx_path, doc_id=950, doc_version=1)

    assert len(blocks) == 4
    assert blocks[0].text_raw.startswith("I - ")
    assert blocks[1].text_raw.startswith("II - ")
    assert blocks[2].text_raw.startswith("III - ")
    assert blocks[3].text_raw.startswith("IV - ")
    assert blocks[3].hierarchy_label == "IV"


def test_extract_docx_blocks_ordinal_numbering(tmp_path: Path):
    doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body>
            <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>
                 <w:r><w:t>Primeira alteração</w:t></w:r></w:p>
            <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>
                 <w:r><w:t>Segunda alteração</w:t></w:r></w:p>
        </w:body>
    </w:document>"""

    num_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:abstractNum w:abstractNumId="20">
            <w:lvl w:ilvl="0">
                <w:start w:val="1"/>
                <w:numFmt w:val="ordinal"/>
                <w:lvlText w:val="Cláusula %1 - "/>
            </w:lvl>
        </w:abstractNum>
        <w:num w:numId="1"><w:abstractNumId w:val="20"/></w:num>
    </w:numbering>"""

    docx_path = _build_docx(tmp_path, "ordinal.docx", doc_xml, num_xml)
    blocks = extract_docx_blocks(docx_path, doc_id=960, doc_version=1)

    assert len(blocks) == 2
    assert blocks[0].text_raw.startswith("Cláusula 1º - ")
    assert blocks[1].text_raw.startswith("Cláusula 2º - ")


def test_extract_docx_blocks_integrates_with_parsed_document(tmp_path: Path):
    doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body>
            <w:p><w:r><w:t>CONTRATO SOCIAL</w:t></w:r></w:p>
            <w:p><w:r><w:t>CLÁUSULA 1 - DO CAPITAL</w:t></w:r></w:p>
        </w:body>
    </w:document>"""

    docx_path = _build_docx(tmp_path, "integracao.docx", doc_xml)
    blocks = extract_docx_blocks(docx_path, doc_id=990, doc_version=1)

    parsed_doc = ParsedDocument(
        doc_id=990,
        doc_version=1,
        file_path=str(docx_path),
        file_hash="hash123456",
        parser_name="docx_parser",
        parser_version="1.0.0",
        metadata=ContractMetadata(
            formal_title="CONTRATO SOCIAL",
            instrument_type="Contrato Social",
        ),
        blocks=blocks,
        status="success",
    )
    assert parsed_doc.status == "success"
    assert len(parsed_doc.blocks) == 2
