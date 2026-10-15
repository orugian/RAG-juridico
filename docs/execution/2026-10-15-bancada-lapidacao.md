# Pós-MVP — bancada de lapidação, LibreOffice, latência e publicação (15/10/2026)

Decisões do usuário (15/10): manter VPS e usuário `claw`; Ubuntu será atualizado em breve; uma única chave; desativar e remover o claude-mem; fechar o Funnel; tratar os DOC/RTF com LibreOffice; reservar os documentos sem partes; reduzir latência (4 threads) se não houver prejuízo; interface simples para lapidação; revisão de partes/referências feita pelo próprio usuário sob demanda. Este registro **não** é parecer de juiz.

## Feito

| Frente | Resultado |
|---|---|
| Limitações | `LIMITACOES.md` local (ignorado pelo Git), cada item com efeito, causa e abordagem; atualizado com o que foi resolvido |
| Git | Commit `277a0e1` enviado a `origin/main`; coleção Postman com token removida da árvore e ignorada. **Histórico ainda contém o token (commit `1daa036`)**: reescrita preparada, exige *force push* — aguarda autorização |
| VPS | Funnel `:8443/recon` desligado (só resta o `serve` tailnet-only do OpenClaw); claude-mem parado, porta 37777 fechada, registros e arquivos removidos; backup em `~/backups/claude-mem-20261015T112718` (600) |
| Latência | Gargalo medido por etapa: recuperação relia e revalidava os 3.769 vetores do Chroma a cada pergunta (5–8 s). `ServingVectorStore` verifica a projeção uma vez (revalida a cada 10 min ou se o arquivo mudar; linhas devolvidas continuam conferidas). Resposta 7–10 s → **3,5–4,6 s** (1 usuário) |
| Concorrência | 3 workers, fila 6, limite 120 req/min por chave (com chave única, o limite antigo de 20/min gerava 429 com 5 usuários) |
| Encoder | `cpu_threads` 6 → 4 (= vCPU da VPS; p95 do encoder 1,92 s → 0,81 s no microbenchmark). Muda a identidade do encoder: exige nova geração (em andamento) |
| LibreOffice | 26.8.1 oficial da The Document Foundation (pacote `.deb` extraído no home, sem root), SHA-256 conferido com o publicado. **64/64** DOC/RTF/WordPerfect convertidos; DOCX derivado retido em `data/conversions` e com hash na identidade da fonte (`conversion_sha256`); 61 com partes |
| Reservados | Documentos sem partes não são aprovados; ficam listados (aba Pendências) com prévia do texto e formulário de partes |
| Publicação | `app/publish.py`: ledger novo por publicação + cópia do journal vivo, decisões da bancada aplicadas, build/promote pelo gerenciador aceito, **cache de embeddings** por texto e identidade do encoder (republicações recalculam só o que mudou), troca com ~1 min fora do ar; fontes bloqueadas no journal nunca são republicadas |
| Bancada | `http://100.118.65.94:8790/ui`: Consultar (com busca de contrato e avaliação), Rastreios (o que foi recuperado, oferecido ao modelo, escolhido e citado; tempos), Avaliação (casos e execuções), Documentos (partes, referências, relações), Pendências (reservados, rejeitados, sem texto), Publicar |

## Primeira publicação com a nova pipeline (em andamento)

`pub-20261015T151454`, iniciada 12:14 como unidade isolada (`MemoryMax=5G`), sem ativação automática; o MVP continua no ar até a troca.

| Etapa | Valor |
|---|---|
| Staging | 266 documentos com texto (eram 202), 21 sem texto (PDF/DOCX com falha) |
| Bundle | **225 fontes** (eram 174), 4.475 cláusulas, 4.667 trechos, 22 reservados (sem partes), 19 rejeitados, 0 relações |
| Embedding | ~16 trechos/min com 4 threads a 350% de CPU; término estimado ~17h15 |

## Testes e revisão

- Regressão offline integral local (antes das últimas correções): **1.848 passaram**, 1 erro de setup (timeout do servidor Chroma em loopback em `test_p8_admission`, código não alterado); o arquivo reexecutado isolado: **27/27 verdes**.
- Testes novos/alterados: `test_corpus_build.py` (13), `test_publish.py` (8), `test_workbench.py` (8), `test_serving.py` (8), `test_loadtest.py` (3) e ajustes em `test_service.py`/`test_client.py`. Conjunto focal após as correções: **47 verdes**.
- Revisão adversarial independente: **SEM_ALTOS** (88 testes focais). Médio corrigido: falha ao religar o serviço após a troca não era propagada (agora `activation_failed`). Baixo corrigido: duas publicações podiam ser disparadas em sequência (agora reserva atômica no workbench). Baixos aceitos: cópia do journal durante o build pode falhar fechado se houver bloqueio concorrente; cache da projeção do Chroma por inode/tamanho afeta só ranking (citações vêm do bundle canônico); decisões órfãs inertes.

## Como acessar a bancada

Com a Tailscale ligada no Windows, não é preciso comando: abrir **http://100.118.65.94:8790/ui** no navegador, colar a **chave de operador** (`RAG_MVP_OPERATOR_KEY` do `.env` local; serve para consultar e revisar), informar o nome de revisor e salvar. O navegador marca HTTP como "não seguro", mas o tráfego vai cifrado pelo WireGuard da Tailscale e a porta não existe no IP público.

Alternativa por túnel SSH (se o navegador bloquear algo): `ssh -N -L 8790:100.118.65.94:8790 claw@100.118.65.94` e abrir `http://localhost:8790/ui`.

## Fluxo de lapidação

1. **Consultar** e marcar cada resposta como correta ou com problema (tipo + comentário). Marcar "transformar em caso" grava a pergunta como caso de avaliação.
2. **Rastreios** → filtrar "com problema": ver se o contrato certo foi recuperado (trechos oferecidos), se o modelo escolheu certo (verde) e o tempo de cada etapa. Daí sai o diagnóstico: recuperação, seleção, partes, referência ou relação.
3. **Documentos**: corrigir título/partes, vincular referências ("Cláusula X") à cláusula certa, marcar referências a leis como externas, cadastrar aditivo/distrato → contrato-base com as cláusulas envolvidas.
4. **Pendências**: informar partes dos reservados.
5. **Publicar** com um motivo; o índice é refeito com o cache (minutos quando só partes/referências mudam).
6. **Avaliação** → rodar os casos e comparar a taxa de acerto com a execução anterior.

## Pendências

Ver `LIMITACOES.md`. Principais: autorizar a reescrita do histórico do GitHub; revisão humana de partes, referências e relações; 20 PDFs digitalizados (OCR); segurança do host; D04/Supabase; atualização do Ubuntu e requalificação; juiz independente desta etapa após a troca para a nova geração.
