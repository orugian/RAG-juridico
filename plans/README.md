# Planejamento ativo

| Documento | Finalidade | Estado |
| --- | --- | --- |
| [Plano — RAG jurídico verificável](2026-10-05-rag-juridico-producao.md) | Dez entregas, dependências, TDD, gates de juiz >=9 e recuperação | v0.6 aprovada pelo juiz, 9,35; execução aprovada pelo usuário |
| [Specs — RAG jurídico verificável](../docs/specs/2026-10-05-rag-juridico-producao.md) | Produto, dados, relações entre instrumentos, geração, avaliação e AWS | v0.6 aprovada no escopo documental; hashes preservados |
| [Juízo adversarial formal](../docs/reviews/2026-10-05-juizo-plano-rag.md) | Rubric, scores, achados, versões e condições de aceite | Rodada 1: 9,05/reprovada; rodada 2: 9,35/aprovada; terceira não necessária |

Decisões de 05/10/2026: OpenRouter com política de dados antes de consultas reais; acervo comum para colaboradores internos; revisão humana adiada para o corpus candidato. Execução em [registro próprio](../docs/execution/2026-10-05-execucao-rag.md): P0/G0 9,14, P1/G1 9,09 e P2A técnico 9,15 aprovados; G2/P2B pendente. [Checkpoint de embedding](../docs/execution/2026-10-05-checkpoint-embedding.md) atingido, antes da escolha/download de modelo.

Atualização em 06/10/2026: checkpoint cumprido; usuário escolheu Qwen3-Embedding-0.6B. [P3A técnico](../docs/execution/2026-10-06-p3a-qwen.md) aprovado pelo juiz com 9,17/10 na tentativa 2; 440 testes. G3 pleno permanece pendente; o encoder CPU ainda excede a meta de latência da recuperação.

Correção de nomes/datas e perfil BM25 em [registro próprio](../docs/execution/2026-10-06-correcao-lexical-temporal.md), aprovada tecnicamente com 9,18/10 na rodada 4, LT-J1 fechado; 570 testes passaram. Preservadas as reprovações 8,63/8,80/8,87 e a extensão autorizada para máximo cinco; quinta não necessária. A antiga sugestão de iniciar P3B pertence a esse fechamento histórico, não à próxima sessão.

Encerramento em 07/10/2026: P3B e P4 técnicos foram concluídos e aceitos; a próxima entrega é **P5 técnico sintético, ainda não iniciado**. [HANDOFF.md](../docs/HANDOFF.md) contém o estado corrente, mapa, critérios/fronteiras P5 e prompt copiável; [registro central](../docs/execution/2026-10-05-execucao-rag.md) e pareceres conservam evidências/histórico. Não promover corpus real ou liberar produção por esses aceites.

[CONTEXT.md](../CONTEXT.md) conserva escopo/políticas, plano/specs v0.6 conservam requisitos congelados e HANDOFF é a entrada atual de retomada. Estados antigos não substituem a identidade/parecer correspondente; revisões humanas, relevância documental, política de dados OpenRouter, G3 pleno/G4 operacional e SLO/Linux/AWS seguem pendentes.
