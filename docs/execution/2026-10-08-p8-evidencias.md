# P8 — Homologação ponta a ponta (preparação técnica sintética): evidências

> **Registro vivo do Head.** Estado e aceite do P8 ficam aqui; o contrato técnico está no [P8_E2E](../P8_E2E.md). Nada neste documento é aceite: apenas registro de execução, limites e proveniência. Resultados parciais não viram aprovação.

## Governança, escopo e preservação

Lidos AGENTS.md/RTK.md aplicáveis, [CONTEXT](../../CONTEXT.md), [HANDOFF](../HANDOFF.md), plano/specs v0.6 congelados, registros/pareceres P3B–P7 e o registro central. Sessão iniciada por instrução humana explícita (**"prossiga"**), retomada duas vezes. **Head** responde por integração/evidências/decisões; **Seniores** por implementação em donos separados; **juiz independente** por nota e veredito — o Head e os implementadores **não** atribuem nota nem convertem autorrevisão em aceite.

Base Git `057ec8764420edc0762ea4cab2117a23580c487c`; worktree acumulado P7 preservado, sem reset/clean/commit. Conferência raw: **12 P3B C3 + 14 P4 C3 + 5 P5 C1 + 18 P6 C2 = 49 identidades íntegras**; **11 código/testes P7 C2 íntegros**. Plano SHA-256 `91a6f484016fea4d8db4834b4da95732d2445cc16b086169626799e6e7a34d21`, specs `a98946c4d358d8e8b548656d5eeb479a43feb5b19cc24e841dd9ae4212d7e3e4`, lock `101a5ff70a84efd5c88e02a9ae8cdac2b15b63dd634f6d6aae94ed7b6b0030e4`, temporal `2305cc97f9ae012b5e9305f07a2f78d99f719db4bb5f02f9fb835693192032d9`, Qwen adapter `49fa8d9e9d7176e401efb5ad5874e28abb6b245d3746e9ca8ee16a2fa1671445` e manifesto `025a8edceba57399e67e029505fa1be3140b4c25ec0b59488eb60beed1f57688` conferidos.

**Fronteira de aceite:** P7 técnico sintético aprovado 9,37/10 na rodada 2 final com **12 identidades do candidato 2**, incluindo o runtime da época (`app/answer_runtime.py` SHA histórico `bb4e1852f0c08c72009d7e9c7ef98e5dae77265be173425c827e5157e085b690`). O **delta P8 no runtime é nova identidade** e **não herda** esse aceite; somente o juiz P8 pode aprová-lo. Nenhuma campanha encerrada reaberta, nenhuma escolha Qwen reaberta, nenhum fallback remoto/D01.

## Ambiente e diagnóstico nativo (preservado)

Chroma Rust/SQLite falhava com `unable to open database file` (SQLite14) nos dois modos; a criação real do TEMP nativo em AppData falhava com WinError5. Diagnóstico ACL oficial em [acl-report](../../data/evaluation/p8-acl-recovery/acl-report-644664c620c349d187e4efc0fabf1121.jsonl): **NOT_THIS_CLASS, zero mudanças de ACL** — preservado; nenhuma remoção manual de deny/reparo de ACL. Mitigação **somente processo**: prefixo TEMP local `data/evaluation/p8-runtime-temp`, obrigatório em todo processo (`TEMP/TMP/TMPDIR/SQLITE_TMPDIR`). Sem alterar env global, código ou política.

Artefato Qwen **real** exigido em todo teste de integração: `data/models/qwen3-embedding-0.6b-97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, revisão `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, 1024 dimensões, CPU FP32/seis threads, sem truncamento. Servidor Chroma real fixture `tests.test_p4_vector_store.LocalServer` (loopback privado, filho próprio, `stop()` em `finally`). **Nenhum** modelo gerativo real: o gerador é controlado por IDs canônicos sintéticos (`Scripted`), documentado como limite.

## Decomposição e donos de arquivos

| Entrega | Dono | Arquivos |
|---|---|---|
| Avaliador E2E estrito | Senior | `app/evaluation/e2e.py`, `tests/test_p8_evaluation.py` |
| Matriz E2E + fixture física | Head | `tests/p8_corpus.py`, `tests/test_p8_e2e.py` |
| Carga/limites | Senior carga | `tests/test_p8_load.py` |
| Segurança/falhas/governança | Senior evaluator | `tests/test_p8_safety.py` |
| Delta admission P8 (autorizado) | Senior carga | `app/answer_runtime.py`, `tests/test_p8_admission.py` |
| Integração, evidências, decisão | Head | `docs/P8_E2E.md`, registro/context/handoff |

## Matriz E2E real (TDD)

Seis DOCX físicos determinísticos (fees Alpha/Firm; 1º e 2º aditivos; distrato; contrato de terceiros Omega; locação Gamma/Delta com CNPJ alfanumérico). Oráculos congelados **antes** do parsing (`InstrumentOracle`), regra dos 4 elementos (contrato/título, partes com identificador, localização, transcrição literal), proveniência/versão/evidence_id. Casos: linked fees (7 unidades), histórico (4 base), condições, exceção judicial, terceiros, identificador alfanumérico, union/intersection de partes, clamp por instrumento, ausentes, ambiguidade temporal e de instrumento.

Erros de oracle preservados (não RED produto): oráculo familiar inicial previa 4 citações e o distrato afeta 4 base + 2 aditivos + distrato = **7** (`pwsh-345` 2 passaram/2 avisos/87,72 s); rodapé factual faltante no golden linked (`pwsh-361` 2 falhas/58,68 s → `pwsh-362` 4 passaram/82,69 s); helper puro de golden controlado ausente (1 falha/2,95 s → 1 passou/2,59 s).

Primeira matriz completa + porta pública de auditoria `pwsh-1`: **25 passaram, 4 falharam, 2 avisos, 278,80 s**. O oracle de desenvolvimento supunha OR **entre** dimensões instrumentos/partes; o recuperador aceito aplica union/intersection **entre** partyIDs e depois restringe por instrument_ids. Revisão estática independente `4a924515-23cf-4c61-adc0-8456fb8d15d1` confirmou: specs §§185–188/ACC-03 e plano §§180–183 **não** fixam OR entre campos; álgebra primária **`E = U autorizado ∩ P(ANY/ALL partyIDs) ∩ I`**, I=U quando `instrument_ids` ausente; clamp de I limita raízes, não impede closure material aprovada fora de I em `linked_instruments`. Oracle corrigido **explicitamente antes do freeze**, com sexta fixture Gamma/Delta e cohorts disjuntos. Matriz ampliada **16 casos × 3 transportes × 2 backends**; `pwsh-3` **37 passaram, 2 avisos, 358,16 s**: por backend **48/48 observações**, answered 27/27, controladas 21/21, **129 citações esperadas/emitidas/corretas por backend (258 no total)**. São 16 casos lógicos replicados, **não 96 perguntas humanas**.

Focais adicionais: `pwsh-4`/`pwsh-5` eram falhas do **harness novo** (`answer_config`→`config`; `Scripted([dict])`→assinatura variádica), corrigidas só no teste; `pwsh-6` **2 passaram/81,25 s**. Limites de prompt/orçamento `pwsh-9`: **4 limites passaram**, 4 cenários pending/conflicted falharam na expectativa HTTP200 (ver seção do delta). Multi-item q1/q2 com rótulos e dois primários: **2 passaram, 45 deselecionados, 2 avisos, 65,23 s**.

## Achado funcional admission — delta P8 autorizado

Segunda revisão estática independente confirmou **requisito inequívoco** (ao contrário de OR entre dimensões): specs §4.3:124, §5.1:144, §8:277 e ACC-14:328 exigem esclarecer/abster em pendência documental conhecida. HTTP503 seguro **não** equivale ao resultado funcional exigido; **não** alterar gabarito para verde. Origem: `pin` valida famílias; runtime `except` amplo devolvia 503 **antes** do classificador P6 (que mapearia `family_`/`relation_` para abstention). `readiness` verifica recursos/revisões de unidades e **não** contradiz isso. O limite já constava do parecer P6 (B8) como limite, não como aprovação antecipada. Nenhuma exposição/alucinação observada, mas **bloqueio funcional do recorte P8 antes do freeze**.

Usuário autorizou explicitamente **delta restrito**: TDD, nova identidade, regressão integral, preservando schemas/Qwen e aceites históricos. Dono: Senior carga (runtime + `tests/test_p8_admission.py`), sem tocar P4/P6/P7. Abordagem mínima aprovada pelo Head: reconhecer **apenas** erro documental conhecido comprovado sob geração/registry/ledger atuais; controle interno selado, sem prova/LLM/cache; emissão revalida autoridade sob leases ledger→policy; auth/backend/desconhecido seguem técnicos; sem contornar pin nem `original_text` como fallback.

Senior RED pré-fix: `pwsh-3` (contexto Senior) **2 falhas funcionais pending/conflicted, 2 avisos, 12,45 s** (HTTP503 vs 200), com Chroma embedded + encoder sintético P6 declarado — **não prova Qwen**; complementa os 4 REDs reais do Head. GREEN funcional `pwsh-4`: **2 passaram, 2 avisos, 30,53 s**. Autorrevisão do Head detectou **race** no WIP: promoção entre `_active` fresh (fora dos leases) e aquisição dos leases permitiria emitir `generation_id` obsoleto com policy/ledger idênticos. Confirmado por RED real de intercalação `pwsh-7`: **2 falhas, 4 deselecionados, 2 avisos, 41,88 s** (`DID NOT RAISE`). Fix restrito: receipt canônico/hash de `active.json` + rechecagem barata sob leases antes da emissão, sem `_active/_policy` sob guarda non-reentrante. `pwsh-8` (Senior) **2 passaram, 28,39 s** após o RED. Integração real do Head `pwsh-5`: **4 cenários pending/conflicted passaram, 43 deselecionados, 2 avisos, 199,80 s**, Qwen/Chroma reais nos dois backends, histórico testado **antes** do linked (4 elementos/avisos), linked 200 `abstained/unresolved_relation` sem LLM/citação.

### Delta admission — fechamento funcional

Após a falha do agente dono sem mensagem final, o Head releu/validou os bytes e executou o focal completo: `pwsh-21` **27 casos passaram, 2 avisos, 211,60 s** (`tests/test_p8_admission.py`), Chroma servidor local privado real + encoder sintético P6 declarado. Cobertura: admission pending/conflicted → HTTP200 `abstained/unresolved_relation` com literal controlado, zero citação/gerador/cache, request_id/timestamp novos; audit público completo e streaming `event: answer`; **race de promoção** → `service_unavailable`, zero emit, receipt obsoleto rejeitado; 10 mutações de capacidade selada + runtime estranho → rejeição; erros arbitrários/spoofados continuam **503**; deadline selado → `request_timeout`; 6 mutações de autoridade/armazenamento (credencial → `forbidden`; fonte/registry/artefato/backend/root → `service_unavailable`) negam emissão; exceção do callback em `policy`/`ledger` mantém autoridades retidas até o unwind e libera sem writer órfão.

Limites verificados: captura restrita a `_Blocked` **exato** com razão em `{family_unresolved, relation_unresolved}` **e** `evidence_scope == "linked_instruments"`; reconhecimento exige geração/registry/revisões atuais e replay negativo sob `held_policy`; `original_text` não é fallback; métricas contam apenas após emissão bem-sucedida.

**Ressalva de exposição declarada ao juiz (trade-off inerente ao requisito):** antes do delta, uma família pendente/conflitada retornava 503 e **não** distinguiam se era falha técnica ou estado documental; agora o cliente autorizado com `query` recebe o motivo público `unresolved_relation`, o que **revela metadado de estado documental do corpus** (há relação não resolvida no escopo pedido). Isso é coerente com o vocabulário público P6 já aceito (que já tipa `unresolved_relation` em `classify_authority_error`) e é **exigido** pelo ACC-14 (esclarecimento/abstenção em vez de indisponibilidade). Não há transcrição, título, parte, valor ou citação no corpo — apenas o template fixo. O trade-off é inescapável do requisito e **não** foi ocultado; cabe ao juiz julgar se a fronteira de exposição está correta.

### Carga/limites — Senior

`pwsh-342` **2 carga passaram/165,51 s**; `pwsh-352` **2 deadline passaram/87,10 s**; `pwsh-356` **4 body passaram/84,46 s**; `pwsh-358` **2 rate passaram/70,34 s**. Comportamentos: 2 ativos+2 enfileirados, overflow 429; 4 misses/4 hits/recovery, geradores físicos ≤2 (cap 5 > workers 2 para medir scheduling HTTP, sem exigir FIFO); deadline com stall no port síncrono de contagem mantém worker até release (não confundir gerador abandonado com worker HTTP); body content-length/chunked → 400 antes de cache/gerador; rate principal antes de warm cache, operador separado. Latências com gates/Event são **induzidas**, **não ACC-10/SLO natural**. Suíte final própria `pwsh-359`: **10 passaram, 2 avisos, 201,86 s**, hash `46d27155f99dc4a3efb1f6d578e4cf0505ae3db8374eb084759f071bf953f6a1`. Sem artefato: 10 skips explícitos. Controlado: embedded p50 5.458,40 ms/p95 12.042,78 ms; server p50 5.631,97 ms/p95 12.270,64 ms; denominador quatro requisições admitidas com stalls, **não população/carga natural/SLO**.

### Segurança/falhas/governança — Senior

Entrega [safety](../../tests/test_p8_safety.py), 19.136 bytes/342 linhas, SHA `768c2521f4746ff0d141ae819ecacf3285769e4b3dc66c72608fbadfedc385b4`. Focais baseline **9 exercícios verdes**: auth/injeção 2 (69,15 s); candidato com ID forjado/prosa extra sem cache 2 (125,68 s); indisponibilidade do gerador 503 + recovery 2 (68,05 s); **outage RPC real server-only** stop/start com cache warm bloqueado + recovery 1 (63,74 s); revogação externa com journal físico fora do root + promoção/rollback 2 (121,44 s). Nenhuma outage embedded fabricada. Falha inicial de revocation (2/91,33 s) era **whitelist do harness** que proibia "Andrade Advogados", presente no template fixo independente; correção estreita **somente no helper**, exigindo o literal ENTIRE + `citations == []` antes de auditar envelope; erros/métricas continuam estritos. Sem artefato: 2 erros de setup esperados, sem prova falsa. O integral próprio (`pwsh-19`) ficou **indeterminado** por perda do registro do job — **não** alegado como verde.

### Lacuna de evidência safety encerrada

A regressão oficial (`pwsh-24`) executou `tests/test_p8_safety.py` por completo e **nenhum dos 9 exercícios apareceu nas falhas** — os 4 testes que falharam mais o erro vêm de `test_p4_generations.py` (2), `test_p7_runtime.py` (1), `test_p8_load.py` (1) e `test_p5_retrieval.py` (1, setup). Logo **safety 9/9 verdes em execução durável e oficial**, substituindo a lacuna por resultado real. Ressalva: foi na execução combinada, não num integral dedicado isolado; hash da suíte idêntico ao baseline.

## Ocorrências intermitentes — caracterização e decisão

Focal completo P8 (`pwsh-22`, evaluation+e2e+load+safety+admission no mesmo processo, Qwen/Chroma reais): **193 passaram, 1 falha, 1 erro de setup, 2 avisos, 1.233,20 s**. Regressão offline integral oficial (`pwsh-24`): **1.746 passaram, 4 FALHARAM, 1 ERRO, 1 deselecionado, 3 avisos, 2.522,53 s (42:02)**. **A regressão não foi declarada verde.**

1. **`WinError 5` intermitente no `os.rename(.staging/<gen> → <gen>)`** em `GenerationManager.build` ([app/retrieval/generations.py:274](../../app/retrieval/generations.py#L274)), envolvido como `review_authority_unavailable`. É o **mesmo** erro nas 4 falhas + 1 erro, atingindo suítes **aceitas P4/P5/P7** e P8: `test_p4_generations.py::test_faults_never_expose_partial_generation[after_pointer]`, `::test_actual_pointer_swap_holds_current_authorities_until_commit[policy]`, `test_p7_runtime.py::test_admission_refreshes_epoch_and_rejects_revoked_credentials`, `test_p8_load.py::test_deadline…[server]`, `test_p5_retrieval.py::test_governed_retriever_identifier_selection_cnpj_cpf_alphanumeric` (setup). É **código aceito P4**, hash `19371d77cf…` **byte a byte idêntico** ao aceite — **não alterado pelo P8**. Reprodução isolada: **10/10 verdes (49,95 s)**. Probe dedicado de rename com `chromadb.PersistentClient` (12 tentativas): **0 falhas** — refuta a hipótese simples de handle do Chroma no `write`. Correlação observada: volume de builds na mesma execução. **Causa raiz não determinada** (hipóteses: handle aberto no staging durante varredura/índice do Windows; GC de handles). **Não** mascarado com retry/sleep/skip.
2. **`test_deadline_keeps_stalled_query_worker_until_release_then_recovers`** → 504 na recuperação (embedded no focal, server na regressão). O teste fixa `deadline_seconds=8` **por desenho** (para medir 504) e a recuperação real Qwen+retrieval+gerador excedeu 8 s quando a máquina vinha das suítes Qwen pesadas. Isolado: **2/2 verdes (83,72 s)**. Sensibilidade do harness de carga à carga do host; o caminho feliz do runtime não foi alterado pelo delta.

**Decisão registrada:** **não** alterar código aceito P4 e **não** mascarar. As ocorrências são encaminhadas ao **juiz independente** para julgamento de materialidade, com dados de reprodução. Nenhum resultado parcial virou aceite.

## Freeze bruto do candidato P8 (pré-juiz)

| Arquivo | SHA-256 | bytes | linhas |
|---|---|---|---|
| app/answer_runtime.py | `b8ccc2378656b7f78bf5d89a8a597c6b6fa31894ca33657441623116ec721edb` | 30.871 | 555 |
| app/evaluation/e2e.py | `9ecb7261e14bcb75309bce62b3f6c2093c9dfaf2ffb6be0912004c298a1a96c5` | 15.538 | 348 |
| tests/p8_corpus.py | `da78bc3fb43a2683f8aebb9b1e9dc30c2a0abad658a42571cbeb6198a503de75` | 20.918 | 313 |
| tests/test_p8_evaluation.py | `6168fb83a4ef8760e00c9e3f5ff990138853575f175c2cdce6d14b2fec6507ea` | 23.096 | 509 |
| tests/test_p8_e2e.py | `02f1962975a38724cb78fd4beede83689a2ce49d70181accca178080e1261f88` | 16.550 | 249 |
| tests/test_p8_load.py | `46d27155f99dc4a3efb1f6d578e4cf0505ae3db8374eb084759f071bf953f6a1` | 27.855 | 515 |
| tests/test_p8_safety.py | `768c2521f4746ff0d141ae819ecacf3285769e4b3dc66c72608fbadfedc385b4` | 19.136 | 342 |
| tests/test_p8_admission.py | `9b270539788740be2e73a79a2a33ee3ea053f16f9dcc5fe5fa8057edb1aa7b1f` | 14.431 | 274 |
| docs/P8_E2E.md | `ae7a537e07159d1d1bda99fdbeddfcb8699f9757a76b40cde599e68e1f01413a` | 9.583 | 70 |

**Identidade histórica preservada:** o runtime P7 (`bb4e1852f0c08c72009d7e9c7ef98e5dae77265be173425c827e5157e085b690`) é o aceite P7; o `app/answer_runtime.py` acima é **nova identidade P8** e **não herda** esse aceite.

### Conferência independente de identidades (Head, pós-freeze)

Onze identidades congeladas reconferidas **após** o freeze e antes do juiz, **todas OK**: plano `91a6f484…`, specs `a98946c4…`, `uv.lock` `101a5ff7…`, `app/ingestion/temporal.py` `2305cc97…`, `app/embeddings/qwen.py` `49fa8d9e…`, manifesto `025a8edc…`, `app/retrieval/generations.py` `19371d77…`, pareceres P3B `fb5e861d…`, P4 `d2d5941a…`, P5 `ef260d84…`, P6 `2edda68b…`, P7 `5eb3cc34…`. `generations.py` permanece **byte a byte idêntico** ao aceite P4, confirmando que o P8 não tocou o código do `WinError 5`. As 49 identidades P3B–P6 e os 11 arquivos P7 restantes permanecem intactos.

### Autorrevisão do oráculo (independência)

[tests/p8_corpus.py](../../tests/p8_corpus.py) **não importa** `app.rendering`, `app.grounding` nem `app.evaluation` — o gabarito textual é reconstruído só das declarações `InstrumentOracle`/`LINKS` (título, partes, parágrafos), nunca de saída do renderizador, resolvedor ou candidato. Limitação **declarada no docstring**: `unit_id`/`block_ids` vêm do bundle gerado (impossíveis de declarar antes do build) e servem só para localizar a prova física; nenhum texto de resposta, citação ou transcrição do parser define o oráculo.

### Roteamento de modelo solicitado

O usuário pediu rodar Seniores e juiz com **Gemini 3.8 Flash (Tiered Effort – High)** via modelos Google do Antigravity. `subagent`/`subagent_fork` deste harness **não expõem** parâmetro de modelo; a única via é o override por agente da ferramenta `workflow`. Foram feitas **duas tentativas** (`provider: antigravity` + `model: gemini-3.8-flash`; e `model: gemini-3.8-flash` isolado) e **ambas retornaram subagente não concluído**. Conforme instrução explícita do usuário — *"se obtiver erro em ambas, siga como vinha realizando"* — o juiz foi disparado pelo roteamento padrão do harness. Registrado por acurácia técnica: nenhuma nota é atribuída a modelo diferente do realmente executado.

### Conferência dos 12 arquivos P7 C2

Reconferidos contra a tabela **C2** (última ocorrência prevalece sobre a C1) do [registro P7](2026-10-07-p7-evidencias.md#L155):

- **10/12 byte a byte idênticos ao C2:** `app/main.py` `5d88aa67…`, `app/cache.py` `8ca91430…`, `app/http_contracts.py` `dac80ae7…`, `app/security.py` `ad49c548…`, `app/telemetry.py` `fbfbfab5…`, `tests/test_api.py` `abd10448…`, `tests/test_p7_http_contracts.py` `b82d86b0…`, `tests/test_p7_cache.py` `301b4dff…`, `tests/test_p7_runtime.py` `3b4c2eb6…`, `tests/test_p7_integration.py` `12c08b35…`.
- **`docs/P7_API.md`:** atual `38d13951b388f0576fbac697aae630199558685e33e030df84d9a5c6a8abd979` vs histórico C2 `633e682f…` — **divergência documental já registrada em sessão anterior** (não é EOL-only, não há snapshot; nunca afirmar que só o cabeçalho mudou). Aceite P7 segue atrelado aos bytes históricos; **não reaberto**.
- **`app/answer_runtime.py`:** `b8ccc237…` — **delta P8 autorizado**, nova identidade, fora do aceite P7.

Estado exatamente como esperado: nada além do delta autorizado e da divergência documental conhecida foi alterado.

## Matriz prevista e fronteiras

Gates sintéticos: 100% fidelidade dos 4 elementos/prova esperada e equivalência miss/hit (excluindo envelope de correlação/tempo/cache), 100% ações controladas esperadas, nenhum conteúdo em erro/negativo sem prova, recuperação após falhas controladas. As cotas congeladas do gabarito humano reservado (>=100 reservado, >=50 desenvolvimento, >=60 respondíveis, 20 negativos/20 condições/20 identificadores/10 ambiguidades, >=5 por rota real) **não** serão satisfeitas por repetição sintética: G5/G8 humanos permanecem pendentes. Reusar controles aceitos de auth/revogação/paridade/fallback/telemetria; não reimplementar P0–P7.

Comando final:

```powershell
.venv\Scripts\python.exe -m pytest tests -q -m "not online" --qwen-artifact data/models/qwen3-embedding-0.6b-97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
```

## Parecer do juiz independente — rodada 1 de 3

**VEREDITO: APROVADO_COM_RESSALVAS — média 9,24/10 (≥ 9,0), nenhum achado alto/crítico aberto.** Campanha encerrada na **rodada 1**; máximas de três rodadas não consumidas.

Verificação **própria** do juiz (não transferida): hashes/bytes/linhas dos 9 candidatos **todos exatos**; **49/49 identidades P3B–P6 rehasheadas e íntegras**; P7 **10/11 código/testes C2 íntegros**, `answer_runtime.py` confirmado como nova identidade P8 **sem herança** do aceite P7 (`bb4e1852…` só histórico), `P7_API.md` = `38d13951…` (11.145 B) como identidade pós-divergência registrada; congelados planos/specs/lock/temporal/qwen/manifesto conferem.

Execuções próprias: focal obrigatório (job `pwsh-32`) **153 passaram, 23 erros, 595,23 s**; adicional (`pwsh-46`): `test_p8_e2e.py` retry **47/47 verdes (611 s)** reproduzindo as 96 observações nos dois backends; `test_p8_safety.py` **9/9 verdes (294 s)**; `test_p8_load.py` **10/10 verdes (228 s)**. Não executou a regressão integral de 42 min, online, Linux, AWS/P9, G5/G8 humano nem corpus real. **Nenhum arquivo alterado.**

| Critério | Nota |
|---|---|
| Natureza RAG | 9,3 |
| Arquitetura | 9,4 |
| Governança | 9,5 |
| Aplicabilidade | 9,3 |
| Eficiência | 9,1 |
| Precisão técnica | 9,1 |
| Acurácia técnica | 9,2 |
| Rastreabilidade | 9,4 |
| Observabilidade | 9,2 |
| Confiabilidade | 8,9 |
| **Média** | **9,24** |

**Delta admission:** aprovado como proporcional/correto/seguro — captura exata `type(error) is _Blocked` com razões em `frozenset` fechado (`answer_runtime.py:81,478`); replay negativo que nunca concede pin (`:330-379`); receipt canônico do ponteiro ativo rechecado **sob** leases fecha a race de promoção (`:426-435`, teste de intercalação verde); HMAC por instância com domínios separados; revogação 403 preservada no caminho de exceção (`:413`); spoof/erros arbitrários seguem 503; `original_text` não é fallback; zero prova/LLM/cache; métricas só após emissão. Specs §4.3:124/§5.1:144/§8:277/ACC-14:328 conferem como requisito inequívoco.

### Achados

| # | Severidade | Estado | Achado |
|---|---|---|---|
| 1 | **MÉDIO** | ABERTO | `WinError 5` no rename de [generations.py:274](../../app/retrieval/generations.py#L274) (P4 aceito intocado). Não bloqueador: fora do escopo do delta, fail-safe, Windows-only, não mascarado, isolado 10/10 e retry 47/47 verdes. **Reavaliar em Linux/D04/P9.** |
| 2 | BAIXO-MÉDIO | ABERTO | Janela fixa de 8 s no harness de deadline ([test_p8_load.py:364-419](../../tests/test_p8_load.py#L364-L419)) limita também a recuperação. Fragilidade de **harness**, não do produto. |
| 3 | — | **FECHADO** | Safety INDETERMINADO → **9/9** na execução do próprio juiz. |
| 4 | BAIXO | **FECHADO** | `relation_registry_version="v1"` hardcoded ([answer_runtime.py:401](../../app/answer_runtime.py#L401)). |
| 5 | BAIXO | **FECHADO** | Temporários retidos na raiz (`p8-db-path-*`, `p8-wal-*`). |

### Correção do juiz à caracterização do Head (registrada)

O juiz **contradiz parcialmente** a hipótese do Head de "volume de builds": no seu focal a falha ocorreu no **primeiro build**, com ~28 builds seguintes OK. Acrescentou um **agravante que o Head não havia medido**: a falha acontece no **fixture module-scoped `physical[embedded]`** ([test_p8_e2e.py:36-50](../../tests/test_p8_e2e.py#L36-L50)) e uma única falha de setup **contaminou 23 testes** (toda a matriz embedded daquele run). Ou seja: amplificação por escopo de fixture, não volume de builds. A causa raiz do `WinError 5` **continua não determinada**; a hipótese do Head sobre correlação com volume fica **rebaixada a não corroborada**. Nenhum byte foi alterado — o teste congelado mantém o desenho module-scoped, e o achado viaja com os gates pendentes.

### Ressalvas que viajam com a aprovação

A **regressão oficial integral não está verde** (pela ocorrência #1). Trata-se estritamente de **preparação técnica sintética**: G5/G8 humanos, corpus real, gabarito humano reservado, relevância jurídica humana, D04, Linux, SLO/ACC-10 natural e AWS/P9 **permanecem pendentes**. As 96 observações são 16 casos lógicos replicados, **não** 96 perguntas humanas.

**P8: técnico sintético APROVADO_COM_RESSALVAS na rodada 1 (9,24/10).** Nenhum gate documental/operacional foi avançado; nenhuma campanha encerrada foi reaberta; nenhum arquivo aceito foi alterado.

## Fechamento e limpeza autorizada

Estado/contexto/handoff/registro central atualizados com a decisão efetiva, preservando integralmente as cronologias P0–P7 e as reprovações históricas. Verificação final: **freeze 9/9 válido** e worktree acumulado íntegro (nenhum reset/clean/commit; arquivos empíricos do usuário `docs/dev_server.py` e `docs/p7_teste_empirico.html` mantidos).

Limpeza executada **somente após todos os testes terminarem**, com allowlist absoluta e guarda explícita contra o temporário ativo compartilhado. Consentimento explícito do usuário prévio. Removidos os seis diretórios descartáveis criados nesta sessão:

- `p8-db-path-z17yoce8/` (3 arquivos) · `p8-db-repro-moih7o4o/` (2) · `p8-wal-adqc9o38/` (1)
- `pytest-of-orugi/` na raiz (45) · `data/evaluation/p8-runtime-probe-43e988e871a44923b7b10b452bfc8e1d/` (1) · `data/evaluation/p8-native-runtime-5a8c88784f1642918db7aad9d2f9fa54/` (1)

**Preservados:** `data/evaluation/p8-runtime-temp` (temporário ativo compartilhado, **não** estava no consentimento) e `data/evaluation/p8-acl-recovery/` com o relatório oficial de diagnóstico ACL (`NOT_THIS_CLASS`, zero mudanças de ACL). Nenhum `git clean`, nenhum glob amplo, nenhum arquivo de projeto tocado.
