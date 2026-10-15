# Bloco 1 — estabilização proporcional pós-P8

## Decisão e precondições (antes de editar código/desenho)

Trabalho autorizado pelo usuário: fechar achados pós-P8, **sem reabrir P8 ou P4**. Base Git `607f35a560b30179887cb2aa99c6ef2432e9fc20`; worktree inicial limpo, acumulado preservado. Nenhum reset/clean/commit automático, nenhum segredo/corpus real/serviço externo. Plano/specs v0.6 congelados permanecem autoridade; produto extrativo com instrumento, partes efetivas, localização e literal, sem inferência jurídica.

Lidos [AGENTS ancestral](<C:/Users/orugi/AGENTS.md>) e [RTK efetivo](<C:/Users/orugi/.claude/RTK.md>): referência direta `C:/Users/orugi/RTK.md` ausente; localização efetiva confirmada nos pareceres anteriores. Sem instruções adicionais no repositório. Skills diagnose/TDD/dependências Python/ML e diagnóstico Windows carregadas. Dependências uv existentes; Python explícito da .venv, sem instalação/download. Aceites conferidos em pareceres: lexical/temporal 9,18 R4; P3B 9,20 R3 J1/J2; P4 9,22 R3 J1/J2/J3; P5 9,33 R1; P6 9,35 R2 J1–J8; P7 9,37 R2 P7-J1. Reprovações históricas preservadas. Baseline P7 histórico: 1.556 Head / 319 juiz, não resultados novos.

Auditor Senior independente: **12/12 P3B + 14/14 P4 + 5/5 P5 + 18/18 P6 = 49/49**; P7 **10/12** com somente duas divergências conhecidas (runtime aprovado como identidade P8 e documentação P7_API pós-divergência); **9/9 P8**; 19 controles adicionais e prefixos históricos P4/P7 íntegros. [Relatório raw](<../../data/evaluation/bloco1-preflight-identidades.json>). P8 permanece **aprovado com ressalvas 9,24 R1**, não nova campanha.

## Medição obrigatória pré-desenho

[Injeção na fixture original](<../../data/evaluation/bloco1-amplificacao-before-confirmed.json>): uma chamada de `build_p8_environment` lança PermissionError sintético (winerror=5); pytest executa os 23 consumidores embedded e registra **23 erros de setup, 1 tentativa de build**, 24 deselecionados, 3,78 s. Medição da mecânica real do pytest, **não** evidência de inferência Qwen/paridade nem reprodução natural do rename. Fixture/tokenizer isolados para a injeção; nenhum código alterado durante a medição.

Duas tentativas anteriores chegaram a 23 erros **antes** do build (`build_attempts=0`), devido ao temporário pytest bloqueado pelo sandbox; são diagnósticos inválidos para o achado e foram preservadas separadamente. [Diagnóstico autorizado Windows](<../../data/evaluation/bloco1-acl-recovery/acl-report-ec93dbf19e6c4c3ea9114c96c1b28fab.jsonl>): NOT_THIS_CLASS, **zero alterações de permissões**, nenhum rollback necessário. Usuário mudou explicitamente a sessão para acesso completo; leitura do mesmo caminho voltou a funcionar. Não atribuir esse bloqueio do harness ao produto.

## Hipóteses falsificáveis / feedback

1. Handle Windows sem FILE_SHARE_DELETE no staging/descendente impede rename: criar handle físico deve produzir erro; liberar handle deve permitir o mesmo rename. Senior diagnóstico mede via CreateFileW/Event.
2. Chroma mantém handle após context close: repetição com backends reais e lifecycle encerrado deve reproduzir; ausência de falha não prova inexistência universal.
3. Negativa permanente de acesso não é contenda transitória: helper jamais deve retry indiscriminado ou alegar sucesso sem rename; esgotamento mantém reserva/staging READY e ponteiro anterior.

A hipótese histórica de volume de builds foi refutada parcialmente pelo juiz P8 (primeiro build falhou); permanece não corroborada. Mecanismo de handle reproduzido não identifica por si só o processo causador do incidente histórico.

## Decomposição / propriedade / critérios / resultado esperado

| Frente | Proprietário | Arquivos exclusivos | Critério |
|---|---|---|---|
| Finalização build/recover | Senior rename | [generations.py](<../../app/retrieval/generations.py>), novo `tests/test_generation_finalization.py` | TDD no port público build/recover; tratar somente contenda Windows comprovada com espera limitada/diagnóstico explícito, sem perda de READY, IDs, writer lock ou guards ledger→policy; erro permanente falha fechado; nova identidade P4, não herda aceite C3 |
| Deadline relativo | Senior carga | [test_p8_load.py](<../../tests/test_p8_load.py>) e eventual novo teste permanente específico de harness | Medir baseline local saudável miss antes de escolher prazo relativo; provar 504 ainda retém worker, overflow 429, recuperação miss/hit e cache íntegro; sem mudar produto/SLO nem timeout global arbitrário |
| Isolamento da amplificação | Head | [test_p8_e2e.py](<../../tests/test_p8_e2e.py>), novo `tests/test_p8_fixture_isolation.py` | Após medição acima, falha de build afeta só o consumidor atual; reuso apenas de sucesso, nova raiz para nova tentativa, sem retry oculto no mesmo setup, skip ou fabricar observação; matrix report continua exigir todos os casos |
| Integração/evidências | Head | este registro, CONTEXT, HANDOFF, registro central | Freeze raw das novas identidades, reverificação P4 proporcional e regressão offline integral com Qwen real local; atualizar estado sem reatribuir históricos |
| Juiz independente | Novo juiz, sem implementação | novo `docs/reviews/2026-10-08-bloco1-estabilizacao.md` | Dez critérios, média >=9, nenhum alto/crítico, máximo três rodadas desta correção; não reabre campanhas históricas |

Frentes independentes não alteram contratos compartilhados nem arquivos de outro dono. Mudança aceita exige nova identidade e reverificação proporcional explícita. Seniores não atribuem aprovação; autorrevisão não substitui juiz.

## Saída obrigatória e limites

Regressão offline integral verde **sem ressalvas abertas do Bloco 1**, comando do handoff com `--qwen-artifact` fixado; Chroma embedded/servidor loopback e Qwen reais nas integrações. TEMP/TMP/TMPDIR/SQLITE_TMPDIR somente do processo em data/evaluation/p8-runtime-temp. Não reiniciar campanha P8, mas repetir suites afetadas como regressão da nova identidade.

Pendente: Bloco 2 Linux/EC2/AWS/P9/D04, domínio durável externo, SLO natural; Bloco 3 corpus/homologação humana G5/G8/gabarito reservado e relevância jurídica. Nenhuma aprovação humana fabricada. Credenciais apenas nos destinos já ignorados; sem leitura/exibição/versionamento. Sem commit nesta entrega; se solicitado futuramente, varrer staged antes.

## Execução / resultado

Em andamento; **nenhuma aprovação desta correção ainda**. Resultados TDD, identidades e juiz serão acrescentados conforme execuções efetivas.

### Fixture — TDD Head

[Red](<../../data/evaluation/bloco1-fixture-red.log>): 1 falhou / 1 deselecionado em 6,27 s, teste permanente executando a coleção E2E real em processo isolado comprovou 23 erros de setup para 1 build falho, quando exigia 1 erro/22 consumidores saudáveis. Falha comportamental pertinente, sem negativa de sandbox.

Mudança mínima: factory module-scoped só retém resultado **bem-sucedido**; setup potencialmente falho passa ao consumidor function-scoped. Nova tentativa de outro consumidor tem raiz própria; nenhum retry da observação falha no mesmo teste, skip ou resultado inventado. Servidor falho encerra no erro, servidor saudável encerra uma vez no teardown. Oráculos/casos/corpos dos testes E2E e critério de aggregate completo não foram alterados.

[Green inicial](<../../data/evaluation/bloco1-fixture-green.log>): 2 passaram em 15,78 s. [Green expandido](<../../data/evaluation/bloco1-fixture-green-expanded.log>) job `pwsh-90` coletado: **5 passaram em 24,49 s**. Primeiro build falho: **1 erro, 22 consumidores aprovados, 2 tentativas/2 raízes únicas** nos dois modos. Falha permanente: **23 erros correspondem a 23 tentativas distintas**, nunca um único erro memoizado. Servidor start falho também afeta só um consumidor e é encerrado; starts/stops e raízes verificados. Trata-se de medição de harness com corpos/encoder/servidor controlados, não substitui regressão física Qwen/Chroma.

[Medições pós-correção](<../../data/evaluation/bloco1-fixture-medidas-after.log>): 5 passaram em 24,33 s, com contadores observados de erros/calls/roots/starts/stops por cenário, job `pwsh-99` coletado. Queda da amplificação de 23 para 1 no erro transitório, sem transformar falha persistente em sucesso.

### Rename — diagnóstico Senior pré-implementação

[Diagnóstico físico](<../../data/evaluation/bloco1-rename-diagnostico/resultado.json>) SHA-256 `892bde523c03abdd0733d24be40e5c7979733f8ab27eb7912e9d64fd9fa5e802`: handle GENERIC_READ no **subdiretório** staging/chroma reproduz WinError5 (18/18 tentativas), inclusive com FILE_SHARE_DELETE; soltura Event→CloseHandle permite rename íntegro. API pública: 6/6 ciclos baseline embedded/server; 4/4 builds com handle retido falham Win5, 8/8 recover retidos idem e 4/4 recover pós-release passam mantendo prova/reserva/ponteiro. Lifecycle Chroma: último context close libera (3/3 rename OK); **outro PersistentClient vivo** retém o sistema e reproduz 3/3 Win5, último close libera 3/3. Código observado permaneceu idêntico ao P4 aprovado durante o diagnóstico. Mecanismo confirmado, processo causador do incidente histórico **não identificado**; sem alegação de raiz universal, sem alteração de permissões.

### Rename — TDD vertical Senior (parcial, sem aceite)

[tdd-01-build-red](<../../data/evaluation/bloco1-rename-diagnostico/tdd-01-build-red.log>): 1 falha em 4,08 s, handle real stage/chroma produz WinError5 no port build. Helper ligado **somente** ao build; [tdd-02-build-green](<../../data/evaluation/bloco1-rename-diagnostico/tdd-02-build-green.log>): 1 passou em 4,04 s. Depois teste recover adicionado antes de trocar seu rename: [tdd-03-recover-red](<../../data/evaluation/bloco1-rename-diagnostico/tdd-03-recover-red.log>): 1 falha equivalente; só então helper ligado recover; [tdd-04-recover-green](<../../data/evaluation/bloco1-rename-diagnostico/tdd-04-recover-green.log>): 2 passaram em 7,92 s. Fatias verticais, sem reimplementação P4.

Controle intermediário [tdd-05](<../../data/evaluation/bloco1-rename-diagnostico/tdd-05-controls-green.log>): **22 passaram / 2 falharam em 57,32 s**, não verde. As duas falhas eram a pós-condição de teste `ledger.latest` sem `subject_digest`; ACK/guards/rename já haviam passado. Corrigido somente o teste com digest correto, sem alterar produto para obter verde. Nome histórico do log não transforma execução em aprovação. A execução final proporcional acrescentou writer.lock/ambas portas, destino inicial e limite sem nova tentativa. [tdd-06-final-controls](<../../data/evaluation/bloco1-rename-diagnostico/tdd-06-final-controls.log>) SHA-256 `94d0480a259bba0e26214f266935f1b5f81fbf115c6fb12bf510c5b55d9d5874`: **30 passaram, zero falha/skip, 1 aviso legado, 70,11 s**. Todos os jobs Senior foram coletados e servidores filhos próprios encerrados; não há execução Senior concorrente com a regressão integral.

Helper mínimo mantém rename atômico e autoridades ledger→policy/writer retidas: janela monotônica2 s, pausas≤50 ms, somente Windows nativo {5,32,33}, destino checado em cada tentativa. Sucesso eventual emite evento INFO sanitizado; esgotamento emite WARNING sanitizado e `generation_finalization_unavailable`, preservando última cause técnica, READY/reserva e active anterior. Erros fora da allowlist não são repetidos; nenhum overwrite/recópia/rebuild/download/degradação silenciosa.

### Carga — TDD Senior e integração real afetada

[Relatório](<../../data/evaluation/bloco1-load/result.json>) SHA-256 `e28b8630d153c49b7684ffc7895b6edf6f4dd04fe80869e02412b166c9cbf906`. RED matemático legado8: 1 falhou em 5,01 s (`8 > 10`); GREEN 1 passou em 2,62 s. RED validação das medidas: 7 falharam/3 passaram em 2,97 s; GREEN 10 passaram em 3,01 s. Logs `01`–`04` no diretório do relatório. Não alegar replay HTTP legado a partir dessa seam matemática.

[Integração Qwen/Chroma real](<../../data/evaluation/bloco1-load/05-green-real-deadline.log>): **12 passaram em 185,63 s**, incluindo os dez timings então presentes e ambos backends. [Timing final](<../../data/evaluation/bloco1-load/06-green-final-timing.log>): **17 passaram em 2,60 s**; **19 casos únicos finais verdes**, não somar execuções sobrepostas como 29.

| Backend | 3 misses saudáveis reais (s) | Prazo relativo 2×máximo (s) | 504 observado (s) | Recuperação miss (s) |
|---|---|---|---|---|
| embedded | 7,819730 / 8,562980 / 6,456160 | 17,125959 | 17,135430 | 7,348429 |
| server | 10,339866 / 7,815788 / 6,352577 | 20,679732 | 20,696695 | 6,131246 |

Cada amostra usa cache/runtime/app novos, comprova miss e isolamento por métricas públicas. Configurações HTTP/P6 default30 preservadas; prazo finito **2×máximo**, sem floor/clamp, margem acima do limite 30 falha explicitamente. Watchdogs do stall/task derivados do prazo. Após 504 o worker físico continua ativo/pending=1 até release Event; overflow429 não alcança gerador/cache; health/policy funcionam; release deixa pool/cache vazios; recuperação200 miss/hit com prova exata e final hits1/misses2/entries1. Baselines locais excederam8 naturalmente, corroborando a fragilidade temporal; **não** SLO/ACC10/carga natural/garantia universal.

### C2 — implementado pelo Head (Senior sem capacidade de execução; precedente P3B documentado)

Senior rename falhou **duas vezes consecutivas sem editar nada** (6/6 C1 reconferidos byte-idênticos após cada falha, zero logs parciais). Head assumiu os dois arquivos remanescentes, mantendo o dono/desenho/decisões já validados. Nenhum outro arquivo foi tocado.

TDD vertical Head:

1. **RED** [c2-01](<../../data/evaluation/bloco1-rename-diagnostico/c2-01-red-guard-budget.log>): erro de coleta do próprio teste (faltava argumento `operation` na assinatura) — corrigido somente o teste; [c2-02 RED comportamental](<../../data/evaluation/bloco1-rename-diagnostico/c2-02-red-guard-budget.log>): **2 falharam em 4,84 s**, `DID NOT RAISE` + `generation_finalization_recovered attempts=2 winerror=5` em build e recover: C1 completava retry após orçamento vencido nos guards — reprodução fiel de R1-B1-J1.
2. **GREEN** [c2-03](<../../data/evaluation/bloco1-rename-diagnostico/c2-03-green-guard-budget.log>): 2 passaram em 4,10 s. Delta mínimo: `last_error` persistido (PEP3110), e rechecagem monotônica **depois dos dois path guards** e imediatamente antes de `attempts += 1`/rename; `last_error is not None` identifica retry, preservando primeira tentativa em budget0; exaustão nova faz `from last_error` (causa idêntica ao Win5 inicial). Destino existente continua `generation_id_immutable` com precedência. Docstring agora declara o contrato temporal exato (bound de tentativas adicionais, sem hard-real-time de syscall/logging/preempção).
3. **Controles** [c2-04](<../../data/evaluation/bloco1-rename-diagnostico/c2-04-controls-full.log>): **32 passaram em 45,84 s** = 30 controles C1 preservados + 2 novos (build/recover).

### Conformidade com os contraexemplos R1 do juiz (probe original não modificado)

Reexecução do arquivo do juiz `data/evaluation/bloco1-juiz/test_r1_independent.py` **sem qualquer edição** sobre o C2: [log](<../../data/evaluation/bloco1-conformance/r1-probes-on-c2.log>)/[XML](<../../data/evaluation/bloco1-conformance/r1-probes-on-c2.xml>) — **16 passaram / 2 falharam em 57,43 s**.

- **`test_elapsed_during_rechecked_path_guards_does_not_begin_new_attempt` PASS** — contraexemplo lógico exato de J1 fechado.
- 4 ports nativos de recusa→release, 4 de exaustão real2s e 7 classificador PASS.
- As 2 falhas são `test_exhausting_recovery_blocks_all_three_concurrent_writers_until_unwind` com `policy_authority_reason_code_invalid` — **defeito de fixture própria do juiz já documentado na R1** (reason_code com espaços em `journal.block`, corrigido pelo juiz em probe separado que passou 2/2). Reproduz idêntico em C1 e C2; guarda até a linha 272 (writer/ledger/policy sem ACK durante retries) passou. **Não é achado de produto**; cabe ao juiz repetir com seu probe corrigido na R2.

### Freeze C2

[Manifesto raw C2](<../../data/evaluation/bloco1-candidato2-identidades.json>): **2 arquivos novos vs C1** (`generations.py` `9577f2266410…`, `test_generation_finalization.py` `b227b8296c11…`) e **4 preservados byte-idênticos** (E2E, fixture isolation, load, timing). Parecer R1 ancorado `70a8878c…`; controles históricos reconferidos. Autorrevisão estática Head: delta mínimo verificado (check de orçamento só em retry após guards, precedência immutable, `last_error` PEP3110, causa idêntica, eventos sanitizados, contrato temporal explícito); `git diff --check` limpo. Não é nota nem aceite.

**Regressão integral C2 — VERDE:** job `pwsh-182` coletado exit0: **1.805 passaram, zero falhas/erros/skips, um online deselecionado, três avisos legados em 2.626,47 s (0:43:46)** — exatamente 1.803 + os 2 casos novos. [Log](<../../data/evaluation/bloco1-c2-integral.log>), [visão UTF-8](<../../data/evaluation/bloco1-c2-integral.utf8.log>), [JUnit](<../../data/evaluation/bloco1-c2-integral.xml>), [conferência](<../../data/evaluation/bloco1-c2-integral-result.json>). Pós-execução: 6/6 identidades C2, 19 controles e 66 outros paths reconferidos **inalterados**; 32 casos de finalização no XML. SHA-256: JUnit `8be272cfee4f54cd17a1f2f0f2719cd531b9221c647387ec3a999f726e8787c9`, log `7c6cb3dc9f684d8f6cc2e73c0ea91a17ed4b968eeced176ddcf370bba72fc813`, manifest C2 `a7d4c12eca350f86bce2e2fce96740fda991f6e80d66981433b78962f3b16b42`.

**Juiz autorizado para R2 (rodada 2 de máximo3)** com parecer **novo** `docs/reviews/2026-10-08-bloco1-estabilizacao-r2.md`; R1 `70a8878c…` permanece histórico preservado.

## Fechamento do gate — R2 APROVADO (08/10/2026)

**Veredito: C2 APROVADO para a saída do Bloco 1 sem ressalvas corretivas abertas — média 9,36/10, rodada 2 de máximo3.** [Parecer R2](<../../docs/reviews/2026-10-08-bloco1-estabilizacao-r2.md>) SHA-256 `0b64f38e739c46e37928d841643d6e46b41e47f45071a02ae06d93914c11f527` (conferido pelo Head). Notas: Natureza RAG 9,4 · Arquitetura 9,4 · Governança 9,5 · Aplicabilidade 9,3 · Eficiência 9,1 · Acurácia 9,3 · Precisão 9,4 · Rastreabilidade 9,5 · Observabilidade 9,3 · Confiabilidade 9,4 (93,6/10 sem arredondamento). **R1-B1-J1 FECHADO** por reexecução sem modificação do contraexemplo R1 (`attempt_times=[0.0]`; C1 dava `[0.0,2.25]`) e variações próprias nos dois guards/limite exato (2,0 s para; 1,999 s permite — bound não excessivo). Nenhum achado corretivo novo; os dois defeitos da rodada são **fixtures do próprio juiz** (reason_code com espaços; exigência contraditória própria corrigida no probe), classificados como tal com logs preservados.

Evidências próprias do juiz R2: probes de fronteira 29 instâncias/31 condições verdes; guards com reason_code válido 6/6 (36–37 amostras por porta sem ACK durante retries, ACK só após unwind); regressão proporcional 44/44 (isolation 5 + timing 17 + carga 10 + P4 12); verificação de identidades 6/6 C2 + 19 controles + 8/8 Qwen + 70 identidades P3B–P8 com 5 divergências **todas documentadas**; JUnit integral conferido por parsing próprio (1.805/0).

**Nota de proveniência (divergência de citação resolvida, sem impacto no veredito):** o juiz citou inicialmente `r2-result.json` SHA `5bf2c539…`; investigação do Head mostrou hash em disco `94ab7651b39dd031d8083d39770592b3a93ef0d9a471a03b33d13cb8e6cc1fbc` (6.940 B, mtime 18:54:39). O juiz confirmou por explicação factual: `5bf2c539…` foi calculado **antes** de editar o JSON (acrescentou `r2_review_path`/`r2_review_sha256`) e citou o valor pré-edição por falha de reancoragem; nenhum outro arquivo tem esse digest. **Âncora registrada: `94ab7651…`** (reconferida por ambas as partes). O parecer R2 não contém hashes internos obsoletos. Auditoria R2: `data/evaluation/bloco1-juiz/r2-result.json`.

**Estado final do Bloco 1: CONCLUÍDO.** Saída cumprida: integral offline verde (1.805/0 falhas) + média >=9 + nenhum achado corretivo aberto. Três frentes fechadas; R1 (reprovação) e R2 (aprovação) preservadas como históricas; terceira rodada dispensada. Reprovações históricas P0–P8 intactas; campanhas P4/P8/Qwen não reabertas; nenhum commit/reset/clean/segredo. **Pendências inalteradas (não são ressalvas corretivas):** reavaliação do Win5 em Linux/D04/P9, corpus real, G5/G8 humanos, gabarito reservado, G2/G3 pleno/G4 operacional, SLO e AWS — respectivamente Blocos 2 e 3 do roadmap. **Próxima entrega: Bloco 2 (ambiente EC2/AWS Linux e D04), credenciais fora de banda e nunca versionadas.**

**Transparência de continuidade do juiz:** o juiz R1 original foi interrompido **duas vezes antes de qualquer escrita** ao iniciar R2 (estado verificado após cada falha: sem parecer R2, sem artifacts r2, C2 freeze 6/6 e R1 íntegros). Aplicado o precedente do projeto (P4: juiz interrompido por limite de uso; juiz de continuação concluiu a mesma R2): um **juiz de continuação independente** — que não participou da implementação — recebeu briefing autossuficiente e conclui a mesma R2, preservando todos os artifacts/achados/notas R1. Não é reabertura de campanha nem troca de resultado histórico.

[Manifesto raw C1](<../../data/evaluation/bloco1-candidato1-identidades.json>), congelado em 08/10/2026 18:49:47 UTC. **66 paths únicos aceitos permanecem iguais ao preflight**, e os **19 controles** (plano/specs/lock/temporal/Qwen/pareceres/diagnósticos) permanecem íntegros. Somente três paths aceitos têm novos bytes: generations.py (P4), test_p8_e2e.py e test_p8_load.py (P8); três regressões permanentes novas. Nenhum arquivo congelado será editado durante o julgamento sem registrar novo candidato e verificação proporcional.

| Arquivo candidato | SHA-256 raw C1 |
|---|---|
| [generations.py](<../../app/retrieval/generations.py#L64-L99>) | `b6c36e6bc85f0b7005135e093372b2521da49aa5d856041e3f69fc4f8a4cde95` |
| [test_generation_finalization.py](<../../tests/test_generation_finalization.py>) | `fb724a89c22b7ee7a0a5c5d1b927b87c7cce3705b89371ebb12893d46fd8e776` |
| [test_p8_e2e.py](<../../tests/test_p8_e2e.py#L36-L80>) | `e92f341e7e6331e381753255391e71a6c4dc769beb81809bee2604fac01f4ac4` |
| [test_p8_fixture_isolation.py](<../../tests/test_p8_fixture_isolation.py>) | `00bfb2d208cee6cc8e77e237075527210de591dc85c3f577c5aaeb1667b89c65` |
| [test_p8_load.py](<../../tests/test_p8_load.py#L367-L497>) | `7d2d59b74fc58cd44d5e2b749d8374200de529d5103915a29dc84c8857bd2e7a` |
| [test_p8_load_timing.py](<../../tests/test_p8_load_timing.py>) | `a4fe2f61fab212c43fac31fb5d6f19f361877aeb6ed057da3decd180dc294ea8` |

Identidade histórica P4 de generations.py: `19371d77cf233f43d0a2ddb9dd6c7e9575441f6863274f233f1a7095e0888631`, preservada no registro/parecer P4 e no preflight. Seu aceite C3 não é transferido ao helper novo.

Autorrevisão Head: diff estreito (helper, dois call-sites e harnesses), API pública/contratos/prova/autoridades/parsers/cache/runtime mantidos; nenhum alto/crítico identificado pelo Head no recorte, **não é nota nem aceite**. Diff-check exit0, apenas avisos Git sobre possível conversão futura de EOL; identidades são bytes atuais sem normalização. Staged vazio, segredos continuam ignorados, sem commit.

### Regressão integral C1 — VERDE, juiz formal ainda pendente

Job Head `pwsh-114` **coletado, exit0**; perfil offline do handoff com `--qwen-artifact` fixado, TEMP somente processo. **1.803 passaram, zero falhas/erros/skips, um online deselecionado, três avisos legados em 3.117,44 s (0:51:57)**. [Log original](<../../data/evaluation/bloco1-c1-integral.log>), [visão UTF-8](<../../data/evaluation/bloco1-c1-integral.utf8.log>), [JUnit XML](<../../data/evaluation/bloco1-c1-integral.xml>) e [resultado/conferência pós-integral](<../../data/evaluation/bloco1-c1-integral-result.json>). Comando:

```powershell
.venv\Scripts\python.exe -m pytest tests -q -m "not online" --qwen-artifact data/models/qwen3-embedding-0.6b-97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3 --junitxml=data/evaluation/bloco1-c1-integral.xml
```

Reverificação proporcional na integral: **246 testes históricos P4 + 30 novos de finalização**, 47 E2E P8, 10 carga, 102 avaliação, 9 safety, 27 admission, 5 isolamento e 17 timing passaram. Os testes P8 foram executados **como regressão da identidade nova**, não nova campanha/nota P8. XML confirma 1.803 casos sem failure/error/skipped; seis hashes C1, 66 outros paths e 19 controles reconferidos **inalterados** após execução. SHA-256 XML `5e358851264aaa97b2ddb7710875806b3b58da3506deb643dbf4db280ee2d3e5`; log original `544d3ab7565703e960eed39891977b9328a1c56acefc7a800ac442071a14eec1`; freeze C1 `05c83f35172dcd968ceb653894a450ba5a15165d6cadc5d2d95e72a4a420b2cc`.

Juiz independente autorizado para **rodada formal 1 de máximo3** somente após integral verde, sem execução concorrente com Head. Veredito desta correção ainda pendente. Preparação estática não consumiu rodada. Preparação concluída: 6/6 candidate hashes/tamanhos, 66/66 paths preservados, 19/19 controles e prefixos históricos reconferidos pelo próprio juiz; nenhum risco corretivo material novo confirmado estaticamente. Pesos completos Qwen 8/8 permanecem prova do preflight, sem rehash pesado paralelo à integral.

### R1 — achado novo comunicado antes do laudo (C1 mantido congelado)

Juiz confirmou **R1-B1-J1 MÉDIO**: após check pós-sleep, uma demora na rechecagem `_safe_path` da segunda iteração permite começar retry com orçamento2 s já vencido. [Contraexemplo próprio](<../../data/evaluation/bloco1-juiz/test_r1_independent.py>), [XML comportamental](<../../data/evaluation/bloco1-juiz/r1-boundary.xml>): `attempt_times=[0.0,2.25]` quando exige só `[0.0]`, 1 falha, zero errors/skips, exit1. Não é falha de ambiente nem alegação hard-real-time sobre a syscall; é verificação stale **antes de iniciar nova tentativa**. Não houve perda de prova/bypass de guard identificado. Head reconheceu o achado; **saída sem ressalvas ainda NÃO atingida**, mesmo integral C1 verde. Juiz concluirá laudo R1 antes do handshake para corrigir C2; nenhum frozen file alterado durante a rodada. Confirmação pública posterior **4/4** (build/recover × embedded/server), **WinError5 nativo e relógio monotônico real**, guard deliberadamente atrasado2,20 s: segunda tentativa aos **2,262483 / 2,265380 / 2,264533 / 2,257022 s** completa indevidamente em vez de esgotar. Prova canônica e active anterior intactos; impacto confirmado no bound/duração de leases, não vazamento/perda de prova. Focal própria do juiz: **68 passaram, 2 avisos, 326,10 s**, Qwen/Chroma reais, 504/429 e recuperação verdes. Batch de probes: 31 passaram/7 falharam em 101,32 s; cinco falhas reproduzem o único achado, duas são reason_code inválido na fixture do juiz, com reteste separado pendente. Não contar erros da própria fixture como achados de produto nem ocultar reprovações válidas.

### R1 — DECISÃO FORMAL: C1 REPROVADO para saída sem ressalvas (histórica, preservada)

[Parecer R1](<../../docs/reviews/2026-10-08-bloco1-estabilizacao.md>) SHA-256 `70a8878cf24f5b18ec96d3f3e5c87e2c9f3a8a2a201b4b1b467d0d8bc55e97ee`: **rodada 1 de máximo3, média 9,18/10** (Natureza RAG 9,4 · Arquitetura 9,3 · Governança 9,4 · Aplicabilidade 9,2 · Eficiência 9,0 · Acurácia 9,2 · Precisão 8,8 · Rastreabilidade 9,5 · Observabilidade 9,2 · Confiabilidade 8,8). **R1-B1-J1 MÉDIO aberto** é o único achado corretivo; 0 alto/crítico. Saída exige integral verde **E** nenhum achado aberto do Bloco1; média>=9 não dispensa o segundo requisito. Focal juiz **68 passaram/2 avisos/326,10 s** (Qwen/Chroma reais, ambos modos). Probes finais do juiz: 38 instâncias parametrizadas → **37 condições lógicas distintas: 32 verdes/5 falhas** (5 pertencem a J1; 2 erros de fixture própria reason_code invalidado foram corrigidos em probe separado e verdes — 35/37 observações guarded-retry sem ACK durante retries, ACK só após unwind). Normalização de metadados de contagem registrada com original preservado (`r1-result.initial.json`). 6/6 C1 + 66/66 + 19/19 + 8/8 Qwen + prefixos P4/P7 reconferidos pós-laudo. Reprovação R1 fica **histórica e preservada**; não reatribuir aos candidatos futuros.

### C2 — decisões de desenho validadas antes do TDD (Head, contra os 30 controles)

1. **Precedência na reentrada:** path guards (`_safe_path` stage; `destination.exists()` → `generation_id_immutable`) primeiro; verificação de orçamento depois dos dois guards e imediatamente antes de `attempts += 1`/rename. Destino ocupado continua reportando imutabilidade, preservando o controle aceito de no-overwrite; ambos os caminhos são fail-closed.
2. **Bound = tentativas adicionais:** `attempts > 0` garante a primeira syscall mesmo com budget0 (controle zero_budget: exatamente1 tentativa). `>= deadline` após os guards interrompe retry; check pós-sleep existente permanece para oversleep sem leituras extras.
3. **Cadeia de causa:** persistir `last_error` (OSError elegível) fora do except (PEP3110); exaustão nova faz `raise ... from last_error`, `__cause__` idêntico ao Win5 inicial. Sem exceção fabricada.
4. **Contrato temporal preciso:** proíbe início de tentativa nova após prazo observado vencido; **não** promete hard-real-time sobre syscall/filesystem/logging/preempção. Limitação documentada para operação.
5. **Eventos sanitizados:** mesma semântica de contadores (attempts/winerror, sem paths); `recovered` só em sucesso eventual; exaustão usa warning `generation_finalization_unavailable`.
6. **Escopo C2:** somente [generations.py](<../../app/retrieval/generations.py>) + [test_generation_finalization.py](<../../tests/test_generation_finalization.py>); os outros **4 arquivos C1 permanecem byte-idênticos**. Novos logs nomeados `c2-*`; logs R1/C1 e probes do juiz **não são tocados**.
7. **Aceite futuro:** freeze C2 (2 novos + 4 preservados), integral offline nova com Qwen real, e **R2 em parecer novo** [`docs/reviews/2026-10-08-bloco1-estabilizacao-r2.md`](<../../docs/reviews/2026-10-08-bloco1-estabilizacao-r2.md>) — juiz reexecuta seus contraexemplos R1 **sem modificações**; R1 permanece histórico. Máximo 3 rodadas deste gate; R2 é a segunda.

### Decomposição C2 proposta antes de editar — aguardando laudo/handshake

Proprietário permanece Senior rename, **somente** [coordenador](<../../app/retrieval/generations.py>) e [regressões de finalização](<../../tests/test_generation_finalization.py>). Os outros quatro arquivos C1 devem permanecer byte-idênticos. Nada implementado nesta preparação em leitura.

- **RED público:** build/recover × guard de stage/destination com handle Windows real; relógio lógico apenas para atraso determinístico, os.rename original produz Win5 nativo. C1 inicia segunda syscall2,25 s; exigir uma única tentativa e mesma causa técnica inicial. Prova/READY/reserva/active anterior byte-idênticos, recuperação explícita depois do release.
- **GREEN mínimo:** salvar último OSError elegível em variável que persiste fora do except (PEP3110); revalidar monotônico depois dos dois pathguards/exists, imediatamente antes de iniciar retry. Se attempts>0 e orçamento esgotado, evento sanitizado e falha explícita com causa inicial. Preservar primeira tentativa mesmo com budget0, check pós-sleep, allowlist e guard order/writer/no-overwrite.
- **Contrato temporal preciso:** limitar início de tentativas adicionais quando o prazo observado já acabou; **não** prometer hard-real-time para filesystem/syscall/logging/preempção bloqueantes, nem abortar arbitrariamente a primeira syscall.
- **Verificação esperada:** 30 controles anteriores mais regressões pertinentes, freeze C2 de dois bytes novos e quatro arquivos preservados, reverificação proporcional e integral offline nova com Qwen real. Juiz R2 independente retesta seus contraexemplos R1, preserva laudo/reprovações e decide dez critérios/sem achados corretivos abertos. Não reiniciar campanhas P4/P8 nem mudar specs/modelo.

Logs originais do Tee-Object em UTF-16 permanecem íntegros; apenas visões derivadas UTF-8 foram criadas para leitura auditável pelo harness: [fixture/medidas](<../../data/evaluation/bloco1-fixture-after.utf8.log>), [carga real](<../../data/evaluation/bloco1-load-real.utf8.log>), [rename final](<../../data/evaluation/bloco1-rename-final.utf8.log>). Não substituem os hashes dos originais.
