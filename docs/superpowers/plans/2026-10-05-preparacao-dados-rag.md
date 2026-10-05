# Plano de Implementação (Revisado) — Preparação de Dados, Parsing e Ingestão Híbrida RAG

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) ou superpowers:executing-plans para implementar este plano tarefa por tarefa. Cada etapa de desenvolvimento possui um portão mandatório de revisão adversarial pelo Agente Juiz com nota mínima >= 9,0 para aprovação.

**Goal:** Implementar o pipeline ponta a ponta de preparação de dados para o acervo contratual do escritório Andrade Advogados, cobrindo blindagem da curadoria, contrato canônico de dados (`DocumentBlock` e `ContractMetadata`), motores de parsing especializados (DOCX, legado e PDF/OCR com `pypdfium2`), extrator de metadados fidedignos ao texto real (preâmbulo, partes e papéis), motor de reconciliação documental (M-Files vs. Conteúdo), chunking hierárquico com separação estrita entre busca e transcrição literal, e indexação híbrida sincronizada (ChromaDB + BM25) com observabilidade no LangSmith protegida contra vazamento de PII.

**Architecture:** Os documentos são convertidos e parseados localmente em blocos padronizados. O preâmbulo e a epígrafe do texto real passam por um extrator especializado de entidades jurídicas (partes, qualificações, papéis, CNPJs/CPFs e tipo de instrumento), cujos resultados são confrontados pelo `MetadataReconciler` contra as propriedades cadastradas no M-Files. Casos de divergência crítica vão para quarentena de revisão humana. Os documentos aprovados são fatiados em chunks hierárquicos com cabeçalho de busca sintético segregado do texto verbatim da cláusula, e indexados atomicamente no ChromaDB e BM25 com chaves pareadas por `corpus_generation_id`.

**Tech Stack:** Python 3.13, uv, Pydantic v2, docx2python / python-docx, LibreOffice headless, pypdf, pypdfium2, pytesseract, LangChain Core / Community, ChromaDB, rank-bm25, joblib, LangSmith, Pytest.

## Global Constraints

- Soberania e privacidade: 100% dos dados contratuais e do processamento de parsing/OCR permanecem locais na máquina/EC2; nenhum texto integral ou PII de clientes trafega para APIs públicas ou metadados de nuvem.
- Fidelidade estrita de metadados: o conteúdo real do documento (preâmbulo e cláusulas) é a única fonte de verdade para identificação de partes e tipo de instrumento; metadados do M-Files são meramente indicativos e divergências graves bloqueiam a indexação automática.
- Preservação jurídica estrita: nenhum sanitizador, tokenizer ou parser pode remover pontuação de CNPJ/CPF, valores monetários, datas ou caracteres especiais (`§`, `º`, `ª`, `art.`, `cl.`).
- Rastreabilidade mandatória (Regra dos 4 Elementos): todo chunk indexado deve conter separadamente (1) Identificador do Contrato, (2) Partes Qualificadas com Papéis, (3) Localização Exata (cláusula/parágrafo) e (4) Transcrição Literal limpa (`verbatim_text`).
- Atomicidade de Índices: ChromaDB e BM25 devem ser gerados sob o mesmo `corpus_generation_id` com o conjunto estritamente idêntico de `chunk_id`s.
- Portão de Revisão Adversarial (Agente Juiz): nenhuma entrega é mesclada ou considerada concluída sem submissão ao protocolo do Agente Juiz com nota individual >= 9,0/10.

---

### Protocolo Mandatório do Agente Juiz (Revisão Adversarial)

Ao final de cada Tarefa ou Bloco de Tarefas, um subagente de perfil **Auditor / Juiz de Qualidade de IA** é invocado para inspecionar a entrega com base em 6 critérios objetivos:
1. **Estrutura e Qualidade do Código:** Aderência aos padrões do repositório, tipagem estrita Pydantic v2, modularidade e ausência de complexidade desnecessária (YAGNI/DRY).
2. **Integração Arquitetural e de Dados:** Interoperabilidade das interfaces de entrada e saída com o resto do sistema (`load_curated`, LangChain `Document`, `EnsembleRetriever`).
3. **Fidelidade Documental e Qualidade dos Dados:** Fidedignidade absoluta dos metadados extraídos contra o texto real; resolução correta de preâmbulos; reconciliação ativa M-Files vs. Conteúdo; preservação de numeração (`numbering.xml`).
4. **Governança e Privacidade:** Conformidade com a LGPD e sigilo da OAB; mascaramento global de inputs/outputs no LangSmith (`hide_inputs=True`, `hide_outputs=True`).
5. **Robustez e Tratamento de Exceções:** Falhas explícitas para documentos vazios ou corrompidos; proteção contra deadlocks no LibreOffice (`-env:UserInstallation`); isolamento de falhas de OCR.
6. **Adaptabilidade do Produto:** Separação entre texto de busca e texto literal para a Regra dos 4 Elementos; conformidade de busca híbrida com pré-filtro determinístico de CNPJ/CPF.

*Nota mínima para aprovação: 9,0 / 10. Se houver qualquer falha crítica (metadado de cliente/parte falso ou trocado no cabeçalho do chunk, perda de numeração, omissão de erro), a nota é limitada a no máximo 6,0 e a tarefa é rejeitada.*

---

## Estrutura de Arquivos e Módulos do Plano

```
app/ingestion/
├── schemas.py               # Contrato canônico: DocumentBlock, PartyRole, ContractParty, ContractMetadata
├── curation.py              # Curadoria hardening (correção de QA, doc_key e quase-duplicatas)
├── reference_sample.py      # Seletor determinístico do golden batch (35-50 docs estratificados)
├── docx_parser.py           # Parser DOCX nativo com resolução de numbering.xml e tabelas
├── legacy_parser.py         # Conversor headless seguro LibreOffice para .doc, .rtf e wordperfect
├── pdf_parser.py            # Extrator PDF híbrido com pypdf e rasterização pypdfium2 para OCR Tesseract
├── metadata_extractor.py    # Extrator jurídico de epígrafe, preâmbulo, partes, papéis e CNPJ/CPF
├── parsing_pipeline.py      # Orquestrador do lote, reconciliação M-Files vs. Texto e quarentena
└── chunker.py               # Chunker hierárquico por cláusula com cabeçalho segregado de verbatim_text

app/retrieval/
├── indexer.py               # Construtor sincronizado e atômico dos índices ChromaDB e BM25 (joblib)
└── hybrid.py                # EnsembleRetriever LangChain com id_key='chunk_id' e pré-filtro determinístico

tests/
├── test_curation_hardening.py   # Regressões de curadoria e integridade da base
├── test_schemas.py              # Validação dos schemas de blocos e entidades jurídicas
├── test_reference_sample.py     # Testes da amostragem estratificada com casos de risco
├── test_docx_parser.py          # Testes de numeração multinível, tabelas e track changes
├── test_legacy_parser.py        # Testes do conversor headless com perfil isolado
├── test_pdf_parser.py           # Testes de extração nativa e rasterização pypdfium2 para OCR
├── test_metadata_extractor.py   # Testes de extração de partes, qualificações e tipo de instrumento
├── test_parsing_pipeline.py     # Testes de reconciliação de metadados, quase-duplicatas e quarentena
├── test_chunker.py              # Testes de chunking hierárquico e preservação de verbatim_text
├── test_hybrid_retriever.py     # Testes de persistência atômica e busca com pré-filtro de CNPJ
└── test_pii_redaction.py        # Testes de mascaramento de traces LangSmith
```

---

### Tarefa 0: Hardening da Curadoria e Preservação de Anotações

**Files:**
- Modify: `app/ingestion/curation.py:580-605`
- Modify: `app/ingestion/near_dup.py:70-95`
- Test: `tests/test_curation_hardening.py`

**Interfaces:**
- Consumes: `manifest.jsonl`, `data/curation/review_queue.csv`, `data/curation/qa_sample.csv`
- Produces: `curation.py:load_curated()` imutável, preservação de anotações humanas em `qa_sample.csv` e `doc_key` ampliado com metadados indexados e hashes secundários.

- [ ] **Passo 1: Escrever teste de regressão para preservação de QA na reexecução**

```python
# Em tests/test_curation_hardening.py
def test_qa_sample_preserves_human_annotations_across_reruns(tmp_path):
    raw = tmp_path / "raw"
    records = [_doc(raw, 101, "Contrato Teste A", class_name="Contrato")]
    cur_dir = tmp_path / "curation"
    cur_dir.mkdir()
    qa_file = cur_dir / "qa_sample.csv"
    qa_file.write_text("mfiles_id;decisao_final;revisado_por;observacao\n101;include;Dr. Auditor;Validado manual\n", encoding="utf-8")
    
    rows = curate(records, raw, None)
    write_outputs(rows, records, cur_dir, "manifest_hash", None, None)
    
    content = qa_file.read_text(encoding="utf-8")
    assert "Dr. Auditor" in content
    assert "Validado manual" in content
```

- [ ] **Passo 2: Rodar o teste e verificar falha**
Run: `uv run pytest tests/test_curation_hardening.py::test_qa_sample_preserves_human_annotations_across_reruns -v`
Expected: FAIL indicando sobrescrita sem mesclagem prévia.

- [ ] **Passo 3: Implementar preservação de QA e enriquecimento de doc_key**
Carregar `qa_sample.csv` existente antes da gravação e mesclar registros anotados por `mfiles_id`. Atualizar `_doc_key()` para cobrir arquivos secundários e hash de regras.

- [ ] **Passo 4: Rodar suíte de curadoria e verificar aprovação**
Run: `uv run pytest tests/test_curation_hardening.py -v`
Expected: PASS

- [ ] **Passo 5: Commit**
```bash
git add app/ingestion/curation.py tests/test_curation_hardening.py
git commit -m "fix(curation): preservar anotacoes de QA e ampliar doc_key com metadados"
```

---

### Tarefa 1: Schema Intermediário Canônico e Entidades Jurídicas Estruturadas

**Files:**
- Create: `app/ingestion/schemas.py`
- Create: `tests/test_schemas.py`

**Interfaces:**
- Consumes: Tipos primitivos, Pydantic v2
- Produces: `PartyRole`, `ContractParty`, `ContractMetadata`, `DocumentBlock`, `ParsedDocument`

- [ ] **Passo 1: Escrever teste unitário para validação das entidades jurídicas e blocos**

```python
# tests/test_schemas.py
import pytest
from app.ingestion.schemas import (
    DocumentBlock, BlockType, HierarchyLevel, UncertaintyFlag,
    PartyRole, ContractParty, ContractMetadata, ParsedDocument
)

def test_contract_party_and_metadata_validation():
    party1 = ContractParty(
        name="Andrade Advogados Associados",
        role=PartyRole.CONTRATADA,
        clean_identifier="12345678000190",
        raw_identifier="12.345.678/0001-90",
        is_law_firm=True
    )
    party2 = ContractParty(
        name="Empresa Cliente S/A",
        role=PartyRole.CONTRATANTE,
        clean_identifier="98765432000110",
        raw_identifier="98.765.432/0001-10",
        is_law_firm=False
    )
    meta = ContractMetadata(
        formal_title="CONTRATO DE PRESTAÇÃO DE SERVIÇOS JURÍDICOS",
        instrument_type="Honorários",
        parties=[party1, party2],
        execution_date="2024-05-10",
        object_summary="Prestação de serviços contenciosos cíveis"
    )
    assert len(meta.parties) == 2
    assert meta.parties[0].role == PartyRole.CONTRATADA
    assert meta.parties[1].clean_identifier == "98765432000110"
```

- [ ] **Passo 2: Rodar teste e verificar falha**
Run: `uv run pytest tests/test_schemas.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.ingestion.schemas'`

- [ ] **Passo 3: Implementar schemas.py com modelagem jurídica rigorosa**

```python
# app/ingestion/schemas.py
from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, model_validator

class PartyRole(str, Enum):
    CONTRATANTE = "contratante"
    CONTRATADA = "contratada"
    LOCADOR = "locador"
    LOCATARIO = "locatario"
    VENDEDOR = "vendedor"
    COMPRADOR = "comprador"
    ANUENTE = "anuente"
    PARTE_GENERICA = "parte"

class ContractParty(BaseModel):
    name: str
    role: PartyRole = PartyRole.PARTE_GENERICA
    clean_identifier: Optional[str] = None  # CNPJ ou CPF (somente dígitos)
    raw_identifier: Optional[str] = None    # Literal original com pontuação
    is_law_firm: bool = False               # True se for Andrade Advogados

class ContractMetadata(BaseModel):
    formal_title: str
    instrument_type: str                    # Honorários, Prestação de Serviços, Acordo, Locação, Aditivo, Distrato
    parties: List[ContractParty] = Field(default_factory=list)
    execution_date: Optional[str] = None
    object_summary: Optional[str] = None
    mfiles_divergence_flags: List[str] = Field(default_factory=list)

class BlockType(str, Enum):
    TITLE = "title"
    PREAMBLE = "preamble"
    CLAUSE = "clause"
    PARAGRAPH = "paragraph"
    ITEM = "item"
    SUBITEM = "subitem"
    TABLE = "table"
    ANNEX = "annex"
    SIGNATURE = "signature"

class HierarchyLevel(str, Enum):
    TITLE = "title"
    PREAMBLE = "preamble"
    CLAUSE = "clause"
    PARAGRAPH = "paragraph"
    ITEM = "item"
    SUBITEM = "subitem"

class UncertaintyFlag(str, Enum):
    TRACK_CHANGES_PRESENT = "track_changes_present"
    LOW_CONFIDENCE_OCR = "low_confidence_ocr"
    UNRESOLVED_NUMBERING = "unresolved_numbering"
    CORRUPTED_TABLE = "corrupted_table"
    HAS_COMMENTS = "has_comments"

class DocumentBlock(BaseModel):
    block_id: str
    doc_id: int
    doc_version: int
    block_type: BlockType
    hierarchy_level: HierarchyLevel
    hierarchy_label: Optional[str] = None
    parent_clause_id: Optional[str] = None
    order_index: int
    text_raw: str                           # Texto literal exato para a prova jurídica
    text_search: str                        # Texto normalizado para busca léxica
    table_metadata: Optional[Dict[str, Any]] = None
    spans: List[Dict[str, int]] = Field(default_factory=list)
    uncertainty_flags: List[UncertaintyFlag] = Field(default_factory=list)

class ParsedDocument(BaseModel):
    doc_id: int
    doc_version: int
    file_path: str
    file_hash: str
    parser_name: str
    parser_version: str
    metadata: ContractMetadata              # Metadados estruturados fidedignos ao texto
    blocks: List[DocumentBlock]
    status: str = "success"                 # success | review_metadata_mismatch | failed
    error_message: Optional[str] = None

    @model_validator(mode="after")
    def validate_document(self):
        if self.status == "success" and not self.blocks:
            raise ValueError("Documentos com sucesso devem conter ao menos um bloco")
        return self
```

- [ ] **Passo 4: Rodar teste e verificar aprovação**
Run: `uv run pytest tests/test_schemas.py -v`
Expected: PASS

- [ ] **Passo 5: Commit**
```bash
git add app/ingestion/schemas.py tests/test_schemas.py
git commit -m "feat(ingestion): implementar schema canonico com entidades juridicas ContractParty e ContractMetadata"
```

---

### Tarefa 2: Seletor da Amostra de Referência Estratificada (Golden Batch)

**Files:**
- Create: `app/ingestion/reference_sample.py`
- Create: `tests/test_reference_sample.py`

**Interfaces:**
- Consumes: `manifest.jsonl`, `data/curation/curation.jsonl`
- Produces: `select_reference_sample(target_count=40) -> List[Dict[str, Any]]` com cotas obrigatórias por formato e riscos.

- [ ] **Passo 1: Escrever teste para o seletor da amostra**

```python
# tests/test_reference_sample.py
from app.ingestion.reference_sample import select_reference_sample

def test_reference_sample_stratification():
    records = [
        {"mfiles_id": 1, "decision": "include", "route": "docx", "has_numbering": True},
        {"mfiles_id": 2, "decision": "include", "route": "libreoffice", "subformat": "ole_doc"},
        {"mfiles_id": 3, "decision": "include", "route": "libreoffice", "subformat": "rtf"},
        {"mfiles_id": 4, "decision": "include", "route": "pdf", "is_scanned": False},
        {"mfiles_id": 5, "decision": "include", "route": "pdf", "is_scanned": True},
        {"mfiles_id": 5991, "decision": "include", "route": "docx", "is_empty_risk": True},
        {"mfiles_id": 5707, "decision": "include", "route": "docx", "has_track_changes": True},
    ]
    sample = select_reference_sample(records, target_count=5)
    sample_ids = {r["mfiles_id"] for r in sample}
    assert 5991 in sample_ids
    assert 5707 in sample_ids
```

- [ ] **Passo 2: Rodar teste e verificar falha**
Run: `uv run pytest tests/test_reference_sample.py -v`
Expected: FAIL

- [ ] **Passo 3: Implementar reference_sample.py**
Garantir a seleção determinística cobrindo formatos majoritários e casos de borda patológicos do parecer.

- [ ] **Passo 4: Rodar teste e verificar aprovação**
Run: `uv run pytest tests/test_reference_sample.py -v`
Expected: PASS

- [ ] **Passo 5: Commit**
```bash
git add app/ingestion/reference_sample.py tests/test_reference_sample.py
git commit -m "feat(ingestion): implementar amostragem estratificada para calibracao dos parsers"
```

---

### Tarefa 3: Motor de Parsing DOCX com Resolução de Numeração e Revisões

**Files:**
- Create: `app/ingestion/docx_parser.py`
- Create: `tests/test_docx_parser.py`

**Interfaces:**
- Consumes: Arquivo `.docx` em disco
- Produces: Lista de `DocumentBlock` com numeração resolvida (`w:numPr` + `numbering.xml`), tabelas com contexto de coluna e flags de `w:ins`/`w:del`.

- [ ] **Passo 1: Escrever teste para extração de numeração e tabelas**

```python
# tests/test_docx_parser.py
import zipfile
from app.ingestion.docx_parser import extract_docx_blocks
from app.ingestion.schemas import UncertaintyFlag

def test_extract_docx_blocks_with_numbering(tmp_path):
    docx_file = tmp_path / "contrato.docx"
    doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body>
            <w:p><w:r><w:t>CONTRATO DE HONORÁRIOS</w:t></w:r></w:p>
            <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>
                 <w:r><w:t>Do Objeto da Prestação de Serviços</w:t></w:r></w:p>
            <w:p><w:ins w:id="1" w:author="Revisor"><w:r><w:t>Alteração proposta</w:t></w:r></w:ins></w:p>
        </w:body>
    </w:document>"""
    num_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:abstractNum w:abstractNumId="0">
            <w:lvl w:ilvl="0"><w:lvlText w:val="Cláusula %1ª - "/></w:lvl>
        </w:abstractNum>
        <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
    </w:numbering>"""
    with zipfile.ZipFile(docx_file, "w") as zf:
        zf.writestr("word/document.xml", doc_xml)
        zf.writestr("word/numbering.xml", num_xml)
        zf.writestr("[Content_Types].xml", "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'/>")

    blocks = extract_docx_blocks(docx_file, doc_id=501, doc_version=1)
    assert len(blocks) >= 2
    assert any("Cláusula 1ª" in b.text_raw for b in blocks)
    assert any(UncertaintyFlag.TRACK_CHANGES_PRESENT in b.uncertainty_flags for b in blocks)
```

- [ ] **Passo 2: Rodar teste e verificar falha**
Run: `uv run pytest tests/test_docx_parser.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.ingestion.docx_parser'`

- [ ] **Passo 3: Implementar docx_parser.py**
Parser nativo de OOXML que processa parágrafos, reconstrói hierarquia multinível de `numbering.xml` e extrai células de tabela vinculadas ao cabeçalho.

- [ ] **Passo 4: Rodar teste e verificar aprovação**
Run: `uv run pytest tests/test_docx_parser.py -v`
Expected: PASS

- [ ] **Passo 5: Commit**
```bash
git add app/ingestion/docx_parser.py tests/test_docx_parser.py
git commit -m "feat(ingestion): implementar parser docx com reconstrucao de numbering.xml e revisoes"
```

---

### Tarefa 4: Motor de Conversão de Formatos Legados (LibreOffice com Perfil Isolado)

**Files:**
- Create: `app/ingestion/legacy_parser.py`
- Create: `tests/test_legacy_parser.py`

**Interfaces:**
- Consumes: Arquivos `.doc`, `.rtf`, WordPerfect
- Produces: `.docx` em staging temporário -> processado por `docx_parser`.

- [ ] **Passo 1: Escrever teste com mock de flags isoladas do LibreOffice**

```python
# tests/test_legacy_parser.py
from unittest.mock import patch
from app.ingestion.legacy_parser import convert_legacy_to_docx

def test_convert_legacy_to_docx_command_flags(tmp_path):
    doc_path = tmp_path / "antigo.doc"
    doc_path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    out_dir = tmp_path / "staging"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        converted = out_dir / "antigo.docx"
        out_dir.mkdir(parents=True, exist_ok=True)
        converted.write_bytes(b"PK\x03\x04")

        res = convert_legacy_to_docx(doc_path, out_dir, timeout_sec=20)
        assert res.exists()
        # Valida que as flags mandatórias de isolamento foram passadas
        cmd_args = mock_run.call_args[0][0]
        assert "--headless" in cmd_args
        assert any("-env:UserInstallation=" in arg for arg in cmd_args)
```

- [ ] **Passo 2: Rodar teste e verificar falha**
Run: `uv run pytest tests/test_legacy_parser.py -v`
Expected: FAIL

- [ ] **Passo 3: Implementar legacy_parser.py**
Adicionar flags de proteção contra crash/deadlock (`--headless --invisible --nocrashreport --nodefault --nofirststartwizard --nologo --norestore`) e diretório temporário `UserInstallation` limpo em bloco `finally`.

- [ ] **Passo 4: Rodar teste e verificar aprovação**
Run: `uv run pytest tests/test_legacy_parser.py -v`
Expected: PASS

- [ ] **Passo 5: Commit**
```bash
git add app/ingestion/legacy_parser.py tests/test_legacy_parser.py
git commit -m "feat(ingestion): implementar conversor headless seguro do LibreOffice com perfil isolado"
```

---

### Tarefa 5: Motor de Parsing PDF com Rasterização `pypdfium2` para OCR

**Files:**
- Modify: `pyproject.toml` (adicionar `pypdfium2`, `pypdf`, `pytesseract`)
- Create: `app/ingestion/pdf_parser.py`
- Create: `tests/test_pdf_parser.py`

**Interfaces:**
- Consumes: Arquivos `.pdf`
- Produces: Lista de `DocumentBlock` via extração direta de texto ou via rasterização em memória com `pypdfium2` e OCR Tesseract (`por`) quando a página for digitalizada.

- [ ] **Passo 1: Escrever teste de extração híbrida nativa vs OCR com pypdfium2**

```python
# tests/test_pdf_parser.py
from unittest.mock import patch, MagicMock
from app.ingestion.pdf_parser import extract_pdf_blocks
from app.ingestion.schemas import UncertaintyFlag

def test_pdf_ocr_triggered_for_scanned_pages(tmp_path):
    pdf_path = tmp_path / "scan.pdf"
    pdf_path.write_bytes(b"%PDF-1.4...")

    with patch("app.ingestion.pdf_parser.pypdf.PdfReader") as mock_pdf, \
         patch("app.ingestion.pdf_parser.pypdfium2.PdfDocument") as mock_ium, \
         patch("app.ingestion.pdf_parser.pytesseract.image_to_string") as mock_ocr:
        
        # Pagina sem texto nativo (< 50 chars)
        page = MagicMock()
        page.extract_text.return_value = ""
        mock_pdf.return_value.pages = [page]
        
        # Simula OCR devolvendo texto
        mock_ocr.return_value = "Cláusula 1ª - Pelo presente instrumento de acordo extrajudicial..."

        blocks = extract_pdf_blocks(pdf_path, doc_id=801, doc_version=1)
        assert len(blocks) >= 1
        assert "acordo extrajudicial" in blocks[0].text_raw
        assert UncertaintyFlag.LOW_CONFIDENCE_OCR in blocks[0].uncertainty_flags
```

- [ ] **Passo 2: Rodar teste e verificar falha**
Run: `uv run pytest tests/test_pdf_parser.py -v`
Expected: FAIL

- [ ] **Passo 3: Adicionar dependências e implementar pdf_parser.py**
Adicionar `pypdfium2`, `pypdf`, `pytesseract` ao projeto via `uv add` e implementar lógica de inspeção de texto nativo com fallback para renderização em bitmap `pypdfium2` e OCR cirúrgico.

- [ ] **Passo 4: Rodar teste e verificar aprovação**
Run: `uv run pytest tests/test_pdf_parser.py -v`
Expected: PASS

- [ ] **Passo 5: Commit**
```bash
git add pyproject.toml uv.lock app/ingestion/pdf_parser.py tests/test_pdf_parser.py
git commit -m "feat(ingestion): implementar parser pdf com pypdf e rasterizacao pypdfium2 para OCR"
```

---

### Tarefa 6: Extrator Jurídico de Metadados Reais (`metadata_extractor.py`)

**Files:**
- Create: `app/ingestion/metadata_extractor.py`
- Create: `tests/test_metadata_extractor.py`

**Interfaces:**
- Consumes: Lista de `DocumentBlock` (Preâmbulo, Título, Primeiras Cláusulas e Fecho)
- Produces: Instância preenchida de `ContractMetadata` contendo:
  - `formal_title`: Epígrafe textual exata.
  - `instrument_type`: Natureza jurídica real.
  - `parties`: Lista de `ContractParty` com nome qualificado, papel e identificador (CNPJ/CPF) estritamente vinculado.
  - `execution_date`: Data de assinatura/celebração extraída do fecho.

- [ ] **Passo 1: Escrever teste rigoroso para extração de partes, papéis e CNPJ/CPF**

```python
# tests/test_metadata_extractor.py
from app.ingestion.metadata_extractor import extract_contract_metadata
from app.ingestion.schemas import DocumentBlock, BlockType, HierarchyLevel, PartyRole

def test_extract_parties_roles_and_cnpjs_from_preamble():
    preamble_text = (
        "CONTRATO DE HONORÁRIOS ADVOCATÍCIOS\n\n"
        "De um lado, ANDRADE ADVOGADOS ASSOCIADOS, sociedade de advogados inscrita na OAB/SP sob nº 1234 "
        "e no CNPJ sob nº 11.222.333/0001-44, doravante denominada simplesmente CONTRATADA; e, de outro lado, "
        "COMPANHIA BRASILEIRA DE ALIMENTOS S.A., inscrita no CNPJ sob nº 99.888.777/0001-66, "
        "representada por seu diretor João da Silva, CPF nº 123.456.789-00, doravante denominada CONTRATANTE."
    )
    blocks = [
        DocumentBlock(
            block_id="b0", doc_id=1, doc_version=1, block_type=BlockType.PREAMBLE,
            hierarchy_level=HierarchyLevel.PREAMBLE, order_index=0,
            text_raw=preamble_text, text_search=preamble_text
        )
    ]
    meta = extract_contract_metadata(blocks)
    assert meta.instrument_type == "Honorários"
    assert "CONTRATO DE HONORÁRIOS ADVOCATÍCIOS" in meta.formal_title
    
    # Validação das partes e papéis
    assert len(meta.parties) == 2
    p1 = next(p for p in meta.parties if p.is_law_firm)
    p2 = next(p for p in meta.parties if not p.is_law_firm)
    
    assert p1.role == PartyRole.CONTRATADA
    assert p1.clean_identifier == "11222333000144"
    assert p2.role == PartyRole.CONTRATANTE
    assert p2.clean_identifier == "99888777000166"
    assert "COMPANHIA BRASILEIRA DE ALIMENTOS" in p2.name
    # CPF do representante João da Silva NÃO pode contaminar a parte principal
    assert p2.clean_identifier != "12345678900"
```

- [ ] **Passo 2: Rodar teste e verificar falha**
Run: `uv run pytest tests/test_metadata_extractor.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.ingestion.metadata_extractor'`

- [ ] **Passo 3: Implementar metadata_extractor.py**
Implementar o motor determinístico baseado em padrões léxicos de contratos em português:
- Regexes de preâmbulo: `de um lado... de outro lado`, `entre si celebram`, `doravante denominado(a) [PAPEL]`.
- Vínculo direto de CNPJ/CPF à entidade correspondente no mesmo parágrafo/cláusula.
- Filtro de signatários/testemunhas (isolando representantes legais de partes contratuais).
- Detecção do tipo de instrumento a partir da epígrafe superior e objeto inicial.

- [ ] **Passo 4: Rodar teste e verificar aprovação**
Run: `uv run pytest tests/test_metadata_extractor.py -v`
Expected: PASS

- [ ] **Passo 5: Commit**
```bash
git add app/ingestion/metadata_extractor.py tests/test_metadata_extractor.py
git commit -m "feat(ingestion): implementar extrator juridico de partes, papeis e identificadores do preambulo"
```

---

### Tarefa 7: Reconciliação Documental (M-Files vs Texto) e Portão de Quarentena

**Files:**
- Create: `app/ingestion/parsing_pipeline.py`
- Create: `tests/test_parsing_pipeline.py`

**Interfaces:**
- Consumes: `mfiles_record` (metadados brutos do M-Files) + `ParsedDocument` com `ContractMetadata`
- Produces: `reconcile_and_validate(mfiles_record, parsed_doc) -> ParsedDocument` com status `success` ou quarentena explícita `review_metadata_mismatch`.

- [ ] **Passo 1: Escrever teste de detecção de divergência e quarentena**

```python
# tests/test_parsing_pipeline.py
from app.ingestion.parsing_pipeline import reconcile_document
from app.ingestion.schemas import ParsedDocument, ContractMetadata, ContractParty, PartyRole, DocumentBlock, BlockType, HierarchyLevel

def test_reconciler_flags_client_mismatch_and_sends_to_quarantine():
    # M-Files dizia que o cliente era "Banco Santander", mas o contrato no texto é entre "Alfa" e "Beta"
    mfiles_record = {
        "mfiles_id": 999,
        "title": "Contrato Locacao.docx",
        "class_name": "Contrato",
        "properties": {"Cliente": "Banco Santander S/A"}
    }
    parties = [
        ContractParty(name="Imobiliária Alfa Ltda", role=PartyRole.LOCADOR, clean_identifier="11111111000100"),
        ContractParty(name="Comércio Beta Ltda", role=PartyRole.LOCATARIO, clean_identifier="22222222000100")
    ]
    meta = ContractMetadata(formal_title="CONTRATO DE LOCAÇÃO", instrument_type="Locação", parties=parties)
    blocks = [DocumentBlock(block_id="b1", doc_id=999, doc_version=1, block_type=BlockType.CLAUSE,
                            hierarchy_level=HierarchyLevel.CLAUSE, order_index=1, text_raw="Texto", text_search="Texto")]
    doc = ParsedDocument(doc_id=999, doc_version=1, file_path="c.docx", file_hash="h1",
                         parser_name="docx", parser_version="1", metadata=meta, blocks=blocks)

    reconciled = reconcile_document(mfiles_record, doc)
    assert reconciled.status == "review_metadata_mismatch"
    assert "DISCREPANCY_CLIENT_MISMATCH" in reconciled.metadata.mfiles_divergence_flags
```

- [ ] **Passo 2: Rodar teste e verificar falha**
Run: `uv run pytest tests/test_parsing_pipeline.py -v`
Expected: FAIL

- [ ] **Passo 3: Implementar parsing_pipeline.py com MetadataReconciler**
Implementar o reconciliador que compara:
1. `Cliente` do M-Files vs Partes extraídas do preâmbulo (se nenhum bater, marca divergência).
2. `class_name` do M-Files vs `instrument_type` real (ex.: M-Files "Contrato" vs Texto "Termo Aditivo" -> atualiza para Aditivo com flag de ajuste).
3. Documentos com divergência severa recebem `status='review_metadata_mismatch'` e são barrados do fluxo de indexação automática.

- [ ] **Passo 4: Rodar teste e verificar aprovação**
Run: `uv run pytest tests/test_parsing_pipeline.py -v`
Expected: PASS

- [ ] **Passo 5: Commit**
```bash
git add app/ingestion/parsing_pipeline.py tests/test_parsing_pipeline.py
git commit -m "feat(ingestion): implementar reconciliador M-Files vs Texto e portao de quarentena de metadados"
```

---

### Tarefa 8: Chunker Hierárquico Jurídico com Cabeçalho e `verbatim_text` Segregados

**Files:**
- Create: `app/ingestion/chunker.py`
- Create: `tests/test_chunker.py`

**Interfaces:**
- Consumes: Lista de `ParsedDocument` com status `success`
- Produces: Chunks LangChain `Document` onde:
  - `page_content`: Contém o cabeçalho contextual de busca formatado seguido da cláusula.
  - `metadata["verbatim_text"]`: Transcrição pura e intocada da cláusula para citação literal da Regra dos 4 Elementos.
  - `metadata["clean_identifiers"]`: Lista de CNPJs/CPFs normalizados para filtro léxico.

- [ ] **Passo 1: Escrever teste de segregação de cabeçalho e verbatim_text**

```python
# tests/test_chunker.py
from app.ingestion.chunker import create_legal_chunks
from app.ingestion.schemas import ParsedDocument, ContractMetadata, ContractParty, PartyRole, DocumentBlock, BlockType, HierarchyLevel

def test_chunker_injects_reconciled_header_and_preserves_verbatim_text():
    parties = [
        ContractParty(name="Andrade Advogados", role=PartyRole.CONTRATADA, clean_identifier="11222333000144", is_law_firm=True),
        ContractParty(name="Tech Soluções", role=PartyRole.CONTRATANTE, clean_identifier="55666777000188")
    ]
    meta = ContractMetadata(formal_title="CONTRATO DE HONORÁRIOS", instrument_type="Honorários", parties=parties)
    raw_clause = "Cláusula 3ª - O valor mensal fixo será de R$ 15.000,00 (quinze mil reais)."
    blocks = [
        DocumentBlock(block_id="b1", doc_id=50, doc_version=1, block_type=BlockType.CLAUSE,
                      hierarchy_level=HierarchyLevel.CLAUSE, hierarchy_label="Cláusula 3ª",
                      order_index=1, text_raw=raw_clause, text_search=raw_clause)
    ]
    doc = ParsedDocument(doc_id=50, doc_version=1, file_path="c.docx", file_hash="h50",
                         parser_name="docx", parser_version="1", metadata=meta, blocks=blocks)

    chunks = create_legal_chunks([doc])
    assert len(chunks) == 1
    chunk = chunks[0]
    # page_content possui cabeçalho de busca
    assert "[Instrumento: Honorários - CONTRATO DE HONORÁRIOS" in chunk.page_content
    assert "Contratada: Andrade Advogados (11222333000144)" in chunk.page_content
    assert "Contratante: Tech Soluções (55666777000188)" in chunk.page_content
    # verbatim_text é estritamente a cláusula pura (sem cabeçalho de busca sintético)
    assert chunk.metadata["verbatim_text"] == raw_clause
    assert "11222333000144" in chunk.metadata["clean_identifiers"]
```

- [ ] **Passo 2: Rodar teste e verificar falha**
Run: `uv run pytest tests/test_chunker.py -v`
Expected: FAIL

- [ ] **Passo 3: Implementar chunker.py**
Implementar montagem de chunks com preâmbulo autônomo, cláusulas preservadas, cabeçalho de busca normalizado e persistência do `verbatim_text` no dicionário de metadados.

- [ ] **Passo 4: Rodar teste e verificar aprovação**
Run: `uv run pytest tests/test_chunker.py -v`
Expected: PASS

- [ ] **Passo 5: Commit**
```bash
git add app/ingestion/chunker.py tests/test_chunker.py
git commit -m "feat(ingestion): implementar chunker com cabecalho contextual e verbatim_text para citacao"
```

---

### Tarefa 9: Indexação Híbrida Atômica (ChromaDB + BM25) com Pré-Filtro de CNPJ

**Files:**
- Modify: `pyproject.toml` (adicionar `chromadb`, `rank-bm25`, `joblib`)
- Create: `app/retrieval/indexer.py`
- Create: `app/retrieval/hybrid.py`
- Create: `tests/test_hybrid_retriever.py`

**Interfaces:**
- Consumes: Chunks LangChain
- Produces: `LegalHybridRetriever` sincronizado sob `corpus_generation_id`, serializando BM25 via `joblib` e aplicando pré-filtragem determinística quando houver CNPJ/CPF na query.

- [ ] **Passo 1: Escrever teste de indexação sincronizada e pré-filtro de CNPJ**

```python
# tests/test_hybrid_retriever.py
from langchain_core.documents import Document
from app.retrieval.hybrid import LegalHybridRetriever, build_and_save_hybrid_index

def test_hybrid_retriever_exact_cnpj_filter_and_atomic_ids(tmp_path):
    chunks = [
        Document(
            page_content="[Instrumento: Honorários] Cláusula 1ª Honorários de 20%",
            metadata={"chunk_id": "doc_1_c1", "doc_id": 1, "clean_identifiers": ["11222333000144"],
                      "verbatim_text": "Cláusula 1ª Honorários de 20%"}
        ),
        Document(
            page_content="[Instrumento: Locação] Cláusula 2ª Aluguel devido",
            metadata={"chunk_id": "doc_2_c1", "doc_id": 2, "clean_identifiers": ["99888777000166"],
                      "verbatim_text": "Cláusula 2ª Aluguel devido"}
        )
    ]
    index_path = tmp_path / "indices"
    retriever = build_and_save_hybrid_index(chunks, generation_id="gen_v1", base_dir=index_path)
    
    # Consulta mencionando CNPJ específico com formatação
    results = retriever.invoke("Qual o percentual no contrato do CNPJ 11.222.333/0001-44?")
    assert len(results) >= 1
    assert results[0].metadata["chunk_id"] == "doc_1_c1"
    assert results[0].metadata["verbatim_text"] == "Cláusula 1ª Honorários de 20%"
```

- [ ] **Passo 2: Rodar teste e verificar falha**
Run: `uv run pytest tests/test_hybrid_retriever.py -v`
Expected: FAIL

- [ ] **Passo 3: Adicionar dependências e implementar indexador e retriever híbrido**
Adicionar `chromadb`, `rank-bm25`, `joblib` via `uv add`. No `hybrid.py`:
- Cria ChromaDB local persistido em `{base_dir}/{gen_id}/chroma`.
- Cria e serializa `BM25Retriever` via `joblib.dump` em `{base_dir}/{gen_id}/bm25.joblib`.
- Implementa `LegalHybridRetriever`: se a query do usuário contiver padrão de CNPJ/CPF, extrai os dígitos e pré-filtra a base de chunks antes de executar a fusão por RRF do `EnsembleRetriever(id_key="chunk_id")`.

- [ ] **Passo 4: Rodar teste e verificar aprovação**
Run: `uv run pytest tests/test_hybrid_retriever.py -v`
Expected: PASS

- [ ] **Passo 5: Commit**
```bash
git add pyproject.toml uv.lock app/retrieval/ tests/test_hybrid_retriever.py
git commit -m "feat(retrieval): implementar indexacao sincrona Chroma+BM25 e LegalHybridRetriever com pre-filtro de CNPJ"
```

---

### Tarefa 10: Observabilidade Segura e Mascaramento Global de PII no LangSmith

**Files:**
- Modify: `app/monitoring.py`
- Create: `tests/test_pii_redaction.py`

**Interfaces:**
- Consumes: Traces e runs do LangChain
- Produces: Interceptador de telemetria assegurando `hide_inputs=True` e `hide_outputs=True` para nós que manipulam texto integral de contratos.

- [ ] **Passo 1: Escrever teste para bloqueio de texto de contratos na telemetria**

```python
# tests/test_pii_redaction.py
from app.monitoring import configure_langsmith_redaction

def test_langsmith_callback_hides_contract_payloads():
    config = configure_langsmith_redaction()
    # Verifica que flags de ocultação de payload de contratos estão ativas
    assert config.get("hide_inputs") is True
    assert config.get("hide_outputs") is True
```

- [ ] **Passo 2: Rodar teste e verificar falha**
Run: `uv run pytest tests/test_pii_redaction.py -v`
Expected: FAIL

- [ ] **Passo 3: Implementar mascaramento em app/monitoring.py**
Configurar callbacks e atributos de tracing do LangChain garantindo que metadados operacionais (latência, tokens, IDs de geração) sejam logados, enquanto `page_content` e `verbatim_text` fiquem restritos ao perímetro local.

- [ ] **Passo 4: Rodar teste e verificar aprovação**
Run: `uv run pytest tests/test_pii_redaction.py -v`
Expected: PASS

- [ ] **Passo 5: Commit**
```bash
git add app/monitoring.py tests/test_pii_redaction.py
git commit -m "feat(monitoring): configurar mascaramento global de inputs/outputs para proteger PII no LangSmith"
```

---

## Critérios de Aceitação e Definição de Concluído (DoD)

1. **Fidedignidade Documental de Metadados:** 100% dos documentos aprovados têm partes e natureza jurídica extraídas diretamente do texto e reconciliadas com o M-Files; qualquer incompatibilidade de cliente envia o arquivo para `review_metadata_mismatch`.
2. **Separação Busca vs. Citação Literal:** O campo `metadata["verbatim_text"]` contém estritamente o texto legal da cláusula sem injeção de cabeçalhos artificiais, viabilizando a Regra dos 4 Elementos.
3. **Fidelidade de Numeração:** Numeração multinível (`numbering.xml`) resolvida com rótulos ordinais preservados.
4. **Resiliência do Parser de PDF:** Rasterização em memória via `pypdfium2` sem falhas de execução no OCR Tesseract.
5. **Sincronia Total de Índices:** Índices ChromaDB e BM25 construídos atomicamente sob a mesma `corpus_generation_id` com o exato mesmo conjunto de `chunk_id`s.
6. **Aprovação do Agente Juiz:** Avaliação do pipeline integrado com nota individual >= 9,0/10 em todos os 6 critérios.
