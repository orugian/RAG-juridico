# Relatório de Conclusão - Tarefa 3: Motor de Parsing DOCX com Resolução de Numeração e Revisões

## 1. Visão Geral
- **Objetivo**: Implementar o motor nativo de parsing DOCX em `app/ingestion/docx_parser.py` e testes abrangentes em `tests/test_docx_parser.py`, reconstruindo a hierarquia e numeração visível a partir de `word/numbering.xml`, inspecionando parágrafos e tabelas, detectando anotações de revisão (`w:ins`, `w:del`) e comentários (`word/comments.xml` e âncoras de comentários), gerando instâncias canônicas de `DocumentBlock`.
- **Branch**: `feat/data-prep-pipeline`
- **Commit**: `4b3ba0c` (`feat(ingestion): implementar parser docx com reconstrucao de numbering.xml e revisoes`)
- **Status**: Concluído com sucesso. Suíte de testes 100% verde (180 testes no total, 12 novos testes dedicados ao parser DOCX).

---

## 2. Arquivos Criados e Implementações

### `app/ingestion/docx_parser.py`
1. **Entrada e Validações de Integridade**:
   - Função principal: `extract_docx_blocks(docx_path: Path | str, doc_id: int, doc_version: int = 1) -> List[DocumentBlock]`.
   - Utiliza exclusivamente as bibliotecas embutidas `zipfile` e `xml.etree.ElementTree`.
   - Validações estritas levantando `ValueError`:
     - Arquivo inexistente (`Arquivo DOCX inexistente: ...`).
     - Arquivo com tamanho zero (`Arquivo DOCX vazio: ...`).
     - Arquivo corrompido ou formato não-zip (`Arquivo DOCX corrompido ou inválido: ...`).
     - Arquivo zip sem a parte principal `word/document.xml` (`Arquivo DOCX sem word/document.xml: ...`).

2. **Reconstrução de Numeração Hierárquica (`NumberingResolver`)**:
   - Inspeciona `word/numbering.xml`, mapeando instâncias `<w:num w:numId="...">` para definições `<w:abstractNum w:abstractNumId="...">`.
   - Extrai configurações de nível `<w:lvl w:ilvl="...">`: `start`, `numFmt` e modelo `lvlText` (ex.: `"Cláusula %1ª - "`, `"§ %1º - "`, `"%1) "`, `"%1.%2"`).
   - Suporte completo a esquemas de formatação:
     - `decimal`: contagem arábica padrão (`1, 2, 3...`).
     - `upperRoman` e `lowerRoman`: numeração romana (`I, II, III, IV...` / `i, ii, iii, iv...`).
     - `upperLetter` e `lowerLetter`: numeração alfabética bijetiva base 26 (`A, B... Z, AA...` / `a, b... z, aa...`).
     - `ordinal`: numerais ordinais (`1º, 2º...`).
   - Manutenção de contadores de estado por `num_id` e reset de níveis filhos:
     - Sempre que um nível superior (ex.: `ilvl 0` de cláusula) é incrementado, todos os níveis inferiores (`ilvl > 0`) são reiniciados para seu respectivo valor inicial (`start`).
     - Substituição dinâmica de marcadores `%k` pelo valor formatado do nível correspondente.
   - Sinalização de incerteza: se um parágrafo faz referência a um `numId` inexistente ou ausente em `numbering.xml`, registra `UncertaintyFlag.UNRESOLVED_NUMBERING`.

3. **Detecção de Revisões (Track Changes) e Comentários**:
   - **Inserções (`w:ins`)**: texto inserido é incluído no fluxo padrão de leitura e marcado com `UncertaintyFlag.TRACK_CHANGES_PRESENT`.
   - **Exclusões (`w:del`)**: texto excluído é estritamente ignorado do `text_raw` / `text_search` e o bloco recebe `UncertaintyFlag.TRACK_CHANGES_PRESENT`.
   - **Comentários**: blocos com tags de âncora (`w:commentRangeStart`, `w:commentRangeEnd`, `w:commentReference`) recebem `UncertaintyFlag.HAS_COMMENTS`. Caso o pacote contenha `word/comments.xml` com comentários globais sem âncoras específicas no corpo, a flag é distribuída entre os blocos como salvaguarda.

4. **Extração Estruturada de Tabelas (`w:tbl`)**:
   - Processamento de linhas (`w:tr`) e células (`w:tc`).
   - Identificação de linha de cabeçalho através de `<w:tblHeader/>` ou primeira linha (índice 0).
   - Mapeamento explícito de metadados de coluna e linha em `table_metadata={"row": r_idx, "col": c_idx, "header": col_name}`.
   - Formatação legível e rastreável do `text_raw`: no cabeçalho mantém o nome da coluna; nas linhas de dados, prefixa como `"{col_name}: {cell_text}"`.
   - Atribuição de `BlockType.TABLE` e `HierarchyLevel.TABLE`, herdando `parent_clause_id` da cláusula em que se encontra.

5. **Classificação Estrutural e Hierarquia**:
   - `BlockType.TITLE` / `HierarchyLevel.TITLE`: parágrafos iniciais com títulos formais do instrumento.
   - `BlockType.PREAMBLE` / `HierarchyLevel.PREAMBLE`: preâmbulo e abertura com qualificação das partes.
   - `BlockType.CLAUSE` / `HierarchyLevel.CLAUSE`: cláusulas numeradas via `numbering.xml` (`ilvl 0`) ou textuais via regex (`CLÁUSULA PRIMEIRA`, etc.).
   - `BlockType.PARAGRAPH` / `HierarchyLevel.PARAGRAPH`: parágrafos subordinados (`ilvl 1`, `§ 1º`, etc.), com `parent_clause_id` apontando para o identificador da cláusula mãe.
   - `BlockType.ITEM` / `HierarchyLevel.ITEM`: itens e alíneas de lista (`ilvl 2`, `a)`, `i.`).
   - `BlockType.SUBITEM` / `HierarchyLevel.SUBITEM`: subitens de lista (`ilvl >= 3`).
   - `BlockType.SIGNATURE` / `HierarchyLevel.SIGNATURE`: fecho, comarcas e seção de assinaturas/testemunhas.

6. **Segregação Canônica entre `text_raw` e `text_search`**:
   - `text_raw`: preserva o literal fiel acrescido da numeração resolvida reconstruída (indispensável como prova jurídica e citação textual).
   - `text_search`: normalizado para busca léxica via colapso de espaços e quebras em branco.

---

## 3. Testes Criados em `tests/test_docx_parser.py`

Suíte abrangente com 12 testes unitários:
1. `test_extract_docx_blocks_simple_document`: Valida fluxo clássico de contrato (título, preâmbulo, cláusula, parágrafo subordinado com `parent_clause_id`, § 1º, fecho e testemunhas).
2. `test_extract_docx_blocks_with_multilevel_numbering`: Valida resolução multinível completa com reinício de contadores em Cláusula 1ª -> § 1º, § 2º, a) -> Cláusula 2ª -> § 1º (reiniciado).
3. `test_extract_docx_blocks_with_track_changes`: Valida detecção de `w:ins` e `w:del`, garantindo inclusão do texto inserido ("24 meses"), exclusão do texto deletado ("12 meses") e flag `TRACK_CHANGES_PRESENT`.
4. `test_extract_docx_blocks_with_comments`: Valida identificação de âncora de comentário e atribuição de `HAS_COMMENTS`.
5. `test_extract_docx_blocks_comments_global_fallback`: Valida fallback para `HAS_COMMENTS` em todos os blocos caso `comments.xml` contenha anotações sem âncoras no texto.
6. `test_extract_docx_blocks_pure_deletion_paragraph_ignored`: Valida que parágrafos onde todo o conteúdo foi excluído em track changes são ignorados (não geram chunks vazios).
7. `test_extract_docx_blocks_with_table`: Valida extração de tabela com cabeçalhos de coluna, células rastreáveis `table_metadata={"row": r, "col": c, "header": col}` e associação à cláusula mãe.
8. `test_extract_docx_blocks_unresolved_numbering`: Valida marcação de `UNRESOLVED_NUMBERING` quando um `numId` desconhecido é referenciado.
9. `test_extract_docx_blocks_invalid_and_corrupt_files`: Valida lançamento de `ValueError` para arquivo inexistente, arquivo vazio (0 bytes), arquivo não-zip corrompido e zip sem `document.xml`.
10. `test_extract_docx_blocks_roman_and_letters`: Valida formatação em algarismos romanos (`I, II, III, IV`).
11. `test_extract_docx_blocks_ordinal_numbering`: Valida formatação ordinal (`1º, 2º`).
12. `test_extract_docx_blocks_integrates_with_parsed_document`: Valida integração ponta a ponta dos blocos extraídos na construção do agregado `ParsedDocument`.

---

## 4. Ciclo TDD e Verificação de Regressão

1. **Fase Vermelha (Red)**:
   - Execução: `uv run pytest tests/test_docx_parser.py`
   - Resultado: **FALHA** (`ModuleNotFoundError: No module named 'app.ingestion.docx_parser'`).

2. **Fase Verde (Green)**:
   - Execução: `uv run pytest tests/test_docx_parser.py -v`
   - Resultado: **12 passed in 0.67s**.

3. **Regressão Global**:
   - Execução: `uv run pytest`
   - Resultado: **180 passed in 15.00s** (168 testes pré-existentes mantidos com 100% de sucesso + 12 novos testes do parser DOCX; zero regressões em todo o projeto).
