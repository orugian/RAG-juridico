# Task 4 Brief: Motor de Conversão de Formatos Legados (LibreOffice com Perfil Isolado)

## Goal
Implementar o motor de conversão de formatos legados (`.doc` binário OLE, `.rtf` e WordPerfect) em `app/ingestion/legacy_parser.py` e testes em `tests/test_legacy_parser.py`. A conversão deve usar LibreOffice em modo headless com perfil de usuário efêmero e isolado (`-env:UserInstallation`), prevenindo deadlocks e interferência concorrente, convertendo para `.docx` em diretório de staging para extração canônica via `docx_parser`.

## Files to Create
- Create: `app/ingestion/legacy_parser.py`
- Create: `tests/test_legacy_parser.py`

## Requirements
1. **Localização do Binário do LibreOffice**:
   - `find_libreoffice_binary() -> Optional[str]`: busca no `PATH` (`soffice`, `libreoffice`), variáveis de ambiente (`LIBREOFFICE_PATH`, `SOFFICE_PATH`) e caminhos padrão do Windows (`C:\Program Files\LibreOffice\program\soffice.exe`, `C:\Program Files (x86)\LibreOffice\program\soffice.exe`).
2. **Conversão Segura com Perfil Isolado**:
   `convert_legacy_to_docx(input_path: Path | str, output_dir: Path | str, timeout_sec: int = 30, libreoffice_cmd: Optional[str] = None) -> Path`
   - Valida existência do arquivo de entrada.
   - Cria diretório temporário efêmero para perfil do usuário (`tempfile.mkdtemp(prefix="soffice_profile_")`).
   - Converte para URI de arquivo no formato exigido pelo LibreOffice (`-env:UserInstallation=file:///{profile_path}`).
   - Executa subprocesso com flags obrigatórias de segurança:
     - `--headless`
     - `--invisible`
     - `--nocrashreport`
     - `--nodefault`
     - `--nofirststartwizard`
     - `--nologo`
     - `--norestore`
     - `-env:UserInstallation=...`
     - `--convert-to docx`
     - `--outdir <output_dir>`
     - `<input_path>`
   - Limpeza mandatória: `shutil.rmtree(profile_dir, ignore_errors=True)` em bloco `finally:`.
   - Tratamento de `subprocess.TimeoutExpired` e erro de código de saída com mensagens claras.
   - Retorna o caminho `Path` do arquivo `.docx` gerado.
3. **Pipeline Completo de Extração de Legados**:
   `extract_legacy_blocks(legacy_path: Path | str, doc_id: int, doc_version: int = 1, staging_dir: Optional[Path | str] = None, timeout_sec: int = 30) -> List[DocumentBlock]`
   - Converte o arquivo legado para `.docx` em diretório staging (efêmero ou fornecido).
   - Invoca `extract_docx_blocks(converted_docx, doc_id, doc_version)` de `app.ingestion.docx_parser`.
   - Limpa o arquivo intermediário caso tenha sido criado em diretório efêmero.
   - Retorna os `DocumentBlock`s extraídos.

## Testing & TDD
- Escrever `tests/test_legacy_parser.py` cobrindo:
  - Validação das flags de isolamento e `-env:UserInstallation` via mock de `subprocess.run`.
  - Limpeza do diretório temporário de perfil em bloco `finally` mesmo em caso de erro.
  - Tratamento de timeout (`subprocess.TimeoutExpired`).
  - Tratamento de erro quando o binário do LibreOffice não é encontrado.
  - Fluxo integrado de `extract_legacy_blocks` com mock do conversor e chamada ao `extract_docx_blocks`.
- TDD Red: verificar falha dos testes antes da implementação.
- TDD Green: implementar `app/ingestion/legacy_parser.py` e passar todos os testes.
- Regressão global: `uv run pytest` deve passar 100% verde em todo o repositório.
- Commit: `git add app/ingestion/legacy_parser.py tests/test_legacy_parser.py; git commit -m "feat(ingestion): implementar conversor headless seguro do LibreOffice com perfil isolado"`.
- Gravar relatório em `docs/superpowers/plans/task-4-report.md`.
