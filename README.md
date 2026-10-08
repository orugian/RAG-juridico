# RAG Andrade Advogados

Backend Python/FastAPI em desenvolvimento para consulta extrativa e auditável do acervo contratual interno: honorários, contratos diversos, instrumentos de clientes com terceiros e relações base/aditivos/distratos. A prova deve preservar instrumento, partes efetivas, localização e transcrição literal; ausência de suporte exige esclarecimento ou abstenção, não interpretação jurídica inventada.

## Retomada do desenvolvimento

P6 técnico sintético foi aprovado (9,35/10, R2). **P7 — API HTTP/FastAPI, cache governado, streaming e concorrência foi aprovado no subgate técnico sintético (9,37/10, Rodada 2 final)**, sem liberação de corpus real nem API de produção. [Contrato HTTP e montagem explícita](docs/P7_API.md), [evidências P7](docs/execution/2026-10-07-p7-evidencias.md), [parecer P7](docs/reviews/2026-10-07-exec-p7.md). Próxima frente: **P8 — Homologação ponta a ponta (preparação técnica sintética)**. Estado, aceites, bloqueios, mapa de código e **prompt de retomada** estão no [HANDOFF](docs/HANDOFF.md).

Leia explicitamente as instruções AGENTS.md/RTK.md aplicáveis e, depois:

1. [CONTEXT](CONTEXT.md): escopo e políticas do produto; distinguir arquitetura-alvo de implementação.
2. [HANDOFF](docs/HANDOFF.md): estado corrente e contrato de retomada.
3. [Plano v0.6](plans/2026-10-05-rag-juridico-producao.md) e [specs v0.6](docs/specs/2026-10-05-rag-juridico-producao.md): requisitos congelados e gates completos.
4. [Registro de execução](docs/execution/2026-10-05-execucao-rag.md) e pareceres vinculados: histórico e evidências verificáveis. Confirme identidades contra os arquivos antes de editar; não presumir leitura automática pelo agente.

Código em app/, testes permanentes em tests/, documentação em docs/ e dados/modelos locais em data/ ignorado pelo Git. Preserve o worktree acumulado; não executar limpeza/reset nem exibir .env/segredos. Qwen local está escolhido/fixado. Revisão humana, política OpenRouter, G3 pleno/G4 operacional e qualificação Linux/AWS continuam pendentes. Não executar sync, publicação, chamada externa ou infraestrutura por causa de comandos históricos da documentação.
