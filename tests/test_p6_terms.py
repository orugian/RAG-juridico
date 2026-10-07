import time

import pytest

from app.terms import Term, absent_terms, content_terms, fold, term_matches


def folded(text):
    return [term.folded for term in content_terms(text)]


def test_fold_lowercases_and_removes_accents():
    assert fold("Rescisão ANTECIPADA") == "rescisao antecipada"
    assert fold("Cláusula 4ª, § 2º") == "clausula 4a 2o"
    assert fold("") == ""


def test_display_preserves_original_spelling():
    terms = content_terms("Qual é a MULTA de rescisão?")
    assert terms == [Term("MULTA", "multa"), Term("rescisão", "rescisao")]


def test_stopwords_and_generic_legal_words_are_dropped():
    question = "Qual a cláusula do contrato que prevê o prazo do aditivo? Existe parágrafo sobre as partes no instrumento?"
    assert folded(question) == ["prazo"]


def test_numbers_and_identifiers_are_dropped():
    question = "Contrato 2024/0012 CPF 123.456.789-09 CNPJ 11.222.333/0001-44 12ABC345DEF678 honorários 5000"
    assert folded(question) == ["honorarios"]


def test_legal_typography_does_not_create_useful_terms():
    assert folded("Cláusula 4ª § 2º nº 7 art. 5º") == []


def test_unique_in_order_of_appearance():
    assert folded("multa prazo MULTA Prazo multa foro") == ["multa", "prazo", "foro"]


def test_short_tokens_are_dropped():
    assert folded("foro de SP em RJ") == ["foro"]


def test_empty_and_blank_text():
    assert content_terms("") == []
    assert content_terms("  \n\t ") == []
    assert absent_terms("", ["qualquer texto"]) == []


def test_term_matches_exact_and_inflection():
    assert term_matches("honorario", "honorarios")
    assert term_matches("multa", "multa")
    assert term_matches("rescisao", "rescisoria")
    assert not term_matches("multa", "multiplo")
    assert not term_matches("prazo", "prado")
    assert not term_matches("dia", "dias")


def test_absent_terms_reports_missing_content_terms():
    texts = ["A multa por rescisão antecipada é de 10%.", "Honorários mensais."]
    assert absent_terms("Qual a multa e os honorários de êxito?", texts) == [Term("êxito", "exito")]
    assert absent_terms("multa honorário", texts) == []
    assert absent_terms("multa", []) == [Term("multa", "multa")]


def test_hostile_input_is_linear():
    question = " ".join(f"termo{chr(97 + i % 26)}{'x' * (i % 40)}" for i in range(20000))
    text = " ".join(f"palavra{i}" for i in range(50000))
    started = time.perf_counter()
    result = absent_terms(question + " " + "a" * 200000, [text])
    assert time.perf_counter() - started < 5
    assert result
    started = time.perf_counter()
    content_terms("(" * 100000 + "a" * 100000 + "!" * 100000)
    assert time.perf_counter() - started < 5


@pytest.mark.parametrize("text", ["\x00\u202e\ufeff", "ﬁnanciamento", "½ ² ①"])
def test_unusual_unicode_does_not_fail(text):
    assert isinstance(content_terms(text), list)


def test_negation_particle_is_not_a_content_term_but_the_negated_subject_is():
    assert [t.folded for t in content_terms("Existe cláusula de não concorrência?")] == ["concorrencia"]


def test_generic_amount_requests_are_not_content_terms():
    assert [t.folded for t in content_terms("Qual o valor da multa e o montante devido?")] == ["multa", "devido"]


def test_masked_and_alphanumeric_identifiers_leave_no_fragment_terms():
    assert [t.folded for t in content_terms("Qual a locação do CNPJ 12.ABC.345/01DE-67?")] == ["locacao"]
    assert [t.folded for t in content_terms("Multa do CPF 123.456.789-01 e CNPJ 11.222.333/0001-44")] == ["multa"]
    assert [t.folded for t in content_terms("12ABC34501DE67 internacionalizacao")] == ["internacionalizacao"]
