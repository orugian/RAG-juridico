# Task 3 Brief: Motor de Parsing DOCX com Resolução de Numeração e Revisões

## Goal
Implementar o motor de parsing DOCX nativo em `app/ingestion/docx_parser.py` com testes em `tests/test_docx_parser.py`. O parser deve inspecionar a estrutura OOXML (`word/document.xml`, `word/numbering.xml`, `word/comments.xml`), extrair parágrafos e tabelas, reconstruir hierarquia e numeração visível a partir de listas/estilos, identificar anotações de revisão (`w:ins`, `w:del`, comentários) gerando instâncias canônicas de `DocumentBlock`.

## Files to Create
- Create: `app/ingestion/docx_parser.py`
- Create: `tests/test_docx_parser.py`

## Requirements
1. **Função Principal**:
   `extract_docx_blocks(docx_path: Path | str, doc_id: int, doc_version: int = 1) -> List[DocumentBlock]`
   - Deve ler o arquivo `.docx` via `zipfile` e `xml.etree.ElementTree`.
   - Se o arquivo for inválido, corrompido ou vazio, levantar `ValueError` ou retornar lista vazia/erro estruturado conforme a regra de validação.
2. **Resolução de Numeração (`numbering.xml`)**:
   - Mapear `w:num` (`numId`) para `w:abstractNum` (`abstractNumId`).
   - Rastrear contadores por `(num_id, ilvl)` incrementando a cada ocorrência.
   - Suportar formatação descrita em `w:lvlText` (ex.: `Cláusula %1ª - `, `%1.%2`, `%1.`, etc.).
   - Suportar tipos de numeração usuais: `decimal` (1, 2, 3...), `upperRoman` / `lowerRoman` (I, II... / i, ii...), `upperLetter` / `lowerLetter` (A, B... / a, b...).
   - Prefixar o texto da cláusula/parágrafo com a numeração resolvida em `text_raw` e `text_search`, e registrar em `hierarchy_label`.
3. **Detecção de Revisões e Incertezas (`UncertaintyFlag`)**:
   - `w:ins` (inserções): incluir o texto inserido e adicionar `UncertaintyFlag.TRACK_CHANGES_PRESENT`.
   - `w:del` (exclusões): ignorar o texto deletado da leitura padrão ou registrar flag `UncertaintyFlag.TRACK_CHANGES_PRESENT`.
   - Comentários (`w:commentRangeStart` ou presença de `word/comments.xml`): adicionar `UncertaintyFlag.HAS_COMMENTS`.
4. **Extração Estruturada de Tabelas (`w:tbl`)**:
   - Para cada linha (`w:tr`), extrair células (`w:tc`).
   - Se houver linha de cabeçalho (primeira linha ou `w:tblHeader`), associar o texto de cada célula ao cabeçalho da coluna (`table_metadata={"row": r_idx, "col": c_idx, "header": col_name}`).
   - Formatar o `text_raw` da célula de maneira legível e rastreável.
   - Tipo de bloco: `BlockType.TABLE`.
5. **Classificação de `BlockType` e `HierarchyLevel`**:
   - Parágrafos iniciais com títulos/honorários -> `BlockType.TITLE` / `HierarchyLevel.TITLE`.
   - Parágrafos de qualificação/abertura ("Pelo presente...", "Entre as partes...") -> `BlockType.PREAMBLE` / `HierarchyLevel.PREAMBLE`.
   - Cláusulas ("CLÁUSULA", "Cláusula X", ou numeração ilvl 0 de cláusula) -> `BlockType.CLAUSE` / `HierarchyLevel.CLAUSE`.
   - Parágrafos subordinados ("Parágrafo Primeiro", "§ 1º", ou ilvl 1) -> `BlockType.PARAGRAPH` / `HierarchyLevel.PARAGRAPH`.
   - Itens de lista ("a)", "i.", ou ilvl 2+) -> `BlockType.ITEM` / `HierarchyLevel.ITEM`.
   - Fecho e assinaturas ("E por estarem de pleno acordo...", "TESTEMUNHAS:") -> `BlockType.SIGNATURE`.
6. **Segregação de `text_raw` e `text_search`**:
   - `text_raw`: texto verbatim fiel, sem alterações léxicas.
   - `text_search`: texto normalizado (espaços em branco colapsados, pontuação uniforme).

## Testing & TDD
- Escrever `tests/test_docx_parser.py` cobrindo:
  - Documento simples com parágrafo e título.
  - Documento com `numbering.xml` multinível resolvendo "Cláusula 1ª" e "§ 1º".
  - Documento com track changes (`w:ins`) disparando `UncertaintyFlag.TRACK_CHANGES_PRESENT`.
  - Documento com tabela associando cabeçalho de coluna no `table_metadata`.
  - Tratamento de arquivo corrompido / não-zip.
- TDD Red: executar teste antes de implementar e verificar falha.
- TDD Green: implementar `app/ingestion/docx_parser.py` e passar todos os testes.
- Regressão global: `uv run pytest` deve passar sem falhas em todo o repositório.
- Commit: `git add app/ingestion/docx_parser.py tests/test_docx_parser.py; git commit -m "feat(ingestion): implementar parser docx com reconstrucao de numbering.xml e revisoes"`.
- Gravar relatório em `docs/superpowers/plans/task-3-report.md`.
