# P6 — geração governada, grounding e citação dos 4 Elementos (técnico sintético)

Data: 07/10/2026. Head `/root`; executores Seniores Engenheiros de IA; juiz independente por entrega.
Estado inicial: P5 técnico sintético aprovado 9,33/10 (rodada 1 final). P6 retomado em fixtures sintéticas.
Nenhum corpus real homologado, nenhuma API operacional, nenhuma provisão AWS, nenhuma chamada OpenRouter/D01.

## Precondições conferidas antes de editar

- Instruções AGENTS.md/RTK.md aplicáveis (somente `~/.claude/RTK.md`; não há AGENTS.md no repositório), CONTEXT.md, docs/HANDOFF.md, plano/specs v0.6 (P6; specs §§4.3, 6, 7, 8, 9, 10), registros e pareceres P3B/P4/P5 lidos.
- Repositório em `d1d8942` (P0–P5 commitados), árvore limpa; nenhum reset/clean/commit automático.
- **38 identidades SHA-256 conferidas com o disco**: 12 P3B C3, 14 P4 C3, 5 P5 C1, plano v0.6, specs v0.6, uv.lock, temporal.py e os três pareceres (P3B `fb5e861d…`, P4 `d2d5941a…`, P5 `ef260d84…`). Zero divergências (conferência normalizando apenas CRLF/LF).
- Aceites reconfirmados nos pareceres: lexical/temporal 9,18 (R4), P3B 9,20 (R3), P4 9,22 (R3), P5 9,33 (R1). Reprovações históricas P3B 8,74/8,91 e P4 8,78/8,80 preservadas; nenhuma campanha reiniciada; escolha Qwen não reaberta.
- Regra: arquivo aceito alterado exige nova identidade; **P6 não altera nenhum dos 38 arquivos**. Todo código P6 é novo (módulos e testes novos abaixo).

## Escopo de P6 técnico sintético

Pipeline de resposta extrativa sobre `GovernedRetrievalResult`/`RetrievalResult` de P5 em fixtures físicas sintéticas:

1. **Contratos**: `GenerationCandidate` (existente, IDs somente), `ResponsePayload` e `AnswerAudit` (novos, `app/answer_contracts.py`).
2. **Contexto/prompt versionado** com fronteira instrução/dado (documentos são JSON escapado, nunca instrução).
3. **Geração** por gerador injetado (double determinístico sintético e adaptador LangChain com porta de política D01 fechada por padrão); orçamento de requisição (≤30 s, tentativas limitadas), fallback somente com destino aprovado (desligado), invalidez ≠ falha técnica.
4. **Grounding** (`app/grounding.py`): validação estrita do candidato (schema/IDs oferecidos/itens/cobertura), fechamento reconsultado por `resolve_closure` (autoridade P3B) com orçamento real, unidades idênticas ao bundle canônico, abstenção categórica por termos ausentes e checagem de suporte por item.
5. **Renderização** (`app/rendering.py`): quatro elementos por unidade (instrumento, partes efetivas, localização, transcrição literal com origem), seções por instrumento, relações aprovadas só como metadado factual, avisos históricos; sem consolidação tácita; nenhum texto livre do modelo; sem afirmação categórica `responde:`.
6. **Orçamentos e revogação tardia**: closure/prompt acima do orçamento → abstenção `budget_exceeded` sem truncar; chamada de gerador cancelada/abandonada no prazo restante via thread com join; revogação durante a requisição → revalidação final `pinned.finish(...)` descarta a resposta; mudança de credencial/época → erro `forbidden`/`service_unavailable`; indisponibilidade técnica → erro de serviço (distinto de abstenção).
7. **Grafo LangGraph** com dependências injetadas, sem instância global nem cliente no import (4 nós compilados: `retrieve` → `generate` → `validate` → `render`; fallback e retries executados no nó `generate` com rastreamento por estágio; montagem final da resposta e auditoria em `_answer`/`_controlled`).

Fora de escopo (não antecipar): API/HTTP/cache (P7), homologação (P8), operação AWS (P9), síntese livre, `approved_fact_ids` (extração de fatos aprovados inexistente: lista deve vir vazia), chamada real ao OpenRouter, revisão humana, corpus real, G2/G3 pleno/G4 operacional/G5/SLO.

## Decomposição e propriedade

| Frente / Responsável | Arquivos (novos) | Critério / resultado esperado |
| --- | --- | --- |
| Head — contratos, grounding, renderização, pipeline/grafo, integração | `app/answer_contracts.py`, `app/grounding.py`, `app/rendering.py`, `app/generation.py`, `tests/test_p6_contracts.py`, `tests/test_p6_grounding.py`, `tests/test_p6_rendering.py`, `tests/test_p6_pipeline.py`, `tests/test_p6_adversarial.py` | Contratos fechados (extra=forbid), validação fail-closed, abstenção categórica, quatro elementos, auditoria reconstruível sem texto bruto, revogação tardia e orçamento controlados, backends reais locais. |
| Senior 1 — corpus sintético governado | `tests/p6_corpus.py`, `tests/test_p6_corpus_support.py` | Construtor reutilizável de geração física (ledger/journal/Chroma/BM25) com cenários base+aditivo, alteração parcial, distrato, terceiro com CNPJ alfanumérico, dependência/exceção, relação pendente/conflitada e consulta histórica; sem tocar arquivos aceitos. |
| Senior 2 — termos, prompts, geradores | `app/terms.py`, `app/prompts.py`, `app/generators.py`, `tests/test_p6_terms.py`, `tests/test_p6_prompts.py`, `tests/test_p6_generators.py` | Extração determinística de termos de conteúdo e termos ausentes; prompt versionado determinístico com fronteira instrução/dado; gerador sintético offline, adaptador LangChain (modelo falso local) e fábrica de provedor bloqueada por D01; erros técnicos tipados. |
| Juiz independente | `docs/reviews/2026-10-07-exec-p6.md` | Dez critérios do handoff, média ≥ 9, nenhum alto/crítico aberto, máximo três rodadas; não implementa código julgado. |

---

## Histórico de Julgamento — Candidato 1 (Rodada 1)

Submetido e avaliado em `docs/reviews/2026-10-07-exec-p6.md`:
- **Resultado:** Reprovado com **8,97/10** (abaixo de 9,00; sem nenhum achado crítico ou alto aberto; cinco achados médios P6-J1 a P6-J5 e três baixos P6-J6 a P6-J8).
- **Probes Adversariais Identificados:** C6 (termo âncora satisfazendo portão global), A5 (atribuição por item fraca e `responde:` do modelo), A3 (ValueError virando abstenção), A4/B7 (timeout de 30 s não imposto), D1 (exceções cruas no render), E6' (teste de revogação de unidade vácuo com `decision="rejected"`), G/H (eco de IDs inventados e avisos longos descartados).

---

## Registro de Correções — Candidato 2 (Rodada 2)

Todas as correções exigidas pelo parecer da Rodada 1 foram implementadas cirurgicamente:

1. **P6-J1 (Cobertura/atribuição por item e portão lexical):**
   - Em `app/grounding.py`, implementada função `unsupported_items(...)` e validação por item em `_validate`: se qualquer item solicitado não tiver suporte nos literais das unidades atribuídas a ele (mais closure), o pipeline abstém com `item_unsupported`.
   - Em `app/terms.py`, `_STOPWORDS` expandido com vocabulário estrutural/financeiro (`valor`, `contrato`, `clausula`, `parte`, etc.) e `_identifying_terms` exclui nomes de partes/títulos da contagem de termos de conteúdo.
   - Em `app/rendering.py`, removido o rótulo categórico `responde:`, substituído por `indicada para:`.
   - Testes com contraexemplos A5 e C6 implementados em `tests/test_p6_pipeline.py:410-425`.

2. **P6-J2 (Separação estrita falha técnica vs saída inválida):**
   - Criada classe explícita `GeneratorOutputInvalid(ValueError)` em `app/generators.py`.
   - Em `app/generation.py:248-260`, `_attempt` trata apenas `GeneratorOutputInvalid` como saída inválida (`invalid_output` -> abstenção). Exceções gerais (`ValueError`, bugs de gerador, etc.) viram `technical_failure` (`service_error`), permitindo retry/fallback e culminando em `service_unavailable`, nunca abstenção.
   - Testado em `tests/test_p6_pipeline.py:427-436`.

3. **P6-J3 (Teste real de revogação de unidade no ledger):**
   - Em `tests/test_p6_pipeline.py:318-336`, teste corrigido para usar `decision="excluded"`, validando `executed == [True]`, `attempts[0].outcome == "candidate"`, `detail_code == "closure_blocked:unit_review_unavailable"` e ausência de dados na carga.

4. **P6-J4 (Imposição do orçamento de 30 s e propagação de timeout):**
   - Em `app/generation.py:229-247`, `_call` executa o gerador dentro de thread dedicada com `worker.join(remaining)` levantando `_CallTimeout` em caso de bloqueio.
   - Em `app/generators.py:75-84`, `LangChainCandidateGenerator.generate` propaga `timeout=max(request.timeout_seconds, 0.001)` para `client.invoke`, com fallback para clientes que não aceitam o parâmetro.
   - `_deadline_check` executado antes e após validação/renderização (`tests/test_p6_pipeline.py:454-466`).
   - Teste unitário de propagação de timeout adicionado em `tests/test_p6_generators.py:219-230`.

5. **P6-J5 (Blindagem contra exceções inesperadas na borda):**
   - Em `app/generation.py:384-406`, bloco `except Exception` em `answer()` captura qualquer exceção crua, mapeia para `AnswerServiceError("internal_error")` com auditoria mínima e suprime `__cause__`/`__context__`.
   - Testado com injeção de falha com segredos em `render_answer` e `_audit` (`tests/test_p6_pipeline.py:468-485`).

6. **P6-J6 (Auditoria limpa e preservação de avisos materiais):**
   - Em `app/grounding.py:102,105`, `ValidationFinding` não recebe mais `unit_id` inventado pelo modelo (`subject_id=None`).
   - `safe_code` canonicaliza avisos não conformes para hashes delimitados (`head:sha256-...`), sem descartar avisos materiais. Testado em `tests/test_p6_grounding.py:204-213` e `tests/test_p6_pipeline.py:505-510`.

7. **P6-J7 (Defesas de injeção em português e rótulos tipados):**
   - Em `app/generation.py:39-44`, adicionado `_PT_INJECTION` para detectar injeções em português.
   - `AnswerRequest.item_labels` validado com limite de 200 caracteres, linha única imprimível e filtro anti-injeção. Testado em `tests/test_p6_pipeline.py:493-503`.

8. **P6-J8 (Topologia do grafo, texto padrão e padronização LF):**
   - Declarada formalmente a topologia de 4 nós compilados (`retrieve`, `generate`, `validate`, `render`), com fallback e retries internos ao estágio de geração.
   - Texto padrão corrigido em `app/rendering.py:38-40` unindo o último termo com `" e "`.
   - Todos os 18 arquivos de P6 normalizados estritamente para formato Unix LF.

---

### Evidências Experimentais — Candidato 2

- **Suíte focal P6 (10 arquivos):** **193 passaram, 1 aviso em 318,48 s (0:05:18)**.
- **Suíte de suporte de corpus P6:** **12 passaram em 42,35 s**.
- **Regressão integral offline Head (Candidato 2):** **1.283 passaram, 1 deselecionado (online), 2 avisos legados em 551,12 s (0:09:11)**.
  - Comando: `.venv\Scripts\python.exe -m pytest tests -q -m "not online" --qwen-artifact data/models/qwen3-embedding-0.6b-97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`.
- **Integridade dos 38 arquivos anteriores:** `git status --short` exibe somente entradas `??`; nenhum arquivo rastreado foi alterado.

---

### Identidades Congeladas — Candidato 2 P6 (LF em disco)

| Arquivo | SHA-256 |
| --- | --- |
| `app/answer_contracts.py` | `7dec3cbc58d4ffc82df5dfef6447c568ba6864b6d37f12c4b81737abe0a93602` |
| `app/terms.py` | `e82db5ae6971c7d1681155d7ba6651d851458cfd915ceff0bba0f4a5e7411f37` |
| `app/prompts.py` | `5b80cad327f2d110283af4a49d0cbcc051a44da9dabb8e5c5fc49d68b9900308` |
| `app/generators.py` | `637d49218e62e04c94c9f5f5ec7d09d1f836560864fb4e61016dbc8cc6ddb675` |
| `app/grounding.py` | `2ecbd3f03e627224a3030072e8b1ecd3bfdcd5e8095abe5ad6af79d010ed1adf` |
| `app/rendering.py` | `a99f00e63b8a5d08a82510c68aa0ed4a0f45ac7fcd084ade2b8dab1ef51555a5` |
| `app/generation.py` | `fe56d2c405e7ce45ec1a3589c4be5118d6bf041af28c38d56d5e6e737bd6fdaf` |
| `tests/p6_corpus.py` | `cb1b84850830298c48b81aacb82467c961eea14c6b3bf67e6f0fe968c8a31835` |
| `tests/test_p6_contracts.py` | `14e79164b37017c3d0c6844dc5555acdb5de65e120928b1fb5201e48e76cf3b6` |
| `tests/test_p6_corpus_support.py` | `fae0a7ddeb46d5a4e768d5c6b9e425106274e32053ccdc37c368d9a7b5ca8af4` |
| `tests/test_p6_terms.py` | `32d3625bfec0dfb2a727b813c73e51fdf6014f08d75ffca7e6ce991c200efe09` |
| `tests/test_p6_prompts.py` | `40972ead0708da5aba67e61f42ffd399615be55638ee70a7d5a86ea184213ff8` |
| `tests/test_p6_generators.py` | `b56dd89eea7a547f0bbebff99c72005eda09b66a0f923b163ae46def649ea496` |
| `tests/test_p6_grounding.py` | `c41f0928e43cf6aeb29d5189c0988957dccf89527760bf268c741441da04921a` |
| `tests/test_p6_rendering.py` | `cfd905adc2d5e9203d90890923f52ab4da26177f7467baccb6bc30eaf69e32e0` |
| `tests/test_p6_pipeline.py` | `8b3b90c98dd0d533e150b5a3a821ebd08e645f0bc2c3804112cc08d188d4ef59` |
| `tests/test_p6_adversarial.py` | `ece080439c7c205d47d7849796773145a1ce929847808179f1724356330f6c6c` |
| `tests/test_p6_real_backends.py` | `ffc6dba4ecd55aa9666736c7b52a1a6f2cac8b70cd2b9b7b9bba619a1d7a628d` |

---

### Limitações Declaradas (Candidato 2)

- Fatos aprovados (`approved_fact_ids`) não existem: candidato com fatos é rejeitado. Síntese livre permanece desabilitada.
- Qualquer revogação de fonte/credencial incrementa `policy_epoch`; requisições em curso recebem erro retentável `service_unavailable`, não abstenção (fail-closed do P4 aceito).
- O portão de termos agora combina exclusão de metadados identificadores com validação estrita por item (`unsupported_items`), eliminando a lacuna de termos âncora.
- Orçamento de tempo (≤30 s) é imposto em thread dedicada e propagado para clientes adaptados.
- Fallback (`fallback_approved=False` por padrão) e provedor real permanecem fechados por D01; nenhuma chamada externa foi efetuada.

---

### Autorrevisão do Head — Candidato 2 (dez critérios)

| Critério | Nota | Justificativa |
| --- | :---: | --- |
| 1. Natureza RAG | 9,4 | Seleção de IDs estrita; 4 Elementos; sem consolidação; portão por item com abstenção controlada em caso de falta de suporte. |
| 2. Arquitetura | 9,3 | Separação contratual clara; 4 nós compilados com fallback por estágio rastreado; sem exceções cruas na borda. |
| 3. Governança | 9,5 | Porta D01 fechada; fallback desligado; defesas PT de injeção; revogação por ledger/epoch perfeitamente fail-closed. |
| 4. Aplicabilidade | 9,2 | Saída convertível a `ChatResponse`; erros tipados `AnswerServiceError`; `item_labels` com limites e sanitização. |
| 5. Eficiência | 9,3 | Thread com limite temporal; propagação de timeout ao cliente; deadline verificado antes e pós render. |
| 6. Acurácia técnica | 9,5 | 100% fidelidade literal contra bundle e chunks; autoridade P3B de closure preservada; contraexemplos C6 e A5 superados. |
| 7. Precisão técnica | 9,4 | Ausência de atribuição indevida; sem `responde:` categórico; avisos materiais preservados com hashes seguros. |
| 8. Rastreabilidade | 9,5 | Auditoria sem vazamento de PII/quotes; sem eco de IDs inventados do modelo; trilha completa de tentativas. |
| 9. Observabilidade | 9,3 | Códigos públicos estritos; categorias de erro limpas; captura geral de `internal_error` sem exposição de stacktrace. |
| 10. Confiabilidade | 9,4 | Teste de revogação de unidade no ledger validado com `decision="excluded"`; 1.283 testes verdes; zero falhas. |
| **Média Head** | **9,38** | Candidato 2 consolidado e pronto para homologação pelo Juiz Independente na Rodada 2. |
