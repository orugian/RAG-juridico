# Juízo independente — correção lexical/temporal

Data: 06/10/2026. Juiz: `/root/revisao_plano_rag`. **Tentativa 1 de no máximo 3.** Esta é uma entrega nova; a nota 9,17 de P3A não é reutilizada. Escopo técnico: datas absolutas candidatas e seus contextos/spans, normalização lexical comum, enriquecimento local de busca, persistência compatível de BM25 e integração diagnóstica. Não inclui aprovação jurídica, G3 pleno, corpus/índice real, publicação P4, autorização P5 ou desempenho AWS.

## Resultado da tentativa 1

**Não aprovado: média 8,63/10; um achado alto aberto.** Nenhum achado crítico encontrado. O achado foi informado ao Head durante a revisão; a implementação permaneceu congelada. O juiz não participou das correções nem escreveu código/testes/staging.

| Critério técnico | Nota |
| --- | ---: |
| Natureza do RAG | 9,0 |
| Arquitetura | 8,9 |
| Governança | 9,3 |
| Aplicabilidade | 9,0 |
| Eficiência | 9,0 |
| Acurácia técnica | 7,4 |
| Precisão técnica | 7,2 |
| Rastreabilidade | 9,2 |
| Observabilidade | 9,1 |
| Confiabilidade | 8,2 |

As notas avaliam o código deste subgate. Não representam precisão jurídica do lote real. Calendário, literalidade e consistência entre coleção/blocos estão bem protegidos; entretanto, a classificação não assertiva reproduz um falso positivo material da própria entrega, propagado ao resumo e ao cabeçalho de busca.

## LT-J1 — alta: negações e hipóteses explícitas promovidas a assinatura

Referências: `app/ingestion/temporal.py`, definições de `_NONASSERTED` e `_CURRENT_SIGNATURE`, checagens em linhas 144–161 e `summarize_execution_date` em 187–191. A regra reconhece alguns marcadores, como não/se/caso/será, mas não nunca/jamais/teria/poderá/deverá. A expressão ampla de assinatura atual aceita os verbos assinado/firmado mesmo nesses contextos.

Contraexemplos sintéticos independentes:

- `Este contrato nunca foi assinado em 10/03/2024.`
- `Este contrato jamais foi firmado em 10/03/2024.`
- `Este contrato poderá ser assinado em 10/03/2024.`
- `Este contrato deverá ser assinado em 10/03/2024.`
- `Este contrato teria sido assinado em 10/03/2024.`

**Todos os cinco retornam kind=signature, uncertainty_flags=[] e execution_date=2024-03-10.** Todos passam por ParsedDocument.model_validate e produzem cabeçalho sintético com `signature = 2024-03-10`. O juiz verificou a propagação completa de extração → schema → chunker. Os exemplos acentuados foram repetidos usando escapes Unicode para eliminar interferência do encoding de stdin do PowerShell. As formas nunca/jamais/teria também foram reproduzidas em ASCII puro.

Isso não é apenas falta de gabarito humano: são negações/previsões/hipóteses explícitas que contradizem o contrato técnico da correção de contexto não assertivo. O estado pending_review impede liberação jurídica, mas não corrige a atribuição técnica errada nem sua propagação ao resumo e à busca. Os validadores conferem consistência com o extrator; não detectam um erro semântico que o próprio extrator reproduz.

Correção exigida: reconhecer os marcadores de negação, modalidade, previsão e hipótese no contexto local, classificando essas menções como unknown com sinalização não assertiva e execution_date=None. Preservar calendário, literal, offsets e menção. Não resolver removendo a data nem presumindo assinatura a partir do bloco. Acrescentar regressões dos cinco casos e variantes/sufixos pertinentes, mantendo controles positivos de assinatura explícita afirmativa e fecho heurístico já sinalizado. Repetir diagnóstico agregado sob nova identidade, preservando o relatório desta rodada como histórico.

Não foram identificados outros achados altos/críticos. A heurística de fecho continua suscetível a falsos positivos explicitamente sinalizados: o probe `Saldo devedor, 10/03/2024.` retorna closing_line_candidate. Trata-se de confirmação do limite já declarado de cidade/data sem geocodificação; não foi contado como um segundo bloqueio. Não interpretar os 100 fechos do relatório como 100 assinaturas corretas.

## Verificações independentes

- **97 testes focais passaram, dois avisos de depreciação, em 6,80 s**, abrangendo temporal, lexical, chunker, metadata e hybrid. Este resultado antecede as novas regressões Red preparadas pelo executor após receber LT-J1. A suíte de 500 sucessos foi informada pelo Head; não é apresentada como repetida pelo juiz.
- Probes lexicais adicionais confirmaram equivalência de nomes com caixa/acentos/apóstrofos, alias de 29/02/2024 em BR/textual/ISO, ausência de alias para calendário inválido e preservação de negação no vocabulário. Só dados sintéticos foram impressos.
- Testes de persistência verificam perfil/dependência de identificadores, rejeição antes de joblib.load em artefato incompatível, conteúdo canônico e projeção JSON de metadados complexos. Joblib continua limitado a origem local confiável; hashes não autenticam pickle externo.
- O relatório agregado reviewed tem o SHA-256 declarado. Os dez hashes de código presentes conferem com disco. O digest das 287 entradas de staging foi recalculado e permanece inalterado. Nenhum texto, nome, identificador ou caminho de original real foi emitido.
- Plano/specs v0.6 e uv.lock têm os hashes congelados do handoff. Não houve download, instalação, inferência semântica, provedor externo, tracing, edição de staging ou aprovação humana pelo juiz.

## Handoff e limites

O handoff atual distingue corretamente aprovação técnica de corpus homologado, mantém P3B como próximo ponto, pede unidades canônicas/closure/ledger e não libera G3/P4/P5/SLO/AWS. O prompt copiável preserva quarentena, revisão humana adiada, política OpenRouter pendente e plano/specs congelados. A sessão deve encerrar somente depois da aprovação desta nova entrega; ainda não pode declarar a correção lexical/temporal aprovada nesta rodada.

Status=success permanece técnico; nomes normalizados não são identidade/autorização; JSON Chroma não constitui filtro escalar; datas relativas e efeitos jurídicos estão fora do escopo. Esses limites são explícitos e não foram usados para exigir implementação de gates futuros durante este julgamento.

## Identidade examinada

```text
temporal.py:
2a0c8c213c9b461d8ab2d132d7cda6acf218b63b3d4c74cecb93c7404f112e03
lexical.py:
3c11425b23a6ee2fd8d511d04cac44bfb4ae8bb62bbbafbe0ce1f55b4454d613
schemas.py:
4454df5c59b2862583dfe2e6fa175cb8a95f39cf394e8af697594ffa075c5205
metadata-audit-reviewed.json:
2974ddff93e9ed96fde16d3ca0eb9bf74968f5b016d01644e805e9a61b59f58d
input_artifacts_digest:
dce85621ebc9e71e7f3fec573f08db55d24314b6fa14f5a9c3043071ebcfe14b
plano v0.6:
91a6f484016fea4d8db4834b4da95732d2445cc16b086169626799e6e7a34d21
specs v0.6:
a98946c4d358d8e8b548656d5eeb479a43feb5b19cc24e841dd9ae4212d7e3e4
uv.lock:
101a5ff70a84efd5c88e02a9ae8cdac2b15b63dd634f6d6aae94ed7b6b0030e4
```

Próxima rodada: fechar LT-J1 via TDD, preservar evidências históricas e ressubmeter a implementação congelada para revisão independente. No máximo duas tentativas permanecem nesta campanha.

## Tentativa 2 de no máximo 3

**Não aprovado: média 8,80/10; LT-J1 parcialmente corrigido, com residual alto aberto.** Nenhum achado crítico novo. Esta seção preserva integralmente o parecer e as identidades da tentativa 1. O juiz não participou do patch nem modificou implementação, testes ou staging.

| Critério técnico | Nota |
| --- | ---: |
| Natureza do RAG | 9,0 |
| Arquitetura | 9,0 |
| Governança | 9,3 |
| Aplicabilidade | 9,0 |
| Eficiência | 9,0 |
| Acurácia técnica | 8,0 |
| Precisão técnica | 7,8 |
| Rastreabilidade | 9,2 |
| Observabilidade | 9,1 |
| Confiabilidade | 8,6 |

A gramática positiva ancorada corrige a vinculação indevida por prefixo; todos os cinco contraexemplos originais agora ficam unknown, sinalizados como não assertivos e sem execution_date. Os controles positivos diretos mantêm signature e a data candidata. Referências por prefixo a acordo/instrumento alheio também ficam unknown. As notas de acurácia/precisão melhoram por esse fechamento demonstrável, mas o tratamento de qualificações posteriores ainda viola o mesmo contrato técnico. Não se atribui acurácia jurídica ao corpus sem gabarito humano.

### LT-J1 residual — alta: qualificação posterior ignorada pelo label de assinatura

Referências do snapshot 2: `app/ingestion/temporal.py:74`, `:75`, `:83`, `:149`, `:155`, `:165` e `:198`. A gramática exige prefixo imediato afirmativo; porém, seu ramo `Assinatura:` continua suficiente quando o sufixo lexical não consta das listas de contexto histórico/não assertivo. O sufixo é coletado, mas a ausência de match nessas listas é tomada como permissão para promover a assinatura.

Contraexemplos sintéticos independentes, reproduzidos em extração → ParsedDocument.model_validate → cabeçalho do chunker:

- `Assinatura: 10/03/2024, hipoteticamente.`
- `Assinatura: 10/03/2024, do outro contrato.`
- `Assinatura: 10/03/2024, referente ao instrumento anexo.`
- `Assinatura: 10/03/2024 (do acordo mencionado).`

**Todos retornam kind=signature, uncertainty_flags=[] e execution_date=2024-03-10**, passam pela validação documental e enriquecem o cabeçalho com signature. O primeiro é hipótese explicitamente posterior; os demais atribuem a data a outro instrumento, sem vínculo positivo ao instrumento atual. Este é um residual do contexto já exigido em LT-J1, não uma exigência de resolver relações ou efeitos jurídicos. A validação documental reproduz o mesmo extrator e não corrige o falso positivo.

Correção exigida para a última tentativa: preservar a menção, calendário, literal e offsets, mas exigir vinculação positiva ao instrumento atual e contexto posterior não qualificador para produzir signature. Qualificação lexical posterior não resolvida deve impedir o resumo de assinatura, com unknown e sinalização explícita de ambiguidade/não assertividade conforme o caso. Uma lista crescente de sinônimos isolados não basta para resolver a classe de falha; o fallback conservador precisa proteger o contexto não compreendido. Manter controles de label simples, assinatura afirmativa direta, fecho heurístico sinalizado e separação de novo evento independente. Não inferir efeitos do instrumento anexo nem remover datas para fazer os testes passarem.

### Evidência independente da tentativa 2

- **156 testes focais passaram, dois avisos, em 9,77 s**: temporal, lexical, chunker, metadata, hybrid e staging. Comando: `.venv/Scripts/python.exe -m pytest tests/test_temporal.py tests/test_lexical.py tests/test_chunker.py tests/test_metadata_extractor.py tests/test_hybrid_retriever.py tests/test_p2_staging.py -q -m 'not online'`. A suíte completa de 529 sucessos é evidência do Head, não repetição declarada pelo juiz.
- Probes sintéticos confirmaram fechamento dos cinco casos originais, sufixo `jamais confirmada`, referência por prefixo a outro instrumento, três controles afirmativos e preservação do literal. Os quatro sufixos acima demonstram o residual e sua propagação completa. Nenhum conteúdo real foi impresso.
- SHA-256 do novo relatório e os dez hashes de código presentes conferem com bytes atuais. Plano, specs e uv.lock mantêm suas identidades congeladas. O relatório reviewed da tentativa 1 permanece com seu hash original.
- O relatório novo registra o mesmo digest de entrada, 287 arquivos/14.791 blocos, 639 menções, 202 quarantined/85 failed, zero aprovações humanas e published=false. As 100 classificações signature são fechos heurísticos ainda pendentes. Alteração de contagens não demonstra qualidade jurídica.
- Handoff e registro de execução continuam delimitando corretamente a entrega; P3B, G3 pleno, revisão humana, publicação P4, autorização P5, SLO e AWS permanecem pendentes. Não são bloqueios adicionais exigidos nesta correção. Sem download, instalação, provedor, tracing ou edição de staging pelo juiz.

### Identidade da tentativa 2

```text
temporal.py:
55bba1242b2ddeefbba3cdc389975bc0b5987dfa3578c58198590f4e3786f7a9
metadata-audit-round2.json:
9dc01b42d4943eb5cc3d3786ea250cc9b049740e51a600aff6f0a7b4819e96c4
input_artifacts_digest:
dce85621ebc9e71e7f3fec573f08db55d24314b6fa14f5a9c3043071ebcfe14b
```

As identidades de lexical.py, schemas.py, plano/specs e uv.lock permanecem as registradas na tentativa 1. Resta **uma tentativa** nesta campanha. O encerramento com a correção aprovada exige fechar o residual alto e obter o gate na terceira revisão; nenhuma etapa P3B foi liberada ou executada por este parecer.

## Tentativa 3 — última da campanha

**Não aprovado: média 8,87/10; residual alto LT-J1 permanece aberto.** Nenhum achado crítico ou segundo achado alto. As tentativas anteriores e seus hashes permanecem preservados. O resultado não presume aprovação por ser a última rodada e não reutiliza o 9,17 do encoder P3A.

| Critério técnico | Nota |
| --- | ---: |
| Natureza do RAG | 9,0 |
| Arquitetura | 9,0 |
| Governança | 9,3 |
| Aplicabilidade | 9,0 |
| Eficiência | 9,0 |
| Acurácia técnica | 8,3 |
| Precisão técnica | 8,1 |
| Rastreabilidade | 9,2 |
| Observabilidade | 9,1 |
| Confiabilidade | 8,7 |

A correção estrutural fecha os cinco exemplos da primeira rodada e os quatro sufixos da segunda, incluindo variantes com ponto, ponto e vírgula, newline e qualificações antes do label. Os controles afirmativos e o fecho heurístico sinalizado permanecem; eventos independentes datados não contaminam a assinatura anterior. Entretanto, o frame completo só é mantido até a data precedente: a presença de qualquer candidato de data anterior ainda permite apagar qualificadores históricos, hipotéticos ou de outro instrumento. A melhora é concreta, mas não fecha o contrato de atribuição conservadora.

### LT-J1 residual — alta: data anterior reinicia a atribuição sem validar o frame

Referências do snapshot 3: `app/ingestion/temporal.py:154` reinicia `previous_end` após qualquer candidato anterior; `:155` recorta `prefix_raw`; `:161` remove separadores iniciais; `:164` constrói contexto sem o qualificador anterior à primeira data; `:181` reconhece então um label simples como assinatura atual; `:219` aceita essa assinatura no resumo. A separação positiva de eventos no sufixo não impede esse reinício automático no prefixo da menção seguinte.

Contraexemplos sintéticos independentes:

- `Do outro contrato de 09/03/2024, Assinatura: 10/03/2024.`
- `Contrato anterior datado de 09/03/2024, Assinatura: 10/03/2024.`
- `Hipoteticamente em 09/03/2024, Assinatura: 10/03/2024.`
- `Do instrumento anexo de 09/03/2024; Assinatura: 10/03/2024.`

**Nos quatro, a segunda menção retorna signature, uncertainty_flags=[] e execution_date=2024-03-10.** Todos passam por ParsedDocument e produzem `signature = 2024-03-10` no cabeçalho; literal e spans continuam preservados. No segundo exemplo, a primeira menção recebe historical_reference; no terceiro, nonasserted_event_context. Essas sinalizações desaparecem na segunda porque o frame foi reiniciado pela data, embora não exista um evento anterior positivamente independente nem sujeito atual explícito para a assinatura. A versão sem primeira data de `Do outro contrato.\nAssinatura: 10/03/2024.` fica corretamente unknown: inserir uma data dentro da mesma alusão altera indevidamente sua atribuição.

Não se exige resolver efeitos ou relações entre instrumentos. O comportamento conservador necessário é preservar unknown quando uma data meramente mencionada no mesmo frame não demonstra independência suficiente para apagar a alusão/hipótese. A assinatura candidata não homologada ainda é uma classificação técnica incorreta nesses exemplos; pending_review não fecha LT-J1.

Correção necessária, caso o usuário autorize nova campanha: fazer a segmentação/atribuição de contexto usar uma fronteira positivamente estabelecida, em vez de tratar todo DateCandidate como fronteira. Propagar ou conservar o frame não resolvido enquanto essa independência não estiver demonstrada. Regressões devem cobrir múltiplas datas, referências históricas/hipotéticas e outro instrumento no prefixo, lado a lado com eventos independentes afirmativos válidos. Não ampliar esta entrega para resolver relações jurídicas nem eliminar menções.

### Verificações independentes da tentativa 3

- **178 testes focais passaram, dois avisos em 8,31 s**, pelo mesmo comando focal da tentativa 2. A suíte completa de 551 sucessos/um online deselecionado é resultado informado pelo Head, não execução atribuída ao juiz.
- Probes adicionais percorreram extração → ParsedDocument → chunker: cinco exemplos iniciais, quatro sufixos originais, variantes de delimitadores, prefixo qualificador completo, assinatura direta, label simples e fecho sinalizado. Todos esses controles fecham a falha anterior, mantendo literal e offsets. Os quatro exemplos com data precedente acima reproduzem o residual end-to-end. Só dados sintéticos foram emitidos.
- SHA-256 de metadata-audit-round3.json confere. Seus dez hashes de código conferem com os arquivos atuais. Plano/specs e uv.lock conservam os hashes congelados; relatórios reviewed e round2 mantêm os hashes originais. Preflight e summary históricos de staging foram rehasheados e permanecem iguais aos controles do relatório.
- O relatório agregado registra 287 arquivos, 223 documentos parseados, 14.791 blocos, 639 menções, 97 signature/520 unknown/21 due/1 termination, 97 fechos heurísticos e 96 resumos candidatos; 202 quarantined/85 failed, zero aprovação humana/publicação. O digest de entrada declarado permanece igual. Não se apresenta essa contagem como acurácia jurídica nem como nova homologação.
- Handoff e registro de execução não antecipam aprovação da rodada 3. Mantêm P3B, G3 pleno, comparação de encoders/gabarito, revisão humana, P4/P5, SLO e AWS pendentes. O prompt de próxima sessão só poderá considerar esta correção aprovada se houver novo gate autorizado e obtido; nesta campanha ela segue reprovada.
- Nenhum download, instalação, provedor, tracing, leitura de segredo ou alteração de código/testes/data foi realizado pelo juiz. O único arquivo editado pelo juiz é este parecer.

### Identidade da tentativa 3

```text
temporal.py:
f885f9030617a40141141421e607b1195038af15c4830d85d4a48f90c9a1a7b7
metadata-audit-round3.json:
22f5d012869b5473775c67f7a4f893d2b2bb19b6f5f3f0791dc4497f9b56b0b7
input_artifacts_digest declarado:
dce85621ebc9e71e7f3fec573f08db55d24314b6fa14f5a9c3043071ebcfe14b
```

As demais identidades congeladas permanecem iguais. **As três tentativas desta campanha foram utilizadas.** Devolver ao usuário o bloqueio concreto e a decisão sobre continuação; não iniciar uma quarta rodada implicitamente, não promover esta correção nem P3B/G3/produção com base neste parecer. A heurística de cidade/fecho sinalizada permanece o limite explícito já aceito na primeira rodada, sem virar um bloqueio adicional nesta avaliação.

## Extensão autorizada e tentativa 4 de no máximo 5

Após a terceira reprovação, o usuário autorizou expressamente **mais duas rodadas para concluir esta etapa na mesma sessão**. A campanha atual foi estendida às tentativas 4 e 5, sem reiniciar a contagem ou apagar as reprovações 8,63/8,80/8,87. O limite usual de três continua nas entregas futuras. Esta seção registra o julgamento do snapshot 4 congelado; o juiz não implementou nem participou da correção.

**Aprovado no subgate técnico: média 9,18/10, LT-J1 fechado, nenhum alto/crítico aberto identificado.** Não é homologação jurídica do corpus, aprovação de G3 pleno ou autorização de produção. A quinta tentativa não foi necessária.

| Critério técnico | Nota |
| --- | ---: |
| Natureza do RAG | 9,2 |
| Arquitetura | 9,2 |
| Governança | 9,4 |
| Aplicabilidade | 9,0 |
| Eficiência | 9,0 |
| Acurácia técnica | 9,0 |
| Precisão técnica | 9,1 |
| Rastreabilidade | 9,4 |
| Observabilidade | 9,3 |
| Confiabilidade | 9,2 |

As notas avaliam a extração conservadora de candidatos, sua integração lexical e a integridade das evidências deste subgate. Acurácia/precisão são técnicas e fundamentadas em casos sintéticos, calendário, vinculação de contexto e preservação de origem; não são métricas jurídicas do lote. Aplicabilidade/eficiência permanecem limitadas ao desenvolvimento local e ao fallback declarado; recuperação avaliada e desempenho operacional não foram homologados.

### Fechamento de LT-J1

Referências: `app/ingestion/temporal.py:126` centraliza uma gramática positiva completa em `_recognize_prefix`; `:152` reconhece a fronteira independente; `:191` mantém frame_start; `:204` exige vinculação positiva/calendário válido e não permite fecho heurístico certificar independência; `:225` exige ausência de ressalvas; `:244` muda a origem somente quando o frame atual está qualificado; `:249` abstém o resumo diante de signature_attribution_uncertain. O cabeçalho tipográfico estreito de cláusula em `:114` não é autoridade documental e o restante precisa satisfazer a mesma gramática.

Os cinco contraexemplos originais, os quatro sufixos da segunda rodada e os quatro frames com data precedente da terceira agora permanecem unknown/sinalizados e não produzem resumo indevido. Datas inválidas, terceira/quarta menções, ranges e qualificações históricas/hipotéticas não conseguem reiniciar o frame com um label conveniente. A gramática compartilhada remove a divergência entre reconhecimento de evento e autorização de fronteira que sustentava o residual.

Controles positivos mantêm assinatura direta/label simples, vencimento independente, intervalo explícito e fecho heurístico flagged. Eventos positivamente independentes ainda separam assinatura e pagamento previsto. Duas assinaturas diferentes abstêm o resumo; assinatura posterior com qualificação não resolvida também impede selecionar silenciosamente a primeira data. Literal, calendário, spans e estado pending_review permanecem intactos.

### Evidências independentes da tentativa 4

- **197 testes focais passaram, dois avisos em 11,86 s**, pelo comando focal registrado na tentativa 2. Abrangem temporal, lexical, chunker, metadata, hybrid e staging. A suíte completa de **570 sucessos, um online deselecionado, dois avisos em 26,29 s** é evidência informada pelo Head; não é apresentada como repetição pelo juiz.
- O juiz construiu e executou **864 combinações sintéticas ponta a ponta**: seis qualificadores anteriores, quatro formas/calendários de primeira data, quatro delimitadores, três caminhos de datas/eventos/ranges e três cabeçalhos. Nenhuma produziu assinatura/resumo indevido; todas preservaram literal, spans e pending_review através de extração → ParsedDocument → chunker. Este é um conjunto finito de contraexemplos e variantes, não garantia de entendimento de toda linguagem contratual.
- Seis controles positivos/conflito adicionais passaram: label simples, declaração direta, pagamento seguido de assinatura, intervalo seguido de assinatura, duas assinaturas divergentes e fecho cidade/data. Probes separados confirmaram signature_attribution_uncertain bloquear resumo conflitante e pagamento previsto não contaminar assinatura independente.
- SHA-256 do relatório final e seus **dez hashes de código** conferem com disco. O perfil lexical aponta o novo hash temporal e preserva parâmetros/Unicode/pacotes. Preflight e summary históricos de staging foram rehasheados e conferem com os controles do relatório. Plano/specs/uv.lock conservam os hashes congelados; relatórios das tentativas 1–3 conservam suas identidades originais.
- O relatório final registra 287 arquivos/223 ParsedDocuments/14.791 blocos, 639 menções em 187 documentos, 97 signature/542 unknown, 71 resumos candidatos, 183 documentos/436 nomes, 202 quarantined/85 failed, zero aprovações humanas/publicação. Seu digest de entrada é o histórico já registrado. Não houve reparse de originais físicos, nem validação humana pelo juiz.
- Sem download, instalação, provedor, tracing, leitura de segredo ou edição de implementação/testes/data. Apenas este parecer foi editado pelo juiz.

### Limites materiais do aceite e handoff

A política agora abstém mais: no lote agregado, menções antes tipadas como vencimento/vigência ficaram unknown, e os resumos caíram de 106 legados para 71 candidatos. **Isso não demonstra ganho de acurácia nem recall jurídico.** As 639 datas e seus literais continuam preservados e pesquisáveis; recall por tipo/evento dependerá de gabarito humano e avaliação posterior. A aprovação não usa a perda de tipagem como substituto de mensuração de qualidade jurídica.

Os 97 fechos seguem heurísticos sinalizados e pending_review; não são 97 assinaturas homologadas. Datas relativas, relações/efeitos jurídicos, prova física, decisões humanas e filtros de acesso continuam fora desta entrega. O normalizador lexical e os aliases são chaves de busca, não identidade/autorização; JSON Chroma é projeção; joblib continua restrito a origem local confiável.

O handoff consolidado está coerente com a entrega e pode ser atualizado pelo Head com este aceite efetivo: distingue P3A de correção atual, não reinicia a campanha, explicita abstensão/recall, quarentena e ausência de produção. Seus oito links relativos existem; a referência histórica ao commit f30197080c8f2ddf2fe2e4b694e75063fe01ddfe:docs/HANDOFF.md foi verificada no Git. A próxima frente P3B é adequadamente condicionada ao aceite, com builders ainda pendentes, decisões escopadas/atuais do ledger, proveniência física, closure e orçamento de tokens. Um review_record_id por si não vira autorização, nem coordenada reconstruída vira prova homologada.

**Próxima sessão pode retomar a implementação técnica de P3B conforme plano/specs congelados, usando fixtures sintéticas.** Esta sessão não executou P3B. Permanecem pendentes G3 completo/comparação e gabarito, integração mantida de Chroma, P4 publicação atômica, P5 recuperação/autorização, revisão humana real, política OpenRouter, SLO e Linux/AWS. O p95 histórico do encoder 1,415 s continua acima da meta de recuperação completa; nenhuma nota deste parecer libera desempenho ou corpus real.

### Identidade aprovada — tentativa 4

```text
temporal.py:
2305cc97f9ae012b5e9305f07a2f78d99f719db4bb5f02f9fb835693192032d9
metadata-audit-round4-final.json:
185f15721bcb0c456019f3220d22e8466f8584cb06d9fabbf7ea3b1e74d6c12c
input_artifacts_digest declarado:
dce85621ebc9e71e7f3fec573f08db55d24314b6fa14f5a9c3043071ebcfe14b
```

As demais identidades congeladas permanecem as registradas nas rodadas anteriores. O relatório round4 sem sufixo final é intermediário, não a identidade aprovada. A campanha encerra com aprovação técnica na quarta de no máximo cinco tentativas; o histórico das três reprovações permanece válido. Não foi identificado outro bloqueio alto/crítico dentro deste escopo.
