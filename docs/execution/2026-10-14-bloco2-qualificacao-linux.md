# Bloco 2 — qualificação Linux sintética (VPS Hostinger)

## Decisão e escopo

Usuário redirecionou o alvo de EC2/AWS para a **VPS Hostinger srv1532181**, acessada só via Tailscale (porta 22 pública filtrada). Escopo executado em 14/10/2026: qualificação Linux **sintética** do worktree atual (base `607f35a5` + acumulado Bloco 1), sem corpus real, sem `.env`, sem chamada a provedores LLM, sem sudo e sem serviço publicado. D04, rede/segredos de produção, restore e G9 **não** foram executados. Sem parecer de juiz nesta etapa: é evidência de ambiente, não aceite de entrega.

Alvo: Ubuntu 25.10, kernel 6.17, glibc 2.42, 4 vCPU AMD EPYC 9354P, 15 GiB RAM (~8,8 GiB disponíveis com co-tenants), **sem swap**, 109 GB livres. Co-tenants ativos: OpenClaw/hermes, bots, containers Docker, worker claude-mem.

## Transporte e ambiente isolados

- Pacote: 206 arquivos do worktree (`git ls-files -co --exclude-standard`) + 9 do artefato Qwen. Excluído `m-files/M-Files.postman_collection.json` (contém `token` literal de 342 caracteres — ver bloqueios). Manifesto `transfer-manifest.sha256` (215 entradas) `2e7ddad3…`; `sha256sum -c` na VPS: **215/215 OK**, tars idênticos (`bce1c67e…`, `962cbf4b…`).
- Tudo em `~/rag-juridico-qual` (modo 700): uv 0.11.6 (instalador `02f6fdf8…`), CPython 3.13.13, cache uv próprio. `uv sync --frozen --extra qwen` em 19,6 s com `uv.lock` `101a5ff7…` (= handoff). Versões idênticas ao Windows: torch 2.8.0+cpu, transformers 4.57.6, chromadb 1.5.9, fastapi 0.141.1.
- Identidades Bloco 1 C2 conferidas na VPS: `generations.py` `9577f2266410…`, `test_generation_finalization.py` `b227b8296c11…`, `test_p8_load.py` `7d2d59b74fc5…`.

## Regressão offline

1. **Integral em processo único — interrompida deliberadamente** em 1.567/1.805 testes, **zero falhas/erros até ali**: RSS do pytest chegou a **9,29 GB** e a memória disponível do host a **805 MB**; sem swap, o OOM killer poderia atingir co-tenants. Causa: cada módulo com fixture Qwen `scope="module"` (~3,9 GB) e o RSS não retorna ao SO entre módulos. No Windows (baseline 1.805 verdes) isso não apareceu por haver mais memória livre.
2. **Reexecução por arquivo** (66 processos sequenciais) dentro de `systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0`: **1.794 passaram, 11 pulados, 0 falhas, 0 erros, 1 online deselecionado**, todos `rc=0`; soma 838 s; maior RSS por processo **3,86 GiB**; mínimo disponível do host 5,8 GiB. 1.794 + 11 = 1.805 = baseline.
   - 10 pulados: gates Windows-only de `test_generation_finalization.py` (HANDLE/lease nativos). 1 pulado: `test_reference_sample` sem `data/curation/curation.jsonl` (dado real não transferido, por desenho).
   - Inclui P8 E2E (47), carga (10, deadline relativo 2×máx. de 3 misses), safety (9), Chroma servidor loopback real e Qwen real.

Limitação: a execução por arquivo não reproduz interações entre módulos de um processo único; para o integral no host é preciso swap/RAM livre ≥10 GB ou fixture Qwen `scope="session"` (alteração de teste, exige nova identidade).

## WinError 5 em Linux

Probe POSIX: `os.rename` de diretório com arquivo descendente aberto e dir-fd aberto **funciona**. O mecanismo de contenda do Windows não existe no Linux; o caminho Linux de `_finalize_rename` não faz retry e propaga qualquer `OSError` (fail-closed), coberto pelos 22 testes não-Windows do módulo e pelos 52 de `test_p4_generations.py`. O processo causador do incidente histórico no Windows continua desconhecido e irrelevante para o alvo Linux.

## Encoder Qwen no alvo

`synthetic_benchmark` (6 docs/6 consultas sintéticas, 18 amostras, ranks esperados 18/18), isolado em scope `MemoryMax=6G`, uma execução por perfil:

| Perfil | Fingerprint | Carga | p50 | p95 | Probe 1.022 tok | Pico RSS |
|---|---|---|---|---|---|---|
| `cpu_threads=6` (default governado) | `a1603b2ba5bb…` (= P3A) | 6,32 s | 1,155 s | **1,921 s** | 12,35 s | 3,67 GiB |
| `cpu_threads=4` (diagnóstico) | `d96eb0b17086…` | 5,38 s | 0,536 s | **0,806 s** | 10,34 s | 3,67 GiB |

Default de 6 threads em 4 vCPU causa oversubscription. Com 4 threads o encoder isolado fica abaixo de 1 s nestas amostras, o que **não** é SLO: falta recuperação completa, API, concorrência e carga sustentada. Trocar para 4 threads é configuração (`EMBEDDING_CPU_THREADS=4`), sem mudança de código, mas muda o fingerprint do encoder: gerações devem ser construídas com o perfil final (não há índice real ainda).

## Bloqueios e decisões para produção neste host

| # | Item | Risco | Decisão necessária |
|---|---|---|---|
| 1 | Ubuntu 25.10 sem suporte desde 09/07/2026 | Sem patches de segurança | Upgrade para 26.04 LTS (afeta todos os serviços) ou VPS nova |
| 2 | Co-tenancy: agentes com chaves de LLM e Claude Code sem permissões; `claw` no grupo `docker` (equivale a root) | Contratos reais legíveis por qualquer processo do host | Usuário Linux dedicado + permissões 700 (exige sudo), ou VPS dedicada |
| 3 | 15 GiB, sem swap, ~8,8 GiB livres; stack ≈ 3,9 GiB/processo + Chroma | OOM afeta co-tenants | Swap e `MemoryMax` no serviço; um processo encoder |
| 4 | `embedding_cpu_threads=6` > 4 vCPU | p95 1,92 s | Fixar 4 threads no host antes de construir gerações |
| 5 | D04: journal/ledger no mesmo disco | Sem domínio de falha independente, sem restore | Escolher armazenamento externo durável e testar restore |
| 6 | Segredo versionado `m-files/M-Files.postman_collection.json` (commit `1daa036`, já no GitHub) | Token M-Files exposto | Rotacionar token; decidir reescrita de histórico |
| 7 | PAT GitHub embutido em `.git/config` de projeto antigo na VPS; credenciais em texto puro no OneDrive | Vazamento | Rotacionar e remover |
| 8 | Aprovação do corpus sem registro por item | Ledger não pode ser preenchido | Preencher `overrides.csv`/ledger com revisor e versão |
| 9 | `provider_policy_approved=False` | Consulta real bloqueada | Política de dados do provedor LLM (D01) |

Facilitadores verificados: `Linger=yes` (serviço `systemd --user` persistente sem sudo); porta 37777 (worker claude-mem, bind 0.0.0.0) e 8443 filtradas no IP público.

## Evidências

Locais, ignoradas pelo Git, em `data/evaluation/bloco2-linux-20261014/`:

| Arquivo | SHA-256 |
|---|---|
| shard-summary.txt | `ec8492e2cd6838a81e8edf8835c4ab5ea918efe2873132736ef35ffb9cdb052d` |
| shard-mem.log | `ca30d40b1f8e5454f2bf7a99b9f72f73305e9449a167e8bdfefe34ab3928064a` |
| integral-interrompida-pytest.log | `bd8baf6189ffa3447b21298cbe3d00ad93990186901fc50fe3f11f5c0911b86e` |
| integral-interrompida-time.txt | `37bbf5baccde01f63c03346b7a1a8780fbeb5799154552219f93e14340c36f82` |
| integral-interrompida-mem.log | `9e6723ff6e52b9ef5495fc34523d4d828cfed63a137b47a7d43907cf9bccac1c` |
| benchmark-threads6.json | `cecce1e9583b20b3bf8b6836fe2faa494b5a2fe4fa970fccb7db4e0564a2d640` |
| benchmark-threads4-diagnostic.json | `153f42984c08f56f91803b42a6f6aff4a3c545c83dc1ebf00f16fdfc59281a66` |
| transfer-manifest.sha256 | `2e7ddad36f6207492345e788f738f8c04d74408f93acc80c2d40e2f47f0edae8` |

Na VPS ficam `~/rag-juridico-qual/{src,runs}` para repetição; nenhum serviço foi deixado em execução.
