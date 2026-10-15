# Parecer independente — Bloco 1 pós-P8 — R1 / Candidato 1

Data: 08/10/2026. Juiz: agente independente designado pelo Head, sem participação na implementação do candidato. **Rodada formal 1 de no máximo 3 deste novo gate**, autorizada pelo Head após a regressão integral. A preparação estática anterior não consumiu rodada. Não se reabrem campanhas P4/P8, escolha Qwen ou aceites históricos.

## 1. Decisão

**REPROVADO PARA A SAÍDA DO BLOCO 1 SEM RESSALVAS. Média 9,18/10.**

- Nenhum achado alto ou crítico identificado no recorte auditado.
- **Um achado MÉDIO aberto: R1-B1-J1**, nova tentativa de rename iniciada depois da janela de finalização esgotar durante a reentrada dos guards de caminho.
- Regressão integral Head verde e suíte focal própria verde **não** dispensam o requisito adicional de **nenhum achado aberto do Bloco 1**. A média supera 9, mas esse requisito de saída não foi atendido.
- Autoridades, prova canônica, reserva, imutabilidade e ponteiro anterior permaneceram protegidos nos controles executados. O defeito é do limite temporal de novas tentativas, não prova de vazamento ou perda de dados.

C1 permanece congelado até o handshake de encerramento desta rodada. Qualquer correção exige nova identidade e reverificação proporcional em R2; não se reatribuem os resultados desta rodada aos bytes futuros.

## 2. Autoridade, identidade e preservação

Lidos [AGENTS ancestral](<C:/Users/orugi/AGENTS.md>) e [RTK efetivo](<C:/Users/orugi/.claude/RTK.md>), [contexto](<../../CONTEXT.md>), [handoff](<../HANDOFF.md>), [plano](<../../plans/2026-10-05-rag-juridico-producao.md>)/[specs v0.6](<../specs/2026-10-05-rag-juridico-producao.md>), registros/pareceres finais P3B–P7, [P8 execution](<../execution/2026-10-08-p8-evidencias.md>) e [registro desta correção](<../execution/2026-10-08-bloco1-estabilizacao.md>). A referência direta RTK ancestral está ausente; a localização efetiva é a registrada nas evidências anteriores. Skill de práticas ML carregada; gerenciamento Python respeitou ambiente existente, sem instalação ou resolução de dependências.

[Preflight](<../../data/evaluation/bloco1-preflight-identidades.json>) registra **49/49 identidades P3B–P6 antes da correção**. Agora somente o coordenador de gerações supersede P4; E2E e carga supersedem suas identidades P8. Não se transfere o aceite P4 C3 ao helper novo. O delta de runtime aprovado em P8 e a divergência documental [P7_API](<../P7_API.md>) são conhecidos, não novos achados.

Conferência própria dos **bytes brutos**, sem normalização CRLF/LF, antes da execução e na auditoria final:

| Arquivo C1 | Bytes | SHA-256 raw conferido |
|---|---:|---|
| [generations.py](<../../app/retrieval/generations.py>) | 35.867 | `b6c36e6bc85f0b7005135e093372b2521da49aa5d856041e3f69fc4f8a4cde95` |
| [test_generation_finalization.py](<../../tests/test_generation_finalization.py>) | 24.378 | `fb724a89c22b7ee7a0a5c5d1b927b87c7cce3705b89371ebb12893d46fd8e776` |
| [test_p8_e2e.py](<../../tests/test_p8_e2e.py>) | 17.437 | `e92f341e7e6331e381753255391e71a6c4dc769beb81809bee2604fac01f4ac4` |
| [test_p8_fixture_isolation.py](<../../tests/test_p8_fixture_isolation.py>) | 4.246 | `00bfb2d208cee6cc8e77e237075527210de591dc85c3f577c5aaeb1667b89c65` |
| [test_p8_load.py](<../../tests/test_p8_load.py>) | 32.461 | `7d2d59b74fc58cd44d5e2b749d8374200de529d5103915a29dc84c8857bd2e7a` |
| [test_p8_load_timing.py](<../../tests/test_p8_load_timing.py>) | 1.788 | `a4fe2f61fab212c43fac31fb5d6f19f361877aeb6ed057da3decd180dc294ea8` |

**6/6 frozen, 66/66 outros paths únicos vs preflight, 19/19 controles, 12/12 controles históricos lexical/staging listados, 8/8 arquivos Qwen inclusive safetensors e 2/2 prefixos históricos íntegros.** A auditoria não recalculou o corpus/staging completo nem homologou conteúdo real. P4 preserva prefixo de 24.980 bytes, hash `bc675408e6dc263bbb35483e24d14c45a86e6bd552bdb17ead19b87e4fb687c8`; P7 preserva 23.239 bytes, hash `854e58bc2878d79ba63a5ca517abee78d94879ab80364b4fcaa20eecddb6d117`. O [freeze C1](<../../data/evaluation/bloco1-candidato1-identidades.json>) tem hash `05c83f35172dcd968ceb653894a450ba5a15165d6cadc5d2d95e72a4a420b2cc`.

O juiz não alterou produto, testes do repositório, [CONTEXT](<../../CONTEXT.md>)/[HANDOFF](<../HANDOFF.md>), [plano](<../../plans/2026-10-05-rag-juridico-producao.md>)/[specs](<../specs/2026-10-05-rag-juridico-producao.md>)/[lock](<../../uv.lock>) ou documentos históricos. Escreveu somente este parecer novo e scripts/evidências próprios sob [diretório do juiz](<../../data/evaluation/bloco1-juiz/r1-result.json>). Destino deste parecer conferido ausente antes da criação. Sem segredos, `.env`, rede externa, provedores, downloads, instalação, reset/clean/commit/delete. Filhos Chroma próprios encerrados em `finally`; handles e threads próprios liberados. TEMP/TMP/TMPDIR/SQLITE_TMPDIR definidos só nos processos, na raiz temporária local autorizada.

## 3. Evidência independente

### 3.1. Integral Head: verificada, não executada pelo juiz

[Log UTF-16 original](<../../data/evaluation/bloco1-c1-integral.log>), [visão UTF-8](<../../data/evaluation/bloco1-c1-integral.utf8.log>) e [XML](<../../data/evaluation/bloco1-c1-integral.xml>) conferidos. **1.803 passaram, um online deselecionado, três avisos legados, 3.117,44 s**. XML: 1.803 casos, zero failures/errors/skips. Job Head `pwsh-114` coletado antes da autorização R1.

Hashes próprios: log original `544d3ab7565703e960eed39891977b9328a1c56acefc7a800ac442071a14eec1`; XML `5e358851264aaa97b2ddb7710875806b3b58da3506deb643dbf4db280ee2d3e5`. Esse verde pertence ao C1 e não demonstra ausência de contraexemplos adicionais.

### 3.2. Focal própria proporcional: 68 passaram

Job juiz `pwsh-135`, exit 0, **68 passaram, zero skips/falhas, dois avisos legados em 326,10 s**. [Log](<../../data/evaluation/bloco1-juiz/r1-focal.log>)/[XML](<../../data/evaluation/bloco1-juiz/r1-focal.xml>) retidos.

Composição: 30 finalization novos, cinco isolation, 17 timing, 12 antigos P4 selecionados, dois deadline HTTP e dois E2E de família física. Seleção P4: build/paridade, IDs/reuso, READY/recover, revogação pré-pointer, guarda de swap, revogação pré-recover, checkpoint de retorno, Qwen físico real e recarga/revogação de servidor. Não se repetiu campanha P8 completa ou a integral de 52 minutos.

Perfil: `.venv/Scripts/python.exe -B -m pytest`, `-q -s -m 'not online' -p no:cacheprovider`, `--qwen-artifact` local fixado, basetemp novo e XML sob diretório próprio. Qwen real 1024-D/tokenizer real e Chroma embedded/servidor real efetivamente exercitados. Gerador de candidatos controlado por IDs, **não LLM gerativo real**. A matriz familiar mantém base, dois aditivos e distrato com citações exatas; não infere prevalência jurídica.

Calibração própria HTTP, três misses saudáveis por backend, runtime/app/cache novos por amostra:

| Backend | Misses reais (s) | 2×máximo (s) | 504 observado (s) | Recuperação miss (s) |
|---|---|---:|---:|---:|
| embedded | 5,761197 / 5,698039 / 6,151845 | 12,303691 | 12,315993 | 5,644572 |
| server | 5,939550 / 5,710509 / 5,919410 | 11,879100 | 11,895371 | 10,855008 |

504 retém worker físico ativo/pending até release; overflow 429 não alcança selector; health/policy continuam disponíveis; após release pool e cache vazios; recuperação 200 miss e hit com prova exata/equivalência, final hits=1/misses=2/entries=1. Sem floor/clamp, cap 30 HTTP/P6 preservado; baseline inválido/2×máximo acima do cap falha explicitamente. **Não SLO/ACC10/carga natural ou garantia universal de CPU.**

Isolamento observado: erro transitório gera um erro de setup/22 consumidores aprovados, duas tentativas e duas raízes; erro permanente gera 23 erros para 23 tentativas independentes. Start falho encerra somente seu servidor; sucesso é reutilizado e encerrado no teardown. Medição controlada de fixture, não substituto de prova Qwen/Chroma. Nenhuma observação falha é retentada dentro do mesmo consumidor ou reclassificada como sucesso.

### 3.3. Probes próprios e correção honesta de fixture do juiz

Infraestrutura física sintética de dados/servidor reutilizada, **não corpos dos testes do implementador**. Novas condições/assertions, handles e instrumentação próprios:

- [Probes de finalização/guards](<../../data/evaluation/bloco1-juiz/test_r1_independent.py>).
- [Corroboração pública com monotônico e handle reais](<../../data/evaluation/bloco1-juiz/test_r1_public_boundary.py>).
- [Probes de fixture/deadline/nooverwrite](<../../data/evaluation/bloco1-juiz/test_r1_harness.py>).
- [Controle de guards com fixture válida](<../../data/evaluation/bloco1-juiz/test_r1_guard_corrected.py>).

Job `pwsh-144`: [log](<../../data/evaluation/bloco1-juiz/r1-probes.log>)/[XML](<../../data/evaluation/bloco1-juiz/r1-probes.xml>), exit 1, **31 passaram / sete falharam em 101,32 s**, dois avisos. Cinco falhas são o **mesmo achado de produto**: um relógio lógico e quatro ports físicos. Duas falhas são **erro de fixture do juiz**, `reason_code` com espaços rejeitado antes do escritor de política; não defeito do produto, não Red comportamental de produto. Script/log/XML originais preservados, sem maquiar resultado.

Correção somente em novo probe próprio separado, reason_code sintético válido: job `pwsh-149`, [log](<../../data/evaluation/bloco1-juiz/r1-guards.log>)/[XML](<../../data/evaluation/bloco1-juiz/r1-guards.xml>), **dois passaram em 17,11 s**, um aviso. Confirma writer/ledger/policy concorrentes sem ACK durante 35/37 observações de retry; ACK após unwind, pointer preservado e readiness antiga falsa depois das revogações deliberadas.

**38 instâncias parametrizadas próprias válidas: 33 passaram e cinco falharam, agrupadas em um achado. São 37 condições lógicas distintas: 32 verdes e cinco falhas**, excluindo o ramo `first_start/embedded`, que usa a mesma injeção `first_build` porque embedded não tem start de servidor. Não somar essa equivalência, rechecagens, a primeira execução dirigida duplicada ou dois controles de fixture inválidos como condições finais novas. Não se apresenta a execução inicial como verde.

Controles positivos próprios: Win5 físico liberado permite build/recover íntegros em ambos backends; handle permanente esgota em aproximadamente 2,001–2,006 s, sem nova tentativa após a janela nas execuções sem stall de guard; READY/reserva/prova/active conservados e recover explícito após close funciona; todas as leases podem ser reobtidas após falha. Allowlist 5/32/33, Linux com winerror=5 e códigos não permitidos, nooverwrite antes/entre tentativas, eventos sanitizados, factory sem cache de erro e cap relativo foram exercitados. Linux nesse classificador é **injeção de plataforma**, não validação operacional Linux.

## 4. R1-B1-J1 — MÉDIO — retry começa depois do deadline na reentrada dos path guards

**Estado: ABERTO.** Local: [helper de finalização:76–95](<../../app/retrieval/generations.py#L76-L95>), consumido por [build:317–322](<../../app/retrieval/generations.py#L317-L322>) e [recover:429–431](<../../app/retrieval/generations.py#L429-L431>).

O deadline é testado após `sleep`, antes de `continue`. A iteração seguinte executa `_safe_path(stage)`, `_safe_path(destination).exists()` e incrementa o contador, **sem reverificar o relógio antes do rename**. Se o guard de caminho/escalonamento gastar o restante da janela, uma segunda operação nativa começa com orçamento vencido. O controle do implementador cobre oversleep antes de `continue`, mas não esse intervalo posterior.

Probe lógico independente: tentativas aos **0,00 e 2,25 s**, quando a única tentativa permitida era a primeira. Corroboração com handle Win5 **nativo**, monotônico **real** e chamada **pública**; só o stall de path-guard de 2,20 s é deliberadamente injetado:

| Backend / port | Início da segunda tentativa relativo à primeira (s) | Observado | Esperado |
|---|---:|---|---|
| embedded / build | 2,262483 | completou rename | falhar fechado, não iniciar novo rename |
| embedded / recover | 2,265380 | completou rename | idem |
| server / build | 2,264533 | completou rename | idem |
| server / recover | 2,257022 | completou rename | idem |

Prova canônica e active anterior iguais em **4/4**, primeira negativa real Win5 em **4/4**. Não se alega filesystem naturalmente lento nem identificação de processo histórico. Também não se exige preempção de uma syscall já iniciada ou hard real-time; exige-se **não iniciar outro retry depois que os próprios guards consumiram a janela**.

Reprodução retida, usando o perfil offline e temporários de processo descritos acima:

```powershell
.venv/Scripts/python.exe -B -m pytest data/evaluation/bloco1-juiz/test_r1_public_boundary.py -p tests.conftest -p no:cacheprovider -q -s -m 'not online' --qwen-artifact data/models/qwen3-embedding-0.6b-97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
```

**Impacto/severidade:** quebra do bound prometido na correção e prolongamento indevido das leases de autoridade; pode finalizar candidato fora da oportunidade limitada, em vez de registrar esgotamento. MÉDIO porque não foi observado bypass de autorização, overwrite, perda de literal/READY/reserva/active ou publicação automática. Com esse achado aberto, não se encerra Bloco 1 sem ressalvas.

**Condição de fechamento, sem implementação pelo juiz:** revalidar monotônico depois dos guards e imediatamente antes de retries; manter primeira tentativa inclusive budget zero, último OSError como causa no esgotamento, counters corretos, warning sanitizado e provas/reserva/ponteiro. R2 precisa contraprova lógica e pública dessa reentrada, novas identidades, regressões positivas de release/allowlist/nooverwrite/leases e integral do novo candidato. Não alterar testes para permitir rename pós-prazo ou aumentar arbitrariamente o bound.

## 5. Dez critérios e cálculo

| Critério | Nota | Fundamentação e limite |
|---|---:|---|
| Natureza RAG | 9,4 | Produto extrativo e literal preservado; família física/Qwen/Chroma reais; sem inferência jurídica ou aceite humano. |
| Arquitetura | 9,3 | Helper comum estreito, dois call-sites, guards ledger→policy/writer e separação de factories/runtime preservados. |
| Governança | 9,4 | Autoridades retidas, ACK concorrente após unwind, nooverwrite/READY/reserva/active; ausência de liberação operacional. |
| Aplicabilidade | 9,2 | Mecanismo Windows nativo e ambos backends físicos; port build/recover observado, processo histórico desconhecido. |
| Eficiência | 9,0 | Reuso só de sucesso e calibração local relativa; contenda finita usual, sem SLO ou garantia de carga natural. |
| Acurácia técnica | 9,2 | Classificação fechada e cenários de erro/cap/recovery corretos; não atribui causa universal ao diagnóstico. |
| Precisão técnica | 8,8 | Promessa de oportunidade de 2 s viola-se na reentrada dos path guards, conforme J1. |
| Rastreabilidade | 9,5 | Freeze raw, controles/prefixos, logs/XML/probes retidos, erros de fixture e resultados sobrepostos discriminados. |
| Observabilidade | 9,2 | Eventos sem paths, telemetria de baseline/504/cache; novo contraexemplo registra tempos/categorias/prova. |
| Confiabilidade | 8,8 | Integral e focal verdes, controles de exaustão/recovery fortes; ainda aceita retry após janela esgotada. |

Soma **91,8 / 10 = 9,18**, pesos iguais, sem arredondar para cumprir limiar. **Critério de saída cumulativo descumprido: um MÉDIO do Bloco 1 aberto.** Nenhum alto/crítico identificado; não se converte isso em aprovação automática.

## 6. Âncoras finais e continuidade

[Auditoria própria de preservação/resultados](<../../data/evaluation/bloco1-juiz/r1-result.json>), SHA-256 `0217df105098270bcf5bde92d8460231973a45068d8c93de0b17f5a03785eee4`, produzida por [script auditável](<../../data/evaluation/bloco1-juiz/audit_r1.py>) com normalização explícita posterior da contagem lógica equivalente descrita acima. A [saída inicial da máquina](<../../data/evaluation/bloco1-juiz/r1-result.initial.json>) foi preservada (`8d5175f700e28a11745b0d3f8e08cd7c115d2d680f332012d7c1e83522df7230`); nenhuma fonte, execução, log/XML ou nota foi alterada nessa normalização de metadados. Contém identidades completas, contagens, observações e hashes de todos os probes/logs/XML; todos os controles de bytes conferem.

| Evidência própria | SHA-256 raw |
|---|---|
| [Probe principal](<../../data/evaluation/bloco1-juiz/test_r1_independent.py>) | `7f97b949a9c7dad581833d5687701ada9b0d45e1a4c80956437b24d2296e8e0c` |
| [Probe port público](<../../data/evaluation/bloco1-juiz/test_r1_public_boundary.py>) | `7237edeeaf604c37b75a2f294b4b3c8c153526f4fc998a7b590b14d6618a9ab2` |
| [Probe harness](<../../data/evaluation/bloco1-juiz/test_r1_harness.py>) | `152c310964e26fb1cb8c46c4bc7681272f7b1561b2d7a3c8667f8df7500d55de` |
| [Probe guards válido](<../../data/evaluation/bloco1-juiz/test_r1_guard_corrected.py>) | `c8cbf3334a439ab54d53601b5f9a6bf7c8ba5fb5e64f02d3b5a9147c4773643b` |
| [XML focal](<../../data/evaluation/bloco1-juiz/r1-focal.xml>) | `cdffc786d13d65cd026f5c3104acb9b625e33f163796adff22b68668adc97989` |
| [Log focal](<../../data/evaluation/bloco1-juiz/r1-focal.log>) | `bb722f135142c2f112ee1693b6aaa8e7df2cbc8207b39ca74c1d7d757443eaef` |
| [XML probes](<../../data/evaluation/bloco1-juiz/r1-probes.xml>) | `039521018eed660dde7bff2dc9b427d8e675bf5211da6b1c20d27b270f7da55c` |
| [Log probes](<../../data/evaluation/bloco1-juiz/r1-probes.log>) | `4a31a0f0ccb47f401e735973fcab766be3fedaf0dacc858bf34443d262f0b83b` |
| [XML guards válidos](<../../data/evaluation/bloco1-juiz/r1-guards.xml>) | `96bd6069c60713e11cdd171524a48e276fac789de584d21e77654ecedd2d9812` |

Jobs próprios **pwsh-135, pwsh-144, pwsh-149 coletados**; nenhuma execução própria pendente. A primeira contraprova dirigida permanece retida como diagnóstico duplicado, não rodada extra nem novo caso final.

**Fechamentos nesta R1:** amplificação de fixture e janela absoluta de 8 s do harness têm controles próprios satisfatórios; o tratamento Win5 tem mecanismos/guards/exaustão comprovados, mas sua garantia temporal permanece incompleta por J1. **Aberto somente R1-B1-J1, MÉDIO.** O proprietário/processo do Win5 histórico continua desconhecido; handle descendente e outro cliente Chroma retido são mecanismos suficientes comprovados, não atribuição histórica.

Bloco 2 Linux/EC2/AWS/D04 e Bloco 3 corpus humano/G5/G8/gabarito reservado/relevância permanecem dependências do roadmap, **não ressalvas corretivas deste gate**. Nenhum corpus real, política externa, SLO, produção ou aprovação humana foi antecipado.

**R1 encerrada: C1 não aprovado para saída sem ressalvas.** Head deve preservar esta reprovação e as evidências, realizar handshake antes de descongelar C1 e, se autorizado, produzir C2 proporcional para R2. Permanecem duas rodadas possíveis desta correção, não uma campanha P4/P8 reiniciada.
