# Mapa do código — atlas-graph-economic-group

> Também conhecida como: **Graph fraud ring (grupo econômico)** (pasta antiga `atlas-graph-fraud-ring`). Portas: frontend **5350** / backend **8350**. Em uma frase: dado um CNPJ, o MongoDB sobe e desce a cadeia societária com `$graphLookup` e devolve o grupo econômico inteiro com a exposição de crédito consolidada, e em volta disso mostra busca por nome (Atlas Search), concentração por significado (Vector Search), escopo por hierarquia comercial, transação ACID de revisão de crédito e alertas por change stream.

Atalhos: [architecture](briefing/architecture.md) · [queries](briefing/queries.md) · [ui-flows](briefing/ui-flows.md). Este mapa só aponta arquivo e linha.

Regra do backend: `backend/main.py` só expõe HTTP; as consultas moram em `backend/app/db/`, os processos em segundo plano em `backend/app/services/`.

## 1. Árvore anotada

```
.
├── start.sh                    Launcher: backend (8350) + frontend (5350)
├── .env.example                Variáveis locais (nomes apenas)
├── backend/
│   ├── main.py                 FastAPI: TODAS as rotas (429 linhas), handlers de erro Mongo, SSE de alertas
│   └── app/
│       ├── config.py           Lê as variáveis de ambiente (índices, limites do grafo, porta, chave de assinatura)
│       ├── db/
│       │   ├── client.py       MongoClient único, `bounded_aggregate` (aplica o teto de tempo), `with_retry`
│       │   ├── ownership.py    O CORAÇÃO: pipeline do grupo econômico com `$graphLookup` (sobe, desce, hidrata)
│       │   ├── hierarchy.py    Hierarquia comercial: equipe do assessor e carteira, `can_see` (`$graphLookup` em `advisors`)
│       │   ├── search.py       Atlas Search: razão social e sócios (`$search` composto)
│       │   ├── concentration.py  Vector Search: o grupo é tão diversificado quanto parece?
│       │   └── credit_decision.py  Transação ACID: abrir/encerrar revisão de crédito do grupo; reset da demo
│       └── services/
│           ├── credit_demo.py  Pontos de entrada (solicitantes preparados) com cache
│           ├── investigation.py  Token assinado que amarra a revisão ao grupo que o analista viu
│           ├── alerts.py       `AlertHub`: change stream em `companies` -> SSE + gravação em `ownership_alerts`
│           └── limits.py       Limite de concorrência por classe de operação
├── frontend/
│   ├── vite.config.js          Porta 5350 + proxy de `/api` e `/health` para 8350
│   ├── public/evidence/results.json   Resultado de validação servido em `/evidence`
│   └── src/
│       ├── main.jsx            Escolhe a tela: `/evidence` abre `ValidationPage`, qualquer outro caminho abre `App`
│       ├── App.jsx             A tela única (1.119 linhas): estado, abas, botões, chamadas
│       ├── GraphCanvas.jsx     Canvas do grafo (vis-network): layout, hover, hop sweep, arrasto
│       ├── api.js              Cliente HTTP (todas as chamadas ao backend)
│       ├── EvidencePanel.jsx / evidence.js / evidenceReport.js   Aba "Resumo" e caminho de evidência
│       ├── ValidationPage.jsx / validation.css   Página `/evidence` (curva de latência por profundidade, lê o JSON estático)
│       ├── QueryDetails.jsx    Gaveta "query executada"
│       └── pov-signature.css   Visual comum do portfólio (cópia)
├── data-generator/             Gera o dado sintético (empresas, sócios, exposição, assessores, embeddings)
│   ├── generate_ownership.py   Empresas, arestas `ownership`, `credit_exposure`, `economic_groups`
│   ├── generate_people.py      Pessoas físicas (sócios)
│   ├── generate_advisors.py    Árvore de assessores e carteira
│   ├── embed_activities.py     Embeddings das atividades (chama Voyage; roda offline, fora do backend)
│   ├── profiles.py, common.py  Perfis de dado e utilitários
│   └── run_all.sh              Atalho histórico
├── schema/                     `indexes.js` (índices comuns), `search_indexes.py` (Search e Vector), `collections.md`
├── scripts/reset_demo.py       RESET ÚNICO do dado: gera, indexa, verifica (`--drop` para carga limpa)
├── queries/                    `bench.py`, `build_evidence.py` + JSON de resultados e `benchmarks.md`
├── tests/                      Testes e scripts de hostilidade/carga (ver seção 6)
└── docs/                       briefing/, adr/ (0001 topologia, 0002 vetores 512d, 0003 arestas), roteiro, protocolos
```

## 2. "Apertei o botão X, o que roda?"

A tela é uma só (`frontend/src/App.jsx`). Toda chamada passa por `frontend/src/api.js`. Ao abrir a página rodam os efeitos automáticos listados primeiro.

| Tela / ação | Componente (arquivo:linha) | Chamada HTTP | Handler | Função de negócio | MongoDB |
|---|---|---|---|---|---|
| Abrir a página: selo de saúde | `App.jsx:60` | `GET /health` | `main.py:133` (`health`) | `health` | `ping`; `estimated_document_count` em `companies`, `ownership`, `credit_exposure`, `people` |
| Abrir a página: lista de solicitantes | `App.jsx:61` | `GET /api/entry-points` | `main.py:202` (`entry_points`) | `services/credit_demo.py::entry_points` (`:29`, `_compute_entry_points` `:46`) | `economic_groups`: `find` (`:67`); `companies`: `find_one` (`:78`) e aggregate com `$lookup` (`:118`); `credit_exposure`: `find_one` (`:83`) |
| Abrir a página: perfis de usuário | `App.jsx:66` | `GET /api/hierarchy/roster` | `main.py:265` (`hierarchy_roster`) | `hierarchy_roster` | `economic_groups`: `find_one`; `advisors`: `find`/`find_one` |
| Selecionar empresa em **Solicitante de crédito** | `App.jsx:396` (`select`), efeito `App.jsx:192` -> `carrega` (`App.jsx:173`) | `GET /api/group/{cnpj}?depth=N` | `main.py:208` (`economic_group`) | `app/db/ownership.py::economic_group` (`:308`) monta `_pipeline_grupo` (`:121`); depois `investigation.issue` (`services/investigation.py:37`) | `companies`: aggregate (`ownership.py:317`) com `$graphLookup` para cima (`:154`) e para baixo (`:215`), `$lookup` de fronteira (`:111`) e hidratação (`:257-291`) |
| Botões de **profundidade** (níveis) | `App.jsx:424` | mesma `GET /api/group/{cnpj}?depth=N` (efeito refaz a consulta) | `main.py:208` | idem; `clamp_depth` (`ownership.py:54`) limita ao teto | idem |
| **Atualizar consulta** | `App.jsx:450` (`carrega`) | `GET /api/group/{cnpj}` | `main.py:208` | idem | idem |
| Aba **Concentração** (automática ao mudar o grupo) | `App.jsx:345` (efeito) -> `api.concentration` | `POST /api/analysis/concentration` | `main.py:335` (`group_concentration`) | `app/db/concentration.py::group_concentration` (`:24`) | `companies`: aggregate com `$lookup` (`:35-40`); `activities`: `find_one` do vetor (`:93`), aggregate com `$vectorSearch` exato (`:108`, `:114`) |
| Aba **Busca** (formulário) | `App.jsx:327` (`doSearch`), form `App.jsx:557` | `POST /api/search/companies` | `main.py:318` (`search_companies`) | `app/db/search.py::resolve_company` (`:142`); sócios por `_busca_socios` (`:65`) | `companies`: aggregate com `$search` composto (`:190`, `:231`); `people`: `$search` (`:92`, `:113`) + `$lookup` em `ownership` (`:105`) |
| Aba **Visibilidade**: trocar usuário | `App.jsx:733` (`setVis`), efeito `App.jsx:87` | `GET /api/hierarchy/{advisor_id}/portfolio` | `main.py:232` (`advisor_portfolio`) | `app/db/hierarchy.py::portfolio` (`:78`); equipe por `_subordinados_pipeline` (`:37`) | `advisors`: aggregate com `$graphLookup` (`:40`) e `$lookup` (`:99`, `:126`; aggregate `:139`) |
| Aba **Visibilidade**: o usuário vê esta empresa? | `App.jsx:100` (efeito) | `GET /api/hierarchy/{advisor_id}/can-see/{cnpj}` | `main.py:253` (`advisor_can_see`) | `hierarchy.py::can_see` (`:187`) | `companies`: aggregate com `$lookup` + `$graphLookup` (`:201-209`) |
| Aba **Alertas** (tempo real) | `App.jsx:114` (`EventSource('/api/alerts/stream')`) | `GET /api/alerts/stream` (SSE) | `main.py:401` (`alerts_stream`) | `AlertHub.subscribe` (`services/alerts.py:96`), `sse_stream_async` (`:301`) | change stream `companies.watch` (`alerts.py:158`) |
| Lista de alertas recentes | (rota existe; a tela usa o SSE) | `GET /api/alerts/recent` | `main.py:427` (`alerts_recent`) | `alerts_recent` | `ownership_alerts`: `find` ordenado por `created_at` |
| **Abrir revisão sobre N empresas** | `App.jsx:442` -> `doReview` (`App.jsx:249`, `api.openReview`) | `POST /api/credit/review` | `main.py:351` (`open_review`) | `credit_decision.py::open_review` (`:127`); verifica o token (`investigation.verify`, `services/investigation.py:48`) | transação `with_transaction` (`credit_decision.py:186`, read concern snapshot): `credit_decisions.find_one`/`insert_one` (`:141`,`:175`), `companies.update_many` (`:163`), `credit_exposure.update_many` (`:169`); guarda em `demo_control` (`:88`,`:95`) |
| Detalhe do caso (após abrir) | `App.jsx:261`, `:265` (`api.caseDetail`) | `GET /api/credit/case/{case_id}` | `main.py:371` (`case`) | `credit_decision.py::case_detail` (`:235`) | `credit_decisions.find_one`, `companies.count_documents` + `find` |
| **Encerrar revisão** | `App.jsx:454` -> `doClose` (`App.jsx:286`) | `POST /api/credit/close/{case_id}` | `main.py:379` (`close`) | `credit_decision.py::close_review` (`:194`) | transação: `companies.update_many` (`:209`), `credit_exposure.update_many` (`:214`), `credit_decisions.update_one` (`:217`) |
| **Reiniciar demo** | `App.jsx:457` -> `doReset` (`App.jsx:306`) | `POST /api/demo/reset` | `main.py:390` (`reset`) | `credit_decision.py::reset_all` (`:258`, trava `_acquire_reset_lease` `:102`); depois `credit_demo.invalidate_cache` | `companies`/`credit_exposure`: `update_many`; `credit_decisions`, `ownership_alerts`: `delete_many`; `ownership`: `delete_many({simulated:true})` (`:272`) |
| Abas **Resumo** e **Empresa**, hover/clique no grafo | `App.jsx:531`, `:532`; `GraphCanvas.jsx:105` | nenhuma (usam o grupo já carregado) | - | `EvidencePanel.jsx`, `NodePanel` (`App.jsx:958`), `evidencePath` (`evidence.js`) | - |
| Página `/evidence` | `main.jsx:10` -> `ValidationPage.jsx:41` | `GET /evidence/results.json` (arquivo estático, não é o backend) | - | `validEvidenceReport` (`evidenceReport.js`) | - |

## 3. Onde estão as queries MongoDB

Texto completo e explicação: [queries.md](briefing/queries.md). Não há `$regex`; busca textual é `$search`.

| O que faz | arquivo::função (linha) | Operador / pipeline principal | Collection(s) |
|---|---|---|---|
| Grupo econômico (principal) | `db/ownership.py::_pipeline_grupo` (`:121`), `_graph_lookup` (`:71`), `economic_group` (`:308`, aggregate `:317`) | `$match` por `cnpj` + `$graphLookup` "up" (`:154`) + `$lookup` correlacionado com `$graphLookup` "down" (`:210-215`) + 3 `$lookup` de hidratação (`:257-291`) | `companies`, `ownership`, `credit_exposure`, `people` |
| Checagem de fronteira de profundidade | `ownership.py::_boundary_check` (`:103`) | `$lookup` que testa se há nível além do limite | `ownership` |
| Busca de empresa por nome | `db/search.py::resolve_company` (`:142`; `_busca` `:185`, `$search` `:190`) | `$search` `compound` (índice `ATLAS_SEARCH_INDEX_NAME`) + `$lookup` (`:198`) | `companies` |
| Busca de sócio por nome | `db/search.py::_busca_socios` (`:65`, `$search` `:92`, `$lookup` `:105`) | `$search` `compound` em `people` + `$lookup` em `ownership` | `people`, `ownership` |
| Status dos índices de Search | `db/search.py::index_status` (`:36`) | `list_search_indexes` | `companies`, `people`, `activities` |
| Concentração por atividade | `db/concentration.py::group_concentration` (`:24`; aggregate `:35`) | `$lookup` agregando atividades do grupo | `companies`, `activities` |
| Atividades equivalentes por significado | `concentration.py` (`find_one` `:93`; pipeline `:108`; aggregate `:114`; reuso `:151`) | `$vectorSearch` `exact: true` (consulta = embedding já gravado, sem chamada ao Voyage) + `$meta vectorSearchScore` | `activities` |
| Equipe do assessor | `db/hierarchy.py::_subordinados_pipeline` (`:37`), `team` (`:61`) | `$graphLookup` em `advisors` por `reports_to` | `advisors` |
| Carteira do assessor | `hierarchy.py::portfolio` (`:78`, aggregate `:139`) | `$graphLookup` + `$lookup` (`:99`, `:126`) | `advisors`, `companies`, `credit_exposure` |
| Pode ver esta empresa? | `hierarchy.py::can_see` (`:187`, `:201-209`) | `$lookup` com `$graphLookup` | `companies`, `advisors` |
| Perfis de exemplo | `main.py::hierarchy_roster` (`:265`) | `find_one`, `find` | `economic_groups`, `advisors` |
| Pontos de entrada | `services/credit_demo.py::_compute_entry_points` (`:46`; `:67`, `:78`, `:83`, `:118`) | `find`, `find_one`, aggregate com `$lookup` | `economic_groups`, `companies`, `credit_exposure` |
| Abrir revisão (ACID) | `db/credit_decision.py::open_review` (`:127`, `with_transaction` `:186`) | transação multi-coleção, read concern `snapshot` | `credit_decisions`, `companies`, `credit_exposure`, `demo_control` |
| Encerrar revisão (ACID) | `credit_decision.py::close_review` (`:194`) | transação | idem |
| Detalhe do caso | `credit_decision.py::case_detail` (`:235`) | `find_one`, `count_documents`, `find` | `credit_decisions`, `companies` |
| Reset da demo | `credit_decision.py::reset_all` (`:258`), lease em `:83-125` | `update_many`, `delete_many`, `find_one_and_update` | várias + `demo_control` |
| Alertas em tempo real | `services/alerts.py::AlertHub._run` (`:129`, `watch` `:158`) | `watch` com `$match` em `updateDescription` (muda `credit_status` ou remove `case_id`), `updateLookup`, resume token | `companies` |
| Gravar alerta | `alerts.py::_monta` (`:242`, `find_one` `:264`; `replace_one` `:290`) | `find_one` + `replace_one` | `credit_decisions`, `ownership_alerts` |
| Saúde | `main.py::health` (`:133`) | `ping`, `estimated_document_count` | 4 coleções |
| Índices comuns / Search / Vector | `schema/indexes.js` (`createIndex` `:57`), `schema/search_indexes.py::main` (`:108`, `create_search_index` `:135`) | índices B-tree, Search e Vector | `companies`, `ownership`, `people`, `activities` |
| Carga de dados | `data-generator/generate_ownership.py::main` (`:497`), `generate_people.py` (`:58`), `generate_advisors.py` (`:172`) | `insert_many` (carga limpa) ou upsert | todas as coleções do domínio |
| Embeddings | `data-generator/embed_activities.py` (`embed` `:51`, `main` `:66`) | chamada HTTP ao Voyage + gravação do vetor | `activities` |
| Reset | `scripts/reset_demo.py` (`reset_review_state` `:76`, `verify` `:92`, `main` `:131`) | orquestra geradores e índices | todas |
| Medição / evidência | `queries/bench.py`, `queries/build_evidence.py::audit` (`:107`), `curve` (`:168`) | `$graphLookup` com `explain`, reconstrução independente do grupo | `companies`, `ownership`, `credit_exposure` |

## 4. Compartilhado × só desta PoV

**`_shared`:** nenhum módulo é importado (sem `import` de `_shared`/`grove_client` no código). Só o visual: `frontend/src/pov-signature.css` é cópia da assinatura do portfólio (fonte em `_shared/ui`).

**Variáveis de ambiente** (`.env.example`, só nomes; lidas em `backend/app/config.py`)
- Comuns (vêm do `.env.shared` via `_shared/sync_env.py`, que lista ambas): `MONGODB_URI`, `VOYAGE_API_KEY`.
- Locais: `MONGODB_DB` (padrão `graph_grupo_economico`), `ATLAS_SEARCH_INDEX_NAME`, `PEOPLE_SEARCH_INDEX_NAME`, `VECTOR_INDEX_NAME`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, `GRAPH_MAX_DEPTH_CAP`, `GRAPH_DEFAULT_DEPTH`, `HUB_FANOUT_THRESHOLD`, `GRAPH_MAX_NODES`, `GRAPH_MAX_TIME_MS`, `MONGODB_OPERATION_TIMEOUT_MS`, `INVESTIGATION_SIGNING_KEY` (assina o token de investigação), `ANALYST_HOURS_PER_CASE`, `ANALYST_COST_PER_HOUR`, `CURRENCY`, `BACKEND_PORT`, `FRONTEND_PORT`, `LOG_LEVEL`.
- Portas: 8350 (`config.py`, `start.sh`) e 5350 (`vite.config.js`, CORS em `main.py:43`).

**Bancos/coleções:** banco próprio `graph_grupo_economico`, exclusivo: `companies`, `ownership`, `credit_exposure`, `people`, `advisors`, `activities`, `economic_groups`, `credit_decisions`, `ownership_alerts`, `demo_control`. Nada compartilhado com outras PoVs. Reset e testes vivos usam bancos `_test`/descartáveis (ver seção 6).

**Chamadas externas:** o **backend em execução não chama LLM nem Voyage**: a busca vetorial usa o embedding já gravado em `activities`. O único cliente externo é `data-generator/embed_activities.py:55` (embeddings via Voyage, rodado offline na carga). Sem Langfuse. Não há `grove_client` aqui.

## 5. "Quero mexer em..." (atalhos)

- A query principal do grafo (profundidade, ramificação, tetos) -> `backend/app/db/ownership.py::_pipeline_grupo` (`:121`) e `_graph_lookup` (`:71`); tetos em `GRAPH_*`/`HUB_FANOUT_THRESHOLD` no `.env`.
- Texto de uma tela, rótulo de botão ou das abas -> `frontend/src/App.jsx` (abas em `TABS`, `:8`; botões `:436-460`).
- Aparência do grafo (cores, tamanho, borda) -> `frontend/src/GraphCanvas.jsx` (`nodeStyle` `:43`, `edgeStyle` `:21`).
- Adicionar uma rota -> `backend/main.py` + consulta em `backend/app/db/`; expor em `frontend/src/api.js`.
- Regra de busca por nome -> `backend/app/db/search.py` (`_clausulas` `:53`); definição do índice em `schema/search_indexes.py`.
- Análise de concentração -> `backend/app/db/concentration.py`.
- Regra de visibilidade por hierarquia -> `backend/app/db/hierarchy.py` (`can_see` `:187`, `portfolio` `:78`).
- O que a transação de revisão altera -> `backend/app/db/credit_decision.py::open_review` (`:127`).
- Condição que dispara alerta -> filtro `$match` do watch em `backend/app/services/alerts.py:141-150`.
- Solicitantes que aparecem no seletor -> `backend/app/services/credit_demo.py::_compute_entry_points` (`:46`) e a carga `economic_groups` em `data-generator/generate_ownership.py`.
- Tamanho/forma do dado sintético -> `data-generator/profiles.py`, `generate_ownership.py` e `scripts/reset_demo.py`.
- Mudar porta -> `start.sh`, `frontend/vite.config.js`, `BACKEND_PORT`/`FRONTEND_PORT`.
- Página de evidência -> `frontend/public/evidence/results.json` (regerado por `queries/build_evidence.py`) e `frontend/src/ValidationPage.jsx`.

## 6. Testes

Comandos (README, linhas 125-132 e 201-204): `backend/venv/bin/python -m unittest discover -s tests -p 'test_*.py'` (offline, sem Atlas); `node --test frontend/tests/*.test.mjs`; `backend/venv/bin/python tests/test_hardening.py`; `npm --prefix frontend run build`. Os scripts "vivos" abaixo exigem cluster (e alguns `./start.sh` no ar) e usam banco próprio descartável.

| Arquivo | O que cobre |
|---|---|
| `tests/test_hardening.py` | Endurecimento do backend (a maior suíte offline) |
| `tests/test_inputs_adversarial.py` | Entradas hostis nas rotas |
| `tests/test_evidence_measurement.py` | Cálculo da evidência/medição |
| `tests/test_client_concurrency.py` | Cliente Mongo único sob concorrência |
| `tests/test_resilience.py`, `tests/http_adversarial.py` | Resiliência e hostilidade via HTTP (precisam de `./start.sh`; somente leitura) |
| `tests/live_hardening.py`, `live_graph_adversarial.py`, `live_reset_race.py` | Contra Atlas, cada um no próprio banco descartável |
| `tests/stress.py` | Carga concorrente |
| `tests/browser-*.cjs` | Verificações de navegador (evidência, grafo grande, offline, resiliência) |
| `frontend/tests/evidence.test.mjs`, `evidence-report.test.mjs`, `http-deadline.test.mjs` | Caminho de evidência, validação do relatório, prazo HTTP |
