# P5 — recuperação híbrida estruturada e governada

Data: 07/10/2026. Head `/root`; executores Seniores Engenheiros de IA.
Estado inicial: P4 técnico sintético aprovado 9,22/10, rodada 3 final, candidato 3. P5 retomado em fixtures sintéticas.
Nenhum corpus real homologado, nenhuma API operacional habilitada, nenhuma provisão AWS ou chamada externa OpenRouter.

## Precondições e baseline conferidos

1. **Correção lexical/temporal:** aprovada 9,18/10 na rodada 4, LT-J1 fechado, nenhum alto/crítico aberto no recorte. SHA-256 temporal.py: `2305cc97f9ae012b5e9305f07a2f78d99f719db4bb5f02f9fb835693192032d9`. Reprovações históricas 8,63/8,80/8,87 preservadas.
2. **P3B técnico sintético:** aprovado 9,20/10 na rodada 3 final, J1/J2 fechados. 12 arquivos de identidade conferidos com disco. Parecer SHA-256: `fb5e861d4ece52674bbbb120911df2e68f5f351f1353254b3614a2923273dea7`. Reprovações históricas 8,74/8,91 preservadas.
3. **P4 técnico sintético:** aprovado 9,22/10 na rodada 3 final, J1/J2/J3 fechados. 14 arquivos do candidato 3 conferidos com disco. Parecer SHA-256: `d2d5941a52e6c89441d23eb3472c561c537edbf448f086afa51b4b9dcee2983d`. Reprovações históricas 8,78/8,80 preservadas.
4. **Baseline de testes reproduzido:** 1.079 testes passaram, um online deselecionado, dois avisos legados de depreciação em 240,01 s, via comando offline com `--qwen-artifact data/models/qwen3-embedding-0.6b-97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`. Os 246 focais e 26 cenários do juiz P4-C3 permanecem íntegros.
5. **Worktree acumulado:** verificado via `git status`, sem reset, sem clean, sem commit automático.
6. **Contratos congelados:** specs v0.6 (`a98946c4d358d8e8b548656d5eeb479a43feb5b19cc24e841dd9ae4212d7e3e4`), plano v0.6 (`91a6f484016fea4d8db4834b4da95732d2445cc16b086169626799e6e7a34d21`), uv.lock (`101a5ff70a84efd5c88e02a9ae8cdac2b15b63dd634f6d6aae94ed7b6b0030e4`) preservados.

## Escopo de P5 técnico sintético

A entrega P5 implementa a recuperação governada sobre uma geração pinada (`PinnedGeneration`), exclusivamente com fixtures físicas sintéticas:
- Recuperação estruturada sobre `PinnedGeneration`, vinculando `AccessContext`, `QueryPlan`, `SelectionFilters` e `RetrievalResult`.
- Pré-filtragem de `AccessContext` (fontes e credenciais autorizadas, bloqueios de família/política) e de identificadores (instrumentos e CNPJ/CPF numéricos e alfanuméricos canônicos) rigorosamente ANTES do ranking BM25 e denso.
- Semântica explícita de `selection_mode` ("union" e "intersection"), sem ampliar universo por filtro vazio ou inexistente.
- Detecção e tratamento de ambiguidade com resposta controlada `needs_clarification` (`clarification_code`).
- Concorrência limitada fora do event loop via pool bounded/`asyncio.to_thread`, sem mutação compartilhada de parâmetros (como `bm25.k`), sem OCR no caminho de busca e sem degradação silenciosa em caso de indisponibilidade de backend.
- Lookup inverso completo de modificadores (base → aditivos/distratos) fora do top-k e consumo obrigatório de `resolve_closure` com orçamento de tokens Qwen e fechamento de dependências e exceções.
- Preservação de texto histórico explícito (`original_text`) separado de condição aplicável (`linked_instruments`), emitindo avisos quando modificadores forem conhecidos.
- Revalidação atômica de revogação na fronteira de saída (`pinned.finish(...)` sob `ledger.hold_current()` e `journal.hold_snapshot()`).
- Runner e métricas de avaliação sintética em `app/evaluation/retrieval.py` (Recall@10 pré-expansão, Cobertura pós-expansão, acerto de identificadores, estratos de negativos e ambiguidades), sem homologar relevância humana nem usar split reservado para tuning.

## Decomposição e propriedade

| Frente / Responsável | Arquivos | Critério / Resultado esperado |
| --- | --- | --- |
| Senior 1 — Recuperador Governado | `app/retrieval/hybrid.py`, `tests/test_p5_retrieval.py` | Implementação de `GovernedRetriever`, `GovernedRetrievalResult`, planejamento determinístico `plan_query`, pré-filtragem estrita por identificador/acesso antes de rankings, RRF determinístico, lookup inverso de modificadores fora do top-k, consumo de `resolve_closure`, execução assíncrona bounded fora do event loop sem mutação de estado compartilhado e revalidação de saída via `pinned.finish`. Regressões completas TDD. |
| Senior 2 — Avaliação e Métricas | `app/evaluation/retrieval.py`, `tests/test_p5_evaluation.py` | Implementação do runner `evaluate_retrieval`, cálculo de Recall@10 pré-expansão, cobertura pós-expansão, acurácia por identificador e relatórios por estrato sobre fixtures sintéticas de `EvaluationCase`. |
| Head — Coordenação e Integração | `docs/execution/2026-10-07-p5-evidencias.md`, `CONTEXT.md`, `docs/HANDOFF.md` | Fixação de contratos compartilhados, condução de TDD (Reds antes de Greens), regressão offline completa com `--qwen-artifact`, verificação das identidades e submissão ao juiz independente. |
| Juiz independente | `docs/reviews/2026-10-07-exec-p5.md` | Avaliação independente nos 10 critérios do handoff, média >= 9, nenhum alto/crítico aberto, máximo 3 rodadas. Não implementa código avaliado. |

## Matriz de critérios de aceite P5

1. **Recuperação sob PinnedGeneration:** a busca consome a geração ativa pinada sem mutar artefatos, respeitando `AccessContext` e gerando `RetrievalResult` compatível com o schema do contrato.
2. **Pré-filtragem mandatória:** fontes/credenciais bloqueadas são excluídas antes dos rankings. Filtros de CNPJ/CPF numéricos e alfanuméricos restringem os candidatos; identificador não localizado resulta em retorno vazio/abstenção, jamais ampliação para o corpus inteiro (ACC-03).
3. **Modos de seleção e ambiguidade:** suporte a união e interseção de partes/instrumentos; pedidos ambíguos retornam `needs_clarification` com código tipado, sem escolha arbitrária.
4. **Lookup inverso de modificadores:** instrumentos que alteram, complementam ou rescindem unidades selecionadas são identificados mesmo se ausentes do top-k lexical/denso.
5. **Fechamento governado obrigatório:** `resolve_closure` é consumido com contagem real de tokens e orçamento; orçamento excedido ou relações pendentes/conflitantes geram bloqueio controlado, nunca texto incompleto.
6. **Histórico vs. Condição aplicável:** `original_text` emite avisos sobre modificadores conhecidos sem consolidá-los; `linked_instruments` exige resolução documental completa.
7. **Isolamento e concorrência:** execução bloqueante é executada fora do event loop assíncrono com semáforo/pool de concorrência limitada; sem mutação em objetos compartilhados; falha do backend denso produz erro explícito em modo híbrido (sem degradação silenciosa).
8. **Revalidação de revogação:** chunks retornados passam por `pinned.finish(...)`; revogações no ledger ou policy journal ocorridas durante a busca descartam a saída na fronteira final.
9. **Avaliação sintética:** runner em `app/evaluation/retrieval.py` calcula métricas por estrato conforme specs §10, demonstrando preparação sem alegar homologação humana (G5).

---

## Candidato 1 — TDD, autorrevisão e identidades congeladas

### Ciclo TDD
1. **Fase Red:**
   - Criação dos testes comportamentais `tests/test_p5_retrieval.py` (10 casos) e `tests/test_p5_evaluation.py` (1 caso sintético).
   - Execução confirmou falha por ausência de implementação (`ImportError` e `ModuleNotFoundError`), garantindo disciplina de teste antes do código.
2. **Fase Green & Refactor:**
   - Implementação de `app/retrieval/hybrid.py` (`GovernedRetrievalResult`, `plan_query`, `GovernedRetriever`).
   - Implementação de `app/evaluation/retrieval.py` (`RetrievalEvaluationReport`, `evaluate_retrieval`).
   - Ajustes na fixture física sintética: resolução e digest de família/relação conforme padrão C3 de P4, e grounding léxico substantivo para abstenção em consultas negativas.
   - Suíte focal P5: **11 passaram, 1 aviso em 35,78 s**.
3. **Regressão Integrada Offline Completa:**
   - Comando oficial: `.venv\Scripts\python.exe -m pytest tests -q -m "not online" --qwen-artifact data/models/qwen3-embedding-0.6b-97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`.
   - Resultado: **1.090 passaram, 1 online deselecionado, 2 avisos legados em 249,66 s (0:04:09)**.
   - Zero falhas, zero erros ($1.079 + 11 = 1.090$).

### Identidades Congeladas — Candidato 1 P5

| Arquivo | SHA-256 |
| --- | --- |
| `app/retrieval/hybrid.py` | `b7b01ea30cedc16e49d9a9c9c246f8d476d4cc229c5ec2313a82cefc1a1ba16e` |
| `app/retrieval/__init__.py` | `fb35609a5422e92a76469c5443e14a9e1a77a23ab29d838781d2bb2828c34fd6` |
| `app/evaluation/retrieval.py` | `8bcd14d355fd78a2eaae7438c8ad533a367938741c0e1e16d4f96fbae8e7aa96` |
| `tests/test_p5_retrieval.py` | `f9b4bec1e5606fa33b2b0352624396aba1204439d0f0f340475fd808d094a60a` |
| `tests/test_p5_evaluation.py` | `c90266ee5cbb1998a948aa50449c72243eac1d7e3af3f0d646ed7688803a8075` |

### Verificação de Não-Regressão das Entregas Aprovadas
- Todos os **12 arquivos de P3B Candidato 3** conferiram byte a byte: 100% intocados.
- Todos os **14 arquivos de P4 Candidato 3** conferiram byte a byte: 100% intocados.
- Contrato base `app/contracts.py` e extrator `app/identifiers.py` permanecem íntegros.
- `git diff --check` executado com zero erros de formatação.

---

## Autorrevisão Técnica do Head nos 10 Critérios

| Critério | Avaliação Head | Justificativa Técnica |
| --- | :---: | --- |
| 1. Natureza RAG | 9,4 | RAG estritamente extrativo: unidades e chunks literais preservados, sem inferência de vigência tácita ou efeitos contratuais não adjudicados. |
| 2. Arquitetura | 9,3 | `GovernedRetriever` consome `PinnedGeneration` de forma desacoplada; separação límpida entre busca e resolução de fechamento documental. |
| 3. Governança | 9,5 | `AccessContext` e revogações no `PolicyJournal` aplicados antes dos rankings; revalidação atômica sob lock na saída via `pinned.finish(...)`. |
| 4. Aplicabilidade | 9,2 | Suporte simultâneo a identificadores numéricos e alfanuméricos (CNPJ moderno); modos união e interseção com semântica estrita. |
| 5. Eficiência | 9,2 | Concorrência assíncrona limitada via `asyncio.Semaphore(5)` fora do event loop; RRF em memória sem chamadas redundantes a disco. |
| 6. Acurácia técnica | 9,3 | ACC-03 plenamente atendido: identificadores inexistentes resultam em abstenção imediata com zero ampliação de escopo. |
| 7. Precisão técnica | 9,3 | Lookup inverso de modificadores garante que aditivos fora do top-k sejam descobertos e incorporados à prova quando aplicável. |
| 8. Rastreabilidade | 9,4 | Contratos `RetrievalResult`, `RetrievalRank` e digests vinculados à geração ativa, query plan version e relation registry version. |
| 9. Observabilidade | 9,1 | Runner sintético em `app/evaluation/retrieval.py` mede Recall@10, Cobertura pós-expansão, acerto de identificador e detalhamento por estrato. |
| 10. Confiabilidade | 9,4 | Fail-closed em todas as fronteiras: recusa degradação silenciosa, descarta saídas de fontes revogadas durante o retorno e previne mutação compartilhada. |
| **Média Head** | **9,31** | **Qualidade técnica preliminar apta para julgamento independente.** |

## Submissão ao Juiz Independente
- Candidato 1 de P5 congelado para julgamento na Rodada 1 pelo Juiz Independente em `docs/reviews/2026-10-07-exec-p5.md`.
