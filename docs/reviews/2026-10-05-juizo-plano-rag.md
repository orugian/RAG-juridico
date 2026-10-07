# Juízo adversarial formal — plano e specs do RAG Andrade Advogados

Data: 05/10/2026. Campanha: planejamento-rag-2026-10-05. Limite: três rodadas. Estado: aprovado pelo agente juiz na rodada 2, score 9,35/10; execução aguarda aprovação do plano pelo usuário.

Objetos: [specs](../specs/2026-10-05-rag-juridico-producao.md) e [plano](../../plans/2026-10-05-rag-juridico-producao.md). Contexto e políticas: [CONTEXT.md](../../CONTEXT.md) e [HANDOFF.md](../HANDOFF.md). Avaliação do planejamento, sem alegar homologação do código futuro, corpus ou infraestrutura.

## Rubric e regra de aprovação

O agente juiz deve avaliar a versão existente, encontrar contraexemplos e explicar notas por evidência verificável. As notas não são alvo a ser inflado; a hipótese inicial é que a proposta pode falhar nos seus objetivos.

| Critério | O que avaliar no planejamento / na execução correspondente |
| --- | --- |
| Natureza do RAG jurídico | Perguntas suportadas, grounding, abstenção, contratos de terceiros, literalidade e restrição de inferência |
| Arquitetura | Coerência entre corpus, chunking, embedding, recuperação, grafo, API/cache e topologia AWS; interfaces e dependências |
| Governança | Fluxos de dados autorizados, acesso interno, revisão humana, decisões, destinos/retencão e limites de responsabilidade |
| Aplicabilidade | Clareza para executar, migrações, estados, gates, decisões pendentes, escopo e resultado útil ao advogado |
| Eficiência | RAM/CPU/concorrência, orçamento de tokens/tempo/custo, reconstrução/reuso, carga e simplicidade compatível com EC2 de 16 GB |
| Acurácia | Gabarito humano, fidelidade, separação desenvolvimento/teste, medição de respostas corretas e erros críticos |
| Precisão | Relevância das evidências/seleções e das respostas, ausência de atribuição indevida, métricas com denominadores e condições |
| Rastreabilidade | Documento/arquivo/versão/hash/spans, decisões, geração/configuração, reconstrução da prova e histórico |
| Observabilidade | Métricas e estados, falhas, payload efetivamente exportado, privacidade e ausência de mutação pela instrumentação |
| Confiabilidade | Falhas fechadas, paridade/publicação, revogação/rollback/restore, indisponibilidade, regressões e recuperação |

Peso igual: 10% por critério. **Score global = média aritmética das dez notas sem arredondamento para aprovação.** Exibir também notas individuais, cálculo e justificativa; nenhum critério é omitido por conveniência. Não exigir que todas as notas individuais sejam >=9 se o usuário exigiu score global; achados altos/críticos permanecem bloqueadores independentemente da média.

Aceite desta campanha: score global >=9, nenhum achado alto/crítico aberto e parecer explícito do juiz sobre a versão avaliada. Scores não são probabilidades de ausência de falhas. No máximo três avaliações completas com score; entre avaliações, corrigir documentos e registrar diferenças. Não criar rodada adicional nem reaproveitar score antigo para texto materialmente alterado. Se não alcançar o limiar, registrar reprovado e retornar diagnóstico ao usuário, sem executar o plano.

Severidade: crítico é risco que invalida objetivo/perímetro; alto é lacuna capaz de produzir implementação aparentemente conforme que falha requisito essencial; médio é ambiguidade/localização com impacto limitado; baixo é apresentação. O juiz pode encontrar problemas novos na reavaliação e deve registrar fechamento dos achados antigos.

Parecer por rodada: versão/hashes do plano e specs; dez notas com justificativa; média; veredito; achados com ID, severidade, referência e impacto; correção/checagem esperadas; limitações da inspeção. Agente juiz realiza revisão de leitura; o coordenador corrige o conteúdo. Não emitir/armazenar raciocínio interno detalhado, apenas fundamentos auditáveis, evidências e decisões.

## Registro da campanha

| Rodada | Versão avaliada | Score | Resultado |
| --- | --- | --- | --- |
| 1 | 0.5 | 9,05 | Reprovado: J1-01 alto aberto, apesar da média >=9; J1-02 médio |
| 2 | 0.6 | 9,35 | Aprovado no planejamento; J1-01/J1-02 fechados; sem novo achado material identificado |

As duas revisões qualitativas anteriores à solicitação do usuário não pertencem a esta campanha e não fornecem nota formal.

### Rodada 1 — parecer do agente juiz

Agente: `/root/juiz_formal_plano_rag`. Snapshot de código inspecionado: `f30197080c8f2ddf2fe2e4b694e75063fe01ddfe`. Hashes SHA256 dos objetos v0.5:

- Specs: `7f3ae0e142a96bad74ffa8d786b121a5d2928f368681d9a6bf4acc715d13bbf2`.
- Plano: `40d0a3578b2390ad76381f4fc4880c8e3b0cda4e88d92b006513ab8762120fc3`.

| Critério | Nota | Justificativa do juiz |
| --- | --- | --- |
| Natureza do RAG jurídico | 8,5 | Literalidade/condições bem definidas, mas faltam modificações feitas por instrumento distinto |
| Arquitetura | 9,0 | Interfaces/índices/cache/perímetro coerentes; ligação de modificadores à prova ainda ausente |
| Governança | 9,5 | Decisões/limites, revisão humana, principal técnico e caminhos externos condicionados explícitos |
| Aplicabilidade | 8,5 | Plano executável, mas contrato-base/aditivo/distrato sem contrato operacional conjunto suficiente |
| Eficiência | 9,0 | Benchmark, budgets, concorrência, reuso e medição de RAM/transição definidos como metas |
| Acurácia | 9,0 | Gabarito independente/split/teste reservado/erros críticos; falta cenário obrigatório entre instrumentos |
| Precisão | 8,5 | Identificação e validade definidas; relevância de unidades emitidas sem medida e limiar próprios |
| Rastreabilidade | 9,5 | Origem/versão/hash/spans/configurações/decisões/geração permitem reconstrução da prova |
| Observabilidade | 9,5 | Allowlist, payload HTTP, erros/nós filhos e não interferência cobrem riscos do código |
| Confiabilidade | 9,5 | Paridade/publicação/restore/revogação e falha fechada com testes previstos |

Cálculo: `(8,5 + 9 + 9,5 + 8,5 + 9 + 9 + 8,5 + 9,5 + 9,5 + 9,5) / 10 = 9,05`. Veredito do juiz: **REPROVADO**, um achado alto e um médio, nenhum crítico identificado.

**J1-01 — Alto: relações entre instrumentos modificadores ausentes do fechamento.** Referências originais v0.5: specs linhas 37–41, 83, 121–123, 165–171, 225–230; plano P2/P3/P5/P6. Contraexemplo: base fixa honorários em R$ 10.000; aditivo aprovado muda para R$ 12.000, sem referência futura no base. Unidade do base pode passar literalidade/localização/sustentação com fechamento vazio e omitir alteração disponível. Schemas e chunker atuais não demonstram ligação modificadora; testes de classificação de aditivo não provam efeito entre instrumentos. Correção solicitada: relações aprovadas com unidades afetadas/prova/estados, lookup inverso, histórico versus condição aplicável, conflito/pendência controlados. Testes solicitados: aditivo sem referência recíproca, alteração parcial, distrato, conflito, vínculo pendente, quarentena, histórico, orçamento, cache e nova geração.

**J1-02 — Médio: relevância sem denominador/aceite próprios.** Referências v0.5: specs 216 e 290–293; plano P3/P5/P8. Contraexemplo: resposta à multa contém unidade correta mais nove cláusulas sem relação; todas são literais/autorizadas e os critérios de cobertura/validade não distinguem o excesso. Correção solicitada: métricas de relevância por unidades selecionadas/emitidas, micro/macro/estrato, com limiar e distinção entre ruído e fechamento necessário. Testes: seleção pertinente, pertinente acrescida de irrelevantes e fechamento material obrigatório.

Limites do parecer: somente leitura; sem implementação, instalação, chamadas externas, dados/credenciais reais nem reexecução da suite. O juiz confirmou ausência do índice operacional e arquivos de API vazios. Baseline 277 offline/1 online excluído é reportada da sessão anterior, não produzida nesta rodada. Aprovações históricas não fundamentam o score. Snapshot documental íntegro está preservado localmente em `data/planning_reviews/planejamento-rag-2026-10-05/rodada-1-v0.5.zip`, com checksums; não contém contratos.

Correções propostas v0.6: DATA-12 e seção 4.3 com InstrumentRelation/RelationRegistry/QueryPlan; fechamento e fingerprints sensíveis a modificadores; bloqueios de família e invalidação de cache; tarefas/gates P0/P2/P3/P4/P5/P6/P8; ACC-13 de precisão e ACC-14 de relações; composição de gabarito/split inclui instrumentos conectados. **Fechamento dos achados depende do novo parecer; não foi presumido pelo coordenador.**

### Rodada 2 — parecer final do agente juiz

Agente: `/root/juiz_formal_plano_rag`. Versão integral avaliada: 0.6. Hashes SHA256 conferidos pelo juiz no início e no fim da inspeção:

- Specs: `a98946c4d358d8e8b548656d5eeb479a43feb5b19cc24e841dd9ae4212d7e3e4`.
- Plano: `91a6f484016fea4d8db4834b4da95732d2445cc16b086169626799e6e7a34d21`.

| Critério | Nota | Justificativa do juiz |
| --- | --- | --- |
| Natureza do RAG jurídico | 9,5 | Quatro elementos/terceiros/abstenção/extrativo e distinção histórico versus condição relacionada; regra original alterada não vira resposta completa |
| Arquitetura | 9,3 | QueryPlan, relações, lookup inverso, fechamento, fingerprints/política/geração integrados; híbrido/grafo/cache/servidor com contratos coerentes |
| Governança | 9,4 | Decisões do usuário e bloqueios preservados; aditivo em review é risco restrito; efeitos exigem adjudicação e políticas pendentes são explícitas |
| Aplicabilidade | 9,2 | Contratos/tarefas/gates concretos para base/aditivo/distrato/conflito/histórico; esforço humano é medido no piloto, sem estimativa fictícia |
| Eficiência | 9,0 | Benchmark, tokenização, budgets, concorrência, reuso e isolamento definidos; fechamento ampliado é limitado/medido, sem prova de desempenho presumida |
| Acurácia | 9,3 | Gabarito independente, revisão humana, split de instrumentos relacionados e mínimos/casos obrigatórios do teste reservado |
| Precisão | 9,3 | ACC-13 mede relevância, denominadores e micro/macro; suporte obrigatório não é ruído e irrelevância compromete resposta completa/correta |
| Rastreabilidade | 9,5 | Prova/versão/hash/spans/decisões/geração e relações/resolução no manifest/auditoria reconstruíveis |
| Observabilidade | 9,5 | Allowlist/não mutação, proteção de metadata/erros/nós filhos, captura real e comparação com tracing desligado |
| Confiabilidade | 9,5 | Publicação/paridade/revogação/restore/journal/rollback fechados; mudança de vínculo invalida cache e bloqueia geração antiga |

Cálculo: `(9,5 + 9,3 + 9,4 + 9,2 + 9 + 9,3 + 9,3 + 9,5 + 9,5 + 9,5) / 10 = 93,5 / 10 = 9,35`.

**Veredito do juiz: APROVADO no escopo do planejamento.** J1-01 e J1-02 fechados documentalmente; nenhum novo achado alto/crítico ou outro achado material identificado. Critérios atendidos na rodada 2/3; terceira rodada não necessária para estes hashes.

Fechamento **J1-01**: DATA-12 e specs seção 4.3; relação de base/modificador, consulta inversa, estados, efeitos, histórico, fechamento, fingerprints, cache e policy_epoch; tarefas/gates P0/P2/P3/P4/P5/P6/P8. O cenário de base com R$ 10.000 e aditivo com R$ 12.000 deixa de ser conforme se a resposta ignorar o modificador. Vínculo aprovado traz as provas; conflito/pendência/quarentena relevante/budget insuficiente levam a esclarecimento/abstenção. Data recente não aprova efeito, histórico não vira regra vigente, e mudanças de vínculo não podem ser ignoradas por cache/rollback.

Fechamento **J1-02**: métricas definidas nas specs seção 10/ACC-13 e gates P6/P8. Uma unidade responsiva e nove sem relação reduzem precisão e não passam a pergunta como completa/correta. Suporte material obrigatório é preservado; duplicação não aumenta acertos; denominador vazio é N/A; mínimos de seleções/unidades e gabarito independente mantidos.

Limites: leitura integral da v0.6 e rubric, verificações locais/hashes/código-base, sem edições pelo juiz, implantação, implementação, instalação, chamadas externas ou dados/credenciais reais. Não houve reexecução de testes nesta campanha; 277 offline/1 online excluído permanecem a baseline reportada. Novos contratos/relações ainda não estão implementados, e os gates humanos/OpenRouter/AWS não foram cumpridos. A aprovação se aplica somente ao conteúdo dos dois hashes registrados; score de desenho não aprova dados, produto ou infraestrutura.

Snapshot v0.6 preservado em `data/planning_reviews/planejamento-rag-2026-10-05/rodada-2-v0.6.zip`, com checksums. O coordenador manteve os dois documentos avaliados sem alteração após o parecer para preservar a identidade da versão aprovada.

## Execução posterior

O usuário fará a aprovação do plano depois da síntese desta campanha. Até essa manifestação, o trabalho se limita a revisão e correção documental. Após aprovação, cada P0–P9/subentrega com gate próprio terá seu registro de TDD e revisão de agente juiz, usando esta rubric e limiar >=9, além do gate técnico/documental específico.
