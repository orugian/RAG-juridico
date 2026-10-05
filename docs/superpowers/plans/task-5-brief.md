# Task 5 Brief: Motor de Parsing PDF com Rasterização `pypdfium2` para OCR

## Goal
Implementar o motor de parsing de arquivos PDF em `app/ingestion/pdf_parser.py` com testes unitários e de integração em `tests/test_pdf_parser.py`. O parser deve extrair texto nativo digital usando `pypdf` e, para páginas digitalizadas (scanned/imagem com texto nativo insuficiente, < 50 caracteres), acionar automaticamente rasterização em memória com `pypdfium2` e OCR cirúrgico via `pytesseract`, marcando os blocos com `UncertaintyFlag.LOW_CONFIDENCE_OCR`.

## Dependencies
Já adicionadas ao projeto via `uv add`:
- `pypdf`
- `pypdfium2`
- `pytesseract`
- `pillow`

## Files to Create/Touch
- Touch: `pyproject.toml`, `uv.lock` (já atualizados com as dependências)
- Create: `app/ingestion/pdf_parser.py`
- Create: `tests/test_pdf_parser.py`

## Requirements
1. **Função Principal**:
   `extract_pdf_blocks(pdf_path: Path | str, doc_id: int, doc_version: int = 1, ocr_min_chars: int = 50, ocr_lang: str = "por") -> List[DocumentBlock]`
   - Valida existência física do arquivo e integridade do cabeçalho PDF (`%PDF-`).
   - Detecta PDFs protegidos/criptografados via `reader.is_encrypted` e lança `ValueError("PDF criptografado não suportado")`.
2. **Estratégia Híbrida Digital / OCR**:
   - Para cada página do PDF:
     - Extrai texto nativo com `page.extract_text()`.
     - Se `len(text.strip()) >= ocr_min_chars`: processa o texto nativo diretamente.
     - Se `len(text.strip()) < ocr_min_chars`: identifica como página escaneada e aciona OCR cirúrgico:
       - Abre o documento com `pypdfium2.PdfDocument(str(pdf_path))`.
       - Renderiza a página via `page_ium.render(scale=2.0).to_pil()`.
       - Extrai texto via `pytesseract.image_to_string(pil_image, lang=ocr_lang)`.
       - Marca os blocos resultantes dessa página com `UncertaintyFlag.LOW_CONFIDENCE_OCR`.
       - Fecha adequadamente instâncias do pypdfium2 (`doc_ium.close()`).
3. **Estruturação em `DocumentBlock`**:
   - Divide o texto da página em parágrafos e cláusulas lógicas.
   - Detecta e classifica:
     - Título / Epígrafe -> `BlockType.TITLE`, `HierarchyLevel.TITLE`
     - Qualificação / Preâmbulo ("Pelo presente...", "Entre as partes...") -> `BlockType.PREAMBLE`, `HierarchyLevel.PREAMBLE`
     - Cláusulas ("CLÁUSULA", "Cláusula X", "CLÁUSULA PRIMEIRA") -> `BlockType.CLAUSE`, `HierarchyLevel.CLAUSE`
     - Parágrafos ("Parágrafo Primeiro", "§", etc.) -> `BlockType.PARAGRAPH`, `HierarchyLevel.PARAGRAPH`
     - Itens -> `BlockType.ITEM`, `HierarchyLevel.ITEM`
     - Assinaturas / Fecho ("E por estarem justos...", assinaturas) -> `BlockType.SIGNATURE`
   - Mantém rastreabilidade de `parent_clause_id` para parágrafos/itens vinculados.
   - Preenche `text_raw` com texto verbatim e `text_search` normalizado.
4. **Tratamento de Exceções**:
   - Arquivo inexistente -> `FileNotFoundError`
   - PDF corrompido ou vazio -> `ValueError`
   - Erro de OCR/Tesseract ausente -> captura amigável e tratamento sem crash da aplicação.

## Testing & TDD
- Escrever `tests/test_pdf_parser.py` cobrindo:
  - Extração nativa de PDF digital (com mock ou fixture sintética).
  - Ativação cirúrgica de OCR para páginas sem texto nativo (< 50 caracteres) com `pypdfium2` e `pytesseract` mockados, verificando presença de `UncertaintyFlag.LOW_CONFIDENCE_OCR`.
  - Rejeição de PDF criptografado com `is_encrypted=True`.
  - Rejeição de arquivo corrompido / não-PDF.
  - Classificação correta de blocos (Title, Preamble, Clause, Paragraph, Signature).
  - Roundtrip em `ParsedDocument`.
- TDD Red: executar teste antes da implementação e verificar falha.
- TDD Green: implementar `app/ingestion/pdf_parser.py` e passar todos os testes.
- Regressão global: `uv run pytest` deve passar 100% verde em todo o repositório.
- Commit: `git add pyproject.toml uv.lock app/ingestion/pdf_parser.py tests/test_pdf_parser.py; git commit -m "feat(ingestion): implementar parser pdf com pypdf e rasterizacao pypdfium2 para OCR"`.
- Gravar relatório em `docs/superpowers/plans/task-5-report.md`.
