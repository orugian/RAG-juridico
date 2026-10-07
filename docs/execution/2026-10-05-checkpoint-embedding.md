# Checkpoint P3 — decisão de embedding

**Atualização D05 — 06/10/2026:** checkpoint cumprido; o usuário decidiu “Vamos seguir com o Qwen”. Qwen3-Embedding-0.6B foi fixado na revisão `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, baixado e executado localmente. A entrega P3A está em [registro próprio](2026-10-06-p3a-qwen.md), com TDD, inventário real de tokens e microbenchmark sintético. Juiz técnico aprovou exclusivamente P3A com 9,17/10 na tentativa 2; 440 testes passaram. G3 pleno/comparação/gabarito/chunker governado e desempenho AWS continuam pendentes. O restante deste documento registra o checkpoint anterior à decisão.

O usuário pediu retorno antes de decidir embedding. Nenhum modelo semântico foi escolhido, peso baixado ou corpus enviado para serviço de embedding. A execução para nesta fronteira, após o subgate técnico P2A; G2 pleno continua pendente de revisão humana.

Gates concluídos: P0 9,14/10 (rodada 3), P1 9,09/10 (rodada 3), P2A 9,15/10 (tentativa 2). Regressão completa: 392 testes passaram, um online deselecionado. Lote v2: 287 arquivos, 14.791 blocos candidatos, 202 quarantined/85 failed; zero aprovação/publicação.

## Decisão a preparar com o usuário

Comparar ao menos dois encoders locais, fixando modelo/revisão/licença/tokenizer/dimensão/normalização e prefixos. O cenário de produção planejado é AWS Linux/EC2 com 16 GiB; o encoder e o índice devem caber junto com API, busca e margem operacional. Uma aceleração disponível na máquina de desenvolvimento não comprova desempenho/capacidade da topologia AWS.

Perfil local observado, somente leitura: Windows, Python 3.13.13, RAM física 31,8 GiB, Intel i7-10750H (6 núcleos/12 processadores lógicos), GPU NVIDIA RTX 2060 e vídeo integrado. `nvidia-smi` reportou 6144 MiB de memória dedicada; memória disponível durante carga e compatibilidade do runtime precisam ser medidas caso se use GPU. Não provisionamos infraestrutura AWS.

Critérios para o experimento: recuperação de cláusulas em português, nomes e identificadores com busca híbrida; contexto e tokenizer sem truncamento; fidelidade e fechamento das unidades jurídicas; RAM de pico, latência e throughput no perfil de produção; licença e execução local com revisão fixada. Escolha exige evidência, não apenas tamanho do modelo ou leaderboard genérico.

## O que o staging permite e o que falta

O staging fornece inventário, texto candidato, localizadores não revisados e riscos por arquivo, para organizar fixtures e futura revisão. Não é corpus homologado. Todos os includes ainda dependem de aceite humano para se tornarem evidência pública do RAG; fontes sem texto aguardam ferramenta/correção.

P3 pode preparar infraestrutura de tokenizer/chunking e testes sintéticos depois deste checkpoint. Um benchmark de qualidade jurídica comparativo exige gabarito documental de desenvolvimento aprovado e famílias isoladas; fixtures sintéticas não substituem os mínimos documentais. G3 não poderá alegar qualidade semântica comprovada apenas com esses fixtures.

As perguntas para a decisão são: confirmar o orçamento operacional disponível na EC2 planejada e a prioridade relativa entre qualidade, latência e custo; definir os candidatos e o protocolo de comparação. As specs exigem tokens reais do encoder, chunking sem perda silenciosa e prova canônica completa independente dos chunks. OpenRouter é o gerador proposto e permanece bloqueado até política de dados; essa escolha não define o embedding.
