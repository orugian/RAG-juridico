# Correção de nomes e datas na recuperação

## Escopo e resultado esperado

O usuário solicitou correção minuciosa da auditoria: nomes já extraídos tinham correspondência lexical frágil; datas podiam ser inválidas ou classificadas como assinatura pelo simples posicionamento no fim do documento. Esta entrega busca recuperar grafias equivalentes sem alterar citações e registrar datas candidatas com calendário, contexto e origem verificáveis. Não libera G3 completo, índices reais, filtros jurídicos de vigência ou produção.

O Head Engenheiro de IA coordena arquitetura, integração, autorrevisão e decisão de aceite. Dois Seniores Engenheiros de IA executam frentes separadas; o agente juiz avalia independentemente. Protocolo: TDD, dez critérios técnicos de 0 a 10, média mínima 9 e nenhum achado alto/crítico aberto; máximo três rodadas por entrega. Evidências e limitações substituem afirmações de precisão sem gabarito humano.

## Frentes implementadas

| Frente | Defeito observado | Correção e comportamento esperado |
| --- | --- | --- |
| Calendário e classificação | 31/02/2024 aceito; pagamento chamado execution_date | Calendário real; todas as datas absolutas; classificação conservadora de assinatura, vigência, vencimento, rescisão, renovação ou desconhecida |
| Evidência temporal | Data isolada sem origem | Literal, block_id e offsets exatos; data ISO deve corresponder ao literal; tipos e coleção conferidos contra o bloco; estados sempre pending_review |
| Ambiguidade | Última data vencendo conflitos/histórico | Referências históricas, previsões/condições e conflitos ficam sinalizados; nenhuma precedência jurídica inferida |
| Busca por nomes | EMPRESA ALFA não correspondia a empresa alfa | Mesma normalização Unicode/caixa/acentos/pontuação em documentos e consultas; nomes originais e chave de busca conservados |
| Busca por datas | BR, texto e ISO incompatíveis | Alias lexical de data somente após validação de calendário; tipos e evidências enriquecem exclusivamente o grupo local |
| Persistência BM25 | Índice antigo reutilizado com outra tokenização | Perfil, implementação, dependências, parâmetros e conteúdo vinculados; carga incompatível requer reconstrução |
| Integração | block_id remapeado pelo worker quebrava referência temporal | Namespace por arquivo aplicado também às menções; documento revalidado; hashes das novas dependências entram nos fingerprints |

`execution_date` é um resumo técnico de candidata de assinatura, nunca homologação jurídica. Fecho convencional cidade/data é heurística de estrutura, sinalizada; não há geocodificação. Intervalos e eventos condicionais não estabelecem vigência efetiva. Nomes normalizados não são identidade única, autorização ou isolamento por cliente; CPF/CNPJ do filtro legado permanece numérico nesta entrega.

O texto original e seus espaços são preservados em `verbatim_text`. Contexto sintético, nomes normalizados e aliases só participam da busca. Metadados temporais estruturados continuam nos documentos canônicos/BM25; na projeção Chroma, valores complexos são JSON determinístico. Esses JSONs não são filtros escalares de cliente/data nem fonte de citação.

## Identidades e migração

- Campos novos são versionados. Metadados históricos sem `temporal_extractor_version` e menções são legados não qualificados; não ganham aprovação por leitura. Calendário impossível é rejeitado ou preservado como ocorrência inválida/desconhecida sem ISO inventado.
- O lote P2A `73d18984-c7bd-4dab-a2aa-4707bfd99482` e relatórios Qwen anteriores permanecem históricos e intactos. Alterar schemas/chunker/tokenização altera fingerprints e exige novo diagnóstico/preflight antes de processamento futuro.
- Índices BM25 legados sem perfil completo exigem reconstrução. `joblib` é aceito somente de diretórios locais confiáveis; hashes de integridade não tornam pickle de terceiro seguro. Publicação, journal, autorização e atomicidade completa pertencem P4.
- Qwen continua `Qwen/Qwen3-Embedding-0.6B`, revisão `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, dimensão 1024. Pesos, configuração do encoder e lock não são alterados por esta correção. O p95 CPU histórico de 1,415 s continua sem liberação de SLO/AWS.

## Organização e continuidade

Código permanente em `app/ingestion` e `app/retrieval`; regressões permanentes em `tests`. Identidades/testes de integração utilizam módulos e suítes existentes. Diagnósticos reais ficam em `data/evaluation`, ignorado pelo Git, sem texto/nomes/documentos expostos nos relatórios agregados. Registros úteis de execução e do juiz ficam em `docs/execution` e `docs/reviews`. Não há scripts descartáveis no diretório raiz, cópia de pesos versionada ou limpeza destrutiva de histórico.

O ponto de entrada da próxima sessão é `docs/HANDOFF.md`, com prompt copiável e estado atual. **Correção aprovada com 9,18/10 na rodada 4, após a extensão expressamente autorizada pelo usuário; LT-J1 fechado.** Esta sessão encerra na correção. A próxima retoma P3B: unidades jurídicas canônicas, fechamento estrutural e vínculo real com o ledger, usando fixtures sintéticas enquanto as revisões humanas permanecem adiadas. Depois seguem índices versionados e recuperação avaliada, conforme plano/specs v0.6 congelados. Consulta real via OpenRouter aguarda política de dados.

## Evidências e aceite

TDD dos executores: Red temporal inicial com 22 falhas, Red lexical por módulo ausente e contraexemplos adicionais de contexto, persistência e literal. Integração do Head: três Red reproduziram fingerprint omitindo temporal.py, referência de data sem namespace do arquivo e hashes faltantes nos diagnósticos; oito Green confirmaram as correções iniciais. Autorrevisão acrescentou sufixos históricos/condicionais, intervalos não assertivos, coleção temporal incompleta e mutação após validação. Dois Red finais demonstraram resumo de assinatura fabricado com coleção versionada vazia e normalizador de identificadores fora do perfil BM25. Ambos receberam correção e regressão permanente. Focais finais: 109 temporais/metadata/parsers e 18 lexicais passaram.

A primeira integração teve 497 sucessos e uma falha: o teste exigia mensagem de um validador, mas a nova checagem de completude rejeitava o span antes. O teste agora exige rejeição e ausência de resultado sem depender da ordem interna. A regressão seguinte teve 499 sucessos/um online deselecionado. Após incluir identidade de `identifiers.py`, a suíte final passou: **500 testes, um online deselecionado, dois avisos em 28,35 s**, com tokenizer real local, nenhum download ou provedor. Não medimos cobertura percentual. Os avisos de depreciação LangChain/Chroma permanecem, com migração prevista em P3; não houve alteração de dependências.

```powershell
.venv\Scripts\python.exe -m pytest tests -q -m "not online" --qwen-artifact data/models/qwen3-embedding-0.6b-97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
```

### Diagnóstico agregado real, sem homologação

Relatório da submissão 1: `data/evaluation/lexical-temporal-20261006/metadata-audit-reviewed.json`, SHA-256 `2974ddff93e9ed96fde16d3ca0eb9bf74968f5b016d01644e805e9a61b59f58d`. O relatório inicial `metadata-audit.json` é diagnóstico intermediário preservado; seu perfil antecede o hash de identificadores. A correção posterior a LT-J1 exige diagnóstico novo, sem reatribuir os hashes de relatórios anteriores ao código atual.

Método: leitura única dos bytes de cada artefato para hash/JSON; `ParsedDocument.model_validate` dos blocos históricos, `extract_contract_metadata(blocks)` em memória e revalidação do documento com os metadados novos. Agregação sem texto, nomes, identificadores pessoais ou caminhos dos documentos. Rede bloqueada por socket/DNS; nenhuma releitura de originais físicos, inferência Qwen ou edição de staging. Ao final, rehash das 287 entradas confirmou imutabilidade; controles/preflight/summary e hashes de código/lock constam no relatório. O procedimento foi repetido após o congelamento final e produziu as mesmas contagens, sem erro de validação.

| Medida | Submissão 1 (histórica) | Submissão 2 (corrigida) |
| --- | --- | --- |
| Arquivos e elegibilidade histórica | 287; 202 quarantined/85 failed | Iguais, inalterados |
| ParsedDocuments/blocos/documentos com blocos | 223 / 14.791 / 202; 64 sem ParsedDocument | Iguais |
| Nomes candidatos antes/depois | 183 documentos / 436 nomes em ambos | Iguais; extrator de partes não alterado |
| Documentos com datas candidatas | 187 | 187 |
| Menções de datas | 639, calendário válido neste lote | 639, todas preservadas |
| Classificação candidata | 102 signature, 25 due, 1 termination, 511 unknown | 100 signature, 22 due, 1 termination, 516 unknown |
| Resumo execution_date antes/depois | 106 legados / 100 candidatos; 10 mudanças, oito removidos | 106 legados / 98 candidatos; oito mudanças/remoções |
| Sinalizações | 100 closing_line_candidate; 378 sem contexto; 76 não assertivas; 34 assinatura não qualificada; 21 históricas; 2 ambíguas | 100 closing_line_candidate; 371 sem contexto; 86 não assertivas; 36 assinatura não qualificada; 21 históricas; 2 ambíguas |
| Aprovações humanas/publicação | Zero / nenhuma | Zero / nenhuma |

Os 100 fechos são heurísticos, explicitamente sinalizados; não comprovam assinatura. A proporção de `unknown` não é taxa de erro nem precisão. Regras conservadoras têm falsos negativos possíveis; gabarito humano e comparação de recuperação ainda são necessários. O lote real não trouxe datas impossíveis, mas as regressões sintéticas exercitam calendário inválido/bissexto. Datas relativas, efeito jurídico, duração, ordenação temporal, filtros exatos por nome/vigência e resolução de relações permanecem fora desta correção.

Digest das entradas: `dce85621ebc9e71e7f3fec573f08db55d24314b6fa14f5a9c3043071ebcfe14b`. `uv.lock` permanece `101a5ff70a84efd5c88e02a9ae8cdac2b15b63dd634f6d6aae94ed7b6b0030e4`. Plano/specs v0.6 permanecem com hashes registrados no handoff. Não repetimos microbenchmark do encoder inalterado; os números P3A continuam históricos e sem aceite de desempenho.

### Aceite independente

**Rodada 1 reprovada: 8,63/10; LT-J1 alto aberto**, no [parecer independente](../reviews/2026-10-06-exec-lexical-temporal.md). O juiz executou 97 testes focais e demonstrou que nunca/jamais/teria/poderá/deverá promoviam assinatura negada ou hipotética, inclusive após schema e header. A implementação foi mantida congelada até o parecer, preservando a identidade desta submissão.

Resposta em TDD: 12 casos adicionais tiveram dez falhas e dois casos já seguros. Autorrevisão do Head identificou também janela arbitrária entre sujeito corrente e verbo de assinatura, capaz de atribuir assinatura de instrumento mencionado: sete comparações tiveram três falhas/quatro sucessos. O Red expandido confirmou **23 falhas em 29 casos, seis controles já seguros**. A correção exige vínculo gramatical afirmativo direto entre o instrumento corrente e a assinatura, sem janela arbitrária; negações, hipóteses e modalidades recebem unknown/sinalização. Menções, calendário e origem permanecem; nenhum efeito jurídico é inferido.

Green focal integrado: **180 testes passaram, dois avisos em 5,19 s**. Regressões de negações/modalidades verificam extração → ParsedDocument → cabeçalho de busca, proibindo signature/summary para esses casos e mantendo afirmações diretas e fecho flagged. Datas de instrumentos apenas mencionados não se transferem ao documento corrente. A gramática positiva restrita pode aumentar falsos negativos: texto sem vínculo explícito suficiente fica candidato desconhecido até revisão, não é apagado.

Novo diagnóstico, após congelar o patch: `data/evaluation/lexical-temporal-20261006/metadata-audit-round2.json`, SHA-256 `9dc01b42d4943eb5cc3d3786ea250cc9b049740e51a600aff6f0a7b4819e96c4`; `temporal.py` SHA-256 `55bba1242b2ddeefbba3cdc389975bc0b5987dfa3578c58198590f4e3786f7a9`. Reextração/revalidação em memória dos mesmos 223 ParsedDocuments, 287 artefatos rehasheados, zero erros e staging inalterado. Perfil BM25 vincula automaticamente o novo hash temporal; os relatórios anteriores foram preservados. As diferenças agregadas não demonstram melhoria quantitativa de acurácia jurídica; o fechamento de LT-J1 é demonstrado por contraexemplos sintéticos.

Regressão completa da submissão 2: **529 passaram, um online deselecionado, dois avisos em 27,58 s**, pelo mesmo comando com tokenizer real local. Nenhum download/provedor ou alteração de dependência.

**Rodada 2 reprovada: 8,80/10; LT-J1 parcialmente fechado, residual alto.** O juiz confirmou os cinco casos originais corrigidos, positivos preservados, hashes e 156 testes focais. Porém qualificações posteriores à data (`hipoteticamente`, `do outro contrato`, referência a instrumento anexo ou acordo mencionado) ainda passavam como assinatura atual. A implementação foi preservada até o parecer; sua identidade e diagnóstico permanecem históricos.

Red para a correção final: dez casos iniciais reproduziram dez falhas na extração → schema → header; a família foi expandida para **18 falhas/18 casos**, incluindo ponto, ponto e vírgula, newline, outro rótulo sem segunda data e qualificação anterior a outro evento datado. A última autorrevisão acrescentou **quatro Red de qualificação antes do rótulo**, que eram apagadas por delimitadores. Não se removeu evidência para fazer os testes passarem.

A solução final exige o frame afirmativo inteiro no prefixo e ausência de qualificação lexical não resolvida no sufixo. Delimitadores não apagam ressalvas; somente um segundo candidato real com prefixo completo reconhecido como evento independente permite separar contextos, conservando qualquer qualificação anterior. Fechos também exigem frame completo/bare, continuam flagged e pending_review. Semântica de outro instrumento, efeito jurídico ou qualificador não é presumida. **Focal integrado final: 202 passaram, dois avisos em 4,90 s.** Possíveis falsos negativos ficam explicitamente desconhecidos até revisão; regras não garantem acurácia jurídica.

Diagnóstico da submissão 3: `data/evaluation/lexical-temporal-20261006/metadata-audit-round3.json`, SHA-256 `22f5d012869b5473775c67f7a4f893d2b2bb19b6f5f3f0791dc4497f9b56b0b7`; `temporal.py` SHA-256 `f885f9030617a40141141421e607b1195038af15c4830d85d4a48f90c9a1a7b7`. Mesma reextração/revalidação offline; dez hashes de código conferem, perfil lexical vincula o hash novo, lock e controles preservados. Nenhum erro/edição/publicação.

| Medida final (submissão 3) | Resultado |
| --- | --- |
| Arquivos / ParsedDocuments / blocos | 287 / 223 / 14.791, mesmos bytes de staging |
| Documentos com menções / menções preservadas | 187 / 639; todas com calendário válido neste lote |
| Classificação candidata | 97 signature, 21 due, 1 termination, 520 unknown |
| Fechos heurísticos / documentos com resumo | 97 closing_line_candidate / 96 resumos candidatos |
| execution_date legado / remoções | 106 / 10; nenhuma comparação é taxa de acerto |
| Sinalizações | 344 sem contexto; 109 não assertivas; 3 qualificações não resolvidas; 34 assinatura não qualificada; 28 históricas; 2 ambíguas; 97 fechos candidatos |
| Nomes, elegibilidade, revisão e publicação | 183 documentos/436 nomes; 202 quarantined/85 failed; zero homologação humana/publicação |

As tabelas de submissões 1–2 são históricas e não substituem a identidade final. O digest das entradas continua `dce85621ebc9e71e7f3fec573f08db55d24314b6fa14f5a9c3043071ebcfe14b`. **Regressão final: 551 passaram, um online deselecionado, dois avisos em 31,47 s**, com o mesmo comando e tokenizer real local. Código congelado para a terceira e última rodada; o aceite depende do juiz, não dos resultados dos executores.

### Encerramento inicial após a rodada 3 — histórico, não aprovado

**Rodada 3 reprovada: 8,87/10; residual alto LT-J1, nenhum crítico ou segundo alto.** O juiz executou 178 focais (8,31 s), confirmou os casos originais/sufixos e controles positivos corrigidos, porém identificou nova perda de contexto: `previous_end` inicia depois de qualquer data anterior, mesmo sem evento independente reconhecido. O `lstrip` remove delimitadores restantes, promovendo um rótulo que pertence a frame histórico/hipotético como assinatura corrente.

Exemplos sintéticos: `Contrato anterior datado de 09/03/2024, Assinatura: 10/03/2024.`; `Do outro contrato de 09/03/2024, Assinatura: 10/03/2024.`; `Hipoteticamente em 09/03/2024, Assinatura: 10/03/2024.`. A segunda menção ainda chega a schema/resumo/header como signature sem ressalva. Não basta constatar calendário válido ou consistência com o mesmo extrator: a delimitação do frame de atribuição precisa ser corrigida.

As **três rodadas permitidas pelo usuário foram consumidas (8,63 → 8,80 → 8,87)**. A implementação da submissão 3 permanece intacta, com o defeito documentado, sem patch posterior ou quarta revisão disfarçada. A suíte de 551 sucessos é evidência da submissão, não regressão dos novos contraexemplos nem aprovação. Nenhum corpus/índice/API/produção foi promovido.

Decisão pendente do usuário: autorizar uma nova campanha para LT-J1, preservando estes pareceres e o limite de três da campanha nova. O [handoff](../HANDOFF.md) contém um prompt com autorização explícita para usar somente se o usuário concordar. Próxima implementação começa por Red ponta a ponta e correção da delimitação estrutural, preservando contexto entre datas até comprovar um evento independente; autorrevisão/diagnóstico novo/juiz precedem P3B. A sessão encerra nesse estado, sem declarar atingido o score mínimo ou apagar o histórico.

O 9,17 de P3A pertence exclusivamente ao encoder/diagnósticos anteriores. Nenhuma aprovação desta correção autoriza G3 completo, revisão jurídica ou produção.

### Reabertura autorizada — duas rodadas adicionais

Após o encerramento acima, o usuário concedeu explicitamente **mais duas rodadas para alcançar o score alvo e finalizar esta etapa na mesma sessão**. Esta autorização estende a campanha atual para no máximo **cinco tentativas totais (4 e 5 adicionais)**; os três pareceres e relatórios anteriores permanecem históricos. Não se inicia campanha nova ou reinicia a contagem. O padrão das próximas entregas continua máximo três rodadas por gate, salvo nova orientação expressa.

Frente retomada: corrigir a delimitação estrutural de contexto entre candidatos, conservando referências/condições até que uma fronteira de evento independente esteja positivamente estabelecida. Red deve cobrir quatro contraexemplos do juiz, três ou mais datas, calendário inválido antecedente, ranges e controles de eventos afirmativos independentes; Green, autorrevisão, regressão integrada e diagnóstico novo precedem rodada 4. Nenhuma parte de P3B é executada nesta correção.

Após o aceite, o Head revisará o handoff contra a solicitação original: roles Head/Seniores, TDD/self/juiz/score, estado e próxima frente, governança, limites de homologação e organização de arquivos. O documento será entregue para validação empírica do usuário antes da próxima sessão; esta intenção de validação não substitui o gate técnico do juiz.

### Submissão 4 — frames positivos, sem reset por candidato de data

Red permanente do Senior: **13 falhas/17 casos**, com quatro controles positivos já passando; inclui quatro contraexemplos do juiz, três datas, data inválida anterior, ranges referentes a outro instrumento e assinatura incerta que ocultava conflito no resumo. A implementação passa a manter frame_start até comprovar positivamente os dois eventos e a ausência de qualificação não resolvida; data, pontuação e rótulo isolados não autorizam mudança de origem. Ranges reconhecidos são unidades de dois endpoints sob a mesma política. Frame desconhecido/histórico/modal/inválido não libera nova fronteira; fecho heurístico não certifica independência. Menção incerta de assinatura bloqueia resumo, evitando seleção silenciosa de uma data qualificada enquanto outra permanece materialmente incerta.

O executor atingiu limite de uso depois de entregar código e testes. O Head assumiu integração/autorrevisão. A primeira regressão integrada teve **567 sucessos e uma falha**: cabeçalho tipográfico de cláusula impedia reconhecer vencimento explícito no worker. Dois Red adicionais reproduziram ordinais textual/numeral; uma gramática estreita de cabeçalho permite apenas o formato estrutural e exige o restante completo, sem confiar em metadata de hierarquia ou remover qualificadores. Focal integrado: **197 passaram, dois avisos em 7,84 s**. Regressões originais e controles permanecem.

Autorrevisão final do Head: **864 probes sintéticos ponta a ponta, nenhuma promoção indevida**, combinando seis qualificadores, quatro datas/formats/calendário inválido, quatro delimitadores, três eventos intermediários e três cabeçalhos. Nenhum script descartável foi criado. **Regressão completa final: 570 passaram, um online deselecionado, dois avisos em 26,29 s**, com tokenizer local real pelo comando registrado. Sem medição de cobertura percentual.

Diagnóstico final da submissão: `data/evaluation/lexical-temporal-20261006/metadata-audit-round4-final.json`, SHA-256 `185f15721bcb0c456019f3220d22e8466f8584cb06d9fabbf7ea3b1e74d6c12c`; `temporal.py` SHA-256 `2305cc97f9ae012b5e9305f07a2f78d99f719db4bb5f02f9fb835693192032d9`. O relatório round4 sem sufixo final é intermediário anterior ao ajuste de cabeçalho, preservado com sua identidade; nenhuma submissão adversarial foi consumida por esse diagnóstico.

Mesmos 287 arquivos/223 ParsedDocuments/14.791 blocos/639 menções em 187 documentos, 183 documentos/436 nomes, 202 quarantined/85 failed, zero aprovação/publicação. Agora há 97 signature (todos fechos heurísticos), 542 unknown e 71 resumos candidatos contra 106 legados: 35 removidos. A política estrita deixa desconhecidas também datas antes tipadas como vencimento/vigência; todas permanecem literais e pesquisáveis. Falsos negativos e abstenção no resumo são limites declarados, não ganho de acurácia jurídica. Estado e bytes originais continuam inalterados; hashes de código/perfil/lock/controles são conferidos. P3A não foi reexecutado: encoder/pesos/configuração inalterados, desempenho histórico permanece sem aceite AWS/SLO.

Código congelado para rodada 4, ainda sem aprovação. Handoff consolidado com ponto P3B condicionado ao aceite, tarefas/contratos/ledger/proveniência/closure, limites atuais e prompt; histórico de 05/10 idêntico ao Git foi substituído por referência ao commit existente, sem novo arquivo morto. As notas anteriores e a exceção de cinco rodadas total estão explícitas; entregas futuras seguem máximo três.

### Encerramento efetivo — rodada 4 aprovada

**Juiz independente: 9,18/10; LT-J1 fechado, nenhum alto/crítico aberto identificado**, no [parecer formal](../reviews/2026-10-06-exec-lexical-temporal.md). O juiz executou 197 testes focais (11,86 s), 864 combinações sintéticas ponta a ponta e seis controles positivos/conflito. Conferiu o relatório final, dez hashes de código, perfil lexical, staging, plano/specs/lock e relatórios históricos. A regressão completa de 570 testes é evidência executada pelo Head, não atribuída ao juiz.

A campanha encerra na quarta de no máximo cinco rodadas autorizadas; a quinta não foi necessária. O histórico das três reprovações permanece. Aprovação restrita ao subgate técnico: os 542 unknown, os 35 resumos removidos e os 97 fechos heurísticos não comprovam precisão/recall jurídico. Zero aprovação humana/publicação; G3 pleno, política OpenRouter, SLO e Linux/AWS continuam pendentes.

Handoff atualizado com o aceite real e prompt para validação empírica do usuário. Próxima sessão: P3B, builders de unidades canônicas/evidências, decisões atuais e escopadas do ledger, proveniência física, fechamento e orçamento Qwen. P3B não foi executado nesta sessão. Futuras entregas mantêm Head/Seniores, TDD, autorrevisão e juiz independente, média >=9 sem alto/crítico aberto e máximo três rodadas.
