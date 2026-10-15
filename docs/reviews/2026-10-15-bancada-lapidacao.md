# Parecer independente — Pós-MVP: bancada de lapidação, LibreOffice, latência e publicação (15/10/2026)

Entrega julgada: [registro](../execution/2026-10-15-bancada-lapidacao.md); commits `cbe4263` e `92795b5`. Juiz: subagente independente, sem participação na implementação e sem edição de código; sem SSH; 8 chamadas ao serviço vivo; não disparou publicação, avaliação nem gravou decisões no serviço vivo.

## Evidências verificadas pelo juiz

- Identidades aceitas intactas (`git diff 277a0e1 HEAD -- app/` não toca arquivos protegidos); `config.py` e `embeddings/qwen.py` só com a mudança declarada `cpu_threads` 6 → 4.
- Testes: **123 passaram** (focais + `test_p4_generations` + `test_p7_integration`, Qwen real).
- Serviço vivo: `/health` 200; `/ready` 200 (consulta); `/wb/api/overview` 401 sem chave, 403 com chave de consulta, 200 com operador (225 docs, 4.475 unidades, 22 reservados, 19 rejeitados, 3.786 referências abertas, `pub-20261015T151454`); `/ui` 200; `/chat` filtrado em `doc:4889` respondido com citação literal idêntica à unidade oferecida no rastreio; rastreio com unidades oferecidas, selecionada e tempos por etapa.
- VPS pela tailnet: portas 37777 (claude-mem) e 8443 (Funnel, IP e nome público) recusam conexão; 8790 aberta.
- Probe adversarial próprio: modo estrito aprova só o documento sem pendências; referência não resolvida falha fechado; sem partes vira reservado; ledger dos rejeitados/reservados vazio; auto-vínculo mantém o literal sem fabricar fechamento; excluído nunca gravado; códigos de falha sem texto contratual.
- Honestidade: `LIMITACOES.md` completo; coleção com token removida da árvore, histórico (`1daa036`) ainda com o token, declarado; derivado LibreOffice ligado à identidade da fonte por `conversion_sha256`, com teste de citação a partir do derivado.

## Notas

| Critério | Nota |
|---|---|
| Natureza RAG | 9,5 |
| Arquitetura | 9,4 |
| Governança | 9,4 |
| Aplicabilidade | 9,3 |
| Eficiência | 9,4 |
| Acurácia técnica | 9,4 |
| Precisão técnica | 9,4 |
| Rastreabilidade | 9,5 |
| Observabilidade | 9,2 |
| Confiabilidade | 9,4 |
| **Média** | **9,39** |

**Decisão: APROVADO (rodada 1).** Nenhum achado alto/crítico.

Achados baixos/informativos (declarados ou aceitos): rotas `/wb/api` fora do perímetro P7, chave única e revisor declarativo; rastreios com texto contratual em claro na VPS, sem retenção definida; 3.786 referências auto-vinculadas sem expansão de fechamento; token M-Files no histórico e credenciais não rotacionadas; cópia do journal pode falhar fechado sob bloqueio concorrente e o cache da projeção do Chroma afeta só ranking.

Revisão adversarial anterior (subagente distinto): SEM_ALTOS; um médio (falha ao religar o serviço não propagada) e um baixo (publicações concorrentes) corrigidos com testes.

Este parecer cobre somente a etapa técnica. Não aprova revisão jurídica (G5/G8), G4 operacional, D04, SLO nem segurança do host.
