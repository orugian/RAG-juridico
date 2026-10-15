# Decisões de infraestrutura — Bloco 2/P9 (VPS Hostinger)

Registro das decisões do usuário em 15/10/2026, após a qualificação Linux sintética ([registro](2026-10-14-bloco2-qualificacao-linux.md)) e o [relatório de vulnerabilidade](../../../../Downloads/Relatorio_Vulnerabilidade_VPS_srv1532181_2026-10-15.md) (arquivo fora do repositório, na pasta Downloads). Estas decisões orientam o desenho de P9; **não** são aceite de entrega e não liberam corpus real nem serviço de produção. Plano/specs v0.6 congelados permanecem autoridade.

## D-INFRA-01 — Sistema operacional alvo: Ubuntu 26.04 LTS

A VPS roda Ubuntu 25.10, sem suporte desde 09/07/2026 (V6 do relatório). **Decisão do usuário:** construir sobre a premissa de que a aplicação roda na versão atualizada. Todo o desenho de P9 assume **Ubuntu 26.04 LTS**.

Consequências:
- Antes de qualificar o serviço, tirar snapshot no painel Hostinger e atualizar (`do-release-upgrade`) ou provisionar VPS nova já em 26.04.
- A qualificação Linux de 14/10 foi feita em 25.10 (glibc 2.42, kernel 6.17). Diferença de ambiente exige **repetir** a regressão e o benchmark do encoder no SO atualizado antes do aceite — a mesma regra do handoff para o mecanismo de rename.
- Pin de versões maiores (Python 3.13.13, torch 2.8.0+cpu) continua pelo `uv.lock`, independente do SO.

## D-INFRA-02 — Desempenho sem comprometer a infraestrutura

Problema medido em 14/10: a stack usa ~3,9 GiB de RSS por processo e o encoder, com o default de 6 threads em 4 vCPU, teve p95 de 1,921 s. A suíte em processo único chegou a 9,3 GiB e deixou o host com 805 MB livres, sem swap — risco de OOM para os co-tenants (bots, Hermes, Docker).

Abordagens escolhidas, da menor para a maior intervenção. Nenhuma delas é SLO; o SLO depende de carga sustentada medida no alvo.

| # | Abordagem | Ganho | Custo/risco | Toca código do produto? |
|---|---|---|---|---|
| 1 | `EMBEDDING_CPU_THREADS=4` (igual aos vCPU) | p95 1,921 → **0,806 s** no microbench | Muda o fingerprint do encoder → construir gerações com o perfil final | Não, é configuração. Mas o perfil entra na identidade da geração |
| 2 | `MALLOC_ARENA_MAX=2` no serviço | RSS estável, menos fragmentação do glibc; p95 0,771 s medido | Nenhum relevante | Não, variável de ambiente |
| 3 | zram (swap comprimido em RAM, ~4–8 GiB) | Margem contra OOM sem disco lento | ~CPU na compressão | Não, SO |
| 4 | `MemoryMax` por serviço no systemd (ex.: 6 GiB no RAG) | OOM fica contido no serviço, não derruba co-tenants | Precisa dimensionar | Não, systemd |
| 5 | Um único processo encoder reutilizado (serviço long-lived), não um por requisição | Carrega o modelo uma vez (~3,7 GiB), não repete | Já é o desenho do serviço FastAPI | Não |
| 6 | Fixture Qwen `scope="session"` nos testes | Suíte roda em processo único sem estourar RAM | Altera teste aceito → nova identidade + reverificação | Sim, só em `tests/` |

**Preservar desempenho sem comprometer a infra = 1 + 2 + 4 + 5**: fixar 4 threads e `MALLOC_ARENA_MAX=2`, limitar a memória do serviço no systemd e manter um processo encoder vivo. zram (3) é recomendado no host. A opção 6 só se o integral em processo único for exigido como gate; é mudança de teste e exige nova identidade.

Observação de hardware: a CPU tem `avx512_bf16` e os pesos são `bfloat16`, mas o encoder roda FP32 por decisão fixada (determinismo/estabilidade de 1 ULP no armazenamento cosine). Não trocar para bf16 — é mudança de contrato do encoder, não otimização de infra.

## D-INFRA-03 — Isolamento do RAG no host compartilhado

Enquanto a VPS hospedar os agentes (Hermes, bots, OpenClaw) e `claw` estiver no grupo `docker`, não há fronteira de confiança (V1/V2 do relatório). Decisão (item 6 do usuário, "seguimos com recomendação sua"):

- **Preferencial:** VPS dedicada ao RAG, sem agentes nem Docker com acesso ao host.
- **Alternativa aceitável:** no mesmo host, usuário Linux `rag` **sem sudo e fora do grupo docker**, home 700, serviço `systemd --user` (há `Linger=yes`) ouvindo só em `127.0.0.1` e exposto, se necessário, apenas pela tailnet — nunca por Funnel. Esta alternativa **só é válida depois** de remover `claw` do grupo docker; caso contrário qualquer processo de `claw` lê os dados do `rag` via container.

Em ambos os casos: tracing externo desligado, sem segredo no repositório, corpus real só após aprovação registrada no ledger.

## D-INFRA-04 (D04) — Armazenamento externo do journal: Supabase (Postgres)

O journal de política/revogações e o ledger de revisões são hoje SQLite no mesmo disco do serviço — sem domínio de falha independente e sem restore testado. O usuário autorizou Supabase ou Firebase, priorizando custo zero/baixo.

**Decisão: Supabase (Postgres gerenciado).** Justificativa:

- O sistema já é relacional e transacional: ledger append-only com triggers que bloqueiam UPDATE/DELETE (`app/ingestion/review_store.py`) e journal de épocas com chave primária e digest (`app/retrieval/policy.py`, tabelas `policy_identity/policy_events/policy_head`). Postgres mapeia direto; Firestore (documento/NoSQL) exigiria reescrever a semântica transacional e as travas.
- Firebase: o Firestore tem cota gratuita no plano Spark, mas o Cloud Storage passou a exigir o plano **Blaze** (pago, com cartão) desde fev/2026. Modelo relacional não é o forte.
- Supabase: plano gratuito com Postgres de 500 MB, suficiente para journal/ledger (metadados, não os contratos). Região São Paulo disponível.

**Limitações do plano gratuito do Supabase que viram requisito de desenho (fontes públicas, out/2026):**
- **Pausa após 7 dias de inatividade**, com cold start de 10–30 s. Para um journal de revogações isso é inaceitável. Mitigação: health check periódico mantendo atividade, ou assumir o custo do plano Pro (US$ 25/mês) quando entrar em produção.
- **Sem backup no plano gratuito** (retenção zero; Pro guarda 7 dias). Portanto o restore do D04 **não** pode depender só do Supabase: é obrigatório um dump periódico (`pg_dump` via CLI) para um terceiro local, com teste de restore. Isso atende ao requisito de "domínio de falha independente" melhor do que confiar no plano gerenciado.

**Fronteira de dados obrigatória:** vão para o Supabase apenas metadados de governança (épocas, digests, decisões de revisão, identidades) — **nunca** texto de contrato, trecho literal ou PII. Confirmar que nenhum payload do journal/ledger carrega literal antes de publicar. Em dúvida, cifrar no cliente o campo de payload.

**Restore do D04 (a implementar e testar em P9):** âncora durável fora do host do serviço (o Postgres gerenciado) + dump periódico independente; provar reconstrução de ledger+journal+checkpoint em processo novo, e que uma revogação feita no Supabase invalida a saída na fronteira de retorno mesmo após restart do serviço.

## D-INFRA-05 (D01) — Provedor de LLM: OpenRouter com retenção zero

`provider_policy_approved=False` bloqueia a geração real (`app/generators.py`, `app/agent.py`). O usuário aprovou **consultas pelo OpenRouter**. A base já aponta para lá (`openai_base_url = https://openrouter.ai/api/v1`, modelos `qwen/qwen3.8-flash` e `deepseek/deepseek-v4.1-flash`).

Condições para ligar `provider_policy_approved=True` com segurança (o produto manda trechos de contrato ao provedor na geração):
- Exigir **Zero Data Retention** no OpenRouter. ZDR pode ser imposto por conta, por grupo de modelos ou por requisição; requisições fora da política falham com 404 (fonte: docs OpenRouter, out/2026). Alternativa/complemento: `provider.data_collection = "deny"`, que roteia só para provedores que não armazenam nem treinam com os dados.
- Preferir rota ZDR por requisição no cliente, para não depender só da configuração da conta.
- Registrar a decisão como política D01: quais modelos, ZDR obrigatório, sem fallback para provedor que retém dados. Sem rota ZDR disponível, abster — nunca cair silenciosamente em provedor que retém.
- Chave do OpenRouter só em `.env` (já no `.gitignore`); rotacionar a atual, que está exposta na nota do OneDrive.
- Isso cobre a geração. A **avaliação de qualidade jurídica** (precision/recall) continua dependência humana do Bloco 3; ligar o provedor não a substitui.

## D-INFRA-06 — Observabilidade: LangSmith permanece; PageIndex/DeepSeek-DSH/Supabase-como-storage-de-arquivos são de outro projeto

O `AGENT.md` (não versionado) cita PageIndex, harness DeepSeek DSH e Supabase genérico. O usuário confirmou: **o projeto usa LangSmith** (já integrado em `app/telemetry.py`, `app/monitoring.py`, `app/security.py`, com redação de PII e transporte protegido, hoje desligado por padrão). Os demais tópicos do `AGENT.md` pertencem a outro projeto e **não** entram no escopo do RAG. Nenhuma mudança de arquitetura para PageIndex.

Quando LangSmith for ligado em produção: manter a redação de PII já implementada, confirmar que nenhum literal de contrato sai no trace e tratar a chave como segredo em `.env`.

## Pendências que estas decisões abrem para P9

1. Atualizar o SO (ou VPS nova) e **repetir** regressão + benchmark no alvo.
2. Criar o usuário/serviço isolado conforme D-INFRA-03, após remover `claw` do grupo docker.
3. Implementar o adaptador de journal/ledger no Supabase respeitando a fronteira de dados, mais o dump independente e o teste de restore.
4. Implementar a imposição de ZDR/`data_collection=deny` no cliente OpenRouter e registrar a política D01.
5. Rotacionar todas as credenciais do relatório de vulnerabilidade.
6. Só então: juiz independente da entrega P9, dez critérios, média ≥9.
