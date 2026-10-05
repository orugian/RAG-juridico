# Relatório de Conclusão - Tarefa 6: Extrator Jurídico de Metadados Reais (`metadata_extractor.py`)

## 1. Visão Geral
- **Objetivo**: Implementar o motor determinístico e semântico de extração de metadados contratuais reais em `app/ingestion/metadata_extractor.py` e sua suíte de testes em `tests/test_metadata_extractor.py`. O extrator atende à ressalva máxima do projeto: extrair com fidedignidade absoluta partes, papéis contratuais (`PartyRole`), identificadores (CNPJ/CPF vinculados à entidade e isolando representantes legais), tipo de instrumento e data de celebração diretamente do preâmbulo e fecho do documento.
- **Branch**: `feat/data-prep-pipeline`
- **Commit**: `4ceb605` (`feat(ingestion): implementar extrator juridico de partes, papeis e identificadores do preambulo`)
- **Status**: Concluído com sucesso. Suíte global 100% verde (229 testes aprovados no total, sendo 14 novos testes dedicados ao extrator de metadados).

---

## 2. Arquivos Criados e Implementações

### `app/ingestion/metadata_extractor.py`
1. **Função Canônica de Ponto de Entrada**:
   - `extract_contract_metadata(blocks: List[DocumentBlock]) -> ContractMetadata`: consome a lista ordenada de blocos canônicos do documento e orquestra a extração integrada de epígrafe, tipo de instrumento, partes, objeto, data e flags de anomalia.
   - Defensiva e resiliente: se a lista de blocos for vazia, retorna instância de `ContractMetadata` com valores conservadores padronizados (`formal_title="Não identificado"`, `instrument_type="Outro"`, `parties=[]`, etc.) sem quebrar a execução.

2. **Epígrafe Formal e Taxonomia do Instrumento**:
   - `_extract_formal_title`: localiza os blocos do tipo `BlockType.TITLE` e agrega títulos multi-bloco contíguos (ex.: "PRIMEIRO TERMO ADITIVO" + "AO CONTRATO DE PRESTAÇÃO DE SERVIÇOS"). Na ausência de bloco de título, inspeciona o topo do documento procurando cabeçalhos em maiúsculas ("CONTRATO", "ACORDO", "TERMO").
   - `_classify_instrument_type`: classifica o tipo do instrumento em conformidade com a taxonomia exigida:
     - Priority 1: `Aditivo` ("ADITIVO", "ADITAMENTO") e `Distrato` ("DISTRATO", "RESCISÃO")
     - Priority 2: `Honorários` ("HONORÁRIOS", "ADVOCATÍCIOS", "SERVIÇOS JURÍDICOS" - priorizado em relação a prestação de serviços genérica)
     - Priority 3: `Acordo`, `Locação`, `Comodato`, `Compra e Venda`, `Parceria`
     - Priority 4: `Prestação de Serviços`
     - Fallback: `Outro`

3. **Segmentação e Qualificação Fiel das Partes (`ContractParty`)**:
   - `_extract_parties`: identifica blocos de `BlockType.PREAMBLE` ou blocos introdutórios que contenham qualificação contratual.
   - Remoção de fechos de preâmbulo (`_PREAMBLE_CLOSING_REGEX`): remove fórmulas de encerramento como "têm entre si justo e avençado...", "resolvem celebrar...", impedindo que essas expressões contaminem o nome ou qualificação da última parte.
   - Segmentação avançada (`_PARTY_SPLIT_REGEX`): particiona os polos por ponto e vírgula seguido de conjunções ("de outro lado", "como interveniente"), vírgula com "de outro lado", quebras de linha com marcadores numéricos/alfabéticos e conjunções condicionais vinculadas a papéis contratuais.
   - Limpeza cirúrgica de ruídos preliminares (`_LEADING_NOISE_PATTERNS`): remove preâmbulos de frase ("Pelo presente instrumento...", "Entre as partes:", "São partes neste contrato:", "De um lado,", "E, de outro lado,").

4. **Regra de Isolamento de Representantes Legais (Anti-Poluição de CNPJ/CPF)**:
   - `_extract_identifiers`:
     - Detecta marcadores de representação legal (`_REP_MARKER_REGEX`): "neste ato representada por", "representada por seu sócio/diretor/presidente", etc.
     - Isola o texto da entidade contratante/contratada principal do texto de seus representantes.
     - **Regra Rígida**: se for encontrada máscara de CNPJ na qualificação da entidade (14 dígitos), o CNPJ é fixado como `clean_identifier` e `raw_identifier`. O CPF do sócio, diretor ou procurador é estritamente ignorado e **não contamina** o identificador da entidade.
     - Se nenhuma menção a CNPJ existir na qualificação (caso de pessoas físicas contratantes diretas, como em contratos de locação residencial), o CPF da parte física torna-se o identificador.
     - Suporte tanto a formatos com máscara (`XX.XXX.XXX/XXXX-XX`, `XXX.XXX.XXX-XX`) quanto literais numéricos de 14 ou 11 dígitos precedidos por indicativo textual ("CNPJ sob o nº 12345678000199").

5. **Identificação Estrita de Escritório Andrade Advogados**:
   - `_is_andrade_law_firm`: avalia se a parte qualificada expressamente referencia o escritório Andrade Advogados ("andrade" em conjunto com termos como "advogad", "advocac", "sociedade de advogad").
   - Contratos comerciais de terceiros que envolvam empresas com o nome Andrade (ex.: "Construtora Andrade S/A") ou clientes pessoas físicas (ex.: "José de Andrade") recebem categoricamente `is_law_firm=False`.

6. **Detecção e Mapeamento de Papéis (`PartyRole`)**:
   - `_extract_role` e `_map_role`: mapeia expressões de designação ("doravante denominada simplesmente CONTRATADA", "designado LOCADOR", "como ANUENTE") para o enum `PartyRole`:
     - `CONTRATANTE`, `CONTRATADA`, `LOCADOR`, `LOCATARIO`, `VENDEDOR`, `COMPRADOR`, `ANUENTE`, `PARTE_GENERICA`.
   - Ajuste defensivo bilateral: se um polo for expressamente `CONTRATANTE` e o outro genérico, o polo oposto é inferido como `CONTRATADA` e vice-versa.

7. **Resumo do Objeto (`object_summary`)**:
   - `_extract_object_summary`: localiza a cláusula primeira / do objeto contratual por meio de correspondência de `hierarchy_label` ou início do texto.
   - Prioriza cláusulas com menção expressa a "OBJETO" (evitando captura errônea de "Cláusula 1ª - Das Partes").
   - Trata blocos onde o título da cláusula é separado do parágrafo de conteúdo.
   - Normaliza espaços e remove cabeçalhos redundantes ("CLÁUSULA PRIMEIRA - DO OBJETO: ").
   - Limita o resumo a no máximo 250 caracteres limpos com reticências em fronteira de palavra.

8. **Data de Celebração (`execution_date`)**:
   - `_extract_execution_date`: inspeciona de trás para frente os blocos de assinatura (`BlockType.SIGNATURE`) e os blocos finais do documento.
   - Reconhece datas por extenso em português ("10 de maio de 2024", "aos 15 dias do mês de março de 2023", "01 de janeiro de 2025"), formatos numéricos padrão ("10/05/2024") e formato ISO ("2023-11-20").
   - Converte e padroniza a data para ISO `YYYY-MM-DD` com validação de dia (1-31), mês (1-12) e ano (1900-2100).

---

## 3. Testes Criados em `tests/test_metadata_extractor.py`

Suíte com 14 testes unitários e de integração jurídica:
1. `test_extract_standard_bilateral_contract_with_andrade`: preâmbulo bilateral padrão com Andrade Advogados (CONTRATADA, `is_law_firm=True`) e Cliente S.A. (CONTRATANTE, `is_law_firm=False`), com objeto e data no fecho.
2. `test_legal_representative_isolation_anti_pollution`: teste da regra crítica de isolamento onde entidade jurídica com CNPJ possui administradores e diretores com CPFs. Comprova que os CPFs dos representantes não contaminam o `clean_identifier` da empresa.
3. `test_locacao_natural_persons_with_cpf`: contrato de locação com pessoas físicas portadoras de CPF, validando papéis `LOCADOR` e `LOCATARIO`.
4. `test_aditivo_contratual`: detecção e classificação de `instrument_type == "Aditivo"`.
5. `test_third_party_contract_no_law_firm`: contrato sob custódia entre terceiros onde nenhuma parte é o escritório Andrade Advogados (`is_law_firm=False` em todas as partes).
6. `test_execution_date_formats`: conversão de múltiplos formatos de datação por extenso e numéricos para ISO `YYYY-MM-DD`.
7. `test_document_with_only_clauses_resilience`: documento sem bloco `PREAMBLE` ou `TITLE` explícito, contendo apenas cláusulas; extrai partes, objeto e fecho defensivamente.
8. `test_empty_blocks_resilience`: validação de retorno gracioso quando a lista de blocos é vazia (`[]`).
9. `test_multiparty_with_interveniente_anuente`: preâmbulo tripartite com CEDENTE, CESSIONÁRIA e INTERVENIENTE ANUENTE (`PartyRole.ANUENTE`).
10. `test_object_summary_truncation_under_250_chars`: garantia de que descrições longas do objeto sejam truncadas em <= 250 caracteres com `...`.
11. `test_various_instrument_types`: classificação taxonômica para Distrato, Acordo, Comodato e Parceria.
12. `test_unformatted_cnpj_and_cpf_extraction`: extração e limpeza de identificadores sem pontuação no texto original.
13. `test_andrade_name_discrimination`: diferenciação entre empresas comerciais com o termo Andrade (ex.: "Construtora Andrade S/A", `is_law_firm=False`) e sociedades de advocacia Andrade (`is_law_firm=True`).
14. `test_document_without_execution_date`: ausência de data no fecho tratada sem erro (`execution_date=None`).

---

## 4. Ciclo TDD e Verificação de Regressão

1. **Fase Vermelha (Red)**:
   - Execução: `uv run pytest tests/test_metadata_extractor.py`
   - Resultado: **FALHA** (`ModuleNotFoundError: No module named 'app.ingestion.metadata_extractor'`).

2. **Fase Verde (Green)**:
   - Implementação e refinamento de `app/ingestion/metadata_extractor.py`.
   - Execução: `uv run pytest tests/test_metadata_extractor.py`
   - Resultado: **14 passed in 0.16s**.

3. **Verificação de Regressão Global**:
   - Execução: `uv run pytest`
   - Resultado: **229 passed in 15.44s** (215 testes pré-existentes mantidos com 100% de sucesso + 14 novos testes do extrator de metadados; zero regressões em todo o projeto).

---

## 5. Resumo de Commits
- Commit: `4ceb605`
- Mensagem: `feat(ingestion): implementar extrator juridico de partes, papeis e identificadores do preambulo`
- Arquivos comitados:
  - `app/ingestion/metadata_extractor.py`
  - `tests/test_metadata_extractor.py`
