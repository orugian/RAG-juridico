# Juízo independente EXEC-P3A-QWEN

Data: 06/10/2026. Juiz: `/root/revisao_plano_rag`. Tentativa 1 de no máximo 3. Escopo: aquisição verificável, encoder offline, configuração, diagnóstico de tokens e microbenchmark sintético. Esta revisão não avalia qualidade jurídica nem aprova G3 pleno, chunker governado, corpus, índices ou produção.

## Resultado da tentativa 1

**Não aprovado: média 8,69/10 e um achado alto aberto.** Foram encontrados um achado alto e um médio. Não foram encontrados achados críticos. O executor recebeu os achados antes de qualquer alteração do candidato examinado.

| Critério técnico do subgate | Nota |
| --- | ---: |
| Natureza do RAG | 9,2 |
| Arquitetura | 8,0 |
| Governança | 9,3 |
| Aplicabilidade | 9,0 |
| Eficiência | 8,8 |
| Acurácia técnica | 9,0 |
| Precisão técnica | 9,0 |
| Rastreabilidade | 8,3 |
| Observabilidade | 9,0 |
| Confiabilidade | 7,3 |

As notas se referem ao comportamento técnico entregue. Os bons controles de artefatos, limites, ausência de truncamento e honestidade do escopo não compensam falha reproduzível de concorrência. Não se atribui acurácia jurídica aos seis pares sintéticos.

## P3A-J1 — alta: fast tokenizer compartilhado fora da trava

Referências: `app/embeddings/qwen.py:156`, `:163`, `:168` e `:118`. `_encode` mede todos os textos com o tokenizer compartilhado antes de adquirir a trava. `_Runtime.encode`, dentro da trava, usa esse mesmo fast tokenizer com `padding=True`. As chamadas do Transformers configuram o tokenizer Rust; uma validação concorrente pode tentar alterá-lo enquanto está emprestado para outra tokenização.

Probe independente exclusivamente offline: tokenizer real do artefato local fixado, runtime de vetores sintéticos com a mesma chamada `tokenizer(texts, padding=True, truncation=False, add_special_tokens=True, return_tensors='pt')`, um único QwenEmbeddings, 400 chamadas em oito threads. Cada chamada recebeu dois documentos válidos: a frase sintética `não pagar, salvo condição expressa. ` repetida 40 vezes (522 tokens) e `curto`. Resultado: **399 RuntimeError: Already borrowed; um sucesso**. Nenhuma carga de pesos, rede ou corpus real foi necessária para reproduzir. Uma tentativa anterior usou texto acima do orçamento e foi corretamente rejeitada; não foi contada como evidência da corrida.

O problema é correção do adaptador sob chamadas concorrentes, e não o SLO futuro da API. A trava existente não serializa todo o recurso compartilhado. A interface assíncrona herdada de Embeddings também pode chamar o adaptador por executor.

Correção necessária: proteger contagem/validação/tokenização e inferência com uma fronteira comum de sincronização, mantendo validação da chamada inteira antes da primeira inferência. Evitar exposição irrestrita do tokenizer do encoder; oferecer contagem protegida ou usar tokenizer independente nos diagnósticos. Regressão concorrente deve comprovar ausência de Already borrowed, orçamento com padding, integridade dos textos e ausência de inferência parcial para entrada inválida. Não resolver a corrida silenciando a exceção ou truncando dados.

## P3A-J2 — média: inventário não vincula código do agrupador/schema

Referências: `app/embeddings/experiments.py:72`, `:142`, `:146` e `:150`. `token_inventory` executa ParsedDocument de schemas.py e `_group_blocks`/`_build_search_header` de chunker.py. `_code_identity` vincula cinco módulos do encoder e config.py, mas omite esses dois módulos de ingestão. O lock e os hashes das entradas não identificam a implementação do agrupamento efetivamente utilizada.

Assim, alterar limites/agrupamento/cabeçalhos ou validação do schema pode mudar grupos, tokens e slices sem mudar a identidade do código registrado para o inventário. O relatório atual preserva corretamente os hashes que contém, mas sua lista é incompleta. Gravidade média porque este inventário é diagnóstico, sem aprovação, reuso de embeddings ou publicação de índice.

Correção: incluir os hashes de chunker.py e schemas.py na identidade dos relatórios de inventário, com nomes de caminhos não ambíguos; acrescentar teste de sensibilidade a essas dependências. Executar novo inventário sob a nova identidade, preservando os relatórios anteriores como históricos. Não editar hashes de uma execução passada para atribuir-lhe código que não executou.

## Evidências independentes

- 43 testes focais offline passaram em 8,15 s, usando o Python da .venv e sem resolução/download de dependências.
- O artefato real foi verificado em modo somente leitura contra a lista fixa de tamanhos/SHA-256. Manifesto retornado: `025a8edceba57399e67e029505fa1be3140b4c25ec0b59488eb60beed1f57688`.
- Pooling do último token, normalização e formato Instruct/Query conferem com README e configuração de pooling da revisão local fixada; dimensão 1024 e safetensors/remote-code/offline foram conferidos no código e testes.
- Quatro probes adicionais com o tokenizer real, incluindo Unicode, emoji, caracteres combinantes, espaços, linhas e CNPJ alfanumérico sintético, preservaram todos os caracteres, offsets contíguos e limite final de 48 tokens com cabeçalho.
- Os hashes de código presentes nos relatórios finais conferem com o disco, assim como lock e fingerprint do encoder. O digest das 287 entradas de staging foi recalculado e confere com o inventário, sem alterar os artefatos.
- O p95 informado de 1,214 s do encoder é explicitamente superior à meta de 1 s da recuperação completa. O documento não simula aprovação de desempenho, qualidade jurídica ou G3; esses limites estão corretamente expostos.

## Identidade do candidato examinado

```text
qwen.py:
b2ad9dabb998f50006092aa185798f651eaa4d2dbb9ea509f668139198aa2f3f
experiments.py:
ce6fbcf376ad3031b56fb51bb94c42436775ca54bba57d81570a431869b05682
uv.lock:
101a5ff70a84efd5c88e02a9ae8cdac2b15b63dd634f6d6aae94ed7b6b0030e4
benchmark-final.json:
95bb5f6ff4409e01940aa6f1d827fdf328e582e71be185157228f0d93b436d32
token-inventory-final.json:
cab910a4652497d695fa8cddc54979f38207f51538ad2cb8f6159f32554d704b
```

## Limites e próxima rodada

A regressão completa de 435 testes foi informada pelo executor; o juiz executou os 43 focais e os probes descritos, sem repetir benchmark de inferência, download ou instalação. Nada foi enviado a provedor externo; nenhuma aprovação humana ou índice foi produzido. O único arquivo escrito pelo juiz é este parecer.

Permanecem pendentes os limites já declarados: comparação de encoders, gabarito humano, unidades canônicas/closure, migração da fronteira pública de chunking, avaliação documental, Chroma e capacidade AWS. São condições das entregas futuras; não são disfarçadas de resultados desta etapa.

Corrigir P3A-J1 por TDD e fechar P3A-J2 antes da nova submissão. A tentativa 2 deve trazer evidência concorrente e novo inventário rastreável. A eventual aprovação será somente do subgate P3A técnico.

## Tentativa 2 — aprovação técnica restrita

**Aprovado EXEC-P3A-QWEN: média 9,17/10; nenhum achado alto ou crítico aberto.** P3A-J1 e P3A-J2 foram fechados por revisão de código, testes e probes independentes. Este resultado não aprova G3 pleno, qualidade jurídica, corpus, índice, API, SLO ou AWS.

| Critério técnico do subgate | Nota |
| --- | ---: |
| Natureza do RAG | 9,2 |
| Arquitetura | 9,1 |
| Governança | 9,3 |
| Aplicabilidade | 9,1 |
| Eficiência | 8,9 |
| Acurácia técnica | 9,1 |
| Precisão técnica | 9,1 |
| Rastreabilidade | 9,4 |
| Observabilidade | 9,2 |
| Confiabilidade | 9,3 |

### Fechamento dos achados

**P3A-J1 fechado:** `app/embeddings/qwen.py:165` contém a fronteira comum de sincronização para validação integral, tokenização, formação de batches e inferência. `count_document_tokens`, em `:140`, usa a mesma trava. Não há propriedade pública retornando o tokenizer mutável. O benchmark utiliza o método protegido; o inventário conserva tokenizer próprio.

O juiz repetiu o mesmo probe da tentativa 1: tokenizer Qwen real local, runtime de vetores sintéticos e tokenização de runtime com padding/return_tensors. Foram **400 encodes corretos em oito threads, zero Already borrowed**, em contraste com 399 erros na primeira rodada. Acrescentou **80 contagens concorrentes**, todas com os 522 tokens esperados. Um pedido com documento válido seguido de documento acima do orçamento foi rejeitado sem qualquer inferência parcial. Estes resultados testam sincronização do adaptador, não throughput do modelo real.

**P3A-J2 fechado:** `app/embeddings/experiments.py:72` inclui chunker.py, schemas.py e identifiers.py na identidade, usando caminhos completos relativos ao projeto. Probe independente em árvore temporária sintética alterou cada uma dessas três dependências; seus hashes registrados mudaram em todos os casos. O juiz não modificou a implementação real para executar esse probe.

Novos relatórios foram executados sem sobrescrever a tentativa 1. Os hashes de todos os módulos presentes nos relatórios reviewed conferem com os arquivos atuais, incluindo as dependências do agrupador/schema. Lock e fingerprint do encoder também conferem. O digest das 287 entradas de staging permanece igual ao inventário anterior: 202 quarantined e 85 failed, sem promoção.

### Verificação independente da tentativa 2

Comando executado pelo juiz, sem instalação ou resolução de dependências:

```powershell
.venv/Scripts/python.exe -m pytest tests -q -m 'not online' -k qwen --qwen-artifact data/models/qwen3-embedding-0.6b-97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3
```

Resultado: **48 passaram, 393 deselecionados, um aviso de depreciação em 20,82 s**. O conjunto focal inclui a regressão com tokenizer real, validação completa antes de inferência, proteção do tokenizer, identidade de dependências, preservação de caracteres/offsets e o novo caso de candidato inteiro com contagem não monotônica. A regressão completa de 440 testes é evidência informada pelo executor; o juiz não a apresenta como repetida independentemente.

Os probes adicionais com tokenizer real e mutação de identidades descritos acima também passaram. A rede foi bloqueada no processo de probe; nenhum download, consulta externa, peso novo, segredo ou aprovação humana foi utilizado. O juiz escreveu somente este parecer.

### Identidade aprovada do subgate

```text
qwen.py:
49fa8d9e9d7176e401efb5ad5874e28abb6b245d3746e9ca8ee16a2fa1671445
experiments.py:
28fccad474c0e51c9486ee56dcfa756775b4980092d085992417eea7c8d99a79
encoder_fingerprint:
a1603b2ba5bbff287d541bbc832b3d5523f11bda84ee4305cbbadbd55fd48332
benchmark-reviewed.json:
5d5259e0a899ed9c66a5ff845b51d83208a576dce45019b796a6e41601470d81
token-inventory-reviewed.json:
405b7eb5325eec507bb92e52a865a8ae05f51c7a5a68540e3bca76f16ba715e9
uv.lock:
101a5ff70a84efd5c88e02a9ae8cdac2b15b63dd634f6d6aae94ed7b6b0030e4
input_artifacts_digest:
dce85621ebc9e71e7f3fec573f08db55d24314b6fa14f5a9c3043071ebcfe14b
```

### Condições que continuam pendentes

O novo microbenchmark reporta p95 do encoder de 1,415 s e probe de 1.022 tokens em 19,323 s. O documento reconhece o descumprimento da meta de recuperação completa de 1 s; a revisão não transforma seis pares/18 amostras em prova de capacidade de produção. A contenção por mutex corrige a corrida, mas não demonstra atendimento de concorrência/latência da API.

Comparação de encoders, referência humana, CitationUnits/closure, fronteira governada de chunking, relações documentais, Chroma, avaliação de recuperação e operação AWS permanecem expressamente pendentes. O splitter continua diagnóstico e não constitui unidade jurídica ou prova física aprovada. Os 287 candidatos não foram promovidos. A continuidade deve respeitar esses gates antes de construir/publicar índice real ou declarar G3 pleno.
