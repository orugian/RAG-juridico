# Execução do RAG — 05/10/2026

O usuário aprovou o plano v0.6 e as specs v0.6 nesta sessão. Os documentos aprovados permanecem congelados; este registro acompanha a execução e as decisões posteriores.

## Orientação posterior à aprovação

- Prosseguir com os documentos disponíveis, deixando as revisões humanas pendentes para depois.
- Processamento técnico em staging não equivale a aprovação jurídica. Documentos com riscos ou decisões humanas pendentes não entram no índice de consulta. Não preencher decisões/revisores automaticamente.
- Desenvolver contratos, segurança e preparação com fixtures sintéticas e diagnósticos locais. O aceite humano de conteúdo e relações continua pendente para o corpus lançado.
- Retornar ao usuário em P3, **antes de decidir o embedding ou baixar pesos**: cumprido. Em 06/10, o usuário escolheu Qwen; decisão e entrega em [P3A/D05](2026-10-06-p3a-qwen.md).
- OpenRouter permanece o destino proposto; a política de dados deve ser definida antes das consultas reais. Não há autorização para enviar documentos nesta execução.

## Protocolo

Cada entrega segue Red → Green → Refactor e revisão independente por agente juiz. Média dos dez critérios >=9 e nenhum achado alto/crítico aberto; no máximo três rodadas por gate. Nota de etapa não significa homologação das etapas futuras.

## Estado

| Etapa | Estado | Evidência |
| --- | --- | --- |
| P0 — contratos e testes isolados | Aprovado G0 | Juiz 9,14/10, rodada 3; 329 passaram, 1 online deselecionado |
| P1 — autenticação/telemetria | Aprovado G1 | Juiz 9,09/10, rodada 3; 361 passaram no escopo P1 |
| P2A — preparação técnica em staging | Aprovado subgate técnico | Juiz 9,15/10, tentativa 2; 287 arquivos/14.791 blocos em staging, sem aprovação humana |
| P2B/G2 — corpus e gabarito jurídico | Pendente | Revisões humanas permanecem adiadas; zero fontes aprovadas |
| P3A — Qwen local/tokenizer/diagnóstico | Aprovado subgate técnico | [D05 e evidências](2026-10-06-p3a-qwen.md); juiz 9,17/10, tentativa 2; 440 testes; pesos verificados; sem publicação |
| Correção lexical/temporal após P3A | Aprovado subgate técnico | [Registro](2026-10-06-correcao-lexical-temporal.md) / [parecer](../reviews/2026-10-06-exec-lexical-temporal.md); 9,18/10 na rodada 4, LT-J1 fechado; 570 testes passaram; notas históricas 8,63/8,80/8,87 preservadas; quinta rodada não necessária |
| P3B — prova canônica/chunking governado | Aprovado subgate técnico | [Evidências/identidades](2026-10-06-p3b-evidencias.md) / [parecer](../reviews/2026-10-06-exec-p3b.md); 9,20/10 rodada 3 final, J1/J2 fechados; reprovações 8,74/8,91 históricas; 833 testes Head e 296 focais/seis probes/128 combinações juiz |
| P3/G3 pleno | Pendente | Comparação e avaliação documental, Chroma e qualificação operacional; desempenho CPU/AWS não liberado |
| P4 — gerações/paridade/journal | Aprovado subgate técnico sintético | [Decomposição/evidências/14 identidades](2026-10-07-p4-evidencias.md) / [parecer](../reviews/2026-10-07-exec-p4.md); 9,22/10 rodada 3 final, J1/J2/J3 fechados; reprovações 8,78/8,80 históricas. Head 1.079 integrados finais; juiz 246 focais e 26 cenários próprios aprovados nas execuções válidas |
| P4/G4 operacional | Pendente | D04/fonte durável externa/anchor e domínios físicos de falha não aprovados; nenhum corpus real ou API liberado |
| P5 — recuperação avaliada | Aprovado subgate técnico sintético | [Evidências](2026-10-07-p5-evidencias.md) / [parecer](../reviews/2026-10-07-exec-p5.md); juiz 9,33/10, rodada 1 final; 1.090 passaram |
| P6 — grafo governado e grounding | Aprovado subgate técnico sintético | [Evidências](2026-10-07-p6-evidencias.md) / [parecer](../reviews/2026-10-07-exec-p6.md); juiz 9,35/10, rodada 2 final; J1 a J8 fechados; 1.283 passaram |
| P7 — API, cache governado e concorrência | Aprovado subgate técnico sintético | [Evidências](2026-10-07-p7-evidencias.md) / [parecer](../reviews/2026-10-07-exec-p7.md); juiz 9,37/10, rodada 2 final; P7-J1 fechado; R1 8,94 histórica; 1.556 passaram Head, 319 juiz |
| P8 — homologação ponta a ponta | **Aprovado subgate técnico sintético — COM RESSALVAS** | [Evidências](2026-10-08-p8-evidencias.md) / juiz independente **9,24/10, rodada 1 final**, nenhum alto/crítico aberto. 16 casos × 3 transportes × 2 backends Qwen/Chroma reais, avaliador estrito, carga/segurança e delta de admission autorizado. **Ressalvas abertas:** `WinError 5` intermitente no rename do build (MÉDIO, em código P4 aceito intocado, a reavaliar em Linux/D04/P9) e janela fixa de 8 s no harness de carga (BAIXO-MÉDIO) — regressão integral oficial **não verde**. G5/G8 humanos, relevância jurídica, corpus/gabarito humanos, D04, Linux, SLO e AWS/P9 **pendentes** |

Não há índice de produção, API operacional ou corpus homologado nesta altura. API local técnica sintética está implementada e aprovada, sem serviço de produção aberto a usuários.

Na campanha lexical/temporal, após as três reprovações iniciais, o usuário autorizou explicitamente duas rodadas adicionais, máximo cinco total. A rodada 4 foi aprovada com 9,18/10, sem alto/crítico aberto identificado; campanha encerrada, quinta não necessária. A execução posterior P3B implementou builders/proveniência/ledger/fechamento/Qwen. Rodadas 1/2 reprovaram 8,74/8,91; candidato 3 corrigido via TDD foi aprovado **9,20 na rodada 3 final**, J1/J2 fechados, nenhum alto/crítico aberto identificado. 833 testes offline do Head; 296 testes focais próprios, seis probes físicos Qwen e 128 combinações do juiz. [HANDOFF.md](../HANDOFF.md) atualizado, histórico/identidades preservados. P4 não iniciado nesta entrega; continuidade técnica sugerida somente com fixtures sintéticas. Nenhuma quarta rodada ou promoção G3/corpus/produção autorizada pelo gate atual. Não reexecutar etapas aprovadas nem usar relatórios Qwen históricos como identidade do código posterior.

## Fechamento P4 — 07/10/2026

Atualização de 07/10: P4 executado após aceite P3B, com TDD/Seniores/frentes independentes e juiz. R1/R2 reprovaram 8,78/8,80; C3 aceito 9,22 na rodada 3 final, J1/J2/J3 fechados, nenhum alto/crítico aberto identificado. Identidade aceita: quatorze hashes (doze C2 com duas substituições C3), parecer final d2d5941a52e6c89441d23eb3472c561c537edbf448f086afa51b4b9dcee2983d. O trecho anterior “P4 não iniciado nesta entrega” refere-se ao fechamento P3B de 06/10, não ao estado atual. CONTEXT/HANDOFF atualizados; P5 técnico sintético sugerido, não implementado. G4 operacional/D04, corpus/revisão humana, G3 pleno, política OpenRouter e SLO/Linux/AWS seguem pendentes.

## Fechamento P5 e P6 — 07/10/2026

P5 técnico sintético aprovado em 07/10: 9,33/10 na rodada 1 final; 5 identidades congeladas, 1.090 testes integrados. P6 técnico sintético aprovado em 07/10: 9,35/10 na rodada 2 final, J1 a J8 fechados; 18 identidades congeladas, 1.283 testes integrados.

## Fechamento P7 — 08/10/2026

P7 técnico sintético aprovado em 08/10/2026: R1 reprovou C1 com 8,94/10 devido a P7-J1 alto (controles encerravam autorização na admissão). Candidato C2 implementou ports síncronos de controle com encode pré-guard e assinatura HMAC, reautenticação em voo sob commit e pré-ASGI start, leases físicos segurando escritores até o desenrolamento ASGI e pools bounded separados. Candidato C2 aprovado pelo juiz independente com **9,37/10 na rodada 2 final**, achado P7-J1 formalmente fechado e zero achados altos ou críticos abertos. Regressão oficial Head: 1.556 passaram, 1 online deselecionado, 3 avisos legados em 1.195,43 s; suíte focal independente do juiz: 319 passaram em 485,35 s. Próxima etapa: P8 — Homologação ponta a ponta (preparação técnica sintética). G2, G3 pleno, G4 operacional, D04, G5/P8 humano/G8 documental e P9 seguem pendentes.

## Encerramento documental da sessão — 07/10/2026

Usuário solicitou encerrar e preparar contexto/prompt para a próxima sessão. HANDOFF agora concentra escopo completo, mapa de código, estado fechado P4, critérios/fronteiras da próxima entrega P5 técnica sintética e prompt pronto para copiar. README/índice de planejamento apontam para essas fontes; CONTEXT distingue arquitetura-alvo de módulos legados/implementados e corrige instruções históricas de tracing/provedor. A skill living-docs-governance orientou reutilização dos documentos existentes e separação de estado/histórico, sem criar handoff paralelo ou alterar harness externo.

Não houve implementação P5, nova execução da suíte, nova nota de juiz, mudança de código/tests/ledger/modelos/corpus, sync, publicação, chamada externa ou infraestrutura neste fechamento. Os 1.079 testes e o aceite P4 9,22 continuam evidências da entrega C3 anterior, não desta revisão editorial. Verificação documental final: doze identidades P3B e quatorze P4 conferem; oito controles (plano/specs/lock, Qwen/temporal, pareceres P3B/P4 e diagnóstico lexical/temporal) preservados; 287 entradas de staging com digest inalterado; 51 links locais com destino existente e diff documental sem erros de whitespace. A conferência inicial do staging imprimiu o digest correto, mas a asserção em Python falhou por quoting PowerShell; repetida com comparação no PowerShell, passou. Um link legado file:// e um espaço final foram corrigidos antes da verificação final. Próxima sessão começa por leitura/validação/decomposição P5, mantendo revisão humana/quarentena/política OpenRouter/G3 pleno/G4 operacional/SLO/AWS pendentes.

## Decisões e migrações P0

- D01: OpenRouter mantido, política de dados pendente; `provider_policy_approved=False` impede construir cliente real no caminho legado. Tracing desligado por padrão.
- D02: acervo interno comum, principal técnico e permissão de operação separada; nenhum filtro de usuário concede acesso.
- D03: revisão humana adiada, sem elevar candidatos a aprovados. `ParsedDocument.status` permanece técnico; novos campos `eligibility` e `approval_record_id` conservam artefatos antigos como `pending_review`.
- D04: conta/região/journal/custos AWS pendentes; nenhum recurso provisionado.
- O singleton criado no import foi substituído por `get_production_agent()`; único consumidor existente era `tests/test_agent.py`, migrado. O grafo legado ainda não é o RAG de P6.
- `ChatResponse` exige novos campos de prova e estado; não havia API nem consumidores instanciando esse modelo. `HeathResponse` e `MetricsResponse` legados continuam compatíveis.
- CPF/CNPJ têm normalização canônica sem correção O/0 e sem apagar letras. Validar formato não comprova identidade ou dígitos verificadores; extração/reconciliação/busca migram nas etapas correspondentes.
- Dependências permanecem no `uv.lock` existente. Testes usam o Python da `.venv`, sem resolução/download; comando equivalente pelo uv: `uv run --frozen pytest -q -m "not online"`.

### Evidências TDD P0

Red inicial: 25 falhas e 1 sucesso na suíte nova, incluindo construção de cliente no import e segredo exposto. Uma fixture referenciava função inexistente: foi corrigida antes de validar o comportamento de elegibilidade. Segundo Red: 5 falhas do contrato de avaliação ausente. Green inicial: 26 casos de contratos passaram; regressão completa e juízo ainda pendentes.

Regressão P0 inicial: 312 passaram, 1 teste online deselecionado, 2 avisos. Juízo rodada 1 reprovou com 8,50; achados e correções em `docs/reviews/2026-10-05-exec-p0.md`. As falhas provocadas pelos contraexemplos receberam regressões antes da correção. P1 tem uma suíte Red separada, que ainda não é gate liberado.
