# Relatório de Conclusão - Tarefa 4: Motor de Conversão de Formatos Legados (LibreOffice com Perfil Isolado)

## 1. Visão Geral
- **Objetivo**: Implementar o motor de conversão de formatos legados (`.doc` binário OLE, `.rtf` e WordPerfect) em `app/ingestion/legacy_parser.py` e testes abrangentes em `tests/test_legacy_parser.py`. O conversor utiliza o LibreOffice em modo headless com perfil de usuário efêmero e isolado (`-env:UserInstallation`), prevenindo deadlocks e interferência concorrente, convertendo para `.docx` em diretório de staging para extração canônica via `docx_parser`.
- **Branch**: `feat/data-prep-pipeline`
- **Commit**: `5a0ab64` (`feat(ingestion): implementar conversor headless seguro do LibreOffice com perfil isolado`)
- **Status**: Concluído com sucesso. Suíte de testes 100% verde (201 testes no total, 21 novos testes dedicados ao conversor legado).

---

## 2. Arquivos Criados e Implementações

### `app/ingestion/legacy_parser.py`
1. **Localização Dinâmica do Binário do LibreOffice (`find_libreoffice_binary`)**:
   - Resolução em três camadas com ordenação determinística de prioridade:
     1. **Variáveis de Ambiente**: Inspeciona `LIBREOFFICE_PATH` e `SOFFICE_PATH` aceitando caminho direto para o executável ou diretório contendo o binário (`soffice.exe`, `soffice`, `libreoffice.exe`, `libreoffice`).
     2. **PATH do Sistema Operacional**: Busca via `shutil.which` por `soffice`, `libreoffice`, `soffice.exe` e `libreoffice.exe`.
     3. **Caminhos Padrão de Instalação no Windows**: Varre os diretórios canônicos do LibreOffice em `ProgramFiles` e `ProgramFiles(x86)` (`C:\Program Files\LibreOffice\program\soffice.exe` e `C:\Program Files (x86)\LibreOffice\program\soffice.exe`).
   - Retorna o caminho em string caso localizado, ou `None` caso ausente.

2. **Conversão Segura e Isolamento de Perfil (`convert_legacy_to_docx`)**:
   - Valida a existência do arquivo de entrada e confirma que se trata de arquivo regular (`FileNotFoundError` / `ValueError`).
   - Resolve o binário do LibreOffice (autodetecção ou parâmetro explícito `libreoffice_cmd`).
   - Garante a criação do diretório de destino (`output_dir`).
   - **Isolamento de Perfil com Diretório Efêmero**:
     - Cria diretório temporário exclusivo via `tempfile.mkdtemp(prefix="soffice_profile_")`.
     - Normaliza o caminho para o formato URI aceito pelo LibreOffice: `-env:UserInstallation=file:///{profile_path}`.
     - Previne colisões em ambientes com múltiplos workers/threads e bloqueios de lock files (`.~lock.*`).
   - **Flags Mandatórias de Segurança e Headless**:
     - `--headless`
     - `--invisible`
     - `--nocrashreport`
     - `--nodefault`
     - `--nofirststartwizard`
     - `--nologo`
     - `--norestore`
     - `-env:UserInstallation=file:///...`
     - `--convert-to docx`
     - `--outdir <output_dir>`
     - `<input_path>`
   - Limpeza preventiva de arquivo de destino pré-existente para evitar falsos positivos se a conversão falhar silenciosamente.
   - **Tratamento Robusto de Falhas e Limpeza Garantida**:
     - Bloco `finally:` mandatório com `shutil.rmtree(profile_dir, ignore_errors=True)` que assegura eliminação imediata do perfil temporário em qualquer desfecho (sucesso, erro de execução, timeout ou exceção).
     - Conversão de `subprocess.TimeoutExpired` na exceção tipada `LibreOfficeTimeoutError`.
     - Validação de código de retorno diferente de zero com detalhamento de `stderr`/`stdout` encapsulado em `LibreOfficeConversionError`.
     - Validação explícita da existência do arquivo `.docx` gerado no diretório de destino.

3. **Pipeline Completo de Extração de Legados (`extract_legacy_blocks`)**:
   - Recebe o arquivo legado (`.doc`, `.rtf`, WordPerfect), `doc_id` e `doc_version`.
   - Gerencia o diretório de staging:
     - Caso `staging_dir` não seja informado, cria um diretório efêmero via `tempfile.mkdtemp(prefix="legacy_staging_")`.
     - Caso fornecido, utiliza o diretório customizado especificado.
   - Converte o legado para `.docx` intermediário chamando `convert_legacy_to_docx`.
   - Invoca o parser nativo canônico `extract_docx_blocks(converted_docx, doc_id, doc_version)` de `app.ingestion.docx_parser`.
   - No bloco `finally:`, realiza a limpeza mandatória do diretório de staging e do arquivo `.docx` intermediário caso tenha sido criado em diretório efêmero.
   - Retorna a lista canônica de `DocumentBlock`s.

4. **Hierarquia de Exceções**:
   - `LegacyParserError(Exception)`: Exceção base do módulo.
   - `LibreOfficeNotFoundError(LegacyParserError, FileNotFoundError, RuntimeError)`: Binário ausente.
   - `LibreOfficeConversionError(LegacyParserError, RuntimeError)`: Falha na conversão ou saída não gerada.
   - `LibreOfficeTimeoutError(LibreOfficeConversionError, TimeoutError)`: Timeout na conversão.

---

## 3. Testes Criados em `tests/test_legacy_parser.py`

Suíte completa com 21 testes unitários e de integração:
1. `test_find_libreoffice_binary_from_libreoffice_path_env`: Detecção a partir de `LIBREOFFICE_PATH`.
2. `test_find_libreoffice_binary_from_soffice_path_env`: Detecção a partir de `SOFFICE_PATH`.
3. `test_find_libreoffice_binary_from_env_dir`: Detecção quando a variável aponta para diretório com executável.
4. `test_find_libreoffice_binary_from_path`: Detecção no PATH via `shutil.which`.
5. `test_find_libreoffice_binary_windows_standard_path`: Detecção no caminho padrão do Windows (`ProgramFiles`).
6. `test_find_libreoffice_binary_windows_x86_standard_path`: Detecção no caminho padrão x86 (`ProgramFiles(x86)`).
7. `test_find_libreoffice_binary_from_dir_with_soffice_no_exe`: Detecção de binário sem extensão em diretório.
8. `test_find_libreoffice_binary_not_found`: Retorno de `None` quando o LibreOffice não está disponível.
9. `test_convert_legacy_to_docx_input_file_missing`: Lançamento de `FileNotFoundError` para arquivo inexistente.
10. `test_convert_legacy_to_docx_input_is_directory`: Lançamento de `ValueError` quando a entrada é um diretório.
11. `test_convert_legacy_to_docx_binary_not_found`: Lançamento de `LibreOfficeNotFoundError` se o binário não for localizado.
12. `test_convert_legacy_to_docx_flags_and_profile_isolation`: Verificação detalhada de todas as flags headless, URI do perfil efêmero e exclusão do perfil em caso de sucesso.
13. `test_convert_legacy_to_docx_cleans_profile_on_failure`: Garantia de limpeza do perfil efêmero mesmo com falha no subprocesso.
14. `test_convert_legacy_to_docx_timeout`: Conversão de timeout em `LibreOfficeTimeoutError` e limpeza do perfil temporário.
15. `test_convert_legacy_to_docx_output_file_missing_after_zero_exit`: Tratamento de erro quando o código de retorno é 0 mas o arquivo `.docx` não foi gerado.
16. `test_convert_legacy_to_docx_cleans_stale_target_file`: Limpeza preventiva de arquivo antigo no diretório de destino.
17. `test_extract_legacy_blocks_ephemeral_staging`: Extração ponta a ponta com staging efêmero e sua respectiva remoção.
18. `test_extract_legacy_blocks_custom_staging_preserved`: Preservação do diretório de staging quando explicitamente fornecido pelo chamador.
19. `test_extract_legacy_blocks_cleans_ephemeral_on_docx_parser_exception`: Limpeza do staging efêmero mesmo se o parser de DOCX levantar exceção.
20. `test_extract_legacy_blocks_missing_file`: Lançamento de `FileNotFoundError` para arquivo legado inexistente.
21. `test_extract_legacy_blocks_real_docx_integration`: Teste de integração real gerando pacote OOXML DOCX minimalista e extraindo `DocumentBlock`s com classificação semântica.

---

## 4. Ciclo TDD e Verificação de Regressão

1. **Fase Vermelha (Red)**:
   - Execução: `uv run pytest tests/test_legacy_parser.py`
   - Resultado: **FALHA** (`ModuleNotFoundError: No module named 'app.ingestion.legacy_parser'`).

2. **Fase Verde (Green)**:
   - Execução: `uv run pytest tests/test_legacy_parser.py`
   - Resultado: **21 passed in 0.27s**.

3. **Regressão Global**:
   - Execução: `uv run pytest`
   - Resultado: **201 passed in 13.57s** (180 testes pré-existentes mantidos com 100% de sucesso + 21 novos testes do conversor de formatos legados; zero regressões em todo o projeto).
