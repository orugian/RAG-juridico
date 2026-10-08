# P7 — HTTP, cache governado e concorrência (técnico sintético)

## Estado e precondições antes de editar código

Head Engenheiro de IA coordena; executores Seniores Engenheiros de IA; juiz independente não implementa. P7 em preparação, nenhum aceite antecipado. Base Git `057ec8764420edc0762ea4cab2117a23580c487c`, worktree inicialmente limpo, preservado sem reset/clean/commit. Histórico documental de worktree não commitado não descreve este novo snapshot.

Lidos AGENTS ancestral `C:/Users/orugi/AGENTS.md` e RTK efetivo `C:/Users/orugi/.claude/RTK.md` (a referência no diretório ancestral não existe; localização efetiva confirmada pelos registros, RTK 0.42.0), CONTEXT/HANDOFF, plano/specs v0.6 integrais, registro central, registros/pareceres lexical/temporal/P3A/P3B/P4/P5/P6. Não há instruções adicionais no checkout.

Conferência executada antes de editar: **49 identidades em bytes brutos, zero divergências**: 12 P3B C3, 14 P4 C3, 5 P5 C1, 18 P6 C2. Plano/specs/uv.lock, temporal.py, qwen.py e pareceres P3B/P4/P5 conferem. Parecer P6 atual SHA-256 `2edda68b5ca17c286878b5ba622bdf7ffa844cf72c82060123bb547a4598a60c`. Aceites: lexical/temporal 9,18 R4; P3B 9,20 R3 J1/J2; P4 9,22 R3 J1/J2/J3; P5 9,33 R1; P6 9,35 R2 J1–J8 fechados. Nenhum alto/crítico aberto identificado nos recortes. Reprovações históricas preservadas; nenhuma campanha reaberta. Baseline histórico P6: 1.283 passaram, 1 online deselecionado. **Reprodução atual Head concluída antes do delta security: 1.283 passaram, 1 online deselecionado, 2 avisos legados em 631,83 s (0:10:31)** com comando oficial e Qwen local real; zero falhas. Não é medição de SLO.

## Compatibilidade e decisão de transporte

Plano/specs congelados exigem `/chat` não streaming, `/health`, `/ready`, `/metrics`; solicitação atual/HANDOFF acrescentam `/v1/answer` e `/v1/answer/stream`. Implementar ambos sem editar specs: `/chat` conserva ChatRequest/ChatResponse, `/v1/answer` recebe AnswerRequest e retorna ChatResponse. Thread é apenas correlação. Request_id gerado no servidor substitui o valor fornecido. Streaming SSE **bufferizado**, um evento `answer` contendo a resposta integral validada; nenhuma seleção/quote/token parcial antes de grounding. Erros anteriores ao envio mantêm código HTTP normal. O commit protege a aceitação ASGI dos bytes preparados, não promete impedir que bytes já enviados cheguem depois de ACK de revogação por atraso de rede.

## Decomposição/propriedade fixada antes de código

| Frente | Dono / arquivos exclusivos | Critério / resultado esperado |
| --- | --- | --- |
| Cache | Senior cache: app/cache.py, novos tests/test_p7_cache.py; testes legados test_cache.py preservados | Novo GovernedResponseCache independente do helper legado, identidade completa, TTL/LRU/limites de bytes/entradas, cópias defensivas, concorrência, invalidação por autoridade (journal/epoch/ledger), sem erros/candidato inválido. |
| Transporte HTTP | Senior API: app/main.py, tests/test_api.py, novo app/http_contracts.py e tests/test_p7_http_contracts.py | Factory sem recursos no import, auth X-API-Key antes de cache, AccessContext do servidor, aliases HTTP, erros fixos, corpo/CORS/rate/fila/concorrência/deadline, health mínimo/readiness/metrics internos, envio sob commit governado e event loop livre. |
| Serviço integrado | Head: novo app/answer_runtime.py, tests/test_p7_runtime.py, tests/test_p7_integration.py, main.py se necessário; docs/context/handoff | Pin corrente por requisição, composição P6 sem reimplementar, chave cache completa, revalidação no hit/miss e após serialização até aceitação ASGI; nenhuma autoridade/revisão nova no corpus real. |
| Juiz | Agente independente: docs/reviews/2026-10-07-exec-p7.md | Dez critérios, média >=9, zero altos/críticos abertos, máximo três rodadas; código congelado, provas próprias. |

Frentes de implementação independentes após contratos abaixo; não editar arquivos aceitos P0–P6. Dependências já existem via uv/uv.lock; não instalar/baixar. Frentes executam ciclos verticais Red→Green→Refactor, erros de fixture/ambiente não contam como Red.

## Contratos de coordenação

Cache: `CacheIdentity(question_sha256, access_digest, plan_digest, generation_id, versions_digest, journal_id, policy_epoch, ledger_digest)` dataclass frozen; `GovernedResponseCache(ttl_seconds=300,max_entries=128,max_bytes=8388608,clock=time.monotonic)`, `get(identity)->AnswerOutcome|None`, `set(identity,outcome)->bool`, `stats` agregados. Cache valida schema/audit/payload, vínculo identidade, não aceita `invalid_candidate`; dados não contêm pergunta/chave API. `set` não concede autorização; runtime e commit revalidam autoridade corrente. Nova época/ledger invalida uso automaticamente; identidade de escopo e geração impede cruzamento. Helper ResponseCache legado não é utilizado na API.

Runtime (síncrono, API faz offload bounded): `GovernedAnswerRuntime(manager,generator,config=None,cache=None,token_counter=None)`, `execute(request:AnswerRequest,access:AccessContext,*,thread_id,request_id,deadline:float)->PreparedAnswer`. PreparedAnswer tem `response:ChatResponse`, `body:bytes`, pin/identidade/audit internos (não expostos). `commit(prepared,emit:Callable[[bytes],None],*,stream=False)` prepara SSE antes do guard; retém ledger→policy, revalida identidade/epoch/digests/acesso/escopo/fontes e prova preparada, chama emit bytes sob ambas autoridades. Emit é síncrono: transporte agenda coroutine ASGI no loop e espera no worker, nunca adquire locks bloqueantes no event loop. `authenticate_epoch(access)->AccessContext` recaptura policy e digest no servidor, valida credencial revogada; `readiness()->bool`, `policy_status(access)->dict` operador, métricas agregadas. API define credenciais com Settings injetado, sem `.env` implícito/segredo no import. Contexto não é recebido no corpo.

## Matriz de aceite / gabarito esperado

1. Sucesso extrativo HTTP preserva instrumento/partes efetivas/localização/literal, fechamento base/aditivo/distrato; erro não equivale a abstenção.
2. 401 sem chave/inválida, 403 permissão/revogação, 422 payload sem eco, 400 invalid_request, 429 Retry-After, 503 autoridade/backend, 504 prazo total, 500 erro inesperado. Nenhuma pergunta/PII/segredo/stacktrace nos erros/logs.
3. Cache inclui pergunta limpa SHA-256 (sem case-folding destrutivo), escopo/principal/permissões, plano/filtros/itens/data, geração/manifest/config/gerador/prompt/validator/renderer, journal/epoch/ledger. Hit não reaproveita correlação de requisição anterior.
4. Cache hit e miss autenticam/pinam/revalidam; revogação source/credential/unit/relation/family, troca de geração e rollback não servem resposta obsoleta. Falha de autoridade fecha também hit.
5. Bytes JSON/SSE preparados antes da autorização final; revogação durante cópia/serialização descarta saída integral; writers confirmam somente após liberação do commit ASGI. Desconexão libera guards e não causa saída parcial inválida.
6. Limites de cache/entradas/bytes/TTL, rate principal e perímetro sem confiar em X-Forwarded-For, corpo limitado, fila/worker limitados; cancelamento/timeout não libera capacidade de worker ainda ativo. Health/readiness não bloqueiam pelo gerador.
7. Health sem recurso remoto; ready interno 503 quando não íntegro; metrics/policy operador sem dados do corpus. CORS default fechado, origins explícitas sem wildcard com credenciais.
8. OpenAPI e JSON reais dos três estados/erros; sem memória por thread, sem texto bruto do modelo. Telemetria externa continua desligada, métricas allowlist.
9. TDD permanente, autorrevisão nos dez critérios, backends Chroma embedded/servidor e Qwen reais locais em fixtures sintéticas; regressão integrada oficial `--qwen-artifact`.
10. Identidades congeladas por candidato; revisão independente >=9/sem alto-crítico, máximo três rodadas; separar provas Head/juiz e preservar histórico.

## Controle adicional de integridade do cache

Contraexemplo TDD Head: cache injetado devolveu texto/literal adulterado com hashes recomputados; schema sozinho não é autoridade de prova. O runtime assina somente outcomes já validados por P6, com HMAC secreto efêmero por instância sobre identidade completa + payload + audit, conservado como finding interno `cache_integrity:<digest>`. Hits exigem assinatura íntegra antes de serializar; não reimplementam grounding nem aceitam texto inventado via cache. O token não sai no HTTP nem identifica dado por si só; troca de runtime/configuração não reaproveita assinatura. Epoch/ledger/acesso continuam revalidados no commit, assinatura não concede autorização.

## Ajuste de integração aceito pelo Head antes de editar P1

Senior HTTP identificou import transitivo generation→security→get_settings(), que lê `.env` no import e impede o requisito P7 de montagem explícita. Head assume **somente app/security.py**, substituindo a configuração global legada por `Settings(_env_file=None)` (ambiente de processo continua válido; arquivo .env não é lido implicitamente). Sem monkeypatch global temporário, bypass, reimplementação P1 ou alteração de políticas. Nova identidade deste arquivo P1 será registrada no candidato P7; regressões proporcionais auth/security/P1/exportação/import obrigatório. Teste Red de subprocesso negando acesso a .env precede edição. As 49 identidades P3B–P6 permanecem intocadas.

## Evidências de implementação / autorrevisão em preparação C1

- Head: runtime inexistente → primeiro Green de montagem P6/JSON; revogação tardia de credencial inicialmente enviava bytes (Red) → commit ledger→policy (Green). Auth de época corrente inexistente → Green; deadline vencido inicialmente invocava gerador → Green; pergunta vazia sanitizada retornava internal_error → invalid_request 400; audit do hit ausente → audit corrente com origem do cache e sem tentativas falsas; cache falsificável com hashes recomputados → HMAC validado por instância.
- Head: import generation acionava get_settings (Red subprocesso) → configuração legada sem .env (Green); regressão proporcional P1 **56 passaram** (segurança/auth/monitoring/PII/callback). Uma invocação citou nome inexistente de teste, retornou exit 1/nenhum teste; corrigida usando arquivos descobertos, não contada como Red.
- Head: chamada física abandonada de gerador continuava ocupada mas uma segunda era iniciada (Red comportamental, duas chamadas). Acrescentado semáforo físico conservado até delegate retornar, saturação técnica; Green. Primeiro prazo de teste 1 s impedia entrar no delegate e não é Red válido; probe corrigido para 10 s com evento real.
- Head: controles finais source/family/unit/relation/ledger sem epoch, serialização com revogação, ACK writer sob envio e release após disconnect: **17 passaram em 104,96 s**, antes das extensões subsequentes; Head HTTP sintético físico **3 passaram, 2 real-Qwen deselecionados em 38,96 s**, antes do freeze. Nenhuma prova atribuída ao juiz.
- Senior cache: 16 ciclos Red→Green reais, snapshots bytes/schema+audit/proof, TTL/LRU/bytes/epoch/ledger/clock/threads. Focal final **81 passaram em 1,79 s** (67 P7 + 6 legado + 8 contratos P6). Prefixo helper legado preservado byte a byte, testes legados intactos. Estado de journal bounded, cache não descobre autoridade por si só.
- Senior HTTP: 15 ciclos Red→Green reais; **35 passaram em 2,57 s** no freeze preliminar (retirado pelo Head para corrigir expiração final). Inclui quotas reais query/control, fila finita, disconnect/slow-body/send/shutdown, CORS, rate, payload/error/OpenAPI/3 estados e planning fora loop.
- Autorrevisão Head identificou expiração/remoção da chave anterior entre admissão e envio: precisa reautenticação final no emit/antes ASGI start, pois policy_epoch não representa o prazo de rotação. Senior HTTP fechando via nova fatia TDD antes do candidato integrado; nenhuma rodada de juiz consumida.
- Artefatos aceitos adicionais conferidos em bytes: relatório lexical/temporal R4 final `185f15721bcb0c456019f3220d22e8466f8584cb06d9fabbf7ea3b1e74d6c12c`; manifesto Qwen `025a8edceba57399e67e029505fa1be3140b4c25ec0b59488eb60beed1f57688`.

Focal preliminar integrado Head **130 passaram, 2 avisos em 256,96 s (0:04:16)**, incluindo Qwen real/Chroma embedded e servidor loopback. Posteriormente acrescentados controles de terceiros/distrato, métricas e expiração final; esta contagem não é o candidato final.

Senior HTTP fechou expiração/remoção/alteração de permissões da chave anterior no execute e intervalo worker→ASGI start: Red 200 indevido→Green; freeze renovado **43 passaram, 2 avisos em 3,46 s**. Gauge público query/control pending/active/queued tornou teste de fila determinístico (o sinal de auth concluída não provava entrada na fila; falha da fixture preservada como diagnóstico, não achado do produto).

**Transferência de propriedade após handoff dos Seniores:** Head assume integração final dos arquivos congelados e eventuais deltas proporcionais. Head identificou lacuna de observabilidade em preparação: faltavam estados/tokens disponíveis/percentis; novo Red do runtime→Green; novo Red HTTP de allowlist→delta mínimo em app/main.py. Estados só contam commits ASGI completos, tokens de prompt local de misses observados; output/custo desconhecidos permanecem null. Amostra de latências bounded (1.024), sem PII, não prova de SLO. Nova identidade do main será congelada no candidato integrado. Nenhum juiz iniciou rodada nesta preparação; sem aceite prévio ou corpus/API operacional promovidos.

## Segundo delta proporcional P1 antes do candidato final

Autorrevisão Head durante início da regressão integrada encontrou `safe_trace` consultando get_settings() em execução, capaz de carregar `.env`/exportador ambiente apesar da configuração explícita HTTP. **Head assume app/telemetry.py**: ContextVar de configuração escopada, runtime P7 com configuração explícita de tracing fechado e cliente herdado removido somente no contexto; threads P6 herdam contexto por mecanismos já aceitos. Sem patch global, mutação de componente/modelo ou nova exportação. Novas identidades/verificação proporcional P1 e testes de isolamento concorrente serão exigidos. Código P6 permanece byte a byte. A regressão iniciada sob candidato anterior foi interrompida pelo Head (job pwsh-125), não tratada como reprovação de testes nem gate; resultado final requer nova execução completa. Juiz apenas prepara matriz documental, nenhuma rodada iniciada.

## Candidato 1 integrado — identidade congelada para regressão final

Segunda correção proporcional P1 fechada: Red real bloqueando telemetry.get_settings durante P6 → Green; três controles de contexto/concorrência/restauração passaram em 19,41 s; controle adicional de **cliente exportador pai já ativo** passou em 12,01 s. Regressão proporcional P1 repetida **56 passaram em 4,31 s**. Nenhum patch global ou mudança de código P6. Cache conserva CRLF uniforme para preservar prefixo legado em bytes; novos módulos e deltas P1 usam LF uniforme. Não há arquivo com finais mistos.

Integração HTTP final (sem repetir os dois Qwen nesta focal): **48 passaram, 2 deselecionados Qwen, 2 avisos em 53,48 s**, antes do controle adicional troca/rollback. Troca de geração→miss correto; rollback imutável com política igual→hit permitido; revogação seguida de promote/rollback→abstenção sem prova antiga: **1 passou em 30,68 s**. Autoridade indisponível e backend Chroma servidor real indisponível durante cache quente fazem parte da nova regressão integral, não resultados antecipados.

**Regressão final Head C1: 1.429 passaram, 1 deselecionado online, 3 avisos legados, zero falhas em 1.178,91 s (0:19:38)**, job pwsh-133 coletado. Inclui P0–P7, Qwen local fixado real, Chroma embedded e servidor loopback real; novos controles de indisponibilidade da autoridade com cache quente, backend servidor físico parado/reiniciado, terceiros/distrato, troca/rollback, telemetria contexto e limites físicos passaram. Os três avisos são deprecações Starlette portal alias, langchain-community sunset e Chroma legado; nenhuma dependência mudou.

```powershell
.venv\Scripts\python.exe -m pytest tests -q -m "not online" --qwen-artifact data/models/qwen3-embedding-0.6b-97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
```

Autorrevisão Head C1 concluída com média **9,31**, sem alto/crítico aberto identificado; limites operacionais mantidos. Juiz independente preparou leituras/matriz sem auditar código, executar testes ou emitir scores; preparação **não consome rodada**. Head libera formalmente R1 após este resultado e reconferência do freeze. Estes doze hashes são bytes brutos em disco; substituem preparação/snapshots intermédios acima, sem reatribuir provas históricas.

| Arquivo | SHA-256 candidato 1 |
| --- | --- |
| [main.py](../../app/main.py) | 02a9ac37f074929420af72923faebdd92e96851f59c166fbe394043f3564b3ee |
| [cache.py](../../app/cache.py) | 8ca9143084072ad346f88b8e974e50c0ded81287b0760ea55b1dbd65fa7b8db7 |
| [http_contracts.py](../../app/http_contracts.py) | 2a60d1a3e9029cd1c7bd8b6458f6a874e871c38a8bc815c0f8744e424656e4e6 |
| [answer_runtime.py](../../app/answer_runtime.py) | d5617fde2dfdb9f4efbb28c2627265e7b86cddff482091fc9615ef654061914e |
| [security.py](../../app/security.py) | ad49c548d0a51c78faf0edcb15b0064bc14cdb004ec21cecaff9fe323c6d364f |
| [telemetry.py](../../app/telemetry.py) | fbfbfab5279bacd7113480a8bf7c348741c12ac130c7de41942a69d0d825ce33 |
| [test_api.py](../../tests/test_api.py) | 20461107a80f01fbeefb2ef9287d169f1a77dac400d20c7d57e867d828991bfc |
| [test_p7_http_contracts.py](../../tests/test_p7_http_contracts.py) | 768fdcba3b48fbf5a4c422b5892bcd279bfae693c1b2d083e9cf7ad0909a4cd7 |
| [test_p7_cache.py](../../tests/test_p7_cache.py) | 301b4dffba4fc1ab14c558cc31dd5efba3a79194801ea28aa9f2f9ebc1671b22 |
| [test_p7_runtime.py](../../tests/test_p7_runtime.py) | a73300cb63bac271a986a8f216239aa4a936b9188b3d9f8c53b9dfe1c527cfd9 |
| [test_p7_integration.py](../../tests/test_p7_integration.py) | 02e41287b60e39c4c9250b7ad9099a907d8e8f0c5ed7e8905972c5de3d2436b7 |
| [P7_API.md](../P7_API.md) | 4c723d0bf7b98abdfe5f943944c84524264695c899ac003109825a546d16696f |

As 49 identidades P3B/P4/P5/P6 continuam intocadas; plano/specs/lock/Qwen/temporal e pareceres anteriores não foram editados. security/telemetry são **novos deltas P1 verificados dentro de P7**, não reutilização automática da aprovação anterior para bytes novos.

### Autorrevisão Head — preliminar, sem substituir juiz

| Critério | Nota Head | Evidência / limite |
| --- | ---: | --- |
| Natureza RAG | 9,5 | JSON/SSE conserva P6, quatro elementos, terceiros/distrato, sem tokens/modelo livres; sem corpus humano. |
| Arquitetura | 9,3 | Factory/transport/runtime/cache separados, dependências explícitas e dois deltas P1 pequenos; não implementar P0–P6 novamente. |
| Governança | 9,4 | Auth antes do cache e até ASGI start, pin/epoch/ledger e assinatura de cache, authorities retidas; D04 externo não liberado. |
| Aplicabilidade | 9,3 | /chat compatível e /v1/answer[/stream], health/ready/admin, três estados e erros/OpenAPI; montagem para usuários pendente. |
| Eficiência | 9,1 | Budgets físicos/fila/bytes/LRU/TTL/latência bounded; pin/paridade conservadores, nenhuma meta de SLO homologada. |
| Acurácia técnica | 9,5 | Literal integral vem de P6 canônico e cache íntegro; real Qwen/Chroma em fixtures, não gabarito jurídico. |
| Precisão técnica | 9,4 | Retém partes/localização/correlação correta, distingue ausência/erro/expiração; não promete ordem de chegada de bytes de rede. |
| Rastreabilidade | 9,2 | IDs/audit/origem de hit, hashes/campanhas históricas e TDD preservados; persistência/retenção operacional da trilha pendente D04/P9. |
| Observabilidade | 9,1 | Allowlist, estados só após commit, tokens locais disponíveis/unknown null, ContextVar sem exportação ambiente; qualificação operacional ainda pendente. |
| Confiabilidade | 9,3 | Revogação/serialização/disconnect/deadline/fila e chamadas abandonadas cobertos; falta resultado da regressão final e juiz. |
| **Média Head** | **9,31** | Avaliação preliminar do Head: nenhum alto/crítico aberto identificado nos controles já executados, não aprovação independente. |

## Contraexemplo posterior Head P7-H1 — C1 bloqueado, R1 em curso

Durante espera da auditoria independente, Head verificou também a autorização **final de rotas de controle** (não só query JSON/SSE). Probe stdin com Settings e RuntimeDouble locais: getter `/metrics` remove `operator_api_secret_key` após admissão; saída permanece **200**, contendo métricas agregadas restritas. Asserção esperava 401/403 e falhou com exit1 — **Red real**, não erro de fixture ou interrupção. Nenhum arquivo candidato/teste permanente foi alterado pelo probe; nenhum segredo ou dado contratual real usado/exibido.

Hipótese material Head **P7-H1**: `/metrics`, `/admin/policy` e `/ready` retornam JSON normal sem revalidação final de credencial/política até ASGI start, ao contrário das rotas query. Média/autorrevisão Head 9,31 acima é fotografia **anterior** a esse contraexemplo, não estado atual de aceite. Head retira encaminhamento de aprovação de C1 e mantém bloqueio até análise/correção/verificação; classificação/IDs/decisão formal cabem ao juiz independente. Probe e hipótese enviados ao juiz para reprodução própria, sem score predeterminado. A regressão 1.429 permanece evidência dos testes existentes C1, mas não elimina este contraexemplo novo.

Juiz confirmou **P7-J1 ALTO** por provas próprias: **seis Reds comportamentais em 13,96 s** — três remoções Settings esperavam401 e três revogações ACK em journal/ledger/Chroma embedded físicos sintéticos esperavam403; todos obtiveram200 em `/ready`, `/metrics`, `/admin/policy`. Fundamento: specs §5.3/§8/ACC-07, dados de controle privados; o juiz não alegou vazamento de quote/corpus. Focal própria do juiz coletada: **192 passaram, 2 avisos em 560,49 s**, Qwen embedded/servidor reais; 18 probes leves e quatro contraprovas ASGI policy/ledger positivas. Um probe físico inicialmente expirou4s **antes** do barrier render (precondição falsa), portanto não foi achado/Red de produto; o juiz o está corrigindo por evento. Ainda sem score/parecer formal. Senior HTTP só preparou proposta C2, sem editar/testar; prepare+commit control governados, pool físico/deadline/envio/cancelamento completos, journal current obrigatório e sem corpus pin para métricas/policy.

R1 independente continua sob os mesmos doze hashes intocados; nenhum fix enquanto os bytes estão sendo julgados. Após decisão formal, candidato corrigido exigirá TDD, nova identidade e nova regressão antes de eventual R2; máximo três rodadas mantido. Nenhum gate P8/corpus/AWS avançado.

## Fechamento R1 / início C2 — antes de editar

**Juiz R1 concluiu REPROVADO C1, média exata 8,94/10; P7-J1 ALTO aberto.** [Parecer R1](../reviews/2026-10-07-exec-p7.md), SHA-256 bruto `854e58bc2878d79ba63a5ca517abee78d94879ab80364b4fcaa20eecddb6d117`. O juiz confirmou 192 focais e **42 probes próprios válidos passaram, seis Reds J1**, mais uma execução inválida de precondição declarada/corrigida. Todos os jobs próprios coletados; freeze12C1/49anteriores íntegro ao fechar. Preparação não contou; **R1 consumida, máximo duas restantes**. C1/8,94 não serão apagados nem reatribuídos aos bytes corrigidos.

**Propriedade C2:** Head [answer_runtime.py](../../app/answer_runtime.py), [test_p7_runtime.py](../../tests/test_p7_runtime.py), [test_p7_integration.py](../../tests/test_p7_integration.py) e documentação; Senior HTTP [main.py](../../app/main.py), [http_contracts.py](../../app/http_contracts.py), [test_api.py](../../tests/test_api.py), [test_p7_http_contracts.py](../../tests/test_p7_http_contracts.py). Cache/P1/P6 sem edição prevista. Juiz permanece independente/sem implementação, só novo parecer após liberação R2.

**Port acordado:** `prepare_control(operation, access, *, request_id, deadline, encode)` síncrono, operation fixo `ready|metrics|policy`; chama getter próprio autorizado sob configuração de telemetria fechada, `encode(raw)->(status_code:int, body:bytes)` é projeção/serializador confiável do transporte executado no mesmo worker **antes** do guard final. Retorna `PreparedControl` frozen com body/status/acesso/snapshot/deadline/correlação, ligação privada de integridade e digest ledger só para readiness positiva. `commit_control(prepared, emit)` mantém journal atual até aceitação ASGI/desenrolamento, credencial proibida403, snapshot divergente/autoridade perdida503, prazo504; positive-ready adquire ledger→policy e atesta digest, métricas/policy não pinam corpus/ledger para continuarem úteis com Chroma down. Nenhum bypass de port para runtime legado. Encoder não vem de input HTTP.

**Transporte:** uma ponte governada reutilizável query/control, ControlResponse thin; pool control separado bounded cobre fila→getter→projeção→serialização→commit→send. Reauth Settings sob commit e imediatamente antes ASGI start. Cancelar/unwind a task ASGI real, não liberar future físico prematuramente. /health permanece público mínimo fora de pools. Body/status/control MAC impedem mutação posterior de preparado; statuses200/503 explícitos, sem raw getter na emissão.

**Aceite C2 esperado:** seis contraprovas permanentes de Settings/journal agora401/403; rotação/expiry/permissão durante preparo/serialização/start; writer bloqueado por send lease até completo/disconnect; deadline/saturação libera capacidade só ao término físico; controle sem corpus pin e autoridade indisponível503. TDD→autorrevisão→novos hashes→nova full offline Qwen zerofalhas→R2 própria. G7 permanece aberto até aprovação real; não antecipar nota futura.

## C2 integrado congelado — regressão final pendente

TDD Head: port controle ausente Red→Green (1 em6,75s); correlação interna req-inner-policy incorreta Red→Green (1 em5,90s); callbacks emit instrumentados query/control ainda consultavam configuração ambiente Red2→Green2 em11,11s, contexto fechado agora também envolve ambos commits. Controles complementares de permissões, bytes/status/deadline adulterados, encode uma vez antesguard, indisponibilidade ledger/sem pin, prazo de serialização e autoridade alterada passaram. Getter/encoder são dependências Python confiáveis do servidor, não fonte arbitrária de aprovação de cache.

Head seis contraprovas permanentes de J1 com Config/journal/ledger/Chroma físicos: **6 passaram em25,30s** (401/403, nenhum dado privado, correlação própria). **14 leases físicos passaram em64,37s**, incluindo ASGI start/body suspensos de readiness-ledger e metrics-policy, completo/disconnect, ACK só após envio/desenrolamento; metadata não conta estado answered. Testes reais de servidor down agora verificam também metrics/policy200, ready503; resultado desses três checks adicionais pertence à full C2 a seguir.

Senior HTTP final **135 passaram, 2 avisos em10,49s**. Seus Reds reais: três controles200 após remoção getter; receive504 classificado500; fallback de erro ainda permitia worker original emitir; segunda cancel durante shutdown/HTTP interrompia cleanup e soltava worker antes unwind. Fechados com mesma ponte/pools físicos, classificação HTTPException, stop antesfallback e stop idempotente. Fixture Race TestClient-bodyaccepted→append-control_commits foi corrigida com evento real de término, não Red de produto. Nenhum scope runtime/cache/P1/P6/docs foi editado por Senior.

**Focal integrada Head C2:271 passaram,2 Qwen deselecionados,2 avisos em399,81s (0:06:39)**, jobpwsh-178 coletado, zero falhas. Integra app real/ports físicos/HTTP/caches, não antecipa full/Qwen. Senior cache fez revisão estática extra do port/leases/ponte: nenhum risco material novo identificado no escopo; não executou comandos/tests nem emitiu aprovação/nota/parecer, não consome R2.

Autorrevisão Head C2: natureza9,5; arquitetura9,3; governança9,5; aplicabilidade9,3; eficiência9,1; acurácia9,5; precisão9,4; rastreabilidade9,3; observabilidade9,3; confiabilidade9,4; **média9,36**. Correção J1 demonstrada nas contraprovas do implementador, sem alto novo material identificado; **fechamento formal de J1 e aceite dependem da R2**, ainda não liberada. Não extrapolar para produção/corpus/SLO/retensão operacional.

### Identidades C2 — doze hashes brutos

Substituem C1 apenas para o novo candidato; C1/8,94/freeze/probes permanecem históricos acima e no parecer. Cache/P1/P6 não sofreram alteração nesta correção.

| Arquivo | SHA-256 candidato2 |
| --- | --- |
| [main.py](../../app/main.py) | 5d88aa6701677b0c6c77e6f62b32e61f016e9dad2c36b2c00c1ae47ebd6dbbaa |
| [cache.py](../../app/cache.py) | 8ca9143084072ad346f88b8e974e50c0ded81287b0760ea55b1dbd65fa7b8db7 |
| [http_contracts.py](../../app/http_contracts.py) | dac80ae72723e5012136ac55b0fb8595349140328ad97f001edc3baa22abcc0e |
| [answer_runtime.py](../../app/answer_runtime.py) | bb4e1852f0c08c72009d7e9c7ef98e5dae77265be173425c827e5157e085b690 |
| [security.py](../../app/security.py) | ad49c548d0a51c78faf0edcb15b0064bc14cdb004ec21cecaff9fe323c6d364f |
| [telemetry.py](../../app/telemetry.py) | fbfbfab5279bacd7113480a8bf7c348741c12ac130c7de41942a69d0d825ce33 |
| [test_api.py](../../tests/test_api.py) | abd10448c59cee3335fbe99e5c52b923a45c86476db210d8fead4f53e1d54ba3 |
| [test_p7_http_contracts.py](../../tests/test_p7_http_contracts.py) | b82d86b08e9d6a770c786290f69c3b6a9049d78c7a0958a390246e190a49f6ee |
| [test_p7_cache.py](../../tests/test_p7_cache.py) | 301b4dffba4fc1ab14c558cc31dd5efba3a79194801ea28aa9f2f9ebc1671b22 |
| [test_p7_runtime.py](../../tests/test_p7_runtime.py) | 3b4c2eb677082962758b7ca95a777195ce98dbe48eb5f888cc951bae299c8f20 |
| [test_p7_integration.py](../../tests/test_p7_integration.py) | 12c08b35bd615e10a8fb8c3e1484cce0dc3e267faea82732cb093b0ba9491c5b |
| [P7_API.md](../P7_API.md) | 633e682fa872594dfc1681494dd3329e1827b022378c8143a91883e726b85bd5 |

As49identidades antigas reconferidas raw; diffcheck limpo (avisos Git LF→CRLF cosméticos, sem normalizar hashes). Full C2 será execução nova sobestesbytes; não reatribuir1.429C1 nem192juizC1. Nenhum R2 iniciado até resultado integralzerofalhas/conferência. Nenhum reset/clean/commit/sync/AWS/provider/corpus real.

## Regressão oficial C2 / liberação R2

**Head C2:1.556 passaram,1 online deselecionado,3 avisos legados,zero falhas em1.195,43s (0:19:55)**; jobpwsh-180 coletado. Comando integrado offline com `--qwen-artifact` fixado já registrado acima; esta é **nova execução C2**. Inclui física Qwen/Chroma embedded/servidor, parada/reinício real com controle útil/ready503, 6 contraprovas J1, guards/controlleases e todos P0–P7. Nenhum código/teste/docAPI mudou durantefull. As deprecações permanecem Starlette portal alias, langchain-community sunset e Chroma legado; sem novas dependências.

Freeze C2/controles/R1 reconferidos:12C2raw intactos; plano/specs/lock/temporal/Qwen iguais; R1 rejeitada8,94/SHA854e58bc… preservada. Autorrevisão Head9,36; fechamento formalJ1 aguardará parecer independente. **Head libera formalmente R2** após reconferência pósfull. Juiz só apenderá R2 ao prefixo R1 preservado, provas próprias separadas, sem implementação. G7 ainda aberto até decisão, no máximo R2+R3 restantes; nenhum gate documental/operacional avançado.

## Decisão formal R2 — P7 Técnico Sintético Aprovado

**Juiz independente concluiu APROVADO Candidato C2 na Rodada 2, com nota final 9,37/10 (soma 93,7/10). Achado P7-J1 ALTO formalmente FECHADO. Zero achados altos ou críticos abertos.**

Parecer independente formal apensado em [docs/reviews/2026-10-07-exec-p7.md](../reviews/2026-10-07-exec-p7.md), tamanho 36.886 bytes, SHA-256 bruto `5eb3cc344c6b550fc565544315c89b1032a2e16be8498073fe235ac552d21771`. Prefixo da R1 (23.239 bytes, SHA-256 `854e58bc2878d79ba63a5ca517abee78d94879ab80364b4fcaa20eecddb6d117`) integralmente preservado.

Notas R2 do juiz independente (pesos iguais de 10%):
1. Natureza RAG jurídico: **9,5**
2. Arquitetura: **9,4**
3. Governança: **9,4**
4. Aplicabilidade: **9,3**
5. Eficiência: **9,2**
6. Acurácia técnica: **9,4**
7. Precisão técnica: **9,4**
8. Rastreabilidade: **9,3**
9. Observabilidade: **9,4**
10. Confiabilidade: **9,4**
- **Média aritmética exata:** **9,37/10** (sem arredondamento para liberar).

Evidências independentes do juiz:
- Fechamento de P7-J1: 6 contraprovas comportamentais aprovadas (401 para remoção de chaves em `/ready`, `/metrics`, `/admin/policy`; 403 para revogação física de credencial no SQLitePolicyJournal em voo; zero vazamento de dados).
- Probes adicionais: integridade e HMAC de `PreparedControl`, leases físicos de writer com bloqueio até o desenrolamento ASGI, readiness positivo exigindo digest do ledger sob ordem ledger→policy, readiness negativo devolvendo 503 com `Retry-After: 1`, métricas e política sem pin de corpus/ledger funcionando com Chroma/ledger down, e journal down fechando 503.
- Suíte focal independente do juiz (job `pwsh-228`): **319 passaram, 2 avisos legados, zero falhas e zero skips em 485,35 s (8 min 05 s)**, com Qwen local e Chroma embedded/servidor loopback.
- Regressão Head C2 registrada: **1.556 passaram, 1 online deselecionado, 3 avisos legados, zero falhas em 1.195,43 s** (job `pwsh-180`).
- Freeze de 12/12 identidades C2 e 49 identidades anteriores conferido pré e pós-auditoria com zero divergências.

**Subgate P7 Técnico Sintético (G7) está formalmente APROVADO na Rodada 2.**

## Fronteiras mantidas

Corpus real, G2/G3 pleno/G4 operacional/D04/G5/P8, política externa D01 e Linux/AWS/P9 permanecem pendentes. Não sincronizar M-Files, ler .env/segredos, promover corpus ou iniciar serviço para usuários. Nenhuma memória, ACL por cliente/caso, síntese jurídica, novo embedding, GC de dados ou infraestrutura. Recuperação: desativar cache sem bypass de governança; indisponibilidade controlada se autoridade não vigente. Critérios: natureza RAG, arquitetura, governança, aplicabilidade, eficiência, acurácia técnica, precisão técnica, rastreabilidade, observabilidade, confiabilidade.
