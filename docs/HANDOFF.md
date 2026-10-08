# Handoff atual — RAG Andrade Advogados

Atualizado em 08/10/2026. Ponto de entrada da próxima sessão; estados históricos não substituem o parecer da entrega correspondente.

**P7 técnico sintético aprovado: 9,37/10, rodada 2 final; P7-J1 fechado, nenhum alto/crítico aberto identificado.** [Registro/evidências](execution/2026-10-07-p7-evidencias.md), [parecer independente](reviews/2026-10-07-exec-p7.md) SHA-256 `5eb3cc344c6b550fc565544315c89b1032a2e16be8498073fe235ac552d21771`. Reprovação R1 (8,94/10) preservada como histórica. Candidato 2: 12 identidades congeladas (`app/main.py`, `app/cache.py`, `app/http_contracts.py`, `app/answer_runtime.py`, `app/security.py`, `app/telemetry.py`, `docs/P7_API.md` e testes associados). Regressão offline integrada final Head: **1.556 passaram, um online deselecionado, três avisos legados em 1.195,43 s (0:19:55)** via `--qwen-artifact data/models/qwen3-embedding-0.6b-97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`. Suíte focal independente do juiz: **319 passaram em 485,35 s**. Todas as 49 identidades aceitas de P3B–P6 permanecem 100% intocadas e íntegras. **P8 concluído nesta sessão e aprovado com ressalvas pelo juiz independente na rodada 1 (9,24/10, nenhum alto/crítico aberto)** — ver [evidências P8](execution/2026-10-08-p8-evidencias.md); o aceite P7 acima cobre somente as 12 identidades históricas do candidato 2.

**P6 técnico sintético aprovado: 9,35/10, rodada 2 final; J1 a J8 fechados, nenhum alto/crítico aberto identificado.** [Registro/evidências](execution/2026-10-07-p6-evidencias.md), [parecer independente](reviews/2026-10-07-exec-p6.md) SHA-256 `2edda68b5ca17c286878b5ba622bdf7ffa844cf72c82060123bb547a4598a60c`. Candidato 2: 18 identidades congeladas (`app/answer_contracts.py`, `app/terms.py`, `app/prompts.py`, `app/generators.py`, `app/grounding.py`, `app/rendering.py`, `app/generation.py`, `tests/p6_corpus.py` e testes associados). Regressão offline integrada final: **1.283 passaram, um online deselecionado, dois avisos legados em 551,12 s (0:09:11)**. Suíte focal P6: 193 passaram em 318,48 s + 12 em 42,35 s. Todas as 38 identidades aceitas de P0–P5 permanecem 100% intocadas e íntegras.

**P5 técnico sintético aprovado: 9,33/10, rodada 1 final; nenhum alto/crítico aberto identificado.** [Registro/evidências](execution/2026-10-07-p5-evidencias.md), [parecer independente](reviews/2026-10-07-exec-p5.md) SHA-256 `ef260d849a286fe32dd3784addb93c19582106b1df924a033eade04af8f9d893`. Candidato 1: 5 identidades congeladas (`app/retrieval/hybrid.py`, `app/retrieval/__init__.py`, `app/evaluation/retrieval.py`, `tests/test_p5_retrieval.py`, `tests/test_p5_evaluation.py`). Regressão offline integrada final: **1.090 passaram, um online deselecionado, dois avisos legados em 249,66 s (0:04:09)**. Suíte focal P5: 11 passaram em 35,78 s. Todas as 12 identidades de P3B C3 e 14 de P4 C3 permanecem 100% intocadas e íntegras.

**P4 técnico sintético aprovado: 9,22/10, rodada 3 final; J1/J2/J3 fechados, nenhum alto/crítico aberto identificado.** [Registro/decomposição/14 identidades](execution/2026-10-07-p4-evidencias.md), [parecer independente](reviews/2026-10-07-exec-p4.md). Head C3 final: 1.079 passaram em 220,05 s; juiz 246 focais e 26 cenários próprios. Reprovações 8,78/8,80 preservadas como históricas. G4 operacional/corpus/produção não aprovados.

## Estado e fronteira de continuidade

Plano/specs v0.6 aprovados pelo usuário e congelados. P0, P1 e P2A técnicos aprovados com 9,14/9,09/9,15. [P3A Qwen](execution/2026-10-06-p3a-qwen.md) aprovado exclusivamente no subgate técnico: 9,17, tentativa 2.

A [correção lexical/temporal](execution/2026-10-06-correcao-lexical-temporal.md) foi **aprovada no subgate técnico: 9,18/10, rodada 4; LT-J1 fechado, nenhum alto/crítico aberto identificado**. As reprovações 8,63/8,80/8,87 permanecem históricas. O usuário autorizou duas rodadas adicionais na mesma campanha, máximo cinco total; a quinta não foi necessária. [Parecer independente](reviews/2026-10-06-exec-lexical-temporal.md).

Regressão histórica lexical/temporal: **570 testes passaram, um online deselecionado, dois avisos**, com tokenizer real local. Autorrevisão: 864 combinações sintéticas não promoveram assinatura indevida. O juiz repetiu independentemente 197 testes focais, 864 combinações e seis controles positivos/conflito. Isso não equivale a gabarito jurídico.

**P3B técnico aprovado: 9,20/10, rodada 3 final; J1/J2 fechados, nenhum alto/crítico aberto identificado no recorte.** [Evidências/decomposição/identidades](execution/2026-10-06-p3b-evidencias.md), [parecer independente](reviews/2026-10-06-exec-p3b.md). Reprovações 8,74/8,91 permanecem históricas; identidade aceita é candidato 3, sem quarta rodada. Head: **833 testes passaram, um online deselecionado, nenhum skip, dois avisos em 41,06 s**, com tokenizer real. Juiz: **296 testes próprios, seis probes físicos Qwen e 128 combinações lexicais**, sem alto/crítico novo identificado. G3 pleno/corpus/produção não aprovados.

**Corpus real não homologado:** staging de 287 arquivos/14.791 blocos, 202 quarantined/85 failed; 223 ParsedDocuments, 202 com blocos. Zero fontes aprovadas por responsável humano, zero índice real publicado e nenhum RAG/API de produção operacional. Revisão humana adiada por decisão do usuário; avançar em preparação técnica e fixtures sintéticas. status=success não é autorização.

## Escopo completo e arquitetura-alvo

O produto é um RAG extrativo/auditável do acervo interno, não um consultor de direito genérico. Inclui honorários, contratos diversos, instrumentos de clientes com terceiros e relações entre base, aditivos, complementos e distratos. Políticas detalhadas de inclusão/exclusão estão em CONTEXT §3.1.5: classe M-Files, título, parsing bem-sucedido ou decisão include não aprovam por si sós uma fonte. Nunca presumir a banca como parte de contrato de terceiros. Minutas/modelos genéricos e demais exclusões continuam fora do corpus de consulta.

Fluxo completo previsto: extração M-Files somente leitura → curadoria/staging → revisão escopada e prova física → unidades canônicas/fechamento → gerações com BM25+dense → recuperação governada → seleção/renderização de prova → API/cache → homologação → operação AWS. A Regra dos 4 Elementos exige instrumento, partes efetivas, localização e literal com origem. Não cortar negações, condições/exceções, nem inferir vigência/prevalência jurídica pela data ou pelo ranking. original_text é histórico explícito; linked_instruments exige modificadores e resolução suficientes, inclusive fora do top-k.

Agora há fundações técnicas P0–P7 aprovadas em fixtures sintéticas locais, não um RAG de produção completo. **P5** recuperador governado aceito; **P6** grafo, grounding e seleção de prova aceitos; **P7** API HTTP/FastAPI, cache governado, streaming e concorrência aceitos nas identidades históricas; **P8** homologação ponta a ponta em andamento (qualidade, consistência e concorrência), incluindo um delta restrito no runtime de respostas (pendência documental conhecida → controle 200) que **não** é coberto pelo aceite P7 e ainda não tem aceite próprio; **P9** operação/restore/AWS. Dependências e gates integrais permanecem no plano/specs v0.6; entregas sintéticas não substituem G2/G3/G4/G5/G8/G9 documentais/operacionais pendentes. E-mail é extensão futura, não frente desta retomada.

## Mapa de código para a retomada

| Área | Onde ler | Estado/fronteira |
| --- | --- | --- |
| Contratos e seleção | app/contracts.py, app/models.py, app/identifiers.py | AccessContext, QueryPlan, RetrievalResult e identificadores já existem; schemas não concedem aprovação. |
| Segurança/telemetria | app/security.py, app/telemetry.py, app/monitoring.py | Fundação P1 aceita com deltas de isolamento P7; tracing externo desligado e sem payload contratual. |
| Fontes/staging | app/ingestion/mfiles_client.py, sync.py, curation.py, parsing_pipeline.py, batch.py, schemas.py | Extração/curadoria/parsing locais existentes; não sincronizar, reparsear ou promover corpus real nesta retomada. |
| Prova e fechamento | app/ingestion/evidence.py, provenance.py, references.py, review_store.py, closure.py, chunker.py | P3B aceito; reutilizar builders, decisões correntes e resolve_closure, sem recorte livre da prova. |
| Embedding/lexical | app/embeddings/, app/retrieval/lexical.py | Qwen3-Embedding-0.6B fixado; tokenizer/orçamentos e perfil lexical existentes. |
| Persistência/política | app/retrieval/generation_contracts.py, generation_store.py, vector_store.py, generations.py, policy.py, review_authority.py | P4 aceito; pin/finish governados, backends reais locais, autoridades separadas. |
| Recuperação governada | app/retrieval/hybrid.py, indexer.py; tests/test_p5_retrieval.py | P5 técnico sintético aceito (9,33/10, R1); GovernedRetriever com pré-filtros, RRF, lookup inverso e revalidação de saída. |
| Avaliação sintética | app/evaluation/retrieval.py, dataset.py; tests/test_p5_evaluation.py | P5 aceito; runner evaluate_retrieval calculando Recall@10, cobertura e quebra por estratos. |
| Grafo e geração P6 | app/generation.py, grounding.py, rendering.py, answer_contracts.py; tests/test_p6_*.py | P6 técnico aceito (9,35/10, R2); LangGraph governado, grounding estrito e seleção de prova. |
| API, cache e concorrência P7 | app/main.py, http_contracts.py, answer_runtime.py, cache.py; tests/test_api.py, test_p7_*.py | P7 técnico sintético aceito (9,37/10, R2); factory FastAPI, streaming SSE, reautenticação em voo, cache com HMAC e rotas governadas. **Escopo do aceite:** bytes/identidades históricas do candidato 2 P7; o delta P8 em answer_runtime.py (controle de admission documental) é nova identidade, sem aceite. |

Nos nomes abreviados acima, use o mesmo diretório do primeiro arquivo da linha (app/ingestion/, app/retrieval/ ou app/). O mapa orienta navegação, não atribui propriedade de edição: a próxima entrega deve registrar seus donos antes de alterar contratos compartilhados. Mudança posterior em arquivo aceito exige nova identidade/verificação proporcional; notas antigas não homologam bytes novos.

## Correção lexical/temporal — diagnóstico aceito, não tarefa a reiniciar

Nomes já eram extraídos: 183 documentos/436 nomes candidatos. BM25 agora usa a mesma normalização de caixa/acentos/pontuação nos documentos e consultas; aliases de CPF/CNPJ e datas são chaves de busca, nunca identidade/autorização. Calendário real, literal, bloco e offsets são preservados; original continua separado do contexto sintético.

A nova delimitação conserva o contexto entre datas. Um candidato de data não reinicia o frame: ambos os eventos precisam de vinculação positiva completa, calendário válido e ausência de qualificação não resolvida; frames desconhecidos/históricos/condicionais não liberam fronteira. Ranges usam a mesma política. Assinatura incerta bloqueia o resumo execution_date; fecho cidade/data continua candidato heurístico sinalizado, sem homologação. Uma cláusula com cabeçalho tipográfico reconhecido conserva seus qualificadores e texto.

Diagnóstico atual: 639 menções em 187 documentos, 97 signature (todas closing_line_candidate) e 542 unknown; 71 resumos candidatos contra 106 legados. As 35 remoções não são taxa de acerto. Regras estritas podem deixar datas de vencimento/vigência desconhecidas, inclusive menções antes tipadas; continuam preservadas e pesquisáveis como datas literais. Sem datas relativas, resolução de efeitos jurídicos ou filtros aprovados por nome/vigência.

Relatório local: data/evaluation/lexical-temporal-20261006/metadata-audit-round4-final.json; SHA-256 185f15721bcb0c456019f3220d22e8466f8584cb06d9fabbf7ea3b1e74d6c12c. temporal.py SHA-256 2305cc97f9ae012b5e9305f07a2f78d99f719db4bb5f02f9fb835693192032d9. Relatórios anteriores são históricos; não reatribuir hashes ao código posterior. Leitura/reextração/revalidação em memória, sem editar staging, reparse de originais ou emissão de dados pessoais.

## Leitura obrigatória

1. Instruções AGENTS.md/RTK.md aplicáveis, [CONTEXT.md](../CONTEXT.md), este handoff e [registro central](execution/2026-10-05-execucao-rag.md).
2. [Plano v0.6](../plans/2026-10-05-rag-juridico-producao.md) e [specs v0.6](specs/2026-10-05-rag-juridico-producao.md), sem editar os bytes aprovados.
3. Registro/parecer lexical/temporal, [P3A](execution/2026-10-06-p3a-qwen.md), [P3B](execution/2026-10-06-p3b-evidencias.md) e [P4 atual](execution/2026-10-07-p4-evidencias.md) / [parecer P4](reviews/2026-10-07-exec-p4.md), incluindo identidades por candidato e resultados independentes.
4. Código: app/contracts.py; ingestion/evidence.py, provenance.py, references.py, closure.py, review_store.py, schemas.py, content_validation.py, temporal.py, chunker.py, batch.py; retrieval/lexical.py, indexer.py, hybrid.py, generation_contracts.py, generation_store.py, generations.py, policy.py, review_authority.py, vector_store.py; app/embeddings/ e regressões correspondentes.

O contexto neste checkout chama-se CONTEXT.md, não CONTEXTO.md. Estado e exceções posteriores ficam nos registros de execução; requisitos e gates aprovados permanecem nas specs/plano.

Papel dos documentos: CONTEXT conserva escopo/políticas; plano/specs congelados definem requisitos/aceite; HANDOFF concentra estado e navegação da retomada; execution/reviews conservam evidências, decisões e pareceres por identidade. Ler explicitamente, não presumir hook automático. Comandos históricos, arquitetura-alvo e notas de agentes não são autorização para operações externas; confronte afirmações com código/artefatos atuais.

## Entrega fechada: P3B — candidato 3 aceito

**Resultado esperado:** produzir CitationUnits citáveis e EvidenceChunks de busca vinculados à fonte/versão/arquivo/hash/configuração, ao ledger e a um fechamento verificável. Cabeçalhos e datas candidatas não viram prova jurídica. A Regra dos 4 Elementos exige instrumento, partes efetivas, localização e transcrição literal com origem verificável.

- **Contratos e decisões implementados:** SourceIdentity/SourceSpan físicos/UnitTextSpan e builders em evidence.py; ledger append-only source/parties/unit/relation/family com digest do sujeito, require_current e heads exatos. IDs arbitrários, obsoletos ou revogados não aprovam; fonte não substitui revisão dos escopos derivados.
- **Proveniência implementada:** provenance.py relê original hash-verificado, separa offset literal de XML/célula/página e contexto reconstruído. Conversão exige derivado DOCX retido/hash vinculado. OCR sem prova bloqueia; bbox PDF só medido quando há mapeamento exato, nunca inventado. Igualdade ao parser não equivale a homologação visual humana.
- **Fechamento implementado:** closure.py percorre dependências/pais sem recursão ilimitada, valida unidade inteira/decisões atuais e relações/famílias. Referências são reextraídas do literal físico e integram unit_review_digest; propostas por rótulo único local não concedem aceite. Pendentes, alvos ausentes/ambíguos, relativos e intervalos exigem mapping explícito revisado. Listas reconhecem e/ou, ou/e, e, ou, vírgula, ponto e vírgula e bem como, conservando offsets; ranges não inferem intermediários. Conector explícito sem próximo rótulo válido vira pending, inclusive no fim do bloco; vírgula narrativa pode exigir mapping específico. Não alegar interpretação universal. Falha/ciclo/risco/orçamento retorna bloqueio vazio. Data não resolve efeitos jurídicos.
- **Orçamento implementado:** QwenTokenBudget usa tokenizer real local sem inferência; unidades canônicas completas separadas de EvidenceChunks/slices de busca. Contexto/cabeçalho entram no limite; texto e mapas se reconstruem sem perda. CandidateSlice permanece diagnóstico; fechamento excedente não trunca.
- **Integração implementada:** create_legal_chunks exige ledger/budget/build_requests; create_candidate_chunks é helper explícito sem autorização. Lote governado revalida todos os originais/convertidos e escopos/ledger antes do retorno. Fingerprints incluem fonte/config/parser/mapas/refs/ledger/relações/registry/Qwen/lexical/código/lock. Persistência/paridade/journal estão na entrega P4 abaixo; consumo obrigatório do resolver na recuperação/geração continua dependência de P5/P6.

Decomposição e propriedade foram registradas antes de editar: Head contratos/builders/integração/docs; Seniores ledger, proveniência e fechamento; nova frente independente references após rodada 1. Seniores closure/references atingiram limite de uso após entregas parciais; Head assumiu os casos restantes. Juiz não implementou o código. Não redefinir candidato 1 como corrigido nem atribuir resultado do Head ao juiz.

RelationResolution.registry_digest compara todos os heads atuais de relation, impedindo omitir nova descoberta. É global/conservador: nova relação não pertinente também invalida família; só escopar com registry completo verificável. original_text conserva prova própria e avisos; linked_instruments com reference_date bloqueia adjudicação temporal ausente.

Red obrigatório: decisão falsa/ausente/obsoleta/revogada; fonte/hash/versão alterados; mapa de spans incorreto ou sintético; omissão de condição/negação/exceção; fechamento incompleto/cíclico; relação pendente/conflito; excesso de tokens Unicode/cabeçalho; reconstrução sem perda após split. Usar fixtures sintéticas; não fabricar decisões humanas para documentos reais.

P3B não encerra G3: comparação de ao menos dois encoders locais e BM25, gabarito de desenvolvimento autorizado, relevância/cobertura e qualificação Chroma/Linux/recursos continuam necessários. P4 é somente técnico sintético local; P5 recuperação avaliada e demais entregas mantêm suas dependências. Nenhuma API/índice real é autorizado pelos subgates técnicos.

Identidade final P3B: doze hashes em execution/2026-10-06-p3b-evidencias.md (tabela completa candidato 2 com três substituições candidato 3). Parecer final SHA-256 fb5e861d4ece52674bbbb120911df2e68f5f351f1353254b3614a2923273dea7. Código/testes não alterados durante julgamento. Preserve histórico e confira essas identidades antes de novas edições.

## Entrega fechada: P4 — candidato 3 aceito

R1/R2 reprovadas 8,78/8,80. Juiz anterior interrompido por limite de uso; /root/juiz_p4_continuacao concluiu a mesma R2, sem reset, fechando J1/J2 e confirmando J3 alto na cópia final. C3 corrigido após parecer, via cinco Reds e nove Green focais, foi julgado independentemente e aprovado 9,22 na R3 final, J1/J2/J3 fechados. Histórico e seus hashes preservados; nenhum aceite reatribuído a C1/C2.

Resultado técnico atingido: prova canônica completa, BM25 e vetores recarregáveis com paridade exata nos backends embedded e servidor loopback real, publicação local consistente e revogações correntes. Bundle/manifest preservam unidades, chunks, mapas, referências, revisões, relações/resoluções e suas identidades; projeção Chroma não é fonte citável. Qwen real local apenas em fixtures sintéticas; L2 sobre vetores FP32 normalizados evita alteração de um ULP no armazenamento cosine.

GenerationManager exige root/configuração/runtime/backend/ledger/journal/identidade explícitos. Build reserva ID, mantém staging, valida prova física e recarga, escreve READY e finaliza diretório; não promove. Promote/rollback trocam active.json atomicamente; recover finaliza somente staging READY íntegro, sem promover/recriar autoridade. Pin fixa geração por requisição; finish revalida heads/política antes de devolver cópia canônica. Probe é diagnóstico P4, não resposta citável nem recuperação P5 avaliada; P5/P6 devem consumir closure e orçamento real, incluindo modificadores inversos.

Candidato 1 reprovou 8,78 na rodada 1: J1 alto confirmou promoção após revogação unit no ledger, J2 médio perda do ledger recriando DB/OperationalError. Candidato 2 tem regressões e commit_guard retendo ambas autoridades até swap/READY/recovery; ACK concorrente espera a liberação. Adapter P4 consome ledger existente sem bootstrap/migração (leitura mode=ro; lease não mutante mode=rw/BEGIN IMMEDIATE/query_only/ROLLBACK). Autoridades ficam fora da raiz restaurada. Perda/corrupção recusa admissão e readiness falha fechada; SQLite pode manter sidecars WAL/SHM, sem criar DB/schema/decisões.

Candidato 3 prepara todas as cópias antes do checkpoint, retém ledger→policy e revalida decisões/seleção sob essas autoridades antes do retorno local. Sem inferência/cópia posterior ou reentry; query permission basta e falhas liberam locks. Identidade aceita C3: doze hashes da tabela C2 com duas substituições C3 no registro; 14 conferem, doze P3B preservados. Parecer final SHA-256 **d2d5941a52e6c89441d23eb3472c561c537edbf448f086afa51b4b9dcee2983d**. Os 1.072 integrados Head/239 focais juiz/8,80 pertencem somente ao candidato 2. Campanha encerrada em três rodadas, sem exceção herdada lexical/temporal.

Juiz C3 repetiu 246 focais e 26 cenários próprios válidos (25 iniciais mais um controle corrigido/retestado, não uma única execução integral verde). Probes cobriram primeiro/último chunk em finish/probe com source/credential/unit revogados, writers reais, falhas, família/relação, IDs e cópias aninhadas. Nenhum alto/crítico aberto identificado no recorte; isso não é garantia universal jurídica/operacional. Código/tests não mudaram durante julgamento.

D04/G4 operacional não aprovados: journal local fora do snapshot não é domínio físico independente; rollback integral da autoridade DB+checkpoint em processo novo exige anchor/journal durável externo. Não há corpus humano publicado, API operacional ou avaliação de qualidade P5. Gerações/stagings/reservas permanecem retidos; nenhuma coleta automática/limpeza autorizada. CLI build/verify/promote/rollback/recover e entrypoints indexer/batch são explícitos, sem corpus/backend por default; ver comandos e fronteira de confiança no registro P4.

## Entrega fechada: P5 — candidato 1 aceito

**Resultado esperado:** produzir recuperação estruturada e autorizada sobre uma geração pinada (`PinnedGeneration`), exclusivamente com fixtures físicas sintéticas, com `QueryPlan`, ranks/configuração/filtros/cobertura e prova expandida por fechamento aprovado (`resolve_closure`).

- **Recuperador governado implementado:** `GovernedRetriever` em `app/retrieval/hybrid.py` vincula `AccessContext`, `QueryPlan`, `SelectionFilters` e `RetrievalResult`. Pré-filtragem estrita de credenciais, fontes autorizadas e identificadores canônicos (instrumentos e CNPJs numéricos e alfanuméricos) rigorosamente antes dos rankings BM25 e denso (Chroma).
- **Semântica estrita de seleção e abstenção:** suporte determinístico a modos `union` e `intersection`. Consultas com identificador não localizado resultam em abstenção imediata (ACC-03) sem ampliação espúria para o corpus inteiro. Ambiguidade detectada emite `needs_clarification` com código tipado (`clarification_code`), sem escolha arbitrária.
- **Isolamento de concorrência e resiliência:** execução assíncrona limitada fora do event loop via `asyncio.Semaphore(5)` e `loop.run_in_executor`; sem mutação de estado compartilhado (como `bm25.k`); sem OCR na consulta e sem degradação silenciosa em falha de backend.
- **Lookup inverso e fechamento governado:** instrumentos modificadores (aditivos e distratos) vinculados a unidades candidatas são identificados via lookup inverso mesmo fora do top-k; consumo obrigatório de `resolve_closure` com contagem real de tokens Qwen e orçamento. Orçamento excedido ou relações pendentes/conflito geram bloqueio controlado com status tipado.
- **Histórico explícito vs. condição aplicável:** `original_text` emite aviso de modificador conhecido (`known_modifier`) sem consolidá-lo tacitamente; `linked_instruments` retém apenas os instrumentos previstos no plano com fechamento documental completo.
- **Revalidação de revogação na fronteira:** saída final de unidades passa por `pinned.finish(...)` sob lock; revogações no ledger ou policy journal ocorridas durante a busca descartam a saída na fronteira de retorno.
- **Avaliação sintética implementada:** runner `evaluate_retrieval` em `app/evaluation/retrieval.py` calcula Recall@10 pré-expansão, Cobertura pós-expansão, acurácia de identificadores e relatórios por estratos de avaliação sintética (`negative`, `ambiguous`, `identifier`, `conditions`), demonstrando preparação sem alegar homologação humana (G5).

Identidade final P5: 5 hashes em [docs/execution/2026-10-07-p5-evidencias.md](execution/2026-10-07-p5-evidencias.md). Parecer final SHA-256 `ef260d849a286fe32dd3784addb93c19582106b1df924a033eade04af8f9d893` (aprovado 9,33/10 na rodada 1 final). Regressão offline integrada: **1.090 passaram, um online deselecionado, dois avisos legados em 249,66 s**. Todas as 12 identidades de P3B C3 e 14 de P4 C3 permanecem 100% íntegras.

## Entrega fechada: P7 — candidato 2 aceito

**Resultado esperado:** construir a camada de API HTTP em FastAPI (`app/main.py`), expondo o serviço governado de P6 (`GovernedAnswerService`), com autenticação interna rigorosa (`X-API-Key`), autorização escopada com `AccessContext`, cache governado de respostas (`app/cache.py`) com invalidação automática por mudança de época de política (`policy_epoch`) ou revogações, suporte a streaming SSE bufferizado (um evento `answer` validado integral), controle de concorrência com pools e filas bounded e mapeamento uniforme de exceções técnicas e de governança para códigos de status HTTP padrão sem vazamento de stacktrace ou dados contratuais.

- **Endpoints implementados e governados:** rotas `/health` (liveness mínima pública sem consulta a modelos), `/chat` (alias não-streaming), `/v1/answer` (JSON validado integral), `/v1/answer/stream` (SSE bufferizado com evento único `event: answer`), `/ready` (readiness de autoridades, ledger e configuração), `/metrics` (allowlist estrita de métricas agregadas e gauges numéricos de pools) e `/admin/policy` (estado de época e bloqueios para operador).
- **Fronteira uniforme ASGI e leases físicos:** implementada classe base `_GuardedResponse` com especializações `_QueryResponse` e `_ControlResponse`. Um único worker físico do pool dedicado cobre preparação, serialização pré-guard e commit sob lock de autoridade física.
- **Port de controle síncrono no runtime:** `GovernedAnswerRuntime.prepare_control(...)` e `commit_control(...)` operam com serialização confiável `encode_control`, gerando `PreparedControl` imutável com assinatura HMAC (`integrity`). Leases retêm writers até o desenrolamento do envio ASGI.
- **Revalidação em voo e fechamento de P7-J1:** `reauthenticate_context` é invocado tanto no commit quanto imediatamente antes do início da transmissão ASGI (`http.response.start`), garantindo resposta 401 para chaves removidas em voo e 403 para credenciais revogadas no journal, eliminando qualquer bypass.
- **Cache governado:** `GovernedResponseCache` indexado por `CacheIdentity` completo (pergunta normalizada, digest de acesso, plano, geração, versões, journal, epoch e ledger digest), com orçamento duplo (LRU de entradas e bytes UTF-8) e selagem HMAC com chave secreta por runtime (`cache_integrity`).
- **Resiliência e concorrência:** Pools dedicados `queries` (default 5 workers + 5 fila) e `controls` (default 2 workers + 2 fila), rate limit com RateTable de chaves limitadas, tratamento idempotente de cancelamento e desconexão de cliente sem vazamento de capacidade física. Métricas e política operam mesmo com Chroma down; journal down fecha 503.

Identidade final P7: 12 hashes em [docs/execution/2026-10-07-p7-evidencias.md](execution/2026-10-07-p7-evidencias.md). Parecer final SHA-256 `5eb3cc344c6b550fc565544315c89b1032a2e16be8498073fe235ac552d21771` (aprovado 9,37/10 na rodada 2 final, P7-J1 fechado). Regressão offline integrada Head: **1.556 passaram, um online deselecionado, três avisos legados em 1.195,43 s**. Suíte focal independente do juiz: **319 passaram em 485,35 s**. Todas as 49 identidades anteriores de P3B–P6 permanecem 100% íntegras.

## Entrega concluída: P8 — Homologação ponta a ponta (preparação técnica sintética)

**Decisão:** aprovado com ressalvas pelo juiz independente na **rodada 1 de 3**, **média 9,24/10** (limiar ≥9 atingido), **nenhum achado alto/crítico aberto**. Notas: Natureza RAG 9,3 · Arquitetura 9,4 · Governança 9,5 · Aplicabilidade 9,3 · Eficiência 9,1 · Precisão técnica 9,1 · Acurácia técnica 9,2 · Rastreabilidade 9,4 · Observabilidade 9,2 · **Confiabilidade 8,9**. Verificação de identidades feita pelo próprio juiz (9 candidatos exatos; 49 P3B–P6 íntegras; 10/11 P7 C2 intactos; `answer_runtime.py` como nova identidade P8 sem herança do aceite P7).

**Achados abertos que viajam com a aprovação:** (1) **MÉDIO** — `WinError 5` intermitente no `os.rename` do build ([app/retrieval/generations.py:274](../../app/retrieval/generations.py#L274), código **aceito P4 intocado**), com amplificação por fixture module-scoped (1 falha de setup → 23 erros); não bloqueador, fail-safe, Windows-only, **reavaliar em Linux/D04/P9**. (2) **BAIXO-MÉDIO** — janela fixa de 8 s no harness de deadline ([tests/test_p8_load.py:364-419](../../tests/test_p8_load.py#L364-L419)), fragilidade de harness, não do produto. Por isso a **regressão offline integral oficial não está verde** (1.746 passaram, 4 falharam, 1 erro). Achados fechados: safety INDETERMINADO → 9/9; `relation_registry_version="v1"` hardcoded; temporários na raiz.

**Objetivo:** estruturar a homologação ponta a ponta técnica do sistema RAG exclusivamente sobre seis DOCX sintéticos locais determinísticos e fixtures sintéticas, preparando suites de teste de carga moderada, validação exata dos 4 elementos em múltiplos cenários contratuais (honorários, clientes com terceiros, base + aditivos + distrato), consistência entre consultas repetidas (cache hit vs miss), comportamento sob saturação de fila/workers e robustez de limites operacionais. Sem homologação humana formal de corpus real (G5/G8) e sem implantação em nuvem (P9), que continuam dependências externas pendentes.

1. **Precondições:** ler P8 do plano v0.6 e specs §§5, 8, 9, 10; conferir aceites e identidades de P3B a P7; registrar decomposição, donos de arquivos e critérios antes de editar.
2. **Matriz E2E Sintética:** cobrir todas as categorias de consulta contratual com fixtures determinísticas nos backends Chroma embedded e servidor local com Qwen real fixado.
3. **Avaliação de Concorrência e Carga:** simular carga moderada concorrente dentro dos limites do ambiente de desenvolvimento, aferindo tempos p50/p95 e comportamento de fila/rejeição 429/504.
4. **TDD e regressão:** preservar 100% da suíte offline de testes existente e validar com juiz independente nos 10 critérios.

**Estado factual da execução P8 (preparação técnica sintética, aprovada com ressalvas):** matriz de 16 casos lógicos × JSON miss/hit + SSE bufferizado × 2 backends reais = **96 observações HTTP**, **258 citações esperadas/emitidas/corretas** (129 por backend), texto/4 elementos idênticos ao gabarito independente e equivalência miss/hit; **102** testes unitários do avaliador estrito (expectation/result/aggregate/equivalent, fail-closed); **10** testes de carga/limites com stalls determinísticos (**não é SLO/carga natural**); **9** fatias de segurança/falhas/governança (auth/injeção, candidato inválido, indisponibilidade do gerador, outage RPC real, revogação externa) — o integral próprio ficou indeterminado e foi **fechado 9/9 pela execução do juiz**; **27** casos do delta de admission (pendência documental conhecida → controle 200 `abstained/unresolved_relation`, sem prova/LLM/cache, emissão revalidando ponteiro ativo e autoridades sob leases). Repetição sintética: **não** são 96 perguntas humanas independentes e não substituem os conjuntos humanos reservados (>=100 reservado/>=50 desenvolvimento, >=60 respondíveis, estratos/rotas) nem G5/G8/P9. Qwen fixado 1024 dimensões, Chroma embedded e servidor local reais; nenhum fallback remoto, D04/AWS/Linux/SLO ainda pendentes.

## Roadmap para a entrega do produto final

P8 **não** é o produto final: fechou a *preparação técnica sintética*. A entrega depende de três blocos, nesta ordem.

### Bloco 1 — Próxima sessão: fechar os achados abertos de P8

| # | Tarefa | Local | Por quê |
|---|---|---|---|
| 1 | Resolver o `WinError 5` no rename do build | [app/retrieval/generations.py:274](../../app/retrieval/generations.py#L274) | Única razão de a regressão oficial não estar verde. Está em código **aceito P4**, então exige **nova identidade + reverificação proporcional** — nunca correção silenciosa |
| 2 | Reexecutar em **Linux** | ambiente EC2 (Bloco 2) ou WSL | O erro é Windows-only (rename de diretório com handle aberto); confirma ou elimina metade da causa raiz |
| 3 | Corrigir a amplificação por fixture module-scoped | [tests/test_p8_e2e.py:36-50](../../tests/test_p8_e2e.py#L36-L50) | 1 falha de setup → 23 erros mascara a extensão real (medido pelo juiz, não pelo Head) |
| 4 | Trocar a janela fixa de 8 s por medida relativa | [tests/test_p8_load.py:364-419](../../tests/test_p8_load.py#L364-L419) | Fragilidade de harness que gera falso alarme de produto |

**Critério de saída do Bloco 1:** regressão offline integral **verde e sem ressalvas**, com os dois achados abertos fechados ou rebaixados com justificativa e nova identidade registrada. Não reabrir P8 como campanha; é correção proporcional com nova identidade.

### Bloco 2 — Sessão seguinte: ambiente EC2 / AWS (P9, D04)

O usuário providencia **comando SSH e senha de usuário convencional fora de banda**. Trabalho previsto: preparar o ambiente e executar testes conforme a complexidade do projeto.

> **REGRAS DE CREDENCIAL — obrigatórias:** a senha, o comando SSH e qualquer chave **nunca** podem ser gravados neste repositório, em `docs/`, `CONTEXT.md`, `HANDOFF.md`, mensagens de commit ou logs versionados. Vão apenas para `.env` / `m-files/acesso.md` (ambos **já no `.gitignore`**). Antes de cada `git add`/`commit`, varrer o staged por padrões de senha/chave/token. Preferir chave SSH a senha; se a senha for imprescindível, rotacionar após o uso.

Escopo provável do Bloco 2: qualificar o backend em Linux, medir RAM/CPU real (hoje há apenas microbenchmark P3A: encoder isolado p95 1,415 s, RSS 3,60 GiB — **não** é SLO e não atinge a meta de 1 s), validar stack/concorrência/recursos do alvo, e avançar D04 (fonte durável externa, domínios físicos de falha — hoje o journal externo é SQLite cooperativo no mesmo host, **não** é domínio de falha independente).

### Bloco 3 — Última etapa: dependências humanas (não automatizáveis)

- **Corpus real**: staging tem 287 arquivos/14.791 blocos, **202 quarantined / 85 failed**, **zero aprovação humana** e nenhum corpus/índice publicado.
- **Revisão humana G5/G8**: qualificação de extração e adjudicação de partes/datas/efeitos por revisores jurídicos.
- **Gabarito humano reservado**: >=100 questões **independentes**, separadas de >=50 de desenvolvimento, >=60 respondíveis, estratos por rota (20 identificadores/20 negativos/20 condições/10 ambiguidades, >=5 por rota).
- **Precision/Recall de relevância jurídica**: exige oráculo humano.
- **G2/G3 pleno**, e por fim **G4 operacional** (liberação a usuários).

**Travas que não se negociam:** nenhum gate documental/operacional avançou; o aceite P8 cobre somente preparação técnica sintética; o sistema prova *fidelidade de transcrição*, não *compreensão de efeito jurídico* — distinção intencional, pois o produto nunca infere efeitos jurídicos.

## Configuração e limites preservados

- Qwen/Qwen3-Embedding-0.6B, revisão 97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3; 1024 dimensões e limite 1024 tokens/entrada, CPU FP32/seis threads, batch máximo 2/2048 tokens preenchidos; até 64 entradas/65.536 caracteres por requisição. Query com instrução fixa; documento sem instrução; pooling final válido/L2, sem truncamento. Contagem/tokenização/inferência compartilham trava.
- Artefato: data/models/qwen3-embedding-0.6b-97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3; manifesto SHA-256 025a8edceba57399e67e029505fa1be3140b4c25ec0b59488eb60beed1f57688. Extra qwen instalado; não baixar/reinstalar por rotina.
- Staging: data/staging/73d18984-c7bd-4dab-a2aa-4707bfd99482; digest das 287 entradas dce85621ebc9e71e7f3fec573f08db55d24314b6fa14f5a9c3043071ebcfe14b. Mudanças de código exigem nova identidade/preflight/derivação, sem reescrever histórico.
- Metadados legados sem versão temporal são legíveis e não qualificados. BM25 sem perfil completo exige rebuild; JSON complexo Chroma é projeção, não filtro escalar/fonte de citação. joblib só de origem local confiável; hash não autentica pickle externo.
- OpenRouter escolhido, provider_policy_approved=False; política de dados precede consulta real. Tracing externo desligado; não enviar perguntas/trechos contratuais a provedores. Acervo interno comum, com autenticação; seleção por cliente não é autorização.
- Desenvolvimento Windows local; produção futura Linux/AWS. Microbenchmark P3A: encoder isolado p95 1,415 s, RSS amostrado 3,60 GiB; não atende à meta de recuperação completa de 1 s nem é SLO. Validar stack/concorrência/recursos no alvo AWS antes de implantação. D04 conta/região/journal/custos pendente; LibreOffice/Tesseract ausentes no parsing local.
- SHA-256 plano: 91a6f484016fea4d8db4834b4da95732d2445cc16b086169626799e6e7a34d21; specs: a98946c4d358d8e8b548656d5eeb479a43feb5b19cc24e841dd9ae4212d7e3e4; uv.lock: 101a5ff70a84efd5c88e02a9ae8cdac2b15b63dd634f6d6aae94ed7b6b0030e4.

## Protocolo e organização

Main: **Head Engenheiro de IA**; executores: **Seniores Engenheiros de IA**, com arquivos/frentes definidos. Juiz independente não implementa o código julgado. Cada entrega segue TDD → autorrevisão → juiz, dez critérios 0–10: natureza RAG, arquitetura, governança, aplicabilidade, eficiência, acurácia técnica, precisão técnica, rastreabilidade, observabilidade, confiabilidade. Média >=9 e nenhum alto/crítico aberto; **máximo três rodadas por entrega**. P3B: rodada 3 final aprovada 9,20; P4: rodada 3 final aprovada 9,22; P5: rodada 1 final aprovada 9,33; P6: rodada 2 final aprovada 9,35; P7: rodada 2 final aprovada 9,37 (R1 8,94 histórica preservada).

Código útil em app/, regressões permanentes em tests/, registros úteis em docs/execution e docs/reviews; dados/diagnósticos/modelos em data/ ignorado pelo Git. Probes pontuais em stdin/tmp pytest; sem scripts descartáveis/arquivos concorrentes de handoff na raiz. Não apagar pareceres ou reatribuir resultados antigos. O worktree acumulado de P7/P8 foi commitado e sincronizado com o GitHub (`orugian/RAG-juridico`); sem reset/clean, leitura/exibição de .env ou segredos.

Comando integrado offline, com testes locais do tokenizer já instalado:
~~~powershell
.venv\Scripts\python.exe -m pytest tests -q -m "not online" --qwen-artifact data/models/qwen3-embedding-0.6b-97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
~~~

## Prompt para a próxima sessão — pronto para copiar

~~~text
Assuma como Head Engenheiro de IA do RAG Andrade Advogados; os executores serão Seniores Engenheiros de IA. Leia as instruções AGENTS.md/RTK.md aplicáveis, CONTEXT.md e docs/HANDOFF.md (escopo completo, mapa e próxima entrega), depois o plano/specs v0.6 congelados e os registros/pareceres atuais. O produto é um RAG extrativo e auditável do acervo contratual interno, incluindo contratos de terceiros e relações base/aditivos/distratos; preserve instrumento, partes efetivas, localização e transcrição literal, sem inferir efeitos jurídicos. A sessão anterior encerrou após P7 aprovado (9,37/10, R2); **P8 foi concluído nesta sessão e aprovado com ressalvas pelo juiz (9,24/10, rodada 1)**. Preserve o worktree acumulado, sem reset/clean/commit automático.

Antes de editar, confira a correção lexical/temporal aprovada 9,18/10 na rodada 4, P3B técnico aprovado 9,20/10 na rodada 3 final (J1/J2 fechados), P4 técnico sintético aprovado 9,22/10 na rodada 3 final (J1/J2/J3 fechados), P5 técnico sintético aprovado 9,33/10 na rodada 1 final, P6 técnico sintético aprovado 9,35/10 na rodada 2 final (J1 a J8 fechados) e P7 técnico sintético aprovado 9,37/10 na rodada 2 final (P7-J1 fechado; R1 8,94 preservada), sem alto/crítico aberto identificado nos recortes. Confira identidades e pareceres, incluindo doze arquivos P3B, quatorze P4 candidato 3, cinco P5 candidato 1, dezoito P6 candidato 2 e doze P7 candidato 2. Baseline final P7: 1.556 testes Head integrados (zero falhas, 1 deselecionado online) e 319 testes focais do juiz. Preserve reprovações históricas; não reinicie campanhas encerradas, não reabra a escolha Qwen nem reimplemente P0–P7 do zero. Alterar arquivo aceito exige nova identidade e verificação proporcional.

Após confirmar os aceites, ataque o **Bloco 1 do roadmap** (seção própria acima): P8 já está aprovado com ressalvas (9,24/10, rodada 1) — **não o reexecute nem reabra a campanha**. O trabalho desta sessão é fechar os dois achados abertos: (1) resolver o `WinError 5` intermitente no `os.rename` de [app/retrieval/generations.py:274](../../app/retrieval/generations.py#L274) — código **aceito P4**, portanto **nova identidade + reverificação proporcional**, nunca correção silenciosa; (2) medir e corrigir a amplificação por fixture module-scoped em [tests/test_p8_e2e.py:36-50](../../tests/test_p8_e2e.py#L36-L50) (1 falha de setup → 23 erros) **antes** de qualquer mudança de desenho; (3) trocar a janela fixa de 8 s em [tests/test_p8_load.py:364-419](../../tests/test_p8_load.py#L364-L419) por medida relativa. **Critério de saída:** regressão offline integral verde e sem ressalvas. As sessões seguintes são Bloco 2 (ambiente EC2/AWS, com credenciais fornecidas fora de banda e **nunca** versionadas) e Bloco 3 (dependências humanas). Leia requisitos congelados; registre decomposição, propriedade, critérios e resultado esperado antes de editar e delegue só tarefas independentes.

Execute com TDD, autorrevisão e juiz independente por entrega: dez critérios do handoff, média >=9, nenhum alto/crítico aberto e máximo três rodadas. Preserve os controles anteriores, use os backends e Qwen reais locais e execute a regressão offline com --qwen-artifact conforme o handoff. Se faltar aceite ou surgir bloqueio material, registre e resolva antes de avançar; não fabrique aprovação. Siga o roadmap em três blocos (1: fechar achados de P8; 2: EC2/AWS Linux e D04; 3: homologação humana G5/G8, corpus real e gabarito reservado). **Nenhuma credencial SSH/senha/chave entra no repositório, docs ou commits** — apenas .env / m-files/acesso.md, ambos já ignorados pelo Git; varra o staged antes de cada commit. Corpus real, homologação humana (G5/G8) e AWS (P9) continuam pendentes. Respeite app/tests/docs/data, evite arquivos descartáveis e atualize evidências/context/handoff ao fechar cada gate. Retornos breves com decisão, evidência e resultado esperado.
~~~


## Histórico arquivado

O corpo histórico de 05/10 foi conferido com o Git (normalizando apenas CRLF/LF) e está preservado no commit f30197080c8f2ddf2fe2e4b694e75063fe01ddfe: consulta por `git show f30197080c8f2ddf2fe2e4b694e75063fe01ddfe:docs/HANDOFF.md`. As antigas instruções de indexar/publicar/API não orientam a próxima sessão. Pareceres e diagnósticos recentes continuam nos diretórios próprios; nenhuma cópia de arquivo morto foi criada.
