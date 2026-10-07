"""Calendar, attribution and source-evidence regressions on synthetic legal text."""
import pytest
from pydantic import ValidationError

from app.ingestion.metadata_extractor import extract_contract_metadata
from app.ingestion.schemas import BlockType, ContractMetadata, DocumentBlock, HierarchyLevel, ParsedDocument


def block(text, kind=BlockType.PARAGRAPH, index=0):
    return DocumentBlock(block_id=f"b{index}", doc_id=1, doc_version=1,
                         block_type=kind, hierarchy_level=HierarchyLevel(kind.value),
                         order_index=index, text_raw=text, text_search=text.lower())


@pytest.mark.parametrize("text", [
    "São Paulo, 31/02/2024.", "São Paulo, 29 de fevereiro de 2023.",
    "São Paulo, 2024-04-31.", "São Paulo, 00/12/2024.",
])
def test_invalid_calendar_is_preserved_as_unqualified_evidence(text):
    meta = extract_contract_metadata([block(text, BlockType.SIGNATURE)])
    assert meta.execution_date is None
    mention, = meta.temporal_mentions
    assert mention.normalized_date is None
    assert mention.kind == "unknown"
    assert "invalid_calendar_date" in mention.uncertainty_flags
    assert text[mention.start:mention.end] == mention.raw_text


@pytest.mark.parametrize("kind", [BlockType.SIGNATURE, BlockType.PARAGRAPH])
def test_payment_is_never_promoted_to_execution_even_at_document_end(kind):
    text = "O pagamento vence em 15/03/2024."
    meta = extract_contract_metadata([block(text, kind)])
    assert meta.execution_date is None
    mention, = meta.temporal_mentions
    assert mention.kind == "due"
    assert mention.normalized_date == "2024-03-15"
    assert mention.review_state == "pending_review"


def test_multiple_events_and_offsets_are_retained_without_legal_effect_inference():
    text = ("Assinatura deste contrato: 29/02/2024; início da vigência: 01/03/2024; "
            "fim da vigência: 2025-02-28; vencimento: 10 de março de 2024; "
            "rescisão em 11/04/2024; renovação em 12/05/2024.")
    meta = extract_contract_metadata([block(text)])
    assert [m.kind for m in meta.temporal_mentions] == [
        "signature", "effective_start", "effective_end", "due", "termination", "renewal"]
    assert meta.execution_date == "2024-02-29"
    assert len(meta.temporal_mentions) == 6
    for mention in meta.temporal_mentions:
        assert mention.block_id == "b0"
        assert text[mention.start:mention.end] == mention.raw_text
        assert mention.review_state == "pending_review"


def test_explicit_validity_range_has_two_distinct_events():
    meta = extract_contract_metadata([block("Vigência de 01/03/2024 a 28/02/2025.")])
    assert [m.kind for m in meta.temporal_mentions] == ["effective_start", "effective_end"]
    assert meta.execution_date is None


@pytest.mark.parametrize("text", [
    "Pagamento e assinatura previstos para 10/03/2024.",
    "O contrato anterior foi assinado em 10/03/2024.",
    "São Paulo, contrato original de 10/03/2024.",
    "Data histórica: 10/03/2024.",
    "Referência 10/03/2024.",
    "O contrato citado foi assinado em 10/03/2024.",
    "Assinatura prevista para 10/03/2024.",
    "Este contrato não foi assinado em 10/03/2024.",
    "Se este contrato for assinado em 10/03/2024.",
    "Este contrato será assinado em 10/03/2024.",
    "Assinatura: 10/03/2024 (contrato anterior).",
    "Assinatura: 10/03/2024, do contrato anterior.",
    "Assinatura: 10/03/2024, se aprovado.",
])
def test_ambiguous_or_historical_context_is_unknown_even_in_signature_block(text):
    meta = extract_contract_metadata([block(text, BlockType.SIGNATURE)])
    assert meta.execution_date is None
    mention, = meta.temporal_mentions
    assert mention.kind == "unknown"
    assert mention.normalized_date == "2024-03-10"
    assert mention.uncertainty_flags


def test_conflicting_signatures_do_not_silently_pick_last_date():
    meta = extract_contract_metadata([
        block("Assinatura deste contrato: 01/03/2024.", index=0),
        block("Assinatura deste contrato: 02/03/2024.", index=1),
    ])
    assert meta.execution_date is None
    assert "execution_date_conflict" in meta.mfiles_divergence_flags


@pytest.mark.parametrize("text", [
    "1231/12/2024", "2024-01-0123", "x10/03/2024", "10/03/2024x", "10/03-2024",
    "10/03/2024/documento", "10/03/2024.docx",
])
def test_malformed_tokens_are_not_partially_parsed(text):
    meta = extract_contract_metadata([block(text)])
    assert meta.temporal_mentions == []
    assert meta.execution_date is None


def test_legacy_execution_date_rejects_invalid_calendar():
    with pytest.raises(ValidationError):
        ContractMetadata(formal_title="X", instrument_type="Outro", execution_date="2024-02-31")


def test_temporal_evidence_is_validated_against_actual_blocks():
    source = block("Assinatura: 10/03/2024.")
    meta = extract_contract_metadata([source])
    serialized = meta.model_dump()
    serialized["temporal_mentions"][0]["raw_text"] = "11/03/2024"
    with pytest.raises(ValidationError):
        ParsedDocument(doc_id=1, doc_version=1, file_path="synthetic.docx", file_hash="a" * 64,
                       parser_name="test", parser_version="1", metadata=serialized, blocks=[source])


def test_candidate_schema_does_not_accept_approval_or_inconsistent_summary():
    source = block("Assinatura: 10/03/2024.")
    meta = extract_contract_metadata([source])
    data = meta.model_dump()
    data["temporal_mentions"][0]["review_state"] = "approved"
    with pytest.raises(ValidationError):
        ContractMetadata.model_validate(data)
    data = meta.model_dump()
    data["execution_date"] = "2024-03-11"
    with pytest.raises(ValidationError):
        ContractMetadata.model_validate(data)


def test_declared_normalized_date_must_equal_the_literal_evidence():
    meta = extract_contract_metadata([block("Assinatura: 11/05/2024.")])
    data = meta.model_dump()
    data["temporal_mentions"][0]["normalized_date"] = "2024-05-10"
    data["execution_date"] = "2024-05-10"
    with pytest.raises(ValidationError):
        ContractMetadata.model_validate(data)


def test_extractor_version_is_explicit_but_legacy_reading_has_no_qualification():
    meta = extract_contract_metadata([block("São Paulo, 29/02/2024.", BlockType.SIGNATURE)])
    assert meta.temporal_extractor_version == "temporal-candidates-v1"
    legacy = ContractMetadata(formal_title="X", instrument_type="Outro", execution_date="2024-05-10")
    assert legacy.temporal_mentions == []
    assert legacy.temporal_extractor_version is None


def test_context_forgery_is_rejected_on_document_validation():
    source = block("Pagamento vence em 10/03/2024.")
    data = extract_contract_metadata([source]).model_dump()
    data["temporal_mentions"][0]["kind"] = "signature"
    data["execution_date"] = "2024-03-10"
    with pytest.raises(ValidationError):
        ParsedDocument(doc_id=1, doc_version=1, file_path="synthetic.docx", file_hash="a" * 64,
                       parser_name="test", parser_version="1", metadata=data, blocks=[source])


def test_duplicate_and_cross_document_evidence_are_rejected():
    source = block("Assinatura: 10/03/2024.")
    data = extract_contract_metadata([source]).model_dump()
    data["temporal_mentions"].append(data["temporal_mentions"][0].copy())
    with pytest.raises(ValidationError):
        ContractMetadata.model_validate(data)
    source.doc_id = 2
    data = extract_contract_metadata([source]).model_dump()
    with pytest.raises(ValidationError):
        ParsedDocument(doc_id=1, doc_version=1, file_path="synthetic.docx", file_hash="a" * 64,
                       parser_name="test", parser_version="1", metadata=data, blocks=[source])


def test_nonasserted_validity_range_cannot_qualify_one_endpoint():
    meta = extract_contract_metadata([block("Prevista vigência de 01/03/2024 a 28/02/2025.")])
    assert [m.kind for m in meta.temporal_mentions] == ["unknown", "unknown"]
    assert all("nonasserted_event_context" in m.uncertainty_flags for m in meta.temporal_mentions)


def test_operational_word_is_not_a_city_closing_signature():
    meta = extract_contract_metadata([block("Entregue, 10/03/2024.")])
    assert meta.execution_date is None
    assert meta.temporal_mentions[0].kind == "unknown"


def test_date_context_does_not_leak_across_separate_events_after_comma():
    meta = extract_contract_metadata([block("Assinatura: 10/03/2024, pagamento previsto para 11/03/2024.")])
    assert meta.execution_date == "2024-03-10"
    assert meta.temporal_mentions[0].kind == "signature"
    assert meta.temporal_mentions[1].kind == "unknown"


def test_omission_cannot_hide_conflicting_signature_evidence():
    sources = [block("Assinatura: 10/03/2024.", index=0), block("Assinatura: 11/03/2024.", index=1)]
    data = extract_contract_metadata(sources).model_dump()
    data["temporal_mentions"] = data["temporal_mentions"][:1]
    data["execution_date"] = "2024-03-10"
    with pytest.raises(ValidationError):
        ParsedDocument(doc_id=1, doc_version=1, file_path="synthetic.docx", file_hash="a" * 64,
                       parser_name="test", parser_version="1", metadata=data, blocks=sources)


def test_versioned_empty_mentions_cannot_fabricate_execution_summary():
    source = block("Contrato sem data declarada.")
    data = extract_contract_metadata([source]).model_dump()
    data["execution_date"] = "2024-05-10"
    with pytest.raises(ValidationError):
        ParsedDocument(doc_id=1, doc_version=1, file_path="synthetic.docx", file_hash="a" * 64,
                       parser_name="test", parser_version="1", metadata=data, blocks=[source])


@pytest.mark.parametrize("text", [
    "Este contrato nunca foi assinado em 10/03/2024.",
    "Este contrato jamais foi firmado em 10/03/2024.",
    "Este contrato teria sido assinado em 10/03/2024.",
    "Este contrato haveria sido firmado em 10/03/2024.",
    "Este contrato seria assinado em 10/03/2024.",
    "Este contrato poderia ser assinado em 10/03/2024.",
    "Este contrato deveria ser assinado em 10/03/2024.",
    "Eventual assinatura: 10/03/2024.",
    "Este contrato eventualmente seria assinado em 10/03/2024.",
    "Este contrato ainda não foi assinado em 10/03/2024.",
    "Este contrato, sem assinatura em 10/03/2024.",
    "Não ocorreu assinatura deste contrato em 10/03/2024.",
    "Este contrato poderá ser assinado em 10/03/2024.",
    "Este contrato deverá ser assinado em 10/03/2024.",
    "Estes contratos poderão ser assinados em 10/03/2024.",
    "Estes contratos deverão ser assinados em 10/03/2024.",
    "Este contrato tampouco foi assinado em 10/03/2024.",
    "Este contrato nem foi assinado em 10/03/2024.",
    "Suposta assinatura deste contrato: 10/03/2024.",
    "Este contrato foi supostamente assinado em 10/03/2024.",
    "Nenhuma assinatura deste contrato ocorreu em 10/03/2024.",
    "Ausência de assinatura deste contrato em 10/03/2024.",
])
def test_negation_and_hypothetical_signature_are_nonasserted_candidates(text):
    from app.ingestion.chunker import create_candidate_chunks as create_legal_chunks
    source = block(text, BlockType.SIGNATURE)
    meta = extract_contract_metadata([source])
    assert meta.execution_date is None
    mention, = meta.temporal_mentions
    assert mention.kind == "unknown"
    assert "nonasserted_event_context" in mention.uncertainty_flags
    assert mention.review_state == "pending_review"
    document = ParsedDocument(doc_id=1, doc_version=1, file_path="synthetic.docx", file_hash="a" * 64,
                              parser_name="test", parser_version="1", metadata=meta, blocks=[source])
    chunk, = create_legal_chunks([document])
    assert "signature = 2024-03-10" not in chunk.page_content
    assert "unknown = 2024-03-10" in chunk.page_content
    assert chunk.metadata["verbatim_text"] == text
    assert chunk.metadata["temporal_mentions"][0]["raw_text"] == "10/03/2024"


@pytest.mark.parametrize("text", [
    "Este contrato menciona o acordo assinado em 10/03/2024.",
    "Este contrato cita instrumento firmado em 10/03/2024.",
    "O presente contrato refere-se ao termo assinado em 10/03/2024.",
    "Este contrato incorpora documento celebrado em 10/03/2024.",
])
def test_current_subject_cannot_transfer_another_instruments_signature(text):
    meta = extract_contract_metadata([block(text, BlockType.SIGNATURE)])
    assert meta.execution_date is None
    mention, = meta.temporal_mentions
    assert mention.kind == "unknown"
    assert mention.normalized_date == "2024-03-10"
    assert mention.uncertainty_flags


@pytest.mark.parametrize("text", [
    "Este contrato foi assinado em 10/03/2024.",
    "O presente instrumento foi firmado em 10/03/2024.",
    "Este termo foi celebrado em 10/03/2024.",
])
def test_direct_current_subject_assertion_remains_a_signature_candidate(text):
    from app.ingestion.chunker import create_candidate_chunks as create_legal_chunks
    source = block(text)
    meta = extract_contract_metadata([source])
    assert meta.execution_date == "2024-03-10"
    assert meta.temporal_mentions[0].kind == "signature"
    assert meta.temporal_mentions[0].review_state == "pending_review"
    document = ParsedDocument(doc_id=1, doc_version=1, file_path="synthetic.docx", file_hash="a" * 64,
                              parser_name="test", parser_version="1", metadata=meta, blocks=[source])
    chunk, = create_legal_chunks([document])
    assert "signature = 2024-03-10" in chunk.page_content
    assert chunk.metadata["verbatim_text"] == text


@pytest.mark.parametrize("text", [
    "Assinatura: 10/03/2024, hipoteticamente.",
    "Assinatura: 10/03/2024, do outro contrato.",
    "Assinatura: 10/03/2024, referente ao instrumento anexo.",
    "Assinatura: 10/03/2024 (do acordo mencionado).",
    "Este contrato foi assinado em 10/03/2024, do outro contrato.",
    "O presente instrumento foi firmado em 10/03/2024 (do acordo mencionado).",
    "Este termo foi celebrado em 10/03/2024, referente ao instrumento anexo.",
    "Assinatura: 10/03/2024, alegadamente.",
    "Assinatura: 10/03/2024 [instrumento relacionado].",
    "Assinatura: 10/03/2024, do documento supracitado.",
    "Assinatura: 10/03/2024, assinatura do outro contrato.",
    "Assinatura: 10/03/2024, data de assinatura do outro contrato 11/03/2024.",
    "Assinatura: 10/03/2024; hipoteticamente.",
    "Assinatura: 10/03/2024. do outro contrato.",
    "Assinatura: 10/03/2024\nreferente ao instrumento anexo.",
    "Este contrato foi assinado em 10/03/2024; assinatura do outro contrato.",
    "São Paulo, 10/03/2024.\ndo outro contrato.",
    "Assinatura: 10/03/2024. do outro contrato; Pagamento: 11/03/2024.",
])
def test_unresolved_post_date_qualifier_never_promotes_signature(text):
    from app.ingestion.chunker import create_candidate_chunks as create_legal_chunks
    source = block(text, BlockType.SIGNATURE)
    meta = extract_contract_metadata([source])
    assert meta.execution_date is None
    mention = meta.temporal_mentions[0]
    assert mention.kind == "unknown"
    assert mention.uncertainty_flags
    if "hipoteticamente" in text:
        assert "nonasserted_event_context" in mention.uncertainty_flags
    assert mention.normalized_date == "2024-03-10"
    assert source.text_raw[mention.start:mention.end] == mention.raw_text == "10/03/2024"
    document = ParsedDocument(doc_id=1, doc_version=1, file_path="synthetic.docx", file_hash="a" * 64,
                              parser_name="test", parser_version="1", metadata=meta, blocks=[source])
    chunk, = create_legal_chunks([document])
    assert "signature = 2024-03-10" not in chunk.page_content
    assert "unknown = 2024-03-10" in chunk.page_content
    assert chunk.metadata["verbatim_text"] == text


@pytest.mark.parametrize("text", [
    "Hipoteticamente.\nAssinatura: 10/03/2024.",
    "Talvez. Assinatura: 10/03/2024.",
    "Não houve assinatura.\nSão Paulo, 10/03/2024.",
    "Contrato anterior.\nAssinatura: 10/03/2024.",
])
def test_prefix_delimiter_cannot_erase_unresolved_signature_context(text):
    from app.ingestion.chunker import create_candidate_chunks as create_legal_chunks
    source = block(text, BlockType.SIGNATURE)
    meta = extract_contract_metadata([source])
    assert meta.execution_date is None
    assert meta.temporal_mentions[0].kind == "unknown"
    assert meta.temporal_mentions[0].uncertainty_flags
    document = ParsedDocument(doc_id=1, doc_version=1, file_path="synthetic.docx", file_hash="a" * 64,
                              parser_name="test", parser_version="1", metadata=meta, blocks=[source])
    chunk, = create_legal_chunks([document])
    assert "signature = 2024-03-10" not in chunk.page_content
    assert chunk.metadata["verbatim_text"] == text


@pytest.mark.parametrize("text, count", [
    ("Contrato anterior datado de 09/03/2024, Assinatura: 10/03/2024.", 2),
    ("Do outro contrato de 09/03/2024, Assinatura: 10/03/2024.", 2),
    ("Hipoteticamente em 09/03/2024, Assinatura: 10/03/2024.", 2),
    ("Do instrumento anexo de 09/03/2024; Assinatura: 10/03/2024.", 2),
    ("Do outro contrato de 09/03/2024.\nEste contrato foi assinado em 10/03/2024.", 2),
    ("Contrato anterior de 08/03/2024; Pagamento: 09/03/2024; Assinatura: 10/03/2024.", 3),
    ("Hipoteticamente em 08/03/2024, Vencimento: 09/03/2024, Assinatura: 10/03/2024.", 3),
    ("Do outro contrato de 31/02/2024, Assinatura: 10/03/2024.", 2),
    ("Pagamento em 31/02/2024; Assinatura: 10/03/2024.", 2),
    ("Do instrumento anexo de 07/03/2024, vigência de 08/03/2024 a 09/03/2024; Assinatura: 10/03/2024.", 4),
    ("Hipoteticamente em 07/03/2024. Vigência de 08/03/2024 a 09/03/2024; Assinatura: 10/03/2024.", 4),
    ("Do outro contrato, vigência de 08/03/2024 a 09/03/2024; Assinatura: 10/03/2024.", 3),
])
def test_unqualified_date_does_not_reset_a_later_signature_frame(text, count):
    from app.ingestion.chunker import create_candidate_chunks as create_legal_chunks
    source = block(text, BlockType.SIGNATURE)
    meta = extract_contract_metadata([source])
    assert meta.execution_date is None
    assert len(meta.temporal_mentions) == count
    assert all(mention.kind == "unknown" for mention in meta.temporal_mentions)
    for mention in meta.temporal_mentions:
        assert text[mention.start:mention.end] == mention.raw_text
        assert mention.review_state == "pending_review"
        assert mention.uncertainty_flags
    document = ParsedDocument(doc_id=1, doc_version=1, file_path="synthetic.docx", file_hash="a" * 64,
                              parser_name="test", parser_version="1", metadata=meta, blocks=[source])
    chunk, = create_legal_chunks([document])
    assert "signature =" not in chunk.page_content
    assert chunk.metadata["verbatim_text"] == text
    assert len(chunk.metadata["temporal_mentions"]) == count


@pytest.mark.parametrize("text, kinds, execution", [
    ("Assinatura: 09/03/2024, Pagamento: 10/03/2024.", ["signature", "due"], "2024-03-09"),
    ("Pagamento: 09/03/2024, Assinatura: 10/03/2024.", ["due", "signature"], "2024-03-10"),
    ("O pagamento vence em 09/03/2024; Assinatura: 10/03/2024.", ["due", "signature"], "2024-03-10"),
    ("Assinatura: 08/03/2024; Pagamento: 09/03/2024; Assinatura: 10/03/2024.", ["signature", "due", "signature"], None),
])
def test_only_positive_independent_events_allow_a_new_date_frame(text, kinds, execution):
    meta = extract_contract_metadata([block(text)])
    assert [mention.kind for mention in meta.temporal_mentions] == kinds
    assert meta.execution_date == execution


def test_uncertain_signature_cannot_hide_a_conflicting_signature_date():
    text = "Assinatura: 08/03/2024; Pagamento: 09/03/2024; Assinatura: 10/03/2024 (instrumento relacionado)."
    meta = extract_contract_metadata([block(text)])
    assert meta.execution_date is None
    assert meta.temporal_mentions[-1].kind == "unknown"
    assert len(meta.temporal_mentions) == 3


@pytest.mark.parametrize("heading", ["CLÁUSULA SEGUNDA: ", "Cláusula 2ª - "])
def test_structural_clause_heading_preserves_positive_event_and_qualifications(heading):
    text = heading + "vencimento em 10/05/2024."
    meta = extract_contract_metadata([block(text, BlockType.CLAUSE)])
    assert meta.temporal_mentions[0].kind == "due"
    assert meta.temporal_mentions[0].raw_text == "10/05/2024"
    uncertain = heading + "Contrato anterior de 09/05/2024; Assinatura: 10/05/2024."
    guarded = extract_contract_metadata([block(uncertain, BlockType.CLAUSE)])
    assert guarded.execution_date is None
    assert all(mention.kind == "unknown" for mention in guarded.temporal_mentions)
    assert all(uncertain[m.start:m.end] == m.raw_text for m in guarded.temporal_mentions)
