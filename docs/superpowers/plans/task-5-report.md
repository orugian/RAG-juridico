# Relatório de Conclusão - Tarefa 5: Motor de Parsing PDF com Rasterização `pypdfium2` para OCR

## 1. Visão Geral
- **Objetivo**: Implementar o motor canônico de parsing e extração estrutural de documentos PDF em `app/ingestion/pdf_parser.py` e sua respectiva suíte de testes em `tests/test_pdf_parser.py`. O parser adota uma estratégia híbrida: extrai texto nativo digital via `pypdf` e, cirurgicamente para páginas escaneadas ou com densidade insuficiente de caracteres (< 50 caracteres), ativa rasterização em memória em alta resolução (scale=2.0) com `pypdfium2` e OCR via `pytesseract`, marcando os blocos resultantes com `UncertaintyFlag.LOW_CONFIDENCE_OCR`.
- **Branch**: `feat/data-prep-pipeline`
- **Commit**: `7324744` (`feat(ingestion): implementar parser pdf com pypdf e rasterizacao pypdfium2 para OCR`)
- **Status**: Concluído com sucesso. Suíte de testes 100% verde (215 testes no total, sendo 14 novos testes dedicados ao parser PDF).

---

## 2. Arquivos Criados e Implementações

### `app/ingestion/pdf_parser.py`
1. **Validação de Integridade Física e Cabeçalho PDF**:
   - Valida existência do arquivo no filesystem (`FileNotFoundError`).
   - Valida se o arquivo informado é um arquivo regular e se possui tamanho superior a zero bytes (`ValueError`).
   - Inspeciona os bytes iniciais do arquivo garantindo a presença do cabeçalho canônico `%PDF-` (`ValueError("Arquivo PDF corrompido ou cabeçalho inválido")`).

2. **Detecção e Rejeição de Documentos Criptografados**:
   - Analisa `reader.is_encrypted` e captura `pypdf.errors.FileNotDecryptedError`.
   - Lança `ValueError("PDF criptografado não suportado")`, prevenindo execuções parciais em arquivos protegidos por senha.
   - Valida se o PDF possui ao menos uma página (`len(reader.pages) > 0`).

3. **Estratégia Híbrida Digital / OCR Cirúrgico**:
   - **Extração Digital Nativa**:
     - Utiliza `page.extract_text()` para extração rápida de vetores de caracteres.
     - Se `len(text.strip()) >= ocr_min_chars` (padrão: 50 caracteres): o texto nativo é processado diretamente, sem alocação desnecessária de OCR e com `uncertainty_flags` vazia.
   - **Ativação Cirúrgica de OCR (Páginas Escaneadas / Degradadas)**:
     - Se `len(text.strip()) < ocr_min_chars`: a página é identificada como escaneada.
     - Carrega o documento no `pypdfium2.PdfDocument` e rasteriza a página específica em escala 2.0x (`page_ium.render(scale=2.0).to_pil()`).
     - Executa OCR via `pytesseract.image_to_string(pil_image, lang=ocr_lang)`.
     - Marca todos os blocos gerados a partir dessa página com `UncertaintyFlag.LOW_CONFIDENCE_OCR`.
     - Bloco `finally:` mandatório garante o fechamento explícito da instância do `pypdfium2` (`doc_ium.close()`), prevenindo vazamento de handles de arquivos e memória C nativa.
   - **Tratamento Gracioso de Falha de OCR**:
     - Captura `pytesseract.TesseractNotFoundError` e quaisquer exceções no OCR, emitindo advertência no logger (`logger.warning(...)`) e fazendo fallback seguro para o texto nativo residual disponível, sem causar interrupção (`crash`) do pipeline.

4. **Segmentação e Estruturação Hierárquica em `DocumentBlock`**:
   - Segmentação lógica (`_split_page_into_blocks`) respeitando quebras de linha duplas (`\n\n`), linhas de cabeçalho estrutural e agrupamento de linhas de texto corrido continuadas.
   - Classificação semântica precisa (`_classify_block`):
     - **Título / Epígrafe**: `BlockType.TITLE`, `HierarchyLevel.TITLE`
     - **Qualificação / Preâmbulo**: `BlockType.PREAMBLE`, `HierarchyLevel.PREAMBLE`
     - **Cláusulas**: `BlockType.CLAUSE`, `HierarchyLevel.CLAUSE`, extraindo `hierarchy_label` (ex.: "CLÁUSULA PRIMEIRA", "CLAUSULA 1")
     - **Parágrafos**: `BlockType.PARAGRAPH`, `HierarchyLevel.PARAGRAPH`, extraindo `hierarchy_label` para parágrafos com padrão explícito (ex.: "§ 1º", "PARÁGRAFO PRIMEIRO")
     - **Itens e Subitens**: `BlockType.ITEM`, `HierarchyLevel.ITEM`, extraindo labels como "a)", "1.1", "(i)"
     - **Fecho e Assinaturas**: `BlockType.SIGNATURE`, `HierarchyLevel.SIGNATURE`, detectando seções de assinatura e fechamento contratual ("E por estarem justos...", "TESTEMUNHAS:", "ASSINATURAS:")
   - **Rastreabilidade de Parentesco e Continuidade Multi-página**:
     - Manutenção do objeto `ParserState` ao longo de todas as páginas do PDF.
     - Atribuição automática de `parent_clause_id` para parágrafos e itens subordinados à cláusula ativa, inclusive quando o conteúdo da cláusula se estende para as páginas seguintes.
     - Reset de `parent_clause_id` ao ingressar na seção de assinaturas ou ao iniciar uma nova cláusula.
   - Preenchimento do texto verbatim em `text_raw` e texto normalizado em `text_search` via `_normalize_search_text`.

---

## 3. Testes Criados em `tests/test_pdf_parser.py`

Suíte completa com 14 testes unitários e de integração:
1. `test_pdf_file_not_found`: Validação de lançamento de `FileNotFoundError` para arquivo PDF inexistente.
2. `test_pdf_empty_file`: Validação de lançamento de `ValueError` para arquivo de 0 bytes.
3. `test_pdf_invalid_header`: Validação de lançamento de `ValueError` para arquivo corrompido sem cabeçalho `%PDF-`.
4. `test_pdf_encrypted_rejected`: Rejeição imediata de arquivos PDF criptografados com senha (`ValueError("PDF criptografado não suportado")`).
5. `test_native_digital_pdf_extraction`: Extração de blocos de PDF digital nativo (> 50 chars) com integridade estrutural e sem flags de OCR.
6. `test_scanned_pdf_triggers_ocr`: Verificação da ativação cirúrgica de OCR para páginas com < 50 caracteres, checando integração com `pypdfium2` (renderização scale=2.0), chamada do `pytesseract` e atribuição de `UncertaintyFlag.LOW_CONFIDENCE_OCR`.
7. `test_hybrid_pdf_mixed_digital_and_scanned`: Processamento de documento híbrido multi-página, comprovando que a página 1 (digital) não recebe a flag de incerteza enquanto a página 2 (escaneada) recebe `LOW_CONFIDENCE_OCR`.
8. `test_pdf_structural_classification_and_hierarchy`: Validação exaustiva da classificação semântica de Title, Preamble, Clause, Paragraph, Item e Signature, conferindo `hierarchy_label` e rastreabilidade de `parent_clause_id`.
9. `test_ocr_tesseract_missing_fallback`: Tratamento gracioso quando o Tesseract está ausente (`TesseractNotFoundError`), sem interrupção do sistema.
10. `test_extract_pdf_blocks_integrates_with_parsed_document`: Validação ponta a ponta gerando o agregado `ParsedDocument` com `ContractMetadata` e validando serialização/desserialização JSON (`model_dump` e `model_validate`).
11. `test_custom_ocr_min_chars_and_lang`: Teste de parametrização customizada para limiar mínimo de caracteres (`ocr_min_chars`) e idioma do OCR (`ocr_lang="eng"`).
12. `test_pdf_zero_pages_rejected`: Rejeição de PDFs sem páginas com `ValueError("PDF não contém páginas")`.
13. `test_ocr_resource_cleanup_even_on_failure`: Garantia de fechamento de instâncias do `pypdfium2` (`doc_ium.close()`) mesmo quando o OCR falha por exceção em tempo de execução.
14. `test_pdf_multi_page_state_continuity`: Garantia de preservação da continuidade de estado (`ParserState.current_clause_id`) através de páginas consecutivas do documento.

---

## 4. Ciclo TDD e Verificação de Regressão

1. **Fase Vermelha (Red)**:
   - Execução: `uv run pytest tests/test_pdf_parser.py`
   - Resultado: **FALHA** (11 testes falharam devido a `ModuleNotFoundError: No module named 'app.ingestion.pdf_parser'`).

2. **Fase Verde (Green)**:
   - Implementação de `app/ingestion/pdf_parser.py`.
   - Execução: `uv run pytest tests/test_pdf_parser.py`
   - Resultado: **14 passed in 0.56s**.

3. **Verificação de Regressão Global**:
   - Execução: `uv run pytest`
   - Resultado: **215 passed in 16.56s** (201 testes pré-existentes mantidos com 100% de sucesso + 14 novos testes do parser PDF; zero regressões em todo o projeto).
