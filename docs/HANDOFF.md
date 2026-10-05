# Handoff: Pipeline de Preparação de Dados, Parsing e Ingestão Híbrida — RAG Andrade Advogados

**Data:** 05/10/2026  
**Repositório:** `https://github.com/orugian/RAG-juridico`  
**Branch Atual:** `main` (sincronizada com `origin/main` e `origin/feat/data-prep-pipeline`)  
**Status da Suíte de Testes:** **278 testes passando (100% de sucesso, zero falhas)**

---

## 1. Contextualização: O Que Foi Efetivamente Realizado Nesta Sessão

Nesta sessão, projetamos, implementamos, testamos via TDD e homologamos através de 4 portões de revisão adversarial do **Agente Juiz** todo o pipeline ponta a ponta de preparação de dados, parsing e ingestão híbrida para o acervo contratual da Andrade Advogados.

### A. Cumprimento da Diretriz Crítica do Usuário (Fidedignidade dos Metadados)
- **O Problema Histórico:** Propriedades cadastradas no GED (M-Files) continham imprecisões cadastrais (ex.: clientes preenchidos com o nome da banca, minutas sem partes qualificadas, divergência de titularidade). O usuário ressaltou com ênfase que metadados incorretos levariam a alucinações e respostas falsas em produção.
- **A Solução Implementada:**
  1. [`app/ingestion/metadata_extractor.py`](file:///C:/Users/orugi/Documents/Projetos/production-api-aadvogados/app/ingestion/metadata_extractor.py): extrai partes, papéis ([`PartyRole`](file:///C:/Users/orugi/Documents/Projetos/production-api-aadvogados/app/ingestion/schemas.py)), epígrafe formal, tipo de instrumento e datas **diretamente do preâmbulo e fecho do texto real**.
  2. **Isolamento de Representantes Legais:** se uma empresa possui CNPJ e diretores/sócios qualificados com CPF, o CNPJ da empresa é blindado como identificador principal (`clean_identifier`), sem poluição pelo CPF de signatários.
  3. **Discriminação da Banca:** Andrade Advogados só recebe `is_law_firm=True` quando expressamente qualificada. Contratos sob custódia não recebem presunção indevida.
  4. [`app/ingestion/parsing_pipeline.py`](file:///C:/Users/orugi/Documents/Projetos/production-api-aadvogados/app/ingestion/parsing_pipeline.py) (`reconcile_document`): confronta os dados do M-Files contra o texto real. Inconsistências de cliente disparam `DISCREPANCY_CLIENT_MISMATCH` e o status `review_metadata_mismatch`, **bloqueando sumariamente o documento da indexação automática**.
  5. [`app/ingestion/chunker.py`](file:///C:/Users/orugi/Documents/Projetos/production-api-aadvogados/app/ingestion/chunker.py): constrói o cabeçalho sintético de busca no `page_content`, mas preserva de forma pura, inviolável e segregada a transcrição literal da cláusula em `metadata["verbatim_text"]` para a **Regra dos 4 Elementos**.

### B. Entregas por Tarefa e Commits Realizados
- **Tarefa 0 (`19e36a0`):** Hardening da Curadoria e Preservação de Anotações humanas em `qa_sample.csv`, ampliação de `doc_key` e limiar rígido (0.95) em `near_dup.py`.
- **Tarefa 1 (`2b7e686`):** Schema Intermediário Canônico em `app/ingestion/schemas.py` com Pydantic v2 (`DocumentBlock`, `ContractMetadata`, `ContractParty`, `ParsedDocument`).
- **Tarefa 2 (`c87fe40`):** Seletor da Amostra de Referência Estratificada em `app/ingestion/reference_sample.py` (Golden Batch com cotas determinísticas e injeção compulsória dos casos patológicos 5991, 5707 e 5829).
- **Tarefa 3 (`4b3ba0c`):** Motor de Parsing DOCX nativo em `app/ingestion/docx_parser.py` com resolução hierárquica de `numbering.xml`, detecção de revisões (`w:ins`/`w:del`), comentários e extração contextual de tabelas associando cabeçalhos de coluna.
- **Tarefa 4 (`5a0ab64`):** Conversor de Formatos Legados em `app/ingestion/legacy_parser.py` via LibreOffice headless com perfil de usuário isolado e efêmero `-env:UserInstallation=file:///{temp_dir}`.
- **Tarefa 5 (`7324744`):** Motor de Parsing PDF em `app/ingestion/pdf_parser.py` com extração digital `pypdf` e fallback cirúrgico para renderização em memória 2.0x via `pypdfium2` e OCR Tesseract para páginas escaneadas (< 50 caracteres).
- **Tarefa 6 (`4ceb605`):** Extrator Jurídico de Metadados Reais em `app/ingestion/metadata_extractor.py` atendendo à ressalva do usuário.
- **Tarefa 7 (`2c365e8`):** Reconciliador M-Files vs Texto e Portão de Quarentena em `app/ingestion/parsing_pipeline.py`.
- **Tarefa 8 (`26c6118`):** Chunker Hierárquico Jurídico em `app/ingestion/chunker.py` segregando cabeçalho de busca de `metadata["verbatim_text"]`.
- **Tarefa 9 (`9d70385`):** Indexação Híbrida Atômica em `app/retrieval/indexer.py` e `app/retrieval/hybrid.py` sincronizando ChromaDB e BM25 (`joblib`) sob a mesma `corpus_generation_id`, com `LegalHybridRetriever` aplicando pré-filtro determinístico por CNPJ/CPF e fusão RRF.
- **Tarefa 10 (`ab5fc7d`):** Observabilidade Segura e Mascaramento de PII em `app/monitoring.py` com `configure_langsmith_redaction`, `SafeLangSmithCallbackHandler` e sanitização recursiva de payloads para conformidade LGPD/OAB.

### C. Resultados dos 4 Portões do Agente Juiz
- **Portão 1 (Fundações):** Nota 9,6/10 — Aprovado com Distinção ([`docs/superpowers/plans/gate-1-audit-report.md`](file:///C:/Users/orugi/Documents/Projetos/production-api-aadvogados/docs/superpowers/plans/gate-1-audit-report.md)).
- **Portão 2 (Parsers):** Nota 9,6/10 — Aprovado com Distinção ([`docs/superpowers/plans/gate-2-audit-report.md`](file:///C:/Users/orugi/Documents/Projetos/production-api-aadvogados/docs/superpowers/plans/gate-2-audit-report.md)).
- **Portão 3 (Metadados e Quarentena):** Nota 9,7/10 — Aprovado com Louvor ([`docs/superpowers/plans/gate-3-audit-report.md`](file:///C:/Users/orugi/Documents/Projetos/production-api-aadvogados/docs/superpowers/plans/gate-3-audit-report.md)).
- **Portão 4 (Ingestão Híbrida e PII):** Nota 9,8/10 — Aprovado com Distinção Máxima ([`docs/superpowers/plans/gate-4-audit-report.md`](file:///C:/Users/orugi/Documents/Projetos/production-api-aadvogados/docs/superpowers/plans/gate-4-audit-report.md)).

---

## 2. O Que É Esperado Para a Próxima Sessão

Na próxima sessão de desenvolvimento, o agente assumirá as seguintes frentes:

1. **Execução Operacional da Ingestão em Lote (Batch Run):**
   - Executar o pipeline completo (`run_parsing_pipeline`) sobre todos os ~287 documentos curados em `data/curation/curation.jsonl` a partir dos arquivos físicos em `files/`.
   - Gerar os chunks canônicos via `create_legal_chunks`.
   - Indexar e persistir o conjunto atômico final no disco em `data/indices/{generation_id}/` (`chroma/` e `bm25.joblib`) via `build_and_save_hybrid_index`.
   - Exportar o relatório final de documentos aprovados vs. documentos retidos em quarentena (`review_metadata_mismatch`).

2. **Integração no Grafo LangGraph (`app/agent.py`):**
   - Plugar o [`LegalHybridRetriever`](file:///C:/Users/orugi/Documents/Projetos/production-api-aadvogados/app/retrieval/hybrid.py) no nó de busca (`retrieve_node`) do grafo RAG.
   - Alimentar o nó de geração do LLM com os chunks recuperados, instruindo o prompt a usar estritamente `metadata["verbatim_text"]` para a transcrição da prova jurídica (Regra dos 4 Elementos).
   - Integrar o nó de validação/adjudicação de citações para checar alucinações contra o `verbatim_text`.

3. **Validação Ponta a Ponta na API FastAPI (`app/main.py`):**
   - Atualizar o endpoint `/chat` para consumir o grafo integrado com o novo retriever híbrido persistido.
   - Assegurar que o tracing do LangSmith utilize o `SafeLangSmithCallbackHandler` configurado com `hide_inputs=True` e `hide_outputs=True`.
   - Realizar testes de ponta a ponta com consultas reais de contratos (por exemplo, buscando por CNPJs e por cláusulas temáticas como multas, rescisão e honorários).

---

## 3. Para Quê (Objetivo e Valor do Negócio)

- **Para** disponibilizar aos advogados e operadores do escritório uma ferramenta de inteligência artificial de alta confiabilidade para busca e análise de minutas e contratos históricos.
- **Para** eliminar por completo respostas com metadados falsos ou contratos atribuídos incorretamente a clientes.
- **Para** viabilizar a **Regra dos 4 Elementos** em produção: cada citação do assistente trará (1) Identificador do Contrato, (2) Partes Qualificadas com Papéis, (3) Localização Exata e (4) Transcrição Literal Limpa.
- **Para** assegurar conformidade estrita com o sigilo profissional da OAB e a LGPD através do mascaramento e soberania local.

---

## 4. Resultado Esperado

- Acervo de produção inteiramente indexado de forma híbrida em `data/indices/prod_v1/`.
- Endpoint `POST /chat` respondendo consultas com velocidade, precisão semântica e citações literais verificadas.
- Rastreamento transparente de telemetria sem nenhum vazamento de PII no LangSmith.
- Suíte de testes automatizados expandida e 100% verde cobrindo a camada de API e Grafo.

---

## 5. Suggested Skills

O próximo agente deve considerar a ativação das seguintes skills para conduzir o trabalho:
- `subagent-driven-development`: para orquestrar as tarefas de integração com portões de qualidade.
- `executing-plans`: caso deseje decompor a integração do Grafo e da API em um plano sequencial de checkpoints.
- `tdd`: para manter o ciclo Red-Green na integração dos nós do LangGraph e endpoints FastAPI.
- `verification-before-completion`: para rodar comandos de verificação empírica e testes de regressão antes de qualquer asserção de conclusão.
