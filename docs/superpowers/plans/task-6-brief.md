# Task 6 Brief: Extrator Jurídico de Metadados Reais (`metadata_extractor.py`)

## Goal
Implementar o motor de extração semântica e determinística de metadados reais a partir do texto dos contratos em `app/ingestion/metadata_extractor.py` com testes rigorosos em `tests/test_metadata_extractor.py`. O extrator atende à ressalva máxima do projeto: extrair com fidedignidade absoluta partes, papéis (`PartyRole`), identificadores (CNPJ/CPF vinculados à entidade e isolando representantes legais), tipo de instrumento e data de celebração diretamente do preâmbulo e fecho do documento.

## Files to Create
- Create: `app/ingestion/metadata_extractor.py`
- Create: `tests/test_metadata_extractor.py`

## Requirements
1. **Função Principal**:
   `extract_contract_metadata(blocks: List[DocumentBlock]) -> ContractMetadata`
   - Consome a lista de blocos do documento.
   - Localiza os blocos de Título (`TITLE`), Preâmbulo (`PREAMBLE`), primeiras Cláusulas (`CLAUSE` - objeto) e Fecho (`SIGNATURE`).
2. **Epígrafe Formal e Tipo de Instrumento**:
   - `formal_title`: extraído do primeiro bloco `TITLE` ou do topo do documento (ex.: "CONTRATO DE HONORÁRIOS ADVOCATÍCIOS", "PRIMEIRO TERMO ADITIVO", "ACORDO EXTRAJUDICIAL").
   - `instrument_type`: classificado com base na epígrafe e objeto ("Honorários", "Prestação de Serviços", "Acordo", "Locação", "Aditivo", "Distrato", "Parceria", "Comodato", "Compra e Venda", "Outro").
3. **Extração e Qualificação Fiel das Partes (`ContractParty`)**:
   - Segmentação de preâmbulo: divide os polos contratuais ("De um lado... De outro lado...", "ENTRE: ... E: ...", "pelo presente instrumento... têm entre si justo e avençado...").
   - Identificação da razão social / nome da parte principal de cada polo.
   - Identificação do papel contratual (`role: PartyRole`): mapeia menções como "denominada CONTRATANTE", "denominada CONTRATADA", "LOCADOR", "LOCATÁRIO", etc., com fallback para `PARTE_GENERICA`.
   - `is_law_firm`: `True` se o nome contiver referências ao escritório Andrade Advogados, `False` caso contrário. (Nenhum contrato de terceiros sob custódia pode ter Andrade Advogados presumida como parte).
   - **Regra de Isolamento de Representantes Legais (Anti-Poluição de CNPJ/CPF)**:
     - Quando a parte for pessoa jurídica ("EMPRESA S/A") qualificada com CNPJ e representada por pessoa física ("representada por seu diretor Fulano, CPF 123..."), o identificador principal da parte (`clean_identifier`) DEVE SER O CNPJ DA EMPRESA.
     - O CPF do representante legal não pode substituir ou poluir o `clean_identifier` da entidade contratante/contratada.
     - Se a parte for pessoa física individual sem CNPJ, o CPF torna-se o identificador.
     - `clean_identifier`: apenas dígitos (14 dígitos para CNPJ, 11 dígitos para CPF).
     - `raw_identifier`: a máscara original encontrada no texto.
4. **Resumo do Objeto (`object_summary`)**:
   - Localiza a Cláusula Primeira / Do Objeto e extrai a essência do escopo contratual (até 250 caracteres limpos).
5. **Data de Celebração (`execution_date`)**:
   - Localiza o fecho do documento (últimos blocos ou bloco `SIGNATURE`).
   - Extrai data por extenso ou numérica (ex.: "10 de maio de 2024", "10/05/2024", "aos 15 dias do mês de março de 2023").
   - Converte para string padronizada (ISO `YYYY-MM-DD` quando determinável, ou formato textual limpo).
6. **Robustez e Defensividade**:
   - Se o documento não contiver preâmbulo claro ou for atípico, não quebrar com exceção: preencher campos obrigatórios com fallbacks conservadores e registrar observações pertinentes.

## Testing & TDD
- Escrever `tests/test_metadata_extractor.py` cobrindo:
  - Preâmbulo bilateral padrão com Andrade Advogados (CONTRATADA) e Empresa Cliente (CONTRATANTE).
  - Regra de isolamento de representante legal: CNPJ da empresa preservado sem contaminação pelo CPF do sócio/diretor.
  - Contrato de locação com LOCADOR e LOCATÁRIO (pessoas físicas com CPF).
  - Aditivo contratual com detecção de `instrument_type == "Aditivo"`.
  - Contrato de terceiros onde Andrade Advogados NÃO é parte (`is_law_firm=False` para todas as partes).
  - Extração de data de celebração no fecho.
  - Documento com apenas cláusulas (sem bloco `PREAMBLE` explícito) comportando-se com resiliência.
- TDD Red: executar testes antes da implementação e verificar falha.
- TDD Green: implementar `app/ingestion/metadata_extractor.py` e passar todos os testes.
- Regressão global: `uv run pytest` deve passar 100% verde em todo o repositório.
- Commit: `git add app/ingestion/metadata_extractor.py tests/test_metadata_extractor.py; git commit -m "feat(ingestion): implementar extrator juridico de partes, papeis e identificadores do preambulo"`.
- Gravar relatório em `docs/superpowers/plans/task-6-report.md`.
