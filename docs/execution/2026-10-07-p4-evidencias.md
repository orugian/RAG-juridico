# P4 — gerações e revogações consistentes

Data: 07/10/2026. Head `/root`; executores Seniores Engenheiros de IA. Estado inicial: execução técnica sintética autorizada por “prossiga”; nenhum aceite antecipado.

**Estado final: P4 técnico sintético aprovado 9,22/10, rodada 3 final, candidato 3; J1/J2/J3 fechados e nenhum alto/crítico aberto identificado.** R1 8,78/R2 8,80 preservadas como históricas. G4 operacional/D04/corpus/G3 pleno/P5/P6/API/SLO/Linux/AWS não aprovados; nenhum corpus real publicado. Identidade aceita: tabela completa C2 com somente duas substituições C3 abaixo.

## Precondição e escopo

AGENTS/RTK aplicáveis, CONTEXT/HANDOFF, plano/specs v0.6 e registros/parecer P3B lidos. P3B técnico aprovado 9,20/10, rodada 3 final, J1/J2 fechados. Doze hashes atuais e parecer SHA-256 fb5e861d4ece52674bbbb120911df2e68f5f351f1353254b3614a2923273dea7 conferidos antes de editar. Plano 91a6f484016fea4d8db4834b4da95732d2445cc16b086169626799e6e7a34d21, specs a98946c4d358d8e8b548656d5eeb479a43feb5b19cc24e841dd9ae4212d7e3e4 e uv.lock 101a5ff70a84efd5c88e02a9ae8cdac2b15b63dd634f6d6aae94ed7b6b0030e4 preservados. Worktree acumulado inspecionado, sem reset/clean/commit.

Requisitos integrais P4/ACC-08: persistir prova e índices, recarga/paridade exata, embedded e servidor privado local, writer lock, IDs imutáveis, READY antes do ponteiro, promoção/rollback, pin por requisição, reuso por fingerprint e falhas/restart/restore/revogação. Não substituir servidor por mock como única evidência. Dependências instaladas: chromadb 1.5.9 (CLI Rust incluído), FastAPI 0.141.1, uvicorn 0.52.4, rank-bm25 0.2.2, joblib 1.6.0. Nenhum download/reinstalação previsto.

D04 (fonte externa durável operacional) continua pendente: construir interface e journal local em diretório independente dos snapshots apenas para exercício sintético de perda/restore. Não alegar que diretórios no mesmo computador são domínios de falha físicos distintos ou passar G4 operacional. G3 pleno/corpus humano/política OpenRouter/SLO/Linux/AWS seguem pendentes. Nenhum corpus real publicado, API habilitada ou aprovação humana fabricada. Qwen escolhido/fixado; vetores hash só em perfil explícito synthetic.

## Decomposição/propriedade antes de editar

| Frente / responsável | Arquivos exclusivos | Critério / resultado esperado |
| --- | --- | --- |
| Head — contratos e coordenação | app/retrieval/generation_contracts.py, generations.py; tests/test_p4_contracts.py, test_p4_generations.py; evidências/context/handoff | Bundle completo e imutável revalidado; staging/READY/active atômicos, trava de escritor, pin, promoção/rollback/recovery e reuso seguro; CLI explícita sem corpus/provedor por default. |
| Senior journal | app/retrieval/policy.py; tests/test_p4_policy.py | Interface de autoridade corrente e journal append-only transacional fora da raiz restaurada; epochs/source/credential/family; confirmação só após commit; perda/corrupção/ID trocado falham fechados, restore não recria autoridade implicitamente. |
| Senior prova/BM25 | app/retrieval/generation_store.py; tests/test_p4_store.py | JSON canônico completo de unidades/chunks/relações/decisões, checksums e BM25 confiável local; igualdade exata IDs/texto/metadados após recarga; sem usar projeção Chroma como fonte citável. |
| Senior dense | app/retrieval/vector_store.py; tests/test_p4_vector_store.py | Interface embedded/server explícita via chromadb mantido, coleção exclusiva por geração, projeção/digest/vetores verificáveis; testes de serviço real loopback com fixtures sintéticas, sem downloads/exportação. |
| Juiz independente após integração | docs/reviews/2026-10-07-exec-p4.md | Dez critérios do handoff, média >=9 e nenhum alto/crítico aberto, máximo três rodadas da nova entrega. Não implementa código julgado. |

Contratos compartilhados serão fixados pelo Head via Red/Green antes de delegar implementação dependente. Três frentes Senior são independentes; não editar outros arquivos nem specs/bytes aceitos de P3B sem coordenação. Nenhuma branch/PR/publicação externa autorizada. Skill ecc:python-testing orienta TDD/fixtures; pytest-cov não instalado, sem porcentagem de cobertura alegada.

## Matriz de aceite e resultado esperado

1. Round-trip literal físico, spans/mapas/references/review IDs/relações/config completos; ausência/duplicação/mutação ou incompatibilidade bloqueia antes de unpickle/consulta.
2. Paridade densa/BM25/canônico por conjunto de IDs e conteúdo, ambos backends, vetores com dimensão/configuração explícitas. Chroma server indisponível impede readiness/promoção.
3. Trava entre processos, ID nunca reutilizado (inclusive staging falho), build não promove, READY/active íntegros; leitores pinam geração até concluir.
4. Falha após dense, durante BM25, antes/depois de READY e antes/depois de active preserva estado observável recuperável, nunca conjunto parcial.
5. Reuso e rebuild equivalentes; alteração de conteúdo/metadados/config/ledger/relações exige nova derivação e não reutiliza vetor incompatível.
6. Commit de bloqueio source/credential/family incrementa epoch antes de confirmar; admissão e retorno revalidam política corrente; rollback/restore antigo não reabre dados/condições proibidos. Historical mantém suporte próprio aprovado; relação nova bloqueia linked até resolução compatível.
7. Backup → bloqueio confirmado na autoridade independente → perda simulada da raiz → restore antigo → autoridade corrente aplicada; indisponível/corrompida permanece not ready.
8. Preservar dados reais em quarentena, identidade Qwen e gates anteriores; evidências de Head e juiz separadas. Nenhum benchmark sintético substitui validação documental/SLO.

TDD → autorrevisão → juiz por entrega; falhas de ambiente não contam como Red comportamental. Atualizar resultado/identidades/handoff ao fechar o subgate técnico; não promover G4 operacional por nota de agente.

## Integração em andamento — sem aceite

Head também possui os novos entrypoints governados em indexer.py e batch.py (adição localizada, preservando helpers legados/staging); configuração permanece explícita em GenerationConfig/CLI, sem semântica herdada de Settings. Nenhum módulo P3B aceito será reescrito.

Seniores: journal 37 testes; canônico/BM25 42; dense 51 com servidor Rust real. Journal Red adicional detectou epoch booleano/rollback live; store detectou aliases Windows/overflow/metadados BM25; dense detectou normalização cosine de um ULP, adotou L2 explícito e guard da versão instalada 1.5.9 para o transporte HTTP bounded. Esses resultados são dos executores, não do juiz.

Head: contratos 4 Red interface → 4 Green, family reconciliation 1 Red → Green, identidade real Qwen 1 Red → Green (artifact_sha256 já é hash do manifesto verificado, não inventar chave adicional). Coordenação: fixtures corrigidas antes de contar Red; 13 casos iniciais Green. Red comportamental posterior detectou três mapeamentos de chunks ausentes, recovery READY ausente, readiness ignorando todos revogados e família sem head vigente. Corrigidos; parcial combinada 108 passaram/um aviso em 34,14 s. Falhas injetadas e mocks complementam, não substituem embedded/servidor reais. Não alegar cobertura percentual.

Oito casos de lote/morte real de processo/trava entre dois escritores passaram (40,79 s). Servidor completo build/READY/promote/pin/query/stop/restart/revoke passou (15,32 s), após Red de readiness propagando httpx.ConnectError. Três casos de relação/reuso/config e Qwen real local passaram (47,95 s): inferência exclusivamente em contratos sintéticos, sem consulta a provider. Reuso após mudança de ledger/registry/política foi Red→Green, conservador. Revogação durante revalidação lenta do ledger também foi Red→Green; política é revalidada depois das leituras lentas e antes do retorno.

Regressão preliminar: 1.006 passaram/um online deselecionado/dois avisos em 174,24 s. Foi coletada antes dos últimos refinamentos de testes e guard de retorno, portanto **não representa a identidade final**. Regressão final está sendo repetida sem edição de código/tests; juiz aguardará o resultado antes de iniciar rodada 1. Autorrevisão não substitui juiz nem aprovação humana real.

## Candidato 1 — identidade congelada para verificação

Doze arquivos; nenhum byte de código/testes será alterado durante julgamento. Manifest de geração também vincula configuração explícita, ledger/registry/policy, bundle e checksums; coleções verificam projeção/IDs/vetores FP32 exatos com squared-L2. Diretório local é confiável/controlado pelo operador; hash não autentica pickle externo.

| Arquivo | SHA-256 |
| --- | --- |
| app/retrieval/generation_contracts.py | 704289d56a66246ae52c68eeb1afb2059538eccb6e92ac8c86aa4987f80432a3 |
| app/retrieval/generations.py | 2ea32485892f750aa082141fc99e3953bdf069cedb1c38744205c56d2a98f670 |
| app/retrieval/generation_store.py | a6b7b195ced3dc9940ff4c28b7693d7b964d24e9fcf47052fff211b1a69d2afe |
| app/retrieval/policy.py | c645a90b56738df11f6d7d1dd2d3b4db167448800f6b613cd896f69a5a663ac6 |
| app/retrieval/vector_store.py | 88b0a6a01352c19f8832ee9a4894db99e0713b2e174e41c1c5109bc438db8216 |
| app/retrieval/indexer.py | a657a2712676fb95ff5b21a1aded963904518174fed4b3a38ff244afb571c03f |
| app/ingestion/batch.py | 90074a893de2f75be570cef10f35abf740bba8a4deac70c8c6b3cdcb72836bdf |
| tests/test_p4_contracts.py | 2819fdc5e530924f3217980747dc23d1d4f2a03dbe63cf3e0d9247105e66e771 |
| tests/test_p4_generations.py | 6e8a428c5bcbd960618bc73f1998eea1df2cf4a9ae700966d582b5a32ae9ae08 |
| tests/test_p4_store.py | 9085b10d5ece7942e10295c0e86c4c82a59069b1182815a08a5ddb53a264feda |
| tests/test_p4_policy.py | e2edd460bebd0f093ef7847cf328e6e75d6d388b4bb6c90af030120f9536b94c |
| tests/test_p4_vector_store.py | 4d64e30638ca825d07eda2d8387a32e9d45fe07b4ef8c0e7a4575ab96ef54f88 |

Doze identidades P3B, lexical/temporal, Qwen, plano/specs/lock conferidas e preservadas após implementação. Novas adições indexer/batch mudam sua identidade/preflight futuro; staging anterior não reescrito. Autoridade local não resiste a rollback integral DB+checkpoint em processo novo sem anchor externo: D04 deve fornecer journal_id/epoch confiáveis e domínio durável antes de G4 operacional. O guard live e o piso de epoch/identidade no manifest/pointer não devem ser descritos como uma solução física AWS.

Suite final focada do Head na identidade acima: **38 passaram, nenhum skip, um aviso em 108,17 s**, com Qwen local real, servidor real e filhos encerrados nos pontos de falha. Suite integrada final concluída abaixo. Staging rechecado pelo algoritmo histórico (nome completo com extensão → SHA-256, JSON sort_keys=True): 287 entradas e digest dce85621ebc9e71e7f3fec573f08db55d24314b6fa14f5a9c3043071ebcfe14b. Um probe inicial usou stem sem extensão e produziu outro digest; não foi mudança de corpus nem identidade comparável ao histórico.

Regressão **integrada final do Head no candidato 1: 1.007 passaram, um online deselecionado, nenhum skip, dois avisos em 182,90 s**. Comando do handoff com artefato Qwen local; sem acesso externo. Dois avisos pertencem aos helpers legados langchain-community/BM25/Chroma: novo backend P4 usa chromadb direto mantido. `git diff --check` passou (avisos de conversão LF/CRLF não são erro de diff). Nenhuma identidade código/tests alterada entre freeze e este resultado.

Rodada 1: autorizada ao juiz independente `/root/juiz_p4` após resultado integrado final. Não reutilizar conclusões P3B como aceite P4, nem confundir resultados de autor com probes do juiz. Estado: julgamento pendente, não aprovado.

Juiz reproduziu preliminarmente promoção indevida: revogação de unit no ledger no intervalo before_pointer (policy sem alteração) não é revalidada antes de active.json. Readiness posterior falha fechada, mas promoção retornou sucesso e tirou o ativo anterior servível. **Bloqueio material de P4 registrado; não avançar nem considerar aceite.** Candidato1 permanece congelado até parecer completo; correção deve receber Red específico, nova identidade e nova rodada, preservando histórico e limite de três.

### Rodada 1 encerrada — reprovada 8,78/10

[Parecer independente](../reviews/2026-10-07-exec-p4.md), SHA-256 1e5055ac3e12a1bd2c4e7a4f6154087a326276b464f505f3ceed341996c4720d. P4-J1 alto (commit sem nova revisão do ledger) e P4-J2 médio (ledger ausente recriado/erro SQLite na readiness), nenhum crítico identificado. Juiz: 174 focais/132,53 s e quinze cenários próprios (onze controles passaram, dois diagnósticos confirmaram defeitos, dois comportamentos esperados falharam). Não transferir nota/evidência do candidato 1 para código posterior.

Freeze candidato1 encerrado após parecer formal. Candidato2 em correção; duas rodadas restantes. Propriedade antes de editar: Head generations.py/test_p4_generations.py — Red de revogação unit antes do swap em promote/rollback/recovery, preservar ponteiro anterior e autoridade; Senior canônico nova frente independente app/retrieval/review_authority.py/tests/test_p4_review_authority.py — adapter P4 só leitura, SQLite mode=ro, sem bootstrap, com disponibilidade/identidade/schema/error boundary, consumo dos métodos de leitura atuais. ReviewStore P3B fica inalterado. Novo adapter não faz aprovações/revogação nem modifica journal ou escopos. Head integrará somente após contrato do adapter Green; juiz não implementa. Resultado esperado: confirmação de publicação somente após nova prova vigente, ledger perdido/corrompido permanece indisponível sem arquivo recriado.

TDD Head candidato2: **três Reds comportamentais** reproduziram exatamente J1 em promote/rollback e J2 em readiness, antes de corrigir. Para não deixar uma janela entre a última checagem e o swap, os contratos foram ampliados antes de implementar: Senior authority implementa hold_current() (conexão existente mode=rw para BEGIN IMMEDIATE, seguida query_only e ROLLBACK, sem DDL/DML) que bloqueia escritores do ledger; leituras comuns continuam mode=ro. Senior journal possui policy.py/test_p4_policy.py para hold_snapshot(), lock corrente retido até terminar a seção de publicação, sem reentry. Head combinará esses guards curtos no commit final de ponteiro/READY/recovery. Before_pointer fica fora da seção para uma revogação confirmada preceder efetivamente o commit.

SQLite WAL pode criar sidecars de coordenação em leitura mode=ro; isso não recria DB nem altera bytes/schema/decisões. Não usar immutable=1, que ignoraria WAL/revogações correntes. Não descrever o lease sem mutações como uma conexão mode=ro: a reserva de escrita mode=rw é necessária para excluir commits concorrentes, embora não escreva dados. Esse mecanismo é local e não resolve D04 físico/AWS.

## Candidato 2 — autorrevisão e identidade congelada

Head: três Reds J1/J2, dois Reds adicionais de ACK de revogação concorrente durante o swap real e um Red de ledger dentro da raiz de restore; todos corrigidos. Promote/rollback/READY/recover usam o mesmo guard final: reserva do ledger e snapshot da política retidos, digest e decisões correntes revalidados, depois commit estrutural. Leituras/recarga/inferência e callbacks before_pointer/before_recovery ficam fora do guard; não fazer reentry no journal. CLI consome o adapter existente, sem bootstrap/migração do ReviewStore P3B. Autoridades devem estar fora da raiz restaurada. Revogação já confirmada rejeita o candidato e preserva o ativo anterior servível; revogação concorrente só confirma depois de liberado o commit.

Seniores: adapter 43 testes mais 48 regressões do ledger P3B passaram (91/11,40 s); journal 52 passaram/6,87 s. Head: seis casos focais passaram/15,66 s; suite conjunta generations/adapter/journal **140 passaram, nenhum skip, um aviso em 147,72 s**, com Qwen e servidor Rust reais exclusivamente em fixtures sintéticas. Autorrevisão verificou ordem única dos locks, ausência de reentry, liberação em falha/morte, prevenção de criação SQLite e conservação do ponteiro anterior. Esses resultados não são juízo independente.

Quatorze arquivos congelados abaixo, sem novas edições de código/tests até conclusão da rodada 2. Candidato 1 e sua reprovação continuam históricos. Regressão integrada final do candidato 2 será executada após este freeze; não transferir o resultado 1.007 do candidato 1.

| Arquivo | SHA-256 |
| --- | --- |
| app/retrieval/generation_contracts.py | 704289d56a66246ae52c68eeb1afb2059538eccb6e92ac8c86aa4987f80432a3 |
| app/retrieval/generations.py | 0e78aae414abe9b2a1310c0a2ef8198cbee6f5d11f92f2dc49386d306198a109 |
| app/retrieval/generation_store.py | a6b7b195ced3dc9940ff4c28b7693d7b964d24e9fcf47052fff211b1a69d2afe |
| app/retrieval/policy.py | ed8ccece5c6fec95b02040f44b35d3edc8538f9c71518d92eed92d024848e8fc |
| app/retrieval/vector_store.py | 88b0a6a01352c19f8832ee9a4894db99e0713b2e174e41c1c5109bc438db8216 |
| app/retrieval/review_authority.py | 91f4b95df69c011852d2b939bf4850316588e483115cf6cac3740170f7b01c39 |
| app/retrieval/indexer.py | a657a2712676fb95ff5b21a1aded963904518174fed4b3a38ff244afb571c03f |
| app/ingestion/batch.py | 90074a893de2f75be570cef10f35abf740bba8a4deac70c8c6b3cdcb72836bdf |
| tests/test_p4_contracts.py | 2819fdc5e530924f3217980747dc23d1d4f2a03dbe63cf3e0d9247105e66e771 |
| tests/test_p4_generations.py | f263bd581579ef63c8ff0ef6a52c94aac8a4839be4f5422ef90483471a7a2ab3 |
| tests/test_p4_store.py | 9085b10d5ece7942e10295c0e86c4c82a59069b1182815a08a5ddb53a264feda |
| tests/test_p4_policy.py | dfb3684380d5eb6737907b14e92051d08f61416183ac4236b89fb48622858dcd |
| tests/test_p4_vector_store.py | 4d64e30638ca825d07eda2d8387a32e9d45fe07b4ef8c0e7a4575ab96ef54f88 |
| tests/test_p4_review_authority.py | 5c49d6bb4859211781b8d1be7aa1c82418d02863cd6d8664c09ff42f4f71af97 |

Rechecagem no candidato 2: 14 identidades P4 conferem; doze P3B e seu parecer final, plano/specs/uv.lock, temporal.py/Qwen e diagnóstico lexical/temporal final permanecem iguais. Os sete hashes ainda aplicáveis do diagnóstico lexical/temporal conferem; chunker mudou na entrega P3B aceita e indexer/batch na P4 documentada, portanto não reatribuir o diagnóstico antigo a esses bytes novos. Staging histórico confirmado a partir do input_run_id do relatório (73d18984-c7bd-4dab-a2aa-4707bfd99482), 287 entradas e digest dce85621ebc9e71e7f3fec573f08db55d24314b6fa14f5a9c3043071ebcfe14b, sem mudanças. `rtk git diff --check` passou; worktree acumulado preservado.

Regressão integrada **final do Head no candidato 2: 1.072 passaram, um online deselecionado, nenhum skip, dois avisos em 223,29 s**. Mesmo comando offline do handoff com --qwen-artifact fixado localmente. Os avisos legados não são novos defects nem migração alegada. Nenhuma edição de código/tests entre freeze e conclusão. Juiz independente autorizado à **rodada 2** somente após este resultado; J1/J2 não considerados encerrados até parecer próprio. Uma eventual terceira rodada será a última permitida nesta campanha.

Alerta material independente durante rodada 2: **P4-J3 alto**, checagens finais de finish antecedem model_copy(deep=True); revogação source confirmada durante essa cópia permite retorno normal de EvidenceChunk já bloqueado. Expected ValueError do juiz falhou e diagnóstico confirmou output_contains_blocked_source=true, epoch1 confirmado antes do retorno. Isso pertence ao contrato local P4 de retorno em voo, não à pendência D04/P5. Nenhum aceite/avanço; candidato 2 continua congelado até parecer formal completo. Somente depois começar Red específico e correção para candidato 3, última rodada permitida, sem reatribuir resultados anteriores.

Juiz /root/juiz_p4 interrompido por limite de uso antes de salvar o parecer da rodada 2 (arquivo ainda no hash formal da rodada 1). Usuário reiterou “prossiga”. Continuação independente transferida a /root/juiz_p4_continuacao, sem participação na implementação e com propriedade exclusiva do mesmo parecer; **mesma rodada 2, mesma identidade congelada**, não reiniciar contagem. Reproduções/testes novos serão atribuídos ao novo juiz; mensagens preliminares do anterior não constituem aceite ou nota. Bloqueio J3 permanece alto até correção e julgamento final.

Decomposição prevista para candidato 3, **ainda não iniciado e condicionada ao parecer formal R2**: Head possui somente generations.py/test_p4_generations.py e evidências/handoff; critério é nenhuma cópia final retornar após revogação confirmada durante sua preparação, com retenção de heads/política na fronteira final de retorno. Preparar cópias defensivas antes do guard corrente, revalidar permissões/source/units/relation/family e manter ambas autoridades até completar a fronteira local; não recapturar snapshot com reentry. Resultado esperado: source/credential/unit revogados durante a cópia rejeitam tudo, concorrência durante a checagem final só confirma após liberação, falhas liberam locks e cópias preservam literal/mapas. Nenhuma frente de implementação independente requer delegação nesse ajuste integrado; novo juiz mantém propriedade exclusiva do parecer, não implementa. Demais doze arquivos P4 e doze P3B permanecem inalterados. TDD, autorrevisão, novo freeze/regressão final e **rodada 3 final**, sem quarta rodada automática.

### Rodada 2 encerrada — reprovada 8,80/10

Parecer formal completo /root/juiz_p4_continuacao SHA-256 bc675408e6dc263bbb35483e24d14c45a86e6bd552bdb17ead19b87e4fb687c8 lido pelo Head; prefixo R1 preservado byte a byte. Juiz: 239 focais passaram/nenhum skip/um aviso em 212,07 s; onze probes finais (três expected-fail J3, três diagnósticos do defeito, cinco controles corretos) em 38,26 s. J1/J2 fechados nos contraprovas; J3 alto aberto para source/credential/unit, nenhum crítico identificado. Não somar repetições/probes intermediários nem atribuir 1.072 do Head ao juiz.

Freeze C2 encerrado somente após esse parecer. Decomposição acima passa a vigorar: candidato 3 em TDD, propriedade Head dos dois arquivos delimitados, demais identidades preservadas. **Somente rodada 3 final restante**; J3 não fecha por autorrevisão ou Green. Se reprovar ou não houver julgamento independente, não avançar e pedir direção sem inventar aceite ou quarta rodada.

## Candidato 3 — TDD/autorrevisão e freeze final

Red do Head: **cinco falharam, dois controles passaram, 45 deselecionados, um aviso em 16,25 s**. Três Reds reproduzem source/credential/unit revogados com ACK durante model_copy(deep=True) antes do retorno; dois Reds com writers reais detectam ACK indevido no checkpoint final sem lease. Green focal: **nove passaram, 43 deselecionados, nenhum skip, um aviso em 20,11 s**; inclui sete regressões novas e controles prévios de revisão lenta/família. Não considerar erros de fixture como Reds nem contar testes deselecionados como executados.

finish estabiliza IDs selecionados, valida seleção inicial e prepara todas as cópias defensivas fora do guard. Depois retém ledger→policy na mesma ordem da publicação, revalida identidade, digest corrente, query permission, source/units/relation/family e seleção completa, retornando somente o buffer já preparado. Não há cópia/inferência/backend após a autorização final; helper privado consome o snapshot retido sem recaptura/reentry. Admissão normal continua recapturando política depois de leituras lentas. Checkpoint final bloqueia writers unit/policy até liberação; exceção libera ambas autoridades; acesso query não exige operate. Cópias aninhadas permanecem independentes. É uma fronteira de saída **local P4**, não commit HTTP/API futura nem qualidade/citabilidade P5/P6.

Autorrevisão do Head verificou essas invariantes, ausência de nova escrita/migração de autoridade e preservação das correções J1/J2; diff --check passou. Skill ecc:python-testing influenciou parametrização dos Reds e fixtures permanentes exclusivamente sintéticas; não expandiu escopo/autoridade. Só generations.py/test_p4_generations.py mudaram. As **doze identidades restantes permanecem iguais à tabela completa C2**, conferidas; somente estas substituições formam as 14 identidades do candidato 3:

| Arquivo | SHA-256 candidato 3 |
| --- | --- |
| app/retrieval/generations.py | 19371d77cf233f43d0a2ddb9dd6c7e9575441f6863274f233f1a7095e0888631 |
| tests/test_p4_generations.py | b91cc0f218168bae699a9759e7eb2c3a9fa666b8e4da531fa5ed0c0afc29659b |

Código/tests congelados a partir deste ponto. Regressão integrada final pós-freeze e juiz **rodada 3 final** pendentes; J3 alto aberto no gate até novo parecer, nenhum avanço ou aprovação antecipados. Não atribuir 1.072/8,80 do candidato 2 aos novos bytes.

Regressão integrada **final do Head C3: 1.079 passaram, um online deselecionado, nenhum skip, dois avisos em 220,05 s**, mesmo comando offline do handoff com --qwen-artifact local fixado. Nenhuma edição de código/tests entre freeze e conclusão; 14 identidades conferidas, doze P3B preservadas. Avisos pertencem aos helpers legados; não alegar migração desses módulos nem cobertura percentual/SLO. Juiz independente /root/juiz_p4_continuacao autorizado à **rodada 3 final** após este resultado. Até parecer, J3 alto continua aberto e nenhum gate futuro/liberação real é autorizado.

### Rodada 3 final — aceite técnico 9,22/10 e fechamento

[Parecer independente](../reviews/2026-10-07-exec-p4.md), SHA-256 **d2d5941a52e6c89441d23eb3472c561c537edbf448f086afa51b4b9dcee2983d**, lido/conferido pelo Head. Média dos dez critérios 92,2/10 = 9,22, pesos iguais; J1/J2/J3 fechados na identidade C3, nenhum alto/crítico aberto identificado. Prefixo R1/R2 de 24.980 bytes conserva bc675408e6dc263bbb35483e24d14c45a86e6bd552bdb17ead19b87e4fb687c8. Campanha encerrada na terceira rodada, sem quarta automática; notas/resultados antigos não reatribuídos.

Juiz C3: **246 focais passaram, nenhum skip/deselecionado, um aviso em 216,01 s**, Qwen e Chroma servidor reais locais. **26 cenários próprios distintos aprovados**: 25 válidos iniciais/62,41 s mais um controle de fixture corrigido e retestado/5,94 s; não alegar um run integral verde de 26 nem somar repetições. Doze cenários de source/credential/unit × primeira/última cópia × finish/probe descartaram toda a saída; writers reais source/credential/unit não confirmaram durante o guard; falhas liberaram autoridades; família/relação, query-only/cópias aninhadas, IDs estabilizados e J1/J2 passaram. Head 1.079 integrados finais/220,05 s permanece resultado do autor, não atribuído ao juiz.

Pós-regressão C3: 14 identidades P4 conferem, doze P3B preservadas; staging rehash 287 entradas mantém dce85621ebc9e71e7f3fec573f08db55d24314b6fa14f5a9c3043071ebcfe14b. Nenhuma mudança de código/tests durante julgamento, nenhum corpus/autoridade real aprovado ou publicado. A skill ecc:python-testing orientou Reds permanentes por escopo e fixtures isoladas; não substituiu juiz ou humano.

CONTEXT/HANDOFF/registro central atualizados para o aceite real. Resultado esperado atingido no recorte local sintético: persistência/paridade, publicação/READY/recovery e fronteira de saída governadas sob autoridades correntes. Continuidade sugerida, **não implementada nesta entrega**: P5 técnico sintético, QueryPlan/seleção/ambiguidade/recuperação concorrente e closure obrigatório com modificadores inversos e orçamento Qwen. Relevância/G5 humano e gates operacionais continuam pendentes; não tratar probe P4 como recuperador avaliado ou resposta jurídica. Corpus em quarentena, política OpenRouter, D04/G4 operacional, G3 pleno e SLO/Linux/AWS preservados.

## Uso local e fronteira de confiança

`python -m app.retrieval.generations --help` verificou os comandos build/verify/promote/rollback/recover. Todos exigem --root, --generation, --configuration, --qwen-artifact, --ledger, --journal, --journal-id e --mode; servidor exige host IP loopback/porta explícitos. Build ainda exige --bundle e --source-paths; legado convertido exige --source-documents/--conversion-paths com derivado retido. Nenhum caminho/índice/corpus/staging é selecionado por default.

Configuration JSON usa a identidade efetiva do QwenEmbeddings verificado localmente e lexical_profile vigente, não nomes de modelo preenchidos à mão. CLI recusa perfil synthetic; doubles são injetados explicitamente nos testes. Ledger deve existir; CLI não o cria nem importa/fabrica decisões. Bootstrap SQLitePolicyJournal(initialize=True) é uma ação separada e exclusiva dos exercícios sintéticos; restore nunca usa initialize=True. Diretório root contém pickle local confiável, inacessível a uploads/inputs externos: uma soma SHA-256 detecta corrupção, não autentica remetente.

Build cria reserva imutável, staging, coleção exclusiva, prova/BM25/vetores/manifest, verifica recarga exata e autoridades correntes, depois READY e rename final. Nunca troca active.json. Promote/rollback exigem contexto de operador e ponteiro atômico após nova verificação. Recover só finaliza staging com READY e paridade válidas; não promove, não repara autoridade e não reaproveita ID de build falho. Gerações/stagings/reservas antigos ficam retidos: não implementar GC/limpeza sem retenção e prova de ausência de leitores. Pin verifica política de admissão; finish revalida heads e política antes do retorno. Mudança de epoch descarta requisição antiga de forma conservadora.

Probe de persistência retorna EvidenceChunks diagnósticos e separa Chroma projection da prova canônica. Não é resultado P5 de qualidade nem resposta jurídica/P6: essas frentes precisam de QueryPlan/cobertura, tokenizer/orçamento de contexto e closure obrigatória, incluindo lookup inverso de modificadores. Cli publica somente ponteiro local quando explicitamente chamado; não habilita API/usuários e não autoriza publicar acervo real.
