# P7 — contrato técnico HTTP e montagem explícita

Estado: técnico sintético aprovado (9,37/10, R2); não libera corpus real nem operação. Estado/gate em [evidências](execution/2026-10-07-p7-evidencias.md) e [parecer](reviews/2026-10-07-exec-p7.md); produto/gates pendentes no [HANDOFF](HANDOFF.md).

## Montagem

`app.main.create_app(runtime=..., settings=..., http_config=...)` recebe dependências já construídas pelo operador. O módulo não instancia aplicação/cliente/índice nem lê `.env`. `GovernedAnswerRuntime(manager, generator, config=AnswerConfig(...), cache=GovernedResponseCache(...))` compõe a geração pinada de P4, serviço P6 e cache P7. O manager exige autoridades correntes existentes; a montagem não inicializa, promove nem repara corpus/journal/ledger.

Perfil sintético local deve usar diretórios isolados, originais sintéticos e gerador determinístico identificado; testes permanentes exemplificam montagem completa em [integração HTTP](../tests/test_p7_integration.py). Não transportar esse perfil para produção. Qwen fixado é embedding/tokenizer, não o gerador de respostas. Geradores remotos permanecem bloqueados por D01; nenhum fallback remoto é criado pela API. A execução P7 fornece configuração de telemetria fechada por ContextVar, sem consultar get_settings()/`.env` na requisição e sem herdar exportador externo de um trace pai; o contexto é restaurado em sucesso/erro/timeout e isolado entre threads. A configuração atual conserva fallback desabilitado, conforme P6.

A invocação `uvicorn app.main:create_app --factory` sem runtime explicitamente injetado oferece apenas health de processo; rotas internas retornam indisponível. Não há inicialização silenciosa a partir de `.env`, corpus de staging ou legacy retriever. Nenhum serviço para usuários foi iniciado pelo desenvolvimento.

`Settings` controla credenciais/chave anterior/prazo; `HttpConfig` controla corpo, workers, fila, rate, origins, deadline e shutdown. Os campos legados Settings de rate/concurrency/timeout não substituem HttpConfig implicitamente. `AnswerConfig` limita o serviço P6 dentro do tempo restante HTTP; `max_generator_calls` limita chamadas físicas (default 5), inclusive abandonadas após timeout. Dependências de um runtime são configuração confiável do servidor; substituições exigem novo runtime/identidade. A chave de cache contém namespace aleatório por runtime, portanto compartilhamento entre montagens diferentes não reutiliza provedor/configuração.

## Rotas

| Rota | Acesso | Entrada / saída |
| --- | --- | --- |
| GET /health | Público, sem acervo | `{ "status": "ok" }`, somente processo vivo |
| GET /ready | Consulta autenticada | `{ "status": "ready" }` ou 503; autoridades/índice íntegros, sem chamada de geração |
| POST /chat | Consulta | ChatRequest (`message`, `filters`, `thread_id`) → ChatResponse, não streaming |
| POST /v1/answer | Consulta | AnswerRequest (`question`, `plan`, `item_labels`) → ChatResponse |
| POST /v1/answer/stream | Consulta | Mesmo request; SSE bufferizado `event: answer` com **um JSON completo validado** |
| GET /metrics | Operador | Contadores HTTP/cache allowlist, sem pergunta/prova/chave/IDs |
| GET /admin/policy | Operador | Época/contagens/bools, sem listas de bloqueados |

X-API-Key obrigatório nas rotas internas, inclusive documentação/OpenAPI. Chave de consulta não opera índices/políticas. O contexto é produzido no servidor e incorpora a época corrente; identificadores do corpo filtram seleção, nunca autorização. Chave anterior exige prazo UTC/offset válido e continua revogável por credential_id. Bypass não é permitido na API, nem no desenvolvimento. Configuração de credenciais ausente/inválida fecha as rotas internas (503), não habilita consulta pública.

Request_id é sempre gerado no servidor, refletido no header X-Request-ID e JSON; o valor fornecido pelo cliente é descartado. Thread_id serve somente para correlação; não carrega memória de conversa nem separa cliente/caso. O payload extrativo contém estado, razão, geração e citações dos quatro elementos. AnswerRequest permite plano explícito estrito; /chat constrói plano com normalização compartilhada. A API não retorna audit interno nem texto livre do modelo.

Exemplo sintético de body:

```json
{"question":"Qual o valor dos honorários?","plan":{"version":"query-plan-v1","question_item_ids":["q1"],"filters":{"instrument_ids":["doc:101"],"party_identifiers":[],"selection_mode":"intersection"},"evidence_scope":"linked_instruments","reference_date":null},"item_labels":{}}
```

## Erros e segurança de saída

| HTTP | Código público |
| --- | --- |
| 400 | invalid_request |
| 401 | unauthorized |
| 403 | forbidden |
| 422 | invalid_request (schema/body JSON; sem eco de entrada) |
| 429 | rate_limited (Retry-After) |
| 503 | service_unavailable (Retry-After) |
| 504 | request_timeout |
| 500 | internal_error |

ErrorResponse usa mensagem fixa, request_id e timestamp; não contém exception message, stacktrace, prompt, quote ou segredo. Erro técnico não vira abstenção. `answered` exige prova; `abstained` e `needs_clarification` têm citações vazias/razão controlada. O SSE não abre 200 antes de validação e revalidação final; erros anteriores ao envio são HTTP/JSON normais. Depois de começar a saída não há outro payload sensível/error stack: cancelamento encerra o transporte.

CORS fechado por default, somente origins exatas configuradas, nenhum wildcard/bypass. Limite de corpo independe de Content-Length; rate é por credencial/principal e peer do perímetro sem confiar em X-Forwarded-For. Rede privada/firewall/proxy TLS externo continuam P9, não se deduzem do limiter.

## Cache e fronteira de revogação

Cache governado somente em memória, separado do helper legado ResponseCache (nunca usado na API). Default de cache configurado: TTL 300 s, 128 entradas, 8 MiB de bytes UTF-8 identidade/payload/audit; overhead Python limitado adicionalmente por quantidade de entradas/autoridades. Cache pode ser desabilitado (`cache=None` no runtime ou limites zero no cache), sem desligar autorização. Não mantém perguntas/chaves API em claro, mas contém respostas contratuais autorizadas: proteger memória/disco/processo continua obrigação operacional.

Identidade: SHA-256 da pergunta sanitizada sem case-folding destrutivo, contexto completo, plano/filtros/itens/data, geração/manifest, configs/namespaces/versões prompt-validator-renderer, journal_id/policy_epoch e ledger_digest. Mutação de época/ledger invalida entradas, troca/rollback de geração muda a chave. Schema/audit/citações são revalidados e copiados defensivamente. HMAC efêmero por runtime vincula payload/audit/identidade; cache jamais é fonte canônica de prova. Cache hits atualizam correlação/audit do request corrente, sem alegar nova tentativa de gerador; origem é conservada internamente.

Hit e miss autenticam e pinam primeiro. O JSON (ou evento SSE integral) é preparado antes do commit. Commit retém autoridades ledger→policy, revalida credencial/época/digest/revisões/fontes e deadline, então aceita bytes ASGI. Writers não confirmam revogação enquanto esse envio protegido estiver ativo. Desconexão/backpressure/deadline desenrolam send antes de liberar as autoridades; um worker ainda ativo conserva capacidade. **Não é possível recolher bytes já aceitos/enviados**, nem garantir sua chegada antes de ACK por atraso de rede; esta garantia é de linearização de saída no servidor, não de rede.

Workers/fila de consultas e controles têm budgets separados/bounded. Health não depende de gerador; controles têm capacidade própria e saturação controlada. Trabalho bloqueante vai para worker; cancellation/timeout do cliente não cancela artificialmente a future real nem libera slot antes do término. Chamadas físicas do gerador mantêm semáforo mesmo após o serviço abandonar o resultado; novas chamadas saturadas falham tecnicamente. Não se matam threads/índices para simular cancelamento. Runtime não cooperativo pode atrasar encerramento de processo Python; recuperação/circuit-breaker operacional exige supervisão P9, não SLO fabricado.

Métricas incluem gauges agregados query/control pending/active/queued, contadores de estados **somente após commit ASGI completo**, cache e erros/latência HTTP. Percentis p50/p95/p99 do runtime usam no máximo 1.024 tempos de preparo de respostas posteriormente enviadas; não incluem garantia de transporte/LLM remoto, não são SLO nem benchmark de carga. `total_input_tokens` soma tokens de prompt local observados nos misses concluídos, sem contabilizar hits como novas gerações ou inferir faturamento de retries. `total_output_tokens` e `provider_cost_usd` são null quando não observáveis; nenhum zero fictício. Auditoria restrita por requisição permanece no PreparedAnswer, inclusive origem do hit, sem expor audit no HTTP.

## Fronteira de controle — C2

Após reprovação C1/R1 (8,94; P7-J1 alto), `/ready`, `/metrics` e `/admin/policy` usam **a mesma ponte governada de envio** das consultas. O runtime exige `prepare_control(operation, access, *, request_id, deadline, encode)` e `commit_control(prepared, emit)`; runtime legado sem ports não recebe fallback para getters livres. A operação é constante da rota; `encode` é projeção/serialização confiável do servidor, não input cliente. Getter→allowlist→JSON bytes integral→commit→send ocorrem no mesmo worker físico de controle.

PreparedControl vincula bytes/status/acesso/correlação/snapshot/deadline por integridade privada. Journal corrente e credencial/permissão/configuração são revalidados até ASGI start; guard retido até envio/desenrolamento. Readiness positiva atesta ledger sob ordem ledger→policy; negativa503 preserva diagnóstico com policy vigente. Métricas/admin não dependem de corpus pin/ledger, permanecem úteis quando Chroma está down; journal indisponível fecha503. Rotação/remoção/expiry revoga a saída em curso. Nenhuma proteção de query é inferida automaticamente como proteção de controles.

Falha de receive é erro HTTP normal, não disconnect fictício. Fallback de erro marca output stopped antes de ceder o loop; stop/cancel idempotente não interrompe pela segunda vez um ASGI send ainda desenrolando. HTTP cancel/shutdown não libera slot/lease enquanto worker/envio físico não terminar; limites/qualificação operacional acima continuam aplicáveis. Status preparado503 recebe Retry-After1, sem emitir metadados velhos.

## Reprodução offline

```powershell
.venv\Scripts\python.exe -m pytest tests -q -m "not online" --qwen-artifact data/models/qwen3-embedding-0.6b-97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
```

Usa artefato fixo já existente; sem download, provedor remoto, `.env` ou corpus real. Inclui Chroma embedded/servidor loopback real e Qwen real sobre fixtures físicas sintéticas. Só o parecer independente pode fechar P7. G2/G3 pleno/G4 operacional/G5/P8 e AWS/P9 permanecem pendentes.
