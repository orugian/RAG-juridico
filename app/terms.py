"""Deterministic content terms of a question; lookup keys only, never legal inference."""
import re
import unicodedata
from collections.abc import Sequence
from typing import NamedTuple

_TOKEN = re.compile(r"[^\W_]+")
_IDENTIFIER = re.compile(
    r"(?<!\w)(?:[0-9A-Za-z]{2}\.?[0-9A-Za-z]{3}\.?[0-9A-Za-z]{3}/?[0-9A-Za-z]{4}-?[0-9]{2}|\d{3}\.?\d{3}\.?\d{3}-?\d{2})(?!\w)")
_PREFIX = 5
_MIN_LENGTH = 3

_STOPWORDS = frozenset("""
a o as os um uma uns umas de do da dos das no na nos nas ao aos em por pelo pela pelos pelas para com sem sob sobre nao entre ate apos
desde contra perante durante conforme segundo mediante e ou mas nem que se como quando onde qual quais quem quanto quanta quantos
quantas cujo cuja porque porem pois entao tambem ainda ja so apenas muito mais menos mesmo mesma outro outra outros outras
cada todo toda todos todas qualquer quaisquer algum alguma alguns algumas nenhum nenhuma ele ela eles elas eu voce nos vos seu sua
seus suas meu minha este esta estes estas esse essa esses essas isto isso aquilo aquele aquela lhe lhes
ser sao era eram foi foram sera serao seja sejam sendo estar esta estao estava estiver ter tem tinha tiveram teve haver ha havia
houve houver existe existem exista existam existir pode podem poder deve devem dever
preve previsto prevista previstos previstas prever dispoe disposto disposta dispostos dispor consta constam constar constante
estabelece estabelecido estabelecida estabelecem estabelecer diz dizem dito dita fala falam informa indica indicado menciona
mencionado trata tratam determina determinado define definido refere referente referem
cpf cnpj numero informacao informacoes verificar identificar localizar listar liste mostre mostrar informe indique diga apresente descreva
explique favor gostaria saber valor valores quantia montante
clausula clausulas contrato contratos contratual contratuais instrumento instrumentos aditivo aditivos aditamento distrato
paragrafo paragrafos artigo artigos art inciso incisos alinea alineas item itens anexo anexos parte partes
""".split())


class Term(NamedTuple):
    display: str
    folded: str


def fold(text: str) -> str:
    plain = "".join(char for char in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(char))
    return " ".join(_TOKEN.findall(plain))


def content_terms(text: str) -> list[Term]:
    seen = {}
    for match in _TOKEN.finditer(_IDENTIFIER.sub(" ", text)):
        word = match.group()
        key = fold(word)
        if (len(key) < _MIN_LENGTH or " " in key or key in seen or key in _STOPWORDS
                or any(char.isdigit() for char in key)):
            continue
        seen[key] = Term(word, key)
    return list(seen.values())


def term_matches(term_folded: str, word_folded: str) -> bool:
    return term_folded == word_folded or (
        min(len(term_folded), len(word_folded)) >= _PREFIX and term_folded[:_PREFIX] == word_folded[:_PREFIX])


def absent_terms(question: str, texts: Sequence[str]) -> list[Term]:
    words = {word for text in texts for word in fold(text).split()}
    prefixes = {word[:_PREFIX] for word in words if len(word) >= _PREFIX}
    return [term for term in content_terms(question)
            if term.folded not in words and not (len(term.folded) >= _PREFIX and term.folded[:_PREFIX] in prefixes)]
