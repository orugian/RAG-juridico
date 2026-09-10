"""
Testes de unidade para o ResponseCache (app/cache.py)
Cenários baseados em consultas de contratos jurídicos da Andrade Advogados:
- Honorários advocatícios (pró-labore e quota litis)
- Rescisão contratual e prazos
- Normalização de termos e insensibilidade a maiúsculas/minúsculas
- Expiração por TTL (Time-To-Live)
- Estatísticas de desempenho (hit rate)
"""

import sys
from pathlib import Path

# Permite executar o script diretamente de qualquer diretório
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import time
import pytest
from app.cache import ResponseCache


@pytest.fixture
def cache() -> ResponseCache:
    """Retorna uma nova instância do ResponseCache com TTL padrão de 300 segundos."""
    return ResponseCache(ttl_seconds=300)


def test_cache_miss(cache: ResponseCache):
    """Verifica que uma consulta inédita resulta em cache miss (None) e incrementa misses."""
    query = "Qual a penalidade por inadimplemento no contrato da Empresa Alfa (CNPJ 12.345.678/0001-90)?"
    
    result = cache.get(query)
    
    assert result is None
    assert cache.stats["misses"] == 1
    assert cache.stats["hits"] == 0
    assert cache.stats["hit_rate"] == 0.0


def test_cache_set_and_get_legal_contract_query(cache: ResponseCache):
    """Armazena e recupera resposta sobre honorários advocatícios pró-labore."""
    query = "Qual o valor do pró-labore mensal fixado no contrato da Construtora Rocha Ltda?"
    expected_response = (
        "Conforme a Cláusula 4ª do Contrato de Honorários Advocatícios nº 114/2024, "
        "o valor do pró-labore mensal é de R$ 18.500,00, com vencimento no dia 10 de cada mês."
    )

    # 1. Primeira consulta: miss
    assert cache.get(query) is None

    # 2. Armazena a resposta gerada pelo RAG
    cache.set(query, expected_response)

    # 3. Segunda consulta: hit
    cached_response = cache.get(query)
    assert cached_response == expected_response

    # 4. Valida estatísticas
    stats = cache.stats
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["total"] == 2
    assert stats["hit_rate"] == 0.5


def test_cache_normalization_case_and_whitespace(cache: ResponseCache):
    """
    Garante que variações de maiúsculas/minúsculas e espaços em branco
    gerem a mesma chave de cache (idempotência de consulta jurídica).
    """
    query_base = "qual o percentual de honorários de êxito (quota litis) acordado?"
    query_upper = "  QUAL O PERCENTUAL DE HONORÁRIOS DE ÊXITO (QUOTA LITIS) ACORDADO?  "
    query_mixed = "qUaL o PeRcEnTuAl dE hOnOrÁrIoS dE êXiTo (qUoTa lItIs) aCoRdAdO?\n"

    response = (
        "A Cláusula 5ª estipula honorários de êxito no montante de 20% (vinte por cento) "
        "calculados sobre o benefício econômico auferido pelo cliente ao final da demanda."
    )

    # Armazena usando a query em minúsculas
    cache.set(query_base, response)

    # Deve acertar o cache mesmo com variações de digitação do colaborador
    assert cache.get(query_upper) == response
    assert cache.get(query_mixed) == response

    stats = cache.stats
    assert stats["hits"] == 2
    assert stats["misses"] == 0
    assert stats["hit_rate"] == 1.0


def test_cache_ttl_expiration():
    """Valida a expiração do cache com base no tempo de vida (TTL curto de 1 segundo)."""
    short_cache = ResponseCache(ttl_seconds=1)
    query = "Existe cláusula de confidencialidade (NDA) no contrato de parceria nº 82/2023?"
    response = "Sim, a Cláusula 12ª prevê dever estrito de sigilo e confidencialidade por 5 anos pós-encerramento."

    # Popula o cache
    short_cache.set(query, response)
    assert short_cache.get(query) == response
    assert short_cache.stats["hits"] == 1

    # Aguarda o TTL expirar
    time.sleep(1.1)

    # Após o TTL, deve retornar None e limpar a chave expirada
    assert short_cache.get(query) is None
    assert short_cache.stats["misses"] == 1

    # Chave deve ter sido removida do dicionário interno
    key = short_cache.make_key(query)
    assert key not in short_cache._cache


def test_cache_clear(cache: ResponseCache):
    """Verifica se a limpeza total do cache reseta registros e métricas."""
    q1 = "Qual o prazo de vigência do contrato da Empresa Beta?"
    q2 = "Quem são as testemunhas qualificadas na rescisão do cliente Carlos Eduardo?"

    cache.set(q1, "Vigência de 12 meses a contar da data de assinatura.")
    cache.set(q2, "Testemunhas: Mariana Ramos (CPF: 111.222.333-44) e Lucas Dias (CPF: 555.666.777-88).")

    # Gera hits e misses
    cache.get(q1)
    cache.get("Consulta inexistente")

    assert cache.stats["hits"] == 1
    assert cache.stats["misses"] == 1
    assert len(cache._cache) == 2

    # Executa a limpeza
    cache.clear()

    assert len(cache._cache) == 0
    assert cache.stats["hits"] == 0
    assert cache.stats["misses"] == 0
    assert cache.stats["total"] == 0
    assert cache.stats["hit_rate"] == 0.0
    assert cache.get(q1) is None


def test_cache_stats_calculation(cache: ResponseCache):
    """Verifica a precisão matemática das estatísticas de acerto (hit rate)."""
    q1 = "Consulta Contrato 1"
    q2 = "Consulta Contrato 2"

    cache.set(q1, "Resposta 1")

    # 3 hits
    cache.get(q1)
    cache.get(q1)
    cache.get(q1)

    # 1 miss
    cache.get(q2)

    stats = cache.stats
    assert stats["hits"] == 3
    assert stats["misses"] == 1
    assert stats["total"] == 4
    assert stats["hit_rate"] == pytest.approx(0.75, rel=1e-2)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
