# Parecer independente — Bloco 1 pós-P8 — R2 / Candidato 2

Data: 08/10/2026. Juiz: agente de continuação independente, sem participação na implementação do candidato (o juiz R1 original foi interrompido duas vezes por limite de uso antes de qualquer escrita ao iniciar R2; aplica-se o precedente P4 de juiz de continuação concluindo a mesma rodada). **Rodada formal 2 de no máximo 3 deste gate**, sem reabrir campanhas P4/P8, escolha Qwen ou aceites históricos. O [parecer R1](2026-10-08-bloco1-estabilizacao.md) (`70a8878cf24f5b18ec96d3f3e5c87e2c9f3a8a2a201b4b1b467d0d8bc55e97ee`) permanece histórico, **não editado**; a reprovação R1 do C1 não é reatribuída nem apagada.

## 1. Decisão

**APROVADO PARA A SAÍDA DO BLOCO 1 SEM RESSALVAS CORRETIVAS ABERTAS. Média 9,36/10.**

- **R1-B1-J1 (MÉDIO) está FECHADO no C2**, verificado por reexecução dos contraexemplos R1 originais sem modificação e por três contraprovas lógicas próprias novas.
- **Nenhum achado corretivo novo** do Bloco 1 identificado no recorte auditado; os dois defeitos observados na rodada pertencem à **fixture do próprio juiz**, classificados e reproduzidos como tal (ver §4).
- Critério de saída cumprido: média ≥ 9 **E** nenhum achado corretivo aberto do Bloco 1 **E** regressão offline integral verde (1.805 passaram / 0 falhas, JUnit conferido por parsing próprio).
- A reprovação histórica R1 (9,18, C1) permanece preservada; este veredito vale somente para os bytes congelados do C2.

C2 permanece congelado pelo manifesto [bloco1-candidato2-identidades.json](<../../data/evaluation/bloco1-candidato2-identidades.json>) (`a7d4c12eca350f86bce2e2fce96740fda991f6e80d66981433b78962f3b16b42`). Qualquer byte futuro exige nova identidade e verificação proporcional.

## 2. Autoridade, identidade e preservação

Lidos [AGENTS ancestral](<C:/Users/orugi/AGENTS.md>) e [RTK efetivo](<C:/Users/orugi/.claude/RTK.md>): a referência direta `C:/Users/orugi/RTK.md` **permanece ausente** (lacuna registrada; confirmada por `Test-Path` = False). Lidos também [contexto](<../../CONTEXT.md>), [handoff](<../HANDOFF.md>), [plano](<../../plans/2026-10-05-rag-juridico-producao.md>)/[specs v0.6](<../specs/2026-10-05-rag-juridico-producao.md>) (§§5.3, 8, 9, 10), [registro de execução do Bloco 1](<../execution/2026-10-08-bloco1-estabilizacao.md>) e o parecer R1 integral. Nenhum artefato R1 foi alterado; probes/evidências próprios usam sufixo `r2` sob [diretório do juiz](<../../data/evaluation/bloco1-juiz/>).

Conferência própria dos **bytes brutos** (sem normalização CRLF/LF), antes e **depois** de todas as execuções (`POST_RUN_IDENTITY_BAD=0`):

| Arquivo C2 | Bytes | SHA-256 raw conferido | vs C1 |
|---|---:|---|---|
| [generations.py](<../../app/retrieval/generations.py>) | 36.624 | `9577f22664107ed61081ed98f20dfc4f44a73288c9a16374aa00390dc5d36d80` | novo |
| [test_generation_finalization.py](<../../tests/test_generation_finalization.py>) | 27.077 | `b227b8296c11dbaf0c929134b23b3965f1bb1b38cc37ffd86b5cef38da42a7f7` | novo |
| [test_p8_e2e.py](<../../tests/test_p8_e2e.py>) | 17.437 | `e92f341e7e6331e381753255391e71a6c4dc769beb81809bee2604fac01f4ac4` | byte-idêntico |
| [test_p8_fixture_isolation.py](<../../tests/test_p8_fixture_isolation.py>) | 4.246 | `00bfb2d208cee6cc8e77e237075527210de591dc85c3f577c5aaeb1667b89c65` | byte-idêntico |
| [test_p8_load.py](<../../tests/test_p8_load.py>) | 32.461 | `7d2d59b74fc58cd44d5e2b749d8374200de529d5103915a29dc84c8857bd2e7a` | byte-idêntico |
| [test_p8_load_timing.py](<../../tests/test_p8_load_timing.py>) | 1.788 | `a4fe2f61fab212c43fac31fb5d6f19f361877aeb6ed057da3decd180dc294ea8` | byte-idêntico |

**19/19 controles** (plano/specs/lock/temporal/Qwen/artefato/auditorias/pareceres P3B–P7) conferem; **8/8 arquivos Qwen** inclusive `model.safetensors` conferem; **70 registros de identidades** P3B(12)+P4(14)+P5(5)+P6(18)+P7(12)+P8(9) reconferidos: **5 divergências, todas documentadas e nenhuma inesperada** — `generations.py` superseded pelo C2 (hash P4 histórico `19371d77…` preservado nos registros), `test_p8_e2e.py`/`test_p8_load.py` superseded pelo C1/C2 (os `preflight_sha256` do freeze C1 batem), `answer_runtime.py` (delta P8) e `docs/P7_API.md` (pós-divergência) como divergências conhecidas do preflight. Parecer R1 conferido íntegro: `70a8878c…` = esperado.

## 3. Evidência independente

### 3.1. Integral C2: verificada, não reexecutada

Parsing próprio do [JUnit](<../../data/evaluation/bloco1-c2-integral.xml>): **1.805 nós `testcase`, zero failure/error/skipped**, `time=2626,371 s`; hashes próprios conferem com o relatório do Head (XML `8be272cf…`, log `7c6cb3dc…`), 1 online deselecionado e 3 avisos legados no [resultado](<../../data/evaluation/bloco1-c2-integral-result.json>). A integral (43 min) não foi reexecutada pelo juiz — verificação proporcional idêntica à adotada na R1 — e as suítes afetadas foram reexecutadas por conta própria (abaixo). Evidência TDD do delta conferida nos logs `c2-02` (RED: 2 falhas, `DID NOT RAISE` + `generation_finalization_recovered attempts=2 winerror=5` em build/recover) → `c2-03` (GREEN: 2 passaram) → `c2-04` (32 controles passaram em 45,84 s).

### 3.2. Contraexemplos R1 originais reexecutados SEM modificação

Comando (perfil offline, TEMP/TMP/TMPDIR/SQLITE_TMPDIR somente de processo em `data/evaluation/p8-runtime-temp`):

```powershell
.venv\Scripts\python.exe -B -m pytest data/evaluation/bloco1-juiz/test_r1_independent.py -p tests.conftest -p no:cacheprovider -q -s -m "not online" --qwen-artifact data/models/qwen3-embedding-0.6b-97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
```

**16 passaram / 2 falharam em 62,21 s** — [log](<../../data/evaluation/bloco1-juiz/r2-r1probes-on-c2.log>)/[XML](<../../data/evaluation/bloco1-juiz/r2-r1probes-on-c2.xml>). Os 18 casos são as mesmas 18 instâncias da R1:

- **`test_elapsed_during_rechecked_path_guards_does_not_begin_new_attempt` PASS** — observação `attempt_times=[0.0]` (C1 produzia `[0.0, 2.25]`). **J1 fechado** por contraprova do próprio contraexemplo.
- 4 ports nativos recusa→release (WinError 5 real, prova canônica e ponteiro iguais) e 4 de exaustão real (~2,00 s; 36–37 tentativas; leases reobtidas; prova/reserva/ponteiro intactos) e 7 classificador PASS.
- As 2 falhas são `test_exhausting_recovery_blocks_all_three_concurrent_writers_until_unwind` com `policy_authority_reason_code_invalid`, **na linha 272** (pós-condição após unwind; as guardas até a linha 271 passaram): a fixture R1 usa `journal.block('source', …, 'judge synthetic concurrent revocation')`, cujo `reason_code` com espaços é rejeitado pela validação executável (`[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}`, [policy.py:57](<../../app/retrieval/policy.py#L57>)) **antes** de tocar qualquer autoridade concorrente. Classificação provada por probe próprio novo (§3.3): **defeito de fixture do juiz, não achado de produto**, reproduzido idêntico em C1 e C2.

### 3.3. Probes próprios novos (R2) — infraestrutura sintética física própria, não corpos de teste do implementador

- [Fronteiras lógicas/físicas](<../../data/evaluation/bloco1-juiz/test_r2_boundaries.py>) — 29 instâncias (31 condições lógicas), **todas verdes** no [log](<../../data/evaluation/bloco1-juiz/r2-probes.log>)/[XML](<../../data/evaluation/bloco1-juiz/r2-probes.xml>).
- [Guards writer/ledger/policy com reason_code válido](<../../data/evaluation/bloco1-juiz/test_r2_guard_retry.py>) — 6 instâncias, **6 verdes** em 31,14 s no [log](<../../data/evaluation/bloco1-juiz/r2-guard-final.log>)/[XML](<../../data/evaluation/bloco1-juiz/r2-guard-final.xml>).
- Regressão proporcional dos arquivos preservados + reverificação P4 selecionada: **44 passaram em 362,25 s** — [log](<../../data/evaluation/bloco1-juiz/r2-regression.log>)/[XML](<../../data/evaluation/bloco1-juiz/r2-regression.xml>).

Observações fronteira (todas `R2_OBSERVATION`/`R2_GUARD_OBSERVATION` retidas nos logs):

| Fronteira | Observação própria |
|---|---|
| Bound após guarda de destino (2ª guarda) | stall na 4ª visita de guard → `attempt_times=[0.0]`, exaustão `from last_error` |
| Bound após guarda de stage (1ª guarda, seam J1) | `attempt_times=[0.0]` |
| Limite exato | stall a 2,0 → só 1 tentativa; stall a 1,999 → retry **permitido** `[0.0, 1.999]` (bound não é excessivo) |
| Primeira tentativa em budget0 | exatamente 1 tentativa (falha e sucesso), causa preservada |
| Oversleep pós-sleep | preservado: pausa 0,05 com overshoot → exaustão, 1 tentativa |
| Causa idêntica | `__cause__` é o último `OSError` com `winerror` preservado (5/32); `ValueError('generation_finalization_unavailable')` exato |
| Eventos sanitizados | regex própria `^generation_finalization_(unavailable\|recovered) attempts=\d+ winerror=\d+$`; sem paths, sem mensagem bruta do OSError; contadores corretos (`attempts=40`, `attempts=2 winerror=5`) |
| No-overwrite/immutable | destino pré-existente (mesmo com budget0) → `generation_id_immutable` sem rename; destino criado entre tentativas → `generation_id_immutable` (precedência sobre exaustão) |
| Classificador fechado | 12/12: allowlist `{5,32,33}` retry + evento `recovered` observável; `{0,2,3,13,183}`, sem `winerror`, linux/darwin → falha imediata com a **mesma instância** do OSError original (sem wrap/máscara) |
| Exaustão nativa física | 4/4 (build/recover × embedded/server, 37–38 tentativas reais em ~2 s): prova canônica/READY/reserva/active íntegros; leases writer/ledger/policy reobtíveis; `readiness()` verdadeiro; reserva bloqueia reuso do ID |
| Recover explícito pós-release | 4/4: recusa→libera handle→sucesso com prova byte-idêntica, sem promoção, ponteiro anterior igual; evento `recovered` sanitizado |
| Guards writer/ledger→policy durante retry | 4/4 com reason_code válido: 36/36/37/37 amostras de retry com **zero ACK**; ACK somente após unwind; `policy_epoch` 0→1 e digest do ledger mudam só após unwind; após revogações deliberadas o recover explícito **falha fechado** (revalidação de autoridades) |
| Classificação do defeito de fixture | 2/2: reason_code com espaços → `policy_authority_reason_code_invalid` sem tocar autoridades; código válido aceito pelo mesmo journal |

Calibração HTTP própria (regressão, misses reais Qwen/Chroma, runtime/app/cache novos por amostra):

| Backend | 3 misses reais (s) | 2×máximo (s) | 504 observado (s) | Recuperação miss (s) |
|---|---|---:|---:|---:|
| embedded | 6,678755 / 6,542508 / 6,870743 | 13,741485 | 13,746650 | 6,423438 |
| server | 6,705868 / 6,763350 / 6,702618 | 13,526700 | 13,538720 | 6,991387 |

Sem floor/clamp (cap 30 do `HttpConfig` preservado; margem acima do cap falha explicitamente — 17 casos de timing verdes); worker físico retido `pending=1/active=1` após 504 até release; overflow 429 não alcança o gerador; health/policy disponíveis; recuperação 200 miss + hit com prova exata; final `hits=1/misses=2/entries=1`. **Não é SLO/ACC-10/carga natural.**

Isolamento de fixture (sem reabrir): 1 falha de build → **1 erro de setup + 22 consumidores aprovados**, 2 tentativas/2 raízes únicas; falha permanente → **23 erros = 23 tentativas = 23 raízes**; start falho afeta só um consumidor; sucesso reutilizado e encerrado uma vez; **nenhuma observação falha é retentada no mesmo consumidor** (`calls == attempts == unique_roots` em todos os cenários).

### 3.4. Transparência dos defeitos de fixture do próprio juiz

1. **R2-J-FIXTURE** (já presente na R1, aqui classificado): reason_code com espaços no probe R1 → 2 falhas residuais do arquivo R1; não é achado de produto.
2. **R2-J2-FIXTURE** (novo, meu): no primeiro run do probe de guards ([log](<../../data/evaluation/bloco1-juiz/r2-probes.log>), 31/35) exigi, contraditoriamente, revogações pós-unwind **e** recuperar com sucesso em seguida; o produto corretamente recusa recover sob revogação. Corrigido somente o probe ([r2-guard-final](<../../data/evaluation/bloco1-juiz/r2-guard-final.log>) 6/6), transformando a exigência na observação de fail-closed. O log inicial permanece preservado; nenhuma falha de produto foi ocultada nem inventada.

## 4. Achados

| ID | Severidade | Estado | Descrição e reprodução |
|---|---|---|---|
| R1-B1-J1 | MÉDIO | **FECHADO no C2** | Retry iniciado após prazo observado vencido na reentrada dos path guards. Fechamento verificado por: (a) reexecução sem modificação do contraexemplo R1 → `attempt_times=[0.0]`; (b) variações próprias nos dois guards e no limite exato; (c) controles 32/32 e integral verde. Condição de fechamento exigida na R1 (revalidar monotônico após os guards e imediatamente antes de retries; primeira tentativa em budget0; último OSError como causa; counters; warning sanitizado; prova/reserva/ponteiro) atendida e testada. |
| R2-J-FIXTURE | — (fixture do juiz) | classificado | `reason_code` com espaços rejeitado pela validação do policy journal; reproduzido por probe próprio com classificação explícita 2/2. **Não é achado de produto.** |
| R2-J2-FIXTURE | — (fixture do juiz) | corrigido no probe | Exigência contraditória própria; produto fail-closed confirmado. **Não é achado de produto.** |

**Nenhum achado corretivo do Bloco 1 aberto.** O mecanismo Windows (`WinError 5` por handle descendente/Chroma retido) permanece como condição ambiental tratada fail-closed dentro do contrato documentado; o processo causador do incidente histórico segue **desconhecido** (mecanismos suficientes comprovados, sem atribuição histórica) e a reavaliação em Linux/D04/P9 pertence aos Blocos 2/3 — **não** é ressalva corretiva atual. Fechamentos da R1 (amplificação de fixture; janela relativa do harness de carga) permanecem satisfatórios e foram reverificados sem reabrir.

## 5. Dez critérios e cálculo

| Critério | Nota R2 | Nota R1 | Fundamentação e limite |
|---|---:|---:|---|
| Natureza RAG | 9,4 | 9,4 | Produto extrativo/literal preservado; delta restrito à finalização de gerações; famílias físicas Qwen/Chroma reais; sem inferência jurídica ou aceite humano fabricado. |
| Arquitetura | 9,4 | 9,3 | Delta mínimo conferido byte a byte (2 arquivos novos, 4 idênticos); rechecagem monotônica após ambos os guards e imediatamente antes de `attempts += 1`/rename; precedência `generation_id_immutable`; causa PEP3110; contrato temporal explícito no docstring sem promessa hard-real-time. |
| Governança | 9,5 | 9,4 | Writer/ledger→policy sem ACK durante 36–37 retries por porta, ACK só após unwind (probe próprio com reason_code válido); no-overwrite/precedência immutable verificados; exaustão preserva prova/READY/reserva/active; recover pós-revogação falha fechado. |
| Aplicabilidade | 9,3 | 9,2 | Ambos os ports nativos com handle Windows real e relógio real; janela ~2 s observada 4/4; processo histórico do Win5 segue desconhecido (limite explícito). |
| Eficiência | 9,1 | 9,0 | Bound impõe tentativas pós-prazo (não desperdiça leases); pausas ≤50 ms; calibração relativa de carga e reuso só de sucesso preservados; sem SLO ou garantia de carga natural. |
| Acurácia técnica | 9,3 | 9,2 | Classificador fechado 12/12; causa idêntica preservada; contadores exatos; classificação honesta dos defeitos de fixture; não atribui causa universal ao Win5. |
| Precisão técnica | 9,4 | 8,8 | J1 fechado por contraprova lógica e pública próprias; bound exato (2,0 para; 1,999 permite); oversleep e budget0 preservados; contrato limitado a início de tentativas, conforme exigido. |
| Rastreabilidade | 9,5 | 9,5 | Freeze raw reconferido antes/depois; 19 controles + 8 Qwen + 70 registros; logs/XMLs/probes retidos com hashes; defeitos de fixture e execuções sobrepostas discriminados; integridade do parecer R1 preservada. |
| Observabilidade | 9,3 | 9,2 | Eventos verificados por regex própria: só categoria/contadores, sem paths/mensagem bruta; `recovered` só em sucesso eventual; `unavailable` com causa encadeada. |
| Confiabilidade | 9,4 | 8,8 | Integral verde 1.805/0; exaustão real preserva tudo e libera leases; recover explícito íntegro pós-release; garantia temporal de retries agora completa dentro do contrato documentado. |

Soma **93,6 / 10 = 9,36**, pesos iguais, média aritmética sem arredondamento. **Critério de saída cumprido: média ≥ 9, nenhum achado corretivo do Bloco 1 aberto e integral offline verde.**

## 6. Âncoras finais e continuidade

[Auditoria estruturada própria](<../../data/evaluation/bloco1-juiz/r2-result.json>) com identidades, contagens, observações, defeitos de fixture e limites. Evidências próprias R2 e hashes SHA-256 raw:

| Evidência própria | SHA-256 raw |
|---|---|
| [Probe de fronteiras](<../../data/evaluation/bloco1-juiz/test_r2_boundaries.py>) | `0eae5443ebc91732e7424dc698430a8c18baad755725b892383f49da82ea585b` |
| [Probe de guards](<../../data/evaluation/bloco1-juiz/test_r2_guard_retry.py>) | `72ea483ae03331c6973ce9f56c49bc9487477f128fba4a559e0c1dd660945843` |
| [Log reexecução R1](<../../data/evaluation/bloco1-juiz/r2-r1probes-on-c2.log>) | `cb3869867de77aad2814c250700f269fc8abdb39f8fb0f658385fd8dc5c9b91f` |
| [XML reexecução R1](<../../data/evaluation/bloco1-juiz/r2-r1probes-on-c2.xml>) | `23d3b2811f5918990bf0536dc97db345e3e6fbbda5a2561881b5a4e9e46f2312` |
| [Log probes (1º run)](<../../data/evaluation/bloco1-juiz/r2-probes.log>) | `4b08a60a2090225f694de7d4ac02b1d91ccf5888987359261ad730af18e26409` |
| [XML probes (1º run)](<../../data/evaluation/bloco1-juiz/r2-probes.xml>) | `0dce5333490b7a0070260d8cb116512e519081e025c8fe3eb777303109b1eab2` |
| [Log guards final](<../../data/evaluation/bloco1-juiz/r2-guard-final.log>) | `78f621331be545898ae8a9e610dfe66156b0244fd9359c524451c1d94074a5ae` |
| [XML guards final](<../../data/evaluation/bloco1-juiz/r2-guard-final.xml>) | `c6e703fe2ef11f2aa02b37a5631d4b5b2c18ad14b2863e0945386b3fa785056b` |
| [Log regressão](<../../data/evaluation/bloco1-juiz/r2-regression.log>) | `576d321d0c2d1995dd86621c07b0ecc8148dbeb4b9ceb4a48b29afb28dad9e11` |
| [XML regressão](<../../data/evaluation/bloco1-juiz/r2-regression.xml>) | `ef57bce4dfa194b0867ff0ee58d471c08973628c665b9d88cf9efcde2e632de3` |

Execuções próprias coletadas e encerradas: reexecução R1 16/18 (62,21 s); probes 31/35 (78,86 s, 4 falhas = fixture própria); guards 6/6 (31,14 s); regressão 44/44 (362,25 s). Somadas as instâncias lógicas verdes próprias: **29 fronteiras + 6 guards + 44 regressão = 79 observações verdes próprias**, mais 16 do arquivo R1 inalterado. Filhos Chroma/handles/threads próprios encerrados em `finally`; sem rede externa, instalação, `.env`, segredos, delete/reset/clean/commit; TEMP/TMP/TMPDIR/SQLITE_TMPDIR somente de processo. O juiz não alterou produto, testes do repositório, CONTEXT/HANDOFF, plano/specs/lock nem documentos históricos.

## 7. Limites

- **Nenhum gate liberado:** G2/G3/G4/D04/G5/G8, Linux/EC2/AWS, SLO/ACC-10, corpus real ou aprovação humana continuam pendentes. O aceite deste gate cobre somente a correção proporcional do Bloco 1 sobre as fixtures sintéticas.
- **Blocos 2 e 3 pendentes** (ambiente Linux/EC2/D04; dependências humanas, corpus e gabarito reservado) **não são ressalvas corretivas atuais**; viajam como continuidade do roadmap.
- O contrato temporal avaliado é a **impossibilidade de INICIAR tentativa nova após o prazo observado vencido**; não se exige nem se promete cancelamento hard-real-time de syscall em voo, logging ou preempção. Divergências além desse contrato seriam achado legítimo — nenhuma foi observada.
- Reproduções sintéticas (fixtures, handles injetados, stalls deliberados, relógios lógicos) medem a mecânica do produto; **não** equivalem a carga natural, gabarito jurídico ou garantia universal. Linux no classificador é injeção de plataforma, não validação operacional.
- A integral foi **conferida** (JUnit/hashes/contagens), não reexecutada pelo juiz; as suítes afetadas foram reexecutadas integralmente por conta própria. Pesos Qwen reconferidos por hash, sem reinferência.
- Reprovações históricas (P3B 8,74/8,91; P4 8,78/8,80; lexical 8,63/8,80/8,87; P7 8,94; **Bloco 1 R1 9,18/C1**) permanecem preservadas e não são reatribuídas a candidatos futuros.

**R2 encerrada: C2 APROVADO para a saída do Bloco 1 sem ressalvas corretivas abertas.** Resta uma rodada possível deste gate (3 de máximo 3), dispensável; as próximas frentes são os Blocos 2 e 3 do roadmap, com suas credenciais fora de banda e nunca versionadas.
