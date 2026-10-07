# Dataset de avaliação — esqueleto P0

O contrato executável está em `app/evaluation/dataset.py`. Casos reais e gabaritos ficam em `data/evaluation/`, ignorado pelo git. Não há gabarito humano preenchido nem homologação neste esqueleto.

O revisor deve comparar cada fonte original, independente da saída do parser, e registrar: pergunta, família, rota, ação esperada, unidades primárias relevantes, fechamento obrigatório, fatos aprovados e ID da decisão. Família agrupa duplicatas e base/aditivos/distratos; todo o grupo pertence ao mesmo split. O gate valida os grupos informados; não descobre automaticamente parentesco oculto. A adjudicação das famílias é requisito de P2.

Antes de ajustar recuperação: congelar >=50 casos de desenvolvimento e >=100 de teste reservado. No teste reservado: >=20 negativos, >=20 condições em múltiplos spans, >=10 ambiguidades, >=20 seletores por identificador, >=60 respondíveis, >=5 por rota presente. Tags podem se sobrepor. Casos obrigatórios: aditivo sem referência recíproca no base, distrato, alteração parcial, conflito, associação pendente, modificador em quarentena, histórico explícito e orçamento insuficiente. Não inspecionar o teste reservado para escolher parâmetros.

Precisão de seleção = unidades primárias relevantes / unidades selecionadas. Precisão de emissão = unidades relevantes ou de fechamento obrigatório / unidades exibidas. Medir micro/macro; vazio é N/A. Completação inclui condições e exceções; abstenção em caso respondível conta como erro. Recall@10 é medido antes da expansão; medir cobertura após expansão separadamente. Limiares e composição completos permanecem nas specs v0.6.

As fixtures dos testes são sintéticas e verificam os gates; não satisfazem o volume/revisão exigido para homologação.

O aceite estrutural exige snapshot único, escopo autorizado, plano esperado (incluindo seleção, escopo documental e data), instrumentos, spans e relações pertinentes. Repetir pergunta no mesmo contexto/família não aumenta o volume; casos sintéticos não entram nos mínimos documentais. Ambiguidade crítica exige ação de esclarecimento, negativo exige abstenção e condições exigem spans distintos/fechamento. Todos os cenários obrigatórios de relação devem estar representados no reservado, ainda que uma lacuna do acervo precise de fixture sintética separada.

`validate_dataset(acceptance=True)` valida composição/estrutura, não consulta o ledger nem comprova que uma pessoa revisou a fonte. A homologação P8 deverá resolver os IDs de decisão, unidades/spans/relações e origem documental no corpus congelado, além de medir as métricas. As fixtures marcam origem documental somente para testar esse contrato estrutural; não são contadas como evidência real.

O contexto de seleção compara conjuntos ordenados de IDs canônicos; permutar filtros ou trocar máscara/caixa não aumenta a diversidade de perguntas. Somente rotas documentais exigem cinco casos documentais reservados. Fixtures de uma rota ausente são verificadas separadamente e não criam esse requisito. P8 fornecerá `required_documental_routes` a partir do inventário congelado para impedir omissões; sem esse argumento, a checagem conhece apenas as rotas documentais declaradas nos casos.
