import pytest
from pydantic import ValidationError

from app.ingestion.schemas import (
    BlockType,
    ContractMetadata,
    ContractParty,
    DocumentBlock,
    HierarchyLevel,
    ParsedDocument,
    PartyRole,
    UncertaintyFlag,
)


def test_party_role_enum():
    """Verify PartyRole enum members and string representations."""
    expected_roles = {
        "CONTRATANTE",
        "CONTRATADA",
        "LOCADOR",
        "LOCATARIO",
        "VENDEDOR",
        "COMPRADOR",
        "ANUENTE",
        "PARTE_GENERICA",
    }
    actual_names = {role.name for role in PartyRole}
    assert expected_roles == actual_names

    # Test conversion / lookup
    assert PartyRole("contratante") == PartyRole.CONTRATANTE
    assert PartyRole("CONTRATANTE") == PartyRole.CONTRATANTE
    assert PartyRole("parte") == PartyRole.PARTE_GENERICA or PartyRole("parte_generica") == PartyRole.PARTE_GENERICA


def test_contract_party_valid():
    """Verify valid creation of ContractParty with clean identifier and defaults."""
    # Law firm party
    party_law = ContractParty(
        name="Andrade Advogados Associados",
        role=PartyRole.CONTRATADA,
        clean_identifier="12345678000190",
        raw_identifier="12.345.678/0001-90",
        is_law_firm=True,
    )
    assert party_law.name == "Andrade Advogados Associados"
    assert party_law.role == PartyRole.CONTRATADA
    assert party_law.clean_identifier == "12345678000190"
    assert party_law.raw_identifier == "12.345.678/0001-90"
    assert party_law.is_law_firm is True

    # Party with defaults
    party_client = ContractParty(name="Cliente Teste")
    assert party_client.role == PartyRole.PARTE_GENERICA
    assert party_client.clean_identifier is None
    assert party_client.raw_identifier is None
    assert party_client.is_law_firm is False

    # Party with CPF (11 digits)
    party_cpf = ContractParty(
        name="Pessoa Física",
        role=PartyRole.CONTRATANTE,
        clean_identifier="12345678901",
        raw_identifier="123.456.789-01",
    )
    assert party_cpf.clean_identifier == "12345678901"


def test_contract_party_clean_identifier_validation():
    """Verify clean_identifier rejects non-numeric characters."""
    # Letters in identifier
    with pytest.raises(ValidationError):
        ContractParty(name="Empresa A", clean_identifier="1234567800019A")

    # Formatted CNPJ with punctuation in clean_identifier
    with pytest.raises(ValidationError):
        ContractParty(name="Empresa B", clean_identifier="12.345.678/0001-90")

    # Spaces between numbers
    with pytest.raises(ValidationError):
        ContractParty(name="Empresa C", clean_identifier="1234 5678")


def test_contract_metadata_validation():
    """Verify ContractMetadata fields, defaults, and composition."""
    party1 = ContractParty(
        name="Andrade Advogados Associados",
        role=PartyRole.CONTRATADA,
        clean_identifier="12345678000190",
        raw_identifier="12.345.678/0001-90",
        is_law_firm=True,
    )
    party2 = ContractParty(
        name="Empresa Cliente S/A",
        role=PartyRole.CONTRATANTE,
        clean_identifier="98765432000110",
        raw_identifier="98.765.432/0001-10",
        is_law_firm=False,
    )
    metadata = ContractMetadata(
        formal_title="CONTRATO DE PRESTAÇÃO DE SERVIÇOS JURÍDICOS",
        instrument_type="Honorários",
        subject_area="Contencioso Cível",
        parties=[party1, party2],
        execution_date="2024-05-10",
        object_summary="Prestação de serviços contenciosos cíveis",
        mfiles_divergence_flags=["mismatch_date"],
    )

    assert metadata.formal_title == "CONTRATO DE PRESTAÇÃO DE SERVIÇOS JURÍDICOS"
    assert metadata.instrument_type == "Honorários"
    assert metadata.subject_area == "Contencioso Cível"
    assert len(metadata.parties) == 2
    assert metadata.parties[0].clean_identifier == "12345678000190"
    assert metadata.parties[1].clean_identifier == "98765432000110"
    assert metadata.execution_date == "2024-05-10"
    assert metadata.object_summary == "Prestação de serviços contenciosos cíveis"
    assert metadata.mfiles_divergence_flags == ["mismatch_date"]

    # Minimal metadata with defaults
    min_meta = ContractMetadata(
        formal_title="ADITIVO CONTRATUAL",
        instrument_type="Aditivo",
    )
    assert min_meta.parties == []
    assert min_meta.subject_area is None
    assert min_meta.execution_date is None
    assert min_meta.object_summary is None
    assert min_meta.mfiles_divergence_flags == []


def test_block_enums():
    """Verify BlockType, HierarchyLevel, and UncertaintyFlag enum members."""
    expected_block_types = {
        "TITLE",
        "PREAMBLE",
        "CLAUSE",
        "PARAGRAPH",
        "ITEM",
        "SUBITEM",
        "TABLE",
        "ANNEX",
        "SIGNATURE",
    }
    assert {bt.name for bt in BlockType} == expected_block_types

    expected_hierarchy = {
        "TITLE",
        "PREAMBLE",
        "CLAUSE",
        "PARAGRAPH",
        "ITEM",
        "SUBITEM",
        "TABLE",
        "ANNEX",
        "SIGNATURE",
    }
    assert {hl.name for hl in HierarchyLevel} == expected_hierarchy

    expected_uncertainty = {
        "TRACK_CHANGES_PRESENT",
        "LOW_CONFIDENCE_OCR",
        "UNRESOLVED_NUMBERING",
        "CORRUPTED_TABLE",
        "HAS_COMMENTS",
    }
    assert {uf.name for uf in UncertaintyFlag} == expected_uncertainty


def test_document_block_validation():
    """Verify DocumentBlock creation and default values."""
    block = DocumentBlock(
        block_id="doc_100_v1_b0",
        doc_id=100,
        doc_version=1,
        block_type=BlockType.CLAUSE,
        hierarchy_level=HierarchyLevel.CLAUSE,
        hierarchy_label="Cláusula 1ª",
        parent_clause_id=None,
        order_index=0,
        text_raw="CLÁUSULA PRIMEIRA - DO OBJETO\nO presente contrato tem por objeto...",
        text_search="clausula primeira do objeto o presente contrato tem por objeto",
        table_metadata=None,
        spans=[{"start": 0, "end": 64}],
        uncertainty_flags=[UncertaintyFlag.TRACK_CHANGES_PRESENT],
    )

    assert block.block_id == "doc_100_v1_b0"
    assert block.doc_id == 100
    assert block.doc_version == 1
    assert block.block_type == BlockType.CLAUSE
    assert block.hierarchy_level == HierarchyLevel.CLAUSE
    assert block.hierarchy_label == "Cláusula 1ª"
    assert block.parent_clause_id is None
    assert block.order_index == 0
    assert block.text_raw.startswith("CLÁUSULA PRIMEIRA")
    assert block.text_search.startswith("clausula primeira")
    assert block.table_metadata is None
    assert block.spans == [{"start": 0, "end": 64}]
    assert block.uncertainty_flags == [UncertaintyFlag.TRACK_CHANGES_PRESENT]

    # Minimal block with defaults
    min_block = DocumentBlock(
        block_id="b1",
        doc_id=1,
        doc_version=1,
        block_type=BlockType.PARAGRAPH,
        hierarchy_level=HierarchyLevel.PARAGRAPH,
        order_index=1,
        text_raw="Texto parágrafo",
        text_search="texto paragrafo",
    )
    assert min_block.hierarchy_label is None
    assert min_block.parent_clause_id is None
    assert min_block.table_metadata is None
    assert min_block.spans == []
    assert min_block.uncertainty_flags == []


def test_parsed_document_success_valid():
    """Verify ParsedDocument creation with status success and non-empty blocks."""
    metadata = ContractMetadata(
        formal_title="CONTRATO DE HONORÁRIOS",
        instrument_type="Honorários",
    )
    block = DocumentBlock(
        block_id="b0",
        doc_id=1,
        doc_version=1,
        block_type=BlockType.TITLE,
        hierarchy_level=HierarchyLevel.TITLE,
        order_index=0,
        text_raw="CONTRATO DE HONORÁRIOS",
        text_search="contrato de honorarios",
    )
    doc = ParsedDocument(
        doc_id=1,
        doc_version=1,
        file_path="C:/data/contract.docx",
        file_hash="abcdef1234567890",
        parser_name="docx_parser",
        parser_version="1.0.0",
        metadata=metadata,
        blocks=[block],
        status="success",
    )
    assert doc.doc_id == 1
    assert doc.status == "success"
    assert len(doc.blocks) == 1
    assert doc.error_message is None


def test_parsed_document_success_with_empty_blocks_rejected():
    """Verify invariant: status='success' with empty blocks MUST be rejected."""
    metadata = ContractMetadata(
        formal_title="CONTRATO VAZIO",
        instrument_type="Prestação de Serviços",
    )
    with pytest.raises(ValidationError) as exc_info:
        ParsedDocument(
            doc_id=2,
            doc_version=1,
            file_path="C:/data/empty.docx",
            file_hash="0000000000000000",
            parser_name="docx_parser",
            parser_version="1.0.0",
            metadata=metadata,
            blocks=[],
            status="success",
        )
    assert "bloco" in str(exc_info.value).lower() or "blocks" in str(exc_info.value).lower()


def test_parsed_document_non_success_allows_empty_blocks():
    """Verify ParsedDocument with status 'failed' or 'review_metadata_mismatch' allows empty blocks."""
    metadata = ContractMetadata(
        formal_title="DOCUMENTO CORROMPIDO",
        instrument_type="Desconhecido",
    )
    failed_doc = ParsedDocument(
        doc_id=3,
        doc_version=1,
        file_path="C:/data/corrupted.pdf",
        file_hash="ffffffffffffffff",
        parser_name="pdf_parser",
        parser_version="1.0.0",
        metadata=metadata,
        blocks=[],
        status="failed",
        error_message="Arquivo corrompido ou ilegível",
    )
    assert failed_doc.status == "failed"
    assert failed_doc.blocks == []
    assert failed_doc.error_message == "Arquivo corrompido ou ilegível"

    mismatch_doc = ParsedDocument(
        doc_id=4,
        doc_version=1,
        file_path="C:/data/mismatch.docx",
        file_hash="1111222233334444",
        parser_name="docx_parser",
        parser_version="1.0.0",
        metadata=metadata,
        blocks=[],
        status="review_metadata_mismatch",
        error_message="Divergência de metadados críticos",
    )
    assert mismatch_doc.status == "review_metadata_mismatch"
    assert mismatch_doc.blocks == []


def test_parsed_document_roundtrip_serialization():
    """Verify JSON serialization and deserialization preserves all types and invariants."""
    metadata = ContractMetadata(
        formal_title="CONTRATO DE LOCAÇÃO",
        instrument_type="Locação",
        subject_area="Imobiliário",
        parties=[
            ContractParty(name="Locador Silva", role=PartyRole.LOCADOR, clean_identifier="11122233344"),
            ContractParty(name="Locatário Souza", role=PartyRole.LOCATARIO, clean_identifier="55566677788"),
        ],
        execution_date="2024-01-15",
        object_summary="Locação comercial",
    )
    block = DocumentBlock(
        block_id="b0",
        doc_id=10,
        doc_version=2,
        block_type=BlockType.TITLE,
        hierarchy_level=HierarchyLevel.TITLE,
        order_index=0,
        text_raw="CONTRATO DE LOCAÇÃO COMERCIAL",
        text_search="contrato de locacao comercial",
        uncertainty_flags=[UncertaintyFlag.LOW_CONFIDENCE_OCR],
    )
    original_doc = ParsedDocument(
        doc_id=10,
        doc_version=2,
        file_path="C:/data/locacao.pdf",
        file_hash="1234567890abcdef",
        parser_name="pdf_parser",
        parser_version="2.0.0",
        metadata=metadata,
        blocks=[block],
        status="success",
    )

    json_data = original_doc.model_dump_json()
    loaded_doc = ParsedDocument.model_validate_json(json_data)

    assert loaded_doc == original_doc
    assert loaded_doc.metadata.parties[0].role == PartyRole.LOCADOR
    assert loaded_doc.blocks[0].uncertainty_flags[0] == UncertaintyFlag.LOW_CONFIDENCE_OCR
