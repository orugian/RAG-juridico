# P3B — unidades canônicas e chunking governado

Data: 06/10/2026. Head: `/root`, Head Engenheiro de IA. Executores: Seniores Engenheiros de IA. Estado inicial: implementação autorizada; nenhum aceite P3B antecipado.

**Estado final: P3B técnico aprovado na rodada 3 final, 9,20/10; J1/J2 fechados, nenhum alto/crítico aberto identificado no recorte.** [Parecer independente](../reviews/2026-10-06-exec-p3b.md). As duas reprovações abaixo são históricas; não promovem corpus/G3 pleno/produção.

## Precondição conferida antes de editar

Correção lexical/temporal aprovada pelo juiz independente: 9,18/10, rodada 4, LT-J1 fechado e nenhum alto/crítico aberto identificado. Hash de temporal.py `2305cc97f9ae012b5e9305f07a2f78d99f719db4bb5f02f9fb835693192032d9`; relatório final `185f15721bcb0c456019f3220d22e8466f8584cb06d9fabbf7ea3b1e74d6c12c`. Os dez hashes de código e os dois controles do relatório conferem. Plano/specs v0.6 e uv.lock conferem com o handoff; Qwen mantém `49fa8d9e9d7176e401efb5ad5874e28abb6b245d3746e9ca8ee16a2fa1671445`. O worktree acumulado foi inspecionado e preservado.

AGENTS aplicável: C:/Users/orugi/AGENTS.md referencia RTK.md; arquivo localizado em C:/Users/orugi/.claude/RTK.md e lido integralmente (RTK 0.42.0 disponível). Não há instruções adicionais no checkout. CONTEXT, HANDOFF, plano/specs e registros/pareceres atuais foram lidos. Não editar bytes congelados.

## Decomposição e propriedade

| Frente / responsável | Arquivos exclusivos | Critério e resultado esperado |
| --- | --- | --- |
| Head: contratos, builders e integração | app/contracts.py, app/ingestion/evidence.py, app/ingestion/chunker.py, tests/test_p3b_evidence.py, docs/execution e handoff/contexto | Unidades completas com decisões distintas de fonte/partes/unidade, chunks com origem verificável e identidade sensível a texto/configuração/ledger/relações/Qwen/lexical. Fronteira pública recusa candidatos legados; helper diagnóstico explícito conserva compatibilidade. |
| Senior ledger | app/ingestion/review_store.py, tests/test_p3b_ledger.py | Decisões append-only por fonte + scope + subject_id; digest obrigatório nos escopos derivados. ID falso, decisão revogada/obsoleta ou escopo incorreto nunca aprova. Migração conserva registros históricos como scope=source. |
| Senior proveniência | app/ingestion/provenance.py, tests/test_p3b_provenance.py | Releitura local de artefato hash-verificado; DOCX por parte XML/body child/célula e PDF por página/coordenadas disponíveis. Texto original separado de numeração/cabeçalho reconstruído. Conversão sem derivado retido/hash vinculado bloqueia. |
| Senior fechamento | app/ingestion/closure.py, tests/test_p3b_closure.py | Fechamento estrutural transitivo finito, pais e unidades completas; lookup inverso base/modificador; decisões escopadas de relação/família. Ausência/ciclo/risco/conflito/orçamento insuficiente produz resultado controlado vazio. Histórico conserva sua atribuição e avisos factuais; reference_date não resolve efeitos. |
| Juiz independente, após integração | docs/reviews/2026-10-06-exec-p3b.md | Não implementa código julgado; dez critérios, média >=9, nenhum alto/crítico aberto; máximo três rodadas para esta entrega. |

Delegação começa após fixação dos contratos compartilhados pelo Head. Os executores não editam arquivos de outra frente. Integração é sequencial; testes e verificações somente leitura podem ocorrer em paralelo. Cada frente registra Red → Green e autorrevisão nesta entrega; o gate integrado recebe juiz independente, sem atribuir resultados do Head ao juiz.

## Contratos de coordenação

`SourceIdentity`: source_id, instrument_id, doc_version, file_id/hash, snapshot, configuration_version, parser_name/version. Caminhos de artefatos são entradas locais separadas. `SourceSpan.start/end` são offsets no literal físico do bloco; índices XML/célula/página são campos distintos. `CitationUnit` carrega source_identities, contract_title, parties, synthetic_context e parent_unit_id além dos campos atuais.

Ledger: `record(..., scope='source', subject_id=None, subject_digest=None)`; escopos source/parties/unit/relation/family. `latest` e `require_current(record_id=..., source_id=..., snapshot=..., file_hash=..., configuration_version=..., scope=..., subject_id=..., subject_digest=...)` validam o registro atual da chave escopada. `review_digest(value)` calcula JSON canônico SHA-256; `unit_review_digest(unit)` exclui apenas approval_state/review_record_id, preservando todo o conteúdo/contexto/dependências.

Proveniência: `verify_document_provenance(document, identity, *, original_path, conversion_path=None, conversion_sha256=None)` retorna mapa block_id → `VerifiedBlock(block_id, literal_text, synthetic_context, span)`. Verifica conjunto completo, identidades de blocos e offsets; nenhum campo verification do candidato concede autorização. Bloco somente sintético exige resultado controlado, sem transcrição fabricada.

Fechamento: `resolve_closure(selected_unit_ids, *, units, sources, ledger, relations=(), resolutions=(), relation_review_sources=None, family_review_sources=None, evidence_scope='linked_instruments', reference_date=None, count_tokens, max_tokens)` retorna `ClosureResult(status, reason_code, units, warnings, token_count, fingerprint)`. Sources são mapeadas por source_id; os mapas de relação/família apontam para source_id de ancoragem. Toda unidade consumida exige fonte, partes e unidade atuais. Medir renderização integral com título/partes/localização/contexto. Não declarar efeitos em reference_date sem adjudicação. Relação/família: digest exclui SOMENTE review_record_id, incluindo state. Unit digest exclui approval_state/review_record_id pois promoção de candidato é distinta do conteúdo revisado.

Builders: preparar candidatos determinísticos sem escrever ledger; revisar fixtures sintéticas explicitamente; construir chunks somente após conferir artefato e todas as decisões. Split de busca mantém unidade canônica e todos os caracteres, spans recortados e parent_id. Tokenizer real local Qwen, sem inferência/download; contexto/cabeçalhos entram no orçamento. CandidateSlice permanece diagnóstico.

## Limites e gate

TDD obrigatório inclui ID falso/ausente/obsoleto/revogado, hash/versão/configuração alterados, escopo indevido, spans físicos incorretos/sintéticos, preservação de negação/condição/exceção, ciclo/ausência, modificador pendente/conflitante/quarentenado, histórico, excesso Unicode/cabeçalho e reconstrução exata após split.

Dez critérios: natureza RAG, arquitetura, governança, aplicabilidade, eficiência, acurácia técnica, precisão técnica, rastreabilidade, observabilidade e confiabilidade. Gate exclusivamente técnico com fixtures sintéticas. Zero decisão humana real fabricada; staging/corpus em quarentena, OpenRouter bloqueado por política pendente. G3 pleno, comparação/avaliação documental, Chroma, SLO e Linux/AWS continuam pendentes. Nenhuma publicação/API autorizada.

## TDD, integração e autorrevisão — candidato 1

- Contratos Head: dois Red (coordenadas físicas ausentes e identidade ausente), Green de 36 casos com contratos P0. Fixture inicial usou biblioteca não instalada; corrigida para ZIP/XML sintético antes de considerar Red válido. Builders: módulo ausente e fronteira pública permissiva comprovados; Green integrado inicial de 174 casos. Migração dos testes chunker/lexical/temporal apenas troca import para helper candidato, preservando todas as asserções da correção aprovada.
- Ledger Senior: 27 Red; três Red adicionais de importação/escopo/integridade; Green de 59. state_digest: 13 Red; relation_state_digest: seis Red; Green final de 78 incluindo 48 ledger e 30 P2. Append-only e payloads legados preservados.
- Proveniência Senior: Red por módulo ausente; dois Red posteriores por quebra vazia de célula e bbox inválido. Green final de 65 incluindo 37 próprios e 28 parsers. Reread físico conserva whitespace/quebras, contexto reconstruído separado e conversão hash-vinculada.
- Fechamento Senior: Red por módulo ausente e contraexemplos incrementais, Green final de 43. Autorrevisão corrigiu supressão de modificador por trocar state e alcance inexistente. Integração Head reforçou avisos históricos, revisão final de decisões, associação rejeitada com fonte quarantined, registro de relação omitido e família obsoleta.
- Autorrevisão Head: Red comprovou chave inalterada depois de nova decisão relation em outra fonte; state_digest atual do ledger entrou na derivation_key e é revalidado no fim. Outro Red comprovou original alterado durante split ainda retornando chunks; rehash final de original/convertido passou a impedir retorno. Mapas unitários explícitos conservam offsets físicos e separadores; split recupera todos os caracteres/qualificadores. Teste real Qwen com Unicode, emoji, combinantes, negação e cabeçalho passou sem inferência.

`RelationResolution.registry_digest` é obrigatório no resolver governado e vincula heads atuais de todas as decisões relation. Nova descoberta/revisão invalida a resolução anterior mesmo se o chamador omitir o modificador. O digest global é conservador: decisões de relação não pertinentes também exigem nova resolução; escopar isso só será permitido com registro completo verificável em P4. Heads source/parties/unit/family não mudam relation_state_digest; evita dependência circular do próprio aceite de família.

Associação rejeitada exige decisão relation atual com state=rejected e identidade atual do source, mas não exige aprovação do texto do modificador retido. A família precisa ser revista sob o novo digest. Nenhum trecho quarantined é emitido. original_text preserva prova própria e avisos de risco/alteração sem converter data em efeito; linked com reference_date fica temporal_effect_unresolved até contrato específico de adjudicação temporal.

Regressão integrada do candidato congelado:

~~~powershell
.venv/Scripts/python.exe -m pytest tests -q -m 'not online' --qwen-artifact data/models/qwen3-embedding-0.6b-97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
~~~

**711 passaram, um online deselecionado, dois avisos em 48,58 s.** Os avisos LangChain/Chroma são os mesmos limites de migração pendente; nenhum download/reinstalação/provedor/inferência/corpus aprovado. Contagem completa executada pelo Head, não pelo juiz. Sem cobertura percentual alegada. Candidato enviado ao juiz na rodada 1 de no máximo três; aprovação pendente.

## Identidade congelada do candidato 1

| Arquivo | SHA-256 |
| --- | --- |
| app/contracts.py | b5364325b5409bb526d3070f73b5fd774c638cc7a90595414be0c8c287672a10 |
| app/ingestion/evidence.py | ff9876260d40eb1a1f8ba4c0bb5f8b584fc78a7d58596d8d08f61c7d251627c1 |
| app/ingestion/provenance.py | 39923e195d04ac208c3423f4e0b6dc7da23aa6588c9459acac72b3beec5dad20 |
| app/ingestion/review_store.py | 0f5dd029f5eded7f0d4ab6f924017c7b8b14bc4abfc5ef8366ce7b171dc78146 |
| app/ingestion/closure.py | ceb5cb74b27cdb032f9d4a4d4a05afe25b2f6db86252e688e2bb80ac348bd846 |
| app/ingestion/chunker.py | 160eb61a9fcbd9a98b8a07ba318659ee322227c5d80de14851f69927ad569d18 |

Nenhum hash de relatório lexical/temporal ou P3A é reatribuído aos builders novos. Plano/specs/lock/temporal/Qwen continuam identidades preservadas. Corpus histórico segue 202 quarantined/85 failed, zero revisão humana real e zero índice publicado.

## Limites técnicos explícitos

OCR não dispõe de contrato de artefato/offsets retidos e recebe ocr_proof_unavailable; conversão histórica sem derivado retido também bloqueia. PDF usa bbox somente quando glyphs medidos mapeiam exatamente; senão página/bloco verificáveis e bbox ausente. Spans de slices mantêm bbox envolvente do bloco original, sem inventar medição independente por subtrecho. Estes caminhos não passam homologação física humana por igualdade ao parser.

Preparar unidades ou construir chunks não responde consulta nem promove geração. O resolver deve ser consumido obrigatoriamente em P5/P6; persistência/paridade/journal de P4, registry durável de instrumentos relacionados e qualificação operacional continuam dependências. QwenTokenBudget carrega somente tokenizer verificado, separado do runtime de inferência; não reabre escolha do modelo nem mede SLO.

## Rodada 1 — reprovação e correção autorizada

Juiz independente: **8,74/10**, P3B-J1/J2 altos abertos; nenhum crítico. [Parecer](../reviews/2026-10-06-exec-p3b.md): 314 testes próprios e três probes independentes. J1 omite exceção de cláusula referenciada sem dependency declarado; J2 retorna primeiro original alterado durante segundo builder. Candidato 1/hashes/testes permanecem históricos. Gate aberto, sem avanço a P4/G3.

Decomposição de correção após o parecer salvo: Head edita app/contracts.py/evidence.py/chunker.py e tests/test_p3b_evidence.py (integração de referências e revalidação atômica do lote); Senior referências edita SOMENTE novo app/ingestion/references.py e tests/test_p3b_references.py (candidatos literais e vínculos propostos); Senior closure edita SOMENTE closure.py/test_p3b_closure.py (completude de candidatos, pendências/targets fora do fechamento). As frentes Senior são independentes após o contrato abaixo; Head coordena integração. Juiz não implementa e julgará candidato 2 de máximo três.

Contrato compartilhado fixado via Red: UnitReference(reference_id, block_id, start/end físicos, literal, kind, normalized_label, state=pending/bound, target_unit_ids, binding_origin=candidate/exact_label/review_mapping); CitationUnit.references/reference_labels entram no unit_review_digest. references.py: reference_candidates(unit), reference_labels_for_blocks(blocks), bind_references(units, reference_bindings=None), validate_references(unit, all_units=None). Candidatos incluem cabeçalhos, que podem vincular a própria unidade sem criar self-dependency. Rótulo exato único sugere vínculo, ainda pendente de revisão da unidade inteira; alvo ausente/ambíguo permanece pending até mapping explícito por reference_id→unit_ids. Não inferir efeito jurídico. O validador reextrai candidatos completos do literal físico e exige targets em closure_unit_ids quando externos; revisão antiga não aceita remoção de candidato/mudança de binding.

Lote público: capturar state_digest no início; construir sem retornar parcialmente; depois conferir hashes de TODOS originais/convertidos, todas decisões de unidade e digest atual do ledger antes do retorno. Red permanente cobre original/convertido anterior alterado e revogação escopada durante arquivo posterior. Nenhuma nova aprovação humana real é autorizada.

## Correções e autorrevisão — candidato 2

Os Seniores closure/references atingiram limite de uso após entrega parcial; o Head assumiu os casos restantes e a integração, sem atribuir sua implementação ao juiz. Skill ecc:python-testing orientou fixtures e Red → Green; nenhuma configuração externa/hook foi instalada.

J1: candidatos literais reextraídos do mapa físico tornam referências expressas obrigatórias, inclusive cláusula/anexo/artigo/item/parágrafo, algarismos, ordinais e romanos. Rótulos únicos locais propõem dependências, sem aprovação automática; relativos, não identificados, alvos ausentes/ambíguos e intervalos ficam pending. Mapping explícito entra no digest da unidade e exige novo aceite escopado; não reescreve a transcrição nem resolve efeitos. Listas conservam todos os rótulos/offsets; intervalos conservam a expressão inteira e não inferem unidades intermediárias. O detector é conservador e lexical, não interpretação universal da linguagem jurídica.

Red comprovado de omissão expressa/pendência e J2 multiarquivo; Green de sete regressões focais públicas. Senior referências entregou 48 casos verdes e quatro Reds válidos (lista e três intervalos); Head corrigiu esses quatro e acrescentou controles §§, anexos romanos, lista com intervalo e tentativa de vínculo exato de range. O primeiro run destes controles teve uma asserção inserida na função errada, corrigida como erro da fixture, não como Red comportamental. Closure passou a validar completude/binding após a revisão escopada; 55 casos focais verdes. Integração anterior aos quatro controles extras: 128 passaram, um tokenizer real pulado por falta de argumento, um aviso. A regressão completa abaixo usa o argumento do artefato real.

J2: fronteira pública valida todos os originais/convertidos, decisões source/parties/unit e ledger ao concluir o lote, sem retorno parcial. As cinco mutações tardias de fonte/conversão/escopos são regressões permanentes. Builder individual continua revalidando ao fim. Preparação de candidato, aprovação de fixture e fechamento não publicam corpus.

Código/testes congelados para solicitação da **rodada 2 de máximo 3**; nenhum fechamento dos altos pelo Head equivale a aceite independente. A execução completa e o parecer subsequente serão registrados separadamente. Git diff --check passou; avisos CRLF não foram tratados como modificação de conteúdo.

## Identidade congelada do candidato 2

| Arquivo | SHA-256 |
| --- | --- |
| app/contracts.py | dab3e8c9622467ef2abc4aae521911c2b42cb7a764801dd81342f574eb04bad8 |
| app/ingestion/evidence.py | 2a19382ec9fe38919a597ac7517d8c09c2165467d48e41f71937600217d1e9a8 |
| app/ingestion/provenance.py | 39923e195d04ac208c3423f4e0b6dc7da23aa6588c9459acac72b3beec5dad20 |
| app/ingestion/review_store.py | 0f5dd029f5eded7f0d4ab6f924017c7b8b14bc4abfc5ef8366ce7b171dc78146 |
| app/ingestion/closure.py | b09b4728c40d6cb3b0876d1065c7b2e6bf7d196ad7e1164722293788e07eaf65 |
| app/ingestion/references.py | 216649f8a80ac68f2ab0e761d90c9f12c9384b241f1df16864e1f43af2833ee6 |
| app/ingestion/chunker.py | db4f9d85dda5ca402c044795da8d686767fb5d36c3c66d81e8901bce4d1c7215 |
| tests/test_p3b_evidence.py | 11b82a0e6526940dc422a22c17e210e1f135aaf6cc1202fc80e68ca2b72f971c |
| tests/test_p3b_provenance.py | 65ad4c0a2b4a41857231988b52bd4bbb9f5f9d66fec90e879ecbed333c33a295 |
| tests/test_p3b_ledger.py | 4ab52a3fa16f9c974992f33a5d6ba178f1dac4400addcf088803f44dbdffd632 |
| tests/test_p3b_closure.py | 9920f3c88ab0eabc826b5e14bf5a5654f6278caa4cf1e66a0de0d86be3035962 |
| tests/test_p3b_references.py | f6d762f8ba2cd1389a5c521654d697ce3aa6c821fb26581ec51bd382cbf82e3a |

Plano/specs/uv.lock/temporal.py/qwen.py foram rehasheados e continuam iguais à precondição. Staging foi recalculado em leitura: 287 JSONs, digest dce85621ebc9e71e7f3fec573f08db55d24314b6fa14f5a9c3043071ebcfe14b; 202 quarantined/85 failed, sem mudanças. Não reatribuir os hashes históricos dos candidatos anteriores.

Regressão integrada real do candidato 2, pelo Head, mesmo comando offline registrado acima: **788 passaram, um online deselecionado, nenhum skip, dois avisos em 70,30 s**. Avisos LangChain/Chroma já conhecidos, sem afirmar correção dessas migrações. Sem inferência/download/provedor, sem cobertura percentual ou SLO alegados. Juiz acionado para rodada 2; os dois altos permaneciam abertos no gate até o parecer independente. Nenhuma etapa posterior iniciada.

## Rodada 2 — reprovação residual; decomposição do candidato 3

Parecer independente salvo: **8,91/10**, J2 fechado nos contraexemplos, J1 residual alto aberto, nenhum crítico identificado. 251 testes próprios, cinco probes integrados e matriz lexical; referência composta “cláusulas 2ª e/ou 3ª” omitida parcialmente produz complete sem terceira exceção. Diagnósticos ';' e ', bem como' não receberam reprodução física pelo juiz e não são descritos como probes integrados. Histórico/hash dos candidatos 1/2 preservados.

Após parecer salvo, candidato 2 descongelado. Head assume somente app/ingestion/references.py, tests/test_p3b_references.py e tests/test_p3b_evidence.py nesta correção; módulos closure/ledger/proveniência/builders/chunker permanecem iguais. Sem delegação de implementação dependente, em razão do limite dos Seniores. Critério: todos os rótulos explícitos de conectores compostos/espaçados e listas mistas entram na prova ou ficam pendentes; literal/offsets/revisões antigas preservados. Resultado esperado: DOCX físico de três cláusulas → builder → linked closure inclui nota fiscal e exceção de não pagar, sem escolher efeitos de e/ou; alvo ausente/ambíguo bloqueia. Testes Red antes da implementação, Green/autor­revisão/regressão offline com Qwen real antes da última revisão independente.

**Resta somente rodada 3, final de máximo três.** Se reprovada ou juiz indisponível, registrar bloqueio e pedir direção sem avançar P4/G3 ou fabricar aprovação.

## TDD/autorrevisão e congelamento — candidato 3

Red: **26 falharam**, 77 deselecionados, um aviso: 21 combinações de conectores × ordinais/algarismos/romanos, lista+range e quatro DOCX físicos distintos. Green lexical após corrigir conectores; os quatro físicos então expuseram erro de fixture (RelationResolution resolved instanciada antes do review ID). Corrigida para o fluxo candidato → digest resolved → ledger → review ID; esse erro não é Red comportamental nem aprovação real.

Autorrevisão: três Reds adicionais comprovaram que continuação explícita sem rótulo reconhecido desaparecia; passou a gerar candidato unidentified/pending. Um quarto Red confirmou conector e/ou ao fim do bloco sem espaço posterior; fallback agora preserva essa pendência também. Nenhuma remoção de candidate pode ser aceita por review unit genérico. Green focal: **161 passaram, um tokenizer real pulado por ausência de argumento, um aviso em 11,05 s**. Depois acrescentados sete controles de ordinais por extenso e oito de segundo alvo ausente/ambíguo; a regressão completa abaixo usa o argumento Qwen real e não reutiliza essa contagem focal.

O reconhecimento é lexical/conservador: conector desconhecido após separador explícito exige mapping; pode bloquear uma vírgula narrativa depois de rótulo até revisão específica, em vez de inferir se é obrigação ou lista. Conectores reconhecidos e/ou, ou/e, variantes de caixa/espaço, vírgula, ponto e vírgula, e, ou e bem como conservam todos os rótulos extraídos. Intervalo continua pending, sem expansão jurídica automática. Não alegar entendimento universal de qualquer paráfrase contratual.

Código/testes congelados. As nove identidades não listadas abaixo permanecem **iguais à tabela completa do candidato 2**, rehasheadas pelo Head; somente três arquivos foram alterados nesta correção:

| Arquivo | SHA-256 candidato 3 |
| --- | --- |
| app/ingestion/references.py | d87fff08eedef9030b492dc8ea5f664c58c7f348458f1718d0d277c5dca315a1 |
| tests/test_p3b_evidence.py | e5c9fdb9fd251e07dac44f1edb832a6a3070885cab71bdc167cc29fe8f9287ae |
| tests/test_p3b_references.py | 2da113d444bccab503f1c2211c07e12765f724a6a2087623262d7058c78c24ee |

Regressão completa offline concluída pelo Head, mesmo comando com --qwen-artifact registrado acima: **833 passaram, um online deselecionado, nenhum skip, dois avisos em 41,06 s**. Nenhuma edição de código/testes durante o run. J1 permanece alto aberto até julgamento independente do candidato 3; J2 permanece fechado na rodada 2. Sem inferência/download/provedores, cobertura percentual ou SLO alegados. A skill ecc:python-testing influenciou Reds permanentes e fixtures isoladas, sem mudar o escopo da entrega.

## Gate fechado — rodada 3 final

O Head leu o parecer salvo e conferiu a soma das dez notas: 92,0/10 = **9,20**. Juiz independente aprovou exclusivamente o candidato 3 técnico, J1 original/residual e J2 fechados e nenhum alto/crítico aberto identificado. Evidência própria do juiz: **296 testes passaram, um aviso, nenhum skip em 32,08 s; seis probes físicos com tokenizer Qwen real e 128 combinações lexicais próprias sem falha**. Os 833 testes integrados continuam atribuídos ao Head. Doze identidades candidatas conferidas pelo juiz; nenhuma edição de código/testes durante a revisão.

SHA-256 do parecer final neste fechamento: **fb5e861d4ece52674bbbb120911df2e68f5f351f1353254b3614a2923273dea7**. Identidade aceita é a tabela candidato 2 com as três substituições candidato 3 acima; não reatribuir a aprovação aos hashes anteriores. Rodadas 1/2 reprovadas 8,74/8,91 preservadas; campanha encerrada na terceira, sem quarta rodada.

CONTEXT/HANDOFF/registro central atualizados. Nenhuma implementação P4 iniciou nesta entrega; continuidade técnica sugerida: persistência canônica, gerações/paridade/journal em fixtures sintéticas, com novo plano de propriedade/TDD/gate independente. Consumo obrigatório de closure em recuperação/geração continua dependência futura. Nenhum índice real, publicação, aprovação humana, provedor externo ou recurso AWS foi autorizado por este gate. Quarentena, política de dados OpenRouter, G3 pleno e SLO/Linux/AWS permanecem pendentes; limites lexical/OCR/conversão/bbox/registry global continuam explícitos.
