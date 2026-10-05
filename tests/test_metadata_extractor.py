"""
Tests for canonical contract metadata extraction engine (metadata_extractor.py).
"""

import pytest
from app.ingestion.metadata_extractor import extract_contract_metadata
from app.ingestion.schemas import (
    BlockType,
    ContractMetadata,
    ContractParty,
    DocumentBlock,
    HierarchyLevel,
    PartyRole,
)


def _make_block(
    block_type: BlockType,
    text: str,
    order_index: int,
    hierarchy_label: str | None = None,
    doc_id: int = 1,
) -> DocumentBlock:
    """Helper to build canonical DocumentBlock instances for testing."""
    return DocumentBlock(
        block_id=f"doc_{doc_id}_block_{order_index}",
        doc_id=doc_id,
        doc_version=1,
        block_type=block_type,
        hierarchy_level=HierarchyLevel(block_type.value),
        hierarchy_label=hierarchy_label,
        order_index=order_index,
        text_raw=text,
        text_search=text.lower(),
    )


def test_extract_standard_bilateral_contract_with_andrade():
    """Verify standard bilateral preamble with Andrade Advogados as CONTRATADA and Client as CONTRATANTE."""
    blocks = [
        _make_block(
            BlockType.TITLE,
            "CONTRATO DE PRESTAÇÃO DE SERVIÇOS ADVOCATÍCIOS E HONORÁRIOS",
            order_index=0,
        ),
        _make_block(
            BlockType.PREAMBLE,
            (
                "Pelo presente instrumento particular de prestação de serviços advocatícios, de um lado, "
                "ANDRADE ADVOGADOS ASSOCIADOS, sociedade de advogados devidamente inscrita na OAB/SP sob o nº 1.234 "
                "e no CNPJ/MF sob o nº 01.234.567/0001-89, com sede na Av. Paulista, 1000, São Paulo/SP, "
                "doravante denominada simplesmente CONTRATADA; e, de outro lado, "
                "ALPHA INDÚSTRIA E COMÉRCIO S.A., pessoa jurídica de direito privado, inscrita no CNPJ sob o nº 98.765.432/0001-10, "
                "com sede no Rio de Janeiro/RJ, doravante denominada CONTRATANTE; "
                "têm entre si justo e acordado o quanto segue:"
            ),
            order_index=1,
        ),
        _make_block(
            BlockType.CLAUSE,
            (
                "CLÁUSULA PRIMEIRA - DO OBJETO: O presente contrato tem por objeto a prestação de serviços "
                "advocatícios consultivos e contenciosos perante a Justiça Federal e Tribunais Superiores."
            ),
            order_index=2,
            hierarchy_label="Cláusula 1ª",
        ),
        _make_block(
            BlockType.SIGNATURE,
            "São Paulo, 10 de maio de 2024.",
            order_index=3,
        ),
    ]

    metadata = extract_contract_metadata(blocks)

    assert isinstance(metadata, ContractMetadata)
    assert metadata.formal_title == "CONTRATO DE PRESTAÇÃO DE SERVIÇOS ADVOCATÍCIOS E HONORÁRIOS"
    assert metadata.instrument_type == "Honorários"
    assert metadata.execution_date == "2024-05-10"
    assert metadata.object_summary is not None
    assert "prestação de serviços advocatícios" in metadata.object_summary.lower()
    assert "cláusula primeira" not in metadata.object_summary.lower()

    # Partes
    assert len(metadata.parties) == 2

    andrade_party = metadata.parties[0]
    assert andrade_party.name == "ANDRADE ADVOGADOS ASSOCIADOS"
    assert andrade_party.role == PartyRole.CONTRATADA
    assert andrade_party.clean_identifier == "01234567000189"
    assert andrade_party.raw_identifier == "01.234.567/0001-89"
    assert andrade_party.is_law_firm is True

    client_party = metadata.parties[1]
    assert client_party.name == "ALPHA INDÚSTRIA E COMÉRCIO S.A."
    assert client_party.role == PartyRole.CONTRATANTE
    assert client_party.clean_identifier == "98765432000110"
    assert client_party.raw_identifier == "98.765.432/0001-10"
    assert client_party.is_law_firm is False


def test_legal_representative_isolation_anti_pollution():
    """
    CRITICAL REQUIREMENT:
    Verify that when an entity is qualified with CNPJ and represented by natural persons with CPF,
    the clean_identifier MUST be the entity's CNPJ, and MUST NOT be polluted by the CPF(s) of representatives.
    """
    blocks = [
        _make_block(
            BlockType.TITLE,
            "CONTRATO DE PRESTAÇÃO DE SERVIÇOS DE TECNOLOGIA",
            order_index=0,
        ),
        _make_block(
            BlockType.PREAMBLE,
            (
                "De um lado, BETA ENERGIA S.A., sociedade anônima inscrita no CNPJ sob o nº 11.222.333/0001-44, "
                "com sede na Cidade de Curitiba, Estado do Paraná, neste ato representada por seu Diretor Presidente "
                "Sr. CARLOS EDUARDO DE ALBUQUERQUE, brasileiro, casado, engenheiro, portador da cédula de identidade "
                "RG nº 9.876.543 SSP/PR e inscrito no CPF sob o nº 123.456.789-00, doravante denominada simplesmente CONTRATANTE; "
                "e, de outro lado, DELTA TECNOLOGIA E SERVIÇOS LTDA., pessoa jurídica de direito privado, inscrita no "
                "CNPJ sob o nº 55.666.777/0001-88, com sede em São Paulo/SP, neste ato representada por seus sócios "
                "administradores FULANO DE TAL, portador do CPF nº 222.333.444-55, e BELTRANO DE TAL, portador do "
                "CPF nº 333.444.555-66, doravante denominada tão somente CONTRATADA; "
                "têm entre si justo e contratado:"
            ),
            order_index=1,
        ),
    ]

    metadata = extract_contract_metadata(blocks)

    assert len(metadata.parties) == 2

    p1 = metadata.parties[0]
    assert p1.name == "BETA ENERGIA S.A."
    assert p1.role == PartyRole.CONTRATANTE
    assert p1.clean_identifier == "11222333000144"
    assert p1.raw_identifier == "11.222.333/0001-44"
    assert p1.is_law_firm is False
    # Verify representative's CPF did not contaminate p1
    assert p1.clean_identifier != "12345678900"

    p2 = metadata.parties[1]
    assert p2.name == "DELTA TECNOLOGIA E SERVIÇOS LTDA."
    assert p2.role == PartyRole.CONTRATADA
    assert p2.clean_identifier == "55666777000188"
    assert p2.raw_identifier == "55.666.777/0001-88"
    assert p2.is_law_firm is False
    # Verify neither representative's CPF contaminated p2
    assert p2.clean_identifier != "22233344455"
    assert p2.clean_identifier != "33344455566"


def test_locacao_natural_persons_with_cpf():
    """Verify lease contract where parties are natural persons with CPF."""
    blocks = [
        _make_block(
            BlockType.TITLE,
            "CONTRATO DE LOCAÇÃO DE IMÓVEL RESIDENCIAL",
            order_index=0,
        ),
        _make_block(
            BlockType.PREAMBLE,
            (
                "Pelo presente instrumento, de um lado, JOÃO PEREIRA DA SILVA, brasileiro, divorciado, professor, "
                "portador do RG nº 12.345.678 SSP/SP e inscrito no CPF sob o nº 111.222.333-44, residente e domiciliado "
                "na Rua das Flores, 100, São Paulo/SP, doravante denominado LOCADOR; e, de outro lado, "
                "MARIANA GOMES SANTOS, brasileira, solteira, médica, portadora do CPF nº 999.888.777-66, "
                "residente na Av. Brasil, 500, doravante denominada LOCATÁRIA; "
                "celebram o presente contrato sob as cláusulas seguintes:"
            ),
            order_index=1,
        ),
    ]

    metadata = extract_contract_metadata(blocks)

    assert metadata.instrument_type == "Locação"
    assert len(metadata.parties) == 2

    locador = metadata.parties[0]
    assert locador.name == "JOÃO PEREIRA DA SILVA"
    assert locador.role == PartyRole.LOCADOR
    assert locador.clean_identifier == "11122233344"
    assert locador.raw_identifier == "111.222.333-44"
    assert locador.is_law_firm is False

    locataria = metadata.parties[1]
    assert locataria.name == "MARIANA GOMES SANTOS"
    assert locataria.role == PartyRole.LOCATARIO
    assert locataria.clean_identifier == "99988877766"
    assert locataria.raw_identifier == "999.888.777-66"
    assert locataria.is_law_firm is False


def test_aditivo_contratual():
    """Verify contract amendment classification."""
    blocks = [
        _make_block(
            BlockType.TITLE,
            "PRIMEIRO TERMO ADITIVO AO CONTRATO DE PRESTAÇÃO DE SERVIÇOS",
            order_index=0,
        ),
        _make_block(
            BlockType.PREAMBLE,
            (
                "São partes neste aditivo: EMPRESA CONTRATANTE S/A (CNPJ 10.000.000/0001-01), doravante CONTRATANTE, "
                "e EMPRESA CONTRATADA LTDA (CNPJ 20.000.000/0001-02), doravante CONTRATADA."
            ),
            order_index=1,
        ),
    ]

    metadata = extract_contract_metadata(blocks)

    assert metadata.instrument_type == "Aditivo"
    assert metadata.formal_title == "PRIMEIRO TERMO ADITIVO AO CONTRATO DE PRESTAÇÃO DE SERVIÇOS"


def test_third_party_contract_no_law_firm():
    """Verify that third-party custody contracts do not tag Andrade Advogados as is_law_firm."""
    blocks = [
        _make_block(
            BlockType.TITLE,
            "CONTRATO DE COMPRA E VENDA MERCANTIL",
            order_index=0,
        ),
        _make_block(
            BlockType.PREAMBLE,
            (
                "De um lado, FORNECEDOR GLOBAL S/A, CNPJ 30.111.222/0001-33, doravante VENDEDOR; "
                "e, de outro lado, COMPRADORA NACIONAL LTDA., CNPJ 40.222.333/0001-44, doravante COMPRADOR;"
            ),
            order_index=1,
        ),
    ]

    metadata = extract_contract_metadata(blocks)

    assert metadata.instrument_type == "Compra e Venda"
    assert len(metadata.parties) == 2
    assert all(p.is_law_firm is False for p in metadata.parties)
    assert metadata.parties[0].role == PartyRole.VENDEDOR
    assert metadata.parties[1].role == PartyRole.COMPRADOR


def test_execution_date_formats():
    """Verify various Portuguese date expressions are converted to ISO YYYY-MM-DD."""
    test_cases = [
        ("São Paulo, aos 15 dias do mês de março de 2023.", "2023-03-15"),
        ("Curitiba, 10/05/2024.", "2024-05-10"),
        ("Brasília - DF, 01 de janeiro de 2025.", "2025-01-01"),
        ("Rio de Janeiro, 2022-12-01.", "2022-12-01"),
        ("Porto Alegre, 28 de fevereiro de 2021.", "2021-02-28"),
    ]

    for closing_text, expected_iso in test_cases:
        blocks = [
            _make_block(BlockType.TITLE, "CONTRATO DE PRESTAÇÃO DE SERVIÇOS", 0),
            _make_block(
                BlockType.PREAMBLE,
                "De um lado EMPRESA A, CNPJ 11.111.111/0001-11, doravante CONTRATANTE; de outro EMPRESA B, CNPJ 22.222.222/0001-22, doravante CONTRATADA;",
                1,
            ),
            _make_block(BlockType.SIGNATURE, closing_text, 2),
        ]
        meta = extract_contract_metadata(blocks)
        assert meta.execution_date == expected_iso, f"Failed on {closing_text}"


def test_document_with_only_clauses_resilience():
    """Verify documents lacking explicit PREAMBLE block extract metadata resiliently."""
    blocks = [
        _make_block(
            BlockType.CLAUSE,
            (
                "CLÁUSULA 1ª - DAS PARTES: O presente contrato é firmado entre EMPRESA PRIMEIRA LTDA., "
                "inscrita no CNPJ sob o nº 12.345.678/0001-90, doravante denominada simplesmente CONTRATANTE, "
                "e EMPRESA SEGUNDA S.A., inscrita no CNPJ sob o nº 98.765.432/0001-21, doravante CONTRATADA."
            ),
            order_index=0,
            hierarchy_label="Cláusula 1ª",
        ),
        _make_block(
            BlockType.CLAUSE,
            "CLÁUSULA 2ª - DO OBJETO: Prestação de serviços contínuos de suporte em banco de dados.",
            order_index=1,
            hierarchy_label="Cláusula 2ª",
        ),
        _make_block(
            BlockType.PARAGRAPH,
            "Belo Horizonte, 15 de agosto de 2023.",
            order_index=2,
        ),
    ]

    metadata = extract_contract_metadata(blocks)

    assert metadata.formal_title is not None
    assert len(metadata.parties) == 2
    assert metadata.parties[0].clean_identifier == "12345678000190"
    assert metadata.parties[1].clean_identifier == "98765432000121"
    assert metadata.execution_date == "2023-08-15"
    assert metadata.object_summary is not None
    assert "suporte em banco de dados" in metadata.object_summary.lower()


def test_empty_blocks_resilience():
    """Verify engine returns default ContractMetadata gracefully when block list is empty."""
    meta = extract_contract_metadata([])

    assert isinstance(meta, ContractMetadata)
    assert meta.formal_title == "Não identificado"
    assert meta.instrument_type == "Outro"
    assert meta.parties == []
    assert meta.execution_date is None
    assert meta.object_summary is None


def test_multiparty_with_interveniente_anuente():
    """Verify tripartite preamble extraction with interveniente anuente."""
    blocks = [
        _make_block(BlockType.TITLE, "ACORDO DE CESSÃO DE CRÉDITO", 0),
        _make_block(
            BlockType.PREAMBLE,
            (
                "De um lado, CEDENTE CAPITAL S.A., CNPJ 11.111.111/0001-11, doravante simplesmente CEDENTE; "
                "de outro lado, CESSIONÁRIA FINANÇAS LTDA., CNPJ 22.222.222/0001-22, doravante CESSIONÁRIA; "
                "e, como INTERVENIENTE ANUENTE, EMPRESA DEVEDORA S/A, CNPJ 33.333.333/0001-33, doravante denominada ANUENTE; "
                "têm entre si justo e avençado:"
            ),
            1,
        ),
    ]

    metadata = extract_contract_metadata(blocks)

    assert metadata.instrument_type == "Acordo"
    assert len(metadata.parties) == 3

    p1, p2, p3 = metadata.parties
    assert p1.name == "CEDENTE CAPITAL S.A."
    assert p1.clean_identifier == "11111111000111"

    assert p2.name == "CESSIONÁRIA FINANÇAS LTDA."
    assert p2.clean_identifier == "22222222000122"

    assert p3.name == "EMPRESA DEVEDORA S/A"
    assert p3.clean_identifier == "33333333000133"
    assert p3.role == PartyRole.ANUENTE


def test_object_summary_truncation_under_250_chars():
    """Verify object_summary extraction cleanly truncates long descriptions to <= 250 characters."""
    long_object_text = (
        "CLÁUSULA PRIMEIRA - DO OBJETO: Constitui objeto do presente instrumento particular a prestação, "
        "pela CONTRATADA em favor da CONTRATANTE, de amplos serviços jurídicos especializados nas áreas "
        "societária, tributária, regulatória, ambiental e contenciosa estratégica perante todas as instâncias "
        "do Poder Judiciário brasileiro, incluindo a elaboração de pareceres técnicos, defesas administrativas, "
        "recursos aos Tribunais Superiores e acompanhamento de auditorias internas com emissão de relatórios mensais "
        "pormenorizados."
    )
    blocks = [
        _make_block(BlockType.TITLE, "CONTRATO DE HONORÁRIOS", 0),
        _make_block(BlockType.PREAMBLE, "De um lado A, de outro B.", 1),
        _make_block(BlockType.CLAUSE, long_object_text, 2, hierarchy_label="Cláusula 1ª"),
    ]

    metadata = extract_contract_metadata(blocks)

    assert metadata.object_summary is not None
    assert len(metadata.object_summary) <= 250
    assert metadata.object_summary.endswith("...")
    assert "cláusula primeira" not in metadata.object_summary.lower()


def test_various_instrument_types():
    """Verify distinct contract instrument types classification."""
    cases = [
        ("INSTRUMENTO PARTICULAR DE DISTRATO CONTRATUAL", "Distrato"),
        ("TERMO DE TRANSAÇÃO E COMPOSIÇÃO AMIGÁVEL", "Acordo"),
        ("CONTRATO DE COMODATO DE EQUIPAMENTOS", "Comodato"),
        ("INSTRUMENTO DE PARCERIA E COOPERAÇÃO TÉCNICA", "Parceria"),
    ]
    for title, expected_type in cases:
        blocks = [
            _make_block(BlockType.TITLE, title, 0),
            _make_block(BlockType.PREAMBLE, "De um lado A (CNPJ 11.111.111/0001-11), de outro B (CNPJ 22.222.222/0001-22).", 1),
        ]
        meta = extract_contract_metadata(blocks)
        assert meta.instrument_type == expected_type, f"Failed for {title}"


def test_unformatted_cnpj_and_cpf_extraction():
    """Verify extraction of unformatted 14-digit CNPJ and 11-digit CPF."""
    blocks = [
        _make_block(BlockType.TITLE, "CONTRATO DE PRESTAÇÃO DE SERVIÇOS", 0),
        _make_block(
            BlockType.PREAMBLE,
            (
                "De um lado, TECH SOLUTIONS LTDA, inscrita no CNPJ sob o nº 12345678000199, doravante CONTRATADA; "
                "e, de outro lado, PEDRO HENRIQUE, inscrito no CPF sob o nº 12345678909, doravante CONTRATANTE;"
            ),
            1,
        ),
    ]

    metadata = extract_contract_metadata(blocks)
    assert len(metadata.parties) == 2

    p1, p2 = metadata.parties
    assert p1.name == "TECH SOLUTIONS LTDA"
    assert p1.clean_identifier == "12345678000199"
    assert p1.raw_identifier == "12345678000199"

    assert p2.name == "PEDRO HENRIQUE"
    assert p2.clean_identifier == "12345678909"
    assert p2.raw_identifier == "12345678909"


def test_andrade_name_discrimination():
    """
    CRITICAL REQUIREMENT:
    Verify that parties with 'Andrade' who are NOT Andrade Advogados are is_law_firm=False,
    while legitimate variations of Andrade Advogados are is_law_firm=True.
    """
    # Non-law firm commercial companies named Andrade
    blocks_commercial = [
        _make_block(BlockType.TITLE, "CONTRATO DE EMPREITADA", 0),
        _make_block(
            BlockType.PREAMBLE,
            (
                "De um lado, CONSTRUTORA ANDRADE S/A, CNPJ 10.000.000/0001-01, doravante CONTRATADA; "
                "e, de outro lado, CLIENTE X, CNPJ 20.000.000/0001-02, doravante CONTRATANTE;"
            ),
            1,
        ),
    ]
    meta_com = extract_contract_metadata(blocks_commercial)
    assert meta_com.parties[0].is_law_firm is False

    # Legitimate law firm party variations
    blocks_firm = [
        _make_block(BlockType.TITLE, "CONTRATO DE HONORÁRIOS", 0),
        _make_block(
            BlockType.PREAMBLE,
            (
                "De um lado, SOCIEDADE DE ADVOGADOS ANDRADE & SILVA, CNPJ 10.000.000/0001-01, doravante CONTRATADA; "
                "e, de outro lado, CLIENTE Y, CNPJ 20.000.000/0001-02, doravante CONTRATANTE;"
            ),
            1,
        ),
    ]
    meta_firm = extract_contract_metadata(blocks_firm)
    assert meta_firm.parties[0].is_law_firm is True


def test_document_without_execution_date():
    """Verify document lacking date in fecho leaves execution_date as None without error."""
    blocks = [
        _make_block(BlockType.TITLE, "CONTRATO DE COMODATO", 0),
        _make_block(BlockType.PREAMBLE, "De um lado A (CNPJ 11.111.111/0001-11), de outro B (CNPJ 22.222.222/0001-22).", 1),
        _make_block(BlockType.SIGNATURE, "Assinatura das partes e testemunhas instrumentárias.", 2),
    ]
    meta = extract_contract_metadata(blocks)
    assert meta.execution_date is None

