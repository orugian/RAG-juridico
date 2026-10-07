# P1 — autenticação e telemetria

G0 foi liberado pelo juiz com 9,14/10 na terceira rodada. Esta entrega corrige fronteiras de segurança existentes; a API e o journal de revogações ainda pertencem a P4/P7.

## Comportamento

- Sem credencial de consulta configurada, autenticação retorna indisponibilidade; chave ausente/inválida retorna 401 quando a configuração é válida. Bypass exige opção explícita no ambiente development e produz principal sintético; produção não aceita esse perfil.
- `validate_auth_configuration` valida chaves distintas com 32–256 caracteres ASCII, rotação com prazo timezone-aware e ausência de fallback. Deve integrar startup/readiness da API em P7; hoje também é executada em toda autenticação. Chaves precisam ser geradas/distribuídas por processo seguro, não basta satisfazer comprimento.
- Principal é técnico, derivado da credencial, sem atribuição humana. Operador tem permissão adicional e chave distinta; uma chave de consulta recebe 403 em operações. Chave anterior só consulta até prazo explícito. Revogação/journal/policy_epoch vigente serão conectados em P4; o epoch atual é contrato inicial 0, não uma revogação operacional implementada.
- `SafeLangSmithCallbackHandler` conserva uma projeção limitada a 1024 runs, sem modificar inputs/outputs/prompts/LLMResult. Campos desconhecidos, nomes dinâmicos, tags, exceções e serialized não atravessam a allowlist. Desativar flags do callback não libera conteúdo cru.
- `ProtectedRunClient` é fachada de create/update; não expõe batch, attachments ou APIs genéricas do SDK. Faz projeção antes do SDK, configura hide_inputs/hide_outputs e omite runtime. O teste captura o corpo real POST/PATCH com adapter simulado, incluindo runs pai/filho, metadata, serialized, erros, tags e attachments maliciosos. Nenhuma chamada real foi feita.
- Decoradores próprios desativam tracing nativo dentro dos caminhos instrumentados; telemetria externa, quando configurada explicitamente, exporta somente a projeção. Falhas de criação/escrita/fechamento do exportador não alteram o processamento. O retriever fecha essa fronteira antes de criar callbacks LangChain; métricas async e agendamento são qualificados em P5.
- Logs possuem eventos fixos, UUID de correlação e métricas tipadas. Não incluem perguntas, partes, thread_id, nomes de arquivo ou texto de exceção. O helper legado de regex preserva compatibilidade diagnóstica e não é o exportador seguro; também suporta CNPJ alfanumérico.

## Evidências TDD e compatibilidade

Red inicial: 10 falhas de autenticação/projeção/exportador; depois 2 falhas de rotação/comparação segura. HTTP capturado revelou que o SDK remontava adapters na criação; o teste injeta o transporte no adapter final. Novo Red impediu expor batch do SDK; outro Red verificou CNPJ alfanumérico no helper legado.

Os testes antigos que exigiam mutação de prova e registro de texto sensível foram migrados para verificar preservação e projeção separada; não se manteve esse comportamento inseguro. Os testes de métricas numéricas/concor­rência continuaram compatíveis. Red de compatibilidade: 7 falhas/17 sucessos; após migração e refatoração, Green focal e regressão passaram.

`verify_api_key` agora retorna AccessContext; não existia API consumidora. O grafo legado não devolve mais texto bruto de exceção de fornecedor. Suporte factual, recuperação, resposta extrativa e contrato HTTP do serviço ainda serão integrados nos respectivos gates.

Tracing segue desativado por padrão. OpenRouter não recebeu perguntas ou provas e a política de dados permanece pendente. Nenhum segredo real foi usado em testes.

## Correção adversarial P1-J1

A primeira rodada rejeitou P1 com 8,65: callbacks genéricos podiam contornar a projeção. A configuração agora é inspecionada antes dos eventos de fonte: callbacks fornecidos, herdados, persistidos em componentes compostos, hooks ativos e debug/verbose stdout. Somente a classe exata SafeCallbackHandler é aceita; fábrica dinâmica de configuração e observador desconhecido produzem erro controlado anterior ao processamento. Principal e fallback são inspecionados antes de entrar no grafo. Configurações/componentes são preservados; código Python arbitrário não é sandboxed. Oito testes Red reproduziram a falha; testes Green e nova decisão constam no registro de revisão.
