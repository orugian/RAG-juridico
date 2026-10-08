# P8 — qualificação E2E técnica sintética

**Contrato da preparação técnica sintética, não G8 humano.** O estado/aceite e a rodada vigentes pertencem ao [registro](execution/2026-10-08-p8-evidencias.md), não são concedidos por esta página. Produto e fronteiras no [contexto](../CONTEXT.md) e [handoff](HANDOFF.md). Plano/specs v0.6 continuam congelados.

## Modelo de validação

O [oráculo físico](../tests/p8_corpus.py) declara antes do parsing os literais, títulos, papéis e relações sintéticos. Seis DOCX determinísticos: base de honorários Alpha/Andrade, primeiro e segundo aditivos, distrato, contrato Alpha/Omega (terceiro, banca ausente) e locação Gamma/Delta com CNPJ alfanumérico (banca ausente). Decisões do ledger são explicitamente `p8-synthetic-fixture-not-human`, não homologação. Apenas serialização DOCX e envelope de fixture são reutilizados; ingestion/build/persistência/publicação/recuperação/grounding/renderização/HTTP usam interfaces reais aceitas.

- Qwen3-Embedding-0.6B local fixado na revisão `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, 1.024 dimensões, tokenizer sem truncamento.
- Chroma embedded e servidor privado real loopback, versão instalada/fixada pelo lock; nenhum Chroma Cloud.
- Gerador: seletor de IDs determinístico sintético identificado, **não Qwen gerativo/LLM jurídico**. Política remota D01/fallback permanece fechada.
- Identificadores técnicos de unidade/chunk são obtidos da geração para localizar prova; gabarito literal/partes/localização/escopo não é obtido da resposta. Nenhuma chamada a renderer/resolver gera o oracle de citação. Texto answered e controlado também têm golden independente; nenhuma prosa arbitrária conta como fidelidade.
- Partes/título são metadados declarados da fixture e revisados pelo ledger sintético. Este caminho não qualifica a extração/adjudicação humana desses metadados no corpus real; confere sua preservação até as provas, não legitimidade jurídica documental.
- Cada execução exige `--qwen-artifact` local já existente; ausência não deve ser interpretada como homologação parcial.

## Matriz declarada

| Caso | Pedido | Prova / ação esperada |
| --- | --- | --- |
| linked_fees | Honorários vinculados | Base + dois aditivos + distrato, separados, sem prevalência inferida |
| historical_fees | Texto original | Só base, aviso histórico/modificadores conhecidos |
| conditions | Multa condicional | Seleção da cláusula integral; fechamento factual da família de sete unidades |
| exception | Confidencialidade | Seleção da cláusula + exceção; fechamento factual da família de sete unidades |
| third_party | Contrato cliente/terceiro | Alpha/Omega, sem banca como parte |
| party_identifier | CNPJ de Omega | Só instrumento com terceiro, seleção pré-ranking |
| union | CNPJ Alpha OU CNPJ Gamma | Comparação extrativa: dois primários, fechamento Alpha separado, sem inferência |
| party_intersection_absent | CNPJ Alpha E CNPJ Gamma | Nenhum instrumento com ambas as partes: abstenção |
| cross_dimension_absent | Instrumento inexistente + CNPJ Omega, union | Clamp instrumental preservado: abstenção |
| instrument_clamp_union | Instrumento Alpha + CNPJ Alpha OU Gamma | Só Alpha como primário, closure material Alpha permitido |
| alphanumeric_party | CNPJ alfanumérico Gamma | Locação Gamma/Delta, partes efetivas sem banca |
| intersection_absent | ID inexistente E CNPJ Omega | Abstenção sem prova, nenhuma ampliação |
| missing_instrument | Instrumento inexistente | Abstenção sem prova |
| missing_term | Termo sem suporte | Abstenção sem afirmar ausência jurídica |
| ambiguous | Instrumento não especificado | Esclarecimento, nenhuma escolha arbitrária |
| temporal | Data sem adjudicação de efeitos | Esclarecimento, sem inferir vigência |

O distrato da fixture declara afetadas as quatro unidades base; o fechamento obrigatório contém sete unidades (base inteira + dois aditivos + distrato). Isso deriva do mapa sintético revisado, não inferência de efeito jurídico ou prevalência. Sem esse mapa material, não presumir esse conjunto para qualquer distrato real.

Álgebra de seleção primária explicitada nesta configuração, conforme revisão contratual independente: `E = U autorizado ∩ P(ANY/ALL partyIDs) ∩ I`, onde I=U se instrument_ids ausente. union/intersection aplica aos partyIDs, não OR entre dimensões. Closure revisada em linked_instruments pode incorporar modificadores materiais fora de I; não confundir isso com ampliação arbitrária de raízes. A redação antiga geral não foi tratada como promessa inequívoca de OR entre campos. O erro desse oracle de desenvolvimento e a revisão estão preservados no [registro](execution/2026-10-08-p8-evidencias.md).

Cada uma das 16 linhas é exercitada JSON miss, JSON hit e SSE bufferizado hit sobre ambos backends (96 observações HTTP), com **258 citações** esperadas/emitidas/corretas no total (129 por backend). Igualdade semântica ignora somente envelope de correlação/tempo/cache. Critérios obrigatórios: exatidão dos quatro elementos + fonte/versão/evidence_id, nenhum item omitido/adicionado/duplicado, status/motivo/geração corretos, texto controlado sem prova. Comportamentos de segurança/indisponibilidade/orçamentos/revogação são registrados separadamente; não misturar falhas induzidas com latência natural. São **16 casos lógicos replicados entre transportes/backends, não 96 perguntas humanas independentes**, e a repetição sintética não substitui reexecução sob o runtime final nem os conjuntos humanos reservados.

## Pendência documental na admission e controle de emissão

A matriz adicional pending/conflicted exige HTTP200 `abstained/unresolved_relation`, nenhum gerador/prova/cache de conteúdo e template fixo, conforme requisitos expressos frozen ACC-14. O comportamento anterior HTTP503 (seguro contra exposição) foi preservado como RED funcional, não transformado em sucesso pelo gabarito. Usuário autorizou delta P8 proporcional no [runtime](../app/answer_runtime.py); o aceite histórico P7 continua ligado aos bytes anteriores, não é transferido para este delta.

Controle interno separado não concede pin: reconhece somente erro tipado conhecido comprovado em geração/registry/revisões atuais e vínculo pending/conflicted/proposed. Envelope/audit/autoridade são selados; emissão revalida estado e ponteiro ativo sob leases ledger→policy, sem reentrar no journal nem emitir após promoção/revogação/mudança de corpus. Erros técnicos/desconhecidos/auth/deadline mantêm vocabulário próprio. Não usar `original_text` como fallback para pergunta linked: histórico é uma execução separada com literal original aprovado e avisos de família/relação incerta. Testes de capacidade/interleaving são focais lógicos P6 com encoder sintético declarado; integração final Head conserva Qwen1024/embedded/server reais.

## Carga/limites e interpretação

Frente independente exercita workers=2 + fila=2, overflow 429, controles responsivos, recuperação e deadline 504 mantendo worker físico em operação bloqueante até término. Eventos determinísticos no gerador ou no port síncrono de contagem estabilizam o experimento; **isto não é carga natural de cinco consultas por 30 minutos nem comprovação ACC-10/SLO**. Provider abandonado por timeout mantém slot físico do gerador, não necessariamente o worker HTTP; distinguir os dois recursos.

Limites de closure/prompt recusam prova acima do orçamento sem truncamento; tamanho HTTP/rate/fila/deadline têm erros tipados e não retornam conteúdo contratual. Cache de outra configuração/política nunca é prova suficiente de autorização vigente. Regressão preserva testes anteriores de restore/rollback/publicação/auth/revogação/tracing/fallback.

## Execução e gate

```powershell
# Isolamento local desta sessão: TEMP nativo externo não está gravável.
$p = Join-Path (Get-Location) 'data\evaluation\p8-runtime-temp'
New-Item -ItemType Directory -Path $p -Force | Out-Null
$env:TEMP=$p; $env:TMP=$p; $env:TMPDIR=$p; $env:SQLITE_TMPDIR=$p
.venv\Scripts\python.exe -m pytest tests/test_p8_evaluation.py tests/test_p8_e2e.py tests/test_p8_load.py tests/test_p8_safety.py tests/test_p8_admission.py -q -m "not online" --qwen-artifact data/models/qwen3-embedding-0.6b-97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
.venv\Scripts\python.exe -m pytest tests -q -m "not online" --qwen-artifact data/models/qwen3-embedding-0.6b-97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
```

TDD vertical, autorrevisão, freeze raw e juiz independente (dez critérios, média >=9, nenhum alto/crítico aberto, máximo três rodadas). Resultados/denominadores/configuração/identidades pertencem ao [registro](execution/2026-10-08-p8-evidencias.md); não converter autoavaliação em aprovação.

## Gates que esta preparação não encerra

Gabarito documental reservado >=100 independente do desenvolvimento >=50, >=60 respondíveis, mínimos por estrato/rota e separação por família; Recall@10/precisão/sustentação com relevância humana; comparação documental/visual; Linux/Chroma produção; custos remotos; RAM/RSS combinado e carga natural/SLO confirmados D04; fonte durável externa/recovery operacional; corpus real/G2/G3 pleno/G4 operacional/G5/G8; AWS/P9. Nenhuma fixture artificial repetida satisfaz os mínimos reais. Relatórios do avaliador identificam resultados como sintéticos; denominador vazio é N/A, não 100%.
