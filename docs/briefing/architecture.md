# Arquitetura — atlas-graph-economic-group

PoV de portfólio, dado 100% sintético. Mostra o padrão em que `$graphLookup`
ganha de um banco de grafo dedicado: **árvore rasa, consultada por chave de
negócio** (CNPJ, matrícula de assessor) — não exploração livre de um grafo
denso. O gargalo real de um banco não é o algoritmo de travessia, é carregar o
volume e operar mais um sistema.

Dois cenários, o mesmo mecanismo (`$graphLookup` sobre MongoDB Atlas):

1. **Cadeia societária / grupo econômico para risco de crédito** — dado um
   CNPJ, a árvore societária completa (quem controla, quem é controlado) e a
   exposição de crédito consolidada do grupo.
2. **Hierarquia de visibilidade comercial** (gerente → regional → assessor →
   cliente) — quais contas um usuário pode ver, derivado da árvore em tempo de
   consulta, nunca de uma lista pré-calculada.

Banco: `graph_grupo_economico`. Portas: backend `8350`, frontend `5350`.

## Stack

| Camada | Tecnologia | Papel |
|---|---|---|
| Frontend | React + Vite, `vis-network` | canvas do grafo, SSE, painéis |
| Backend | FastAPI (Python), `pymongo` | HTTP, orquestração, sem lógica de negócio na rota |
| Banco | MongoDB Atlas | `$graphLookup`, Atlas Search, Atlas Vector Search, Change Streams, transação ACID multi-documento |
| Geração de dado | `data-generator/` (Python, `Faker`, seed fixa) | dataset sintético idempotente |

```
┌────────────────────────┐   ┌────────────────────────┐   ┌─────────────────────────┐
│ React + Vite (5350)     │──▶│ FastAPI (8350)          │──▶│ MongoDB Atlas            │
│ vis-network             │◀──│ app/db  · acesso a dado │◀──│ $graphLookup             │
│ EventSource (SSE)       │   │ app/services · orquestra│   │ Atlas Search             │
│ design tokens do        │   │ AlertHub (thread própria)│   │ Atlas Vector Search      │
│ workspace                │   │                         │   │ Change Streams            │
└────────────────────────┘   └────────────────────────┘   │ transação ACID            │
                                                             └─────────────────────────┘
                                                                       ▲
                                                            ┌──────────┴──────────┐
                                                            │ data-generator/      │
                                                            │ idempotente, seed fixa│
                                                            └─────────────────────┘
```

## Componentes do backend

Nenhuma rota (`backend/main.py`) importa `pymongo` diretamente. Toda query
vive em `backend/app/db/`; `main.py` só expõe HTTP e traduz exceção em status
code. Isso é o que permite trocar driver ou versão sem tocar nas rotas.

| Arquivo | Responsabilidade |
|---|---|
| `backend/main.py` | rotas HTTP, modelos `pydantic` de entrada, CORS, lifespan (liga/desliga o change stream) |
| `backend/app/config.py` | toda configuração de ambiente centralizada; nada lê `os.environ` fora daqui |
| `backend/app/db/client.py` | `MongoClient` cacheado (`lru_cache`), `with_retry()` (retry só para falha transitória de rede) |
| `backend/app/db/ownership.py` | cadeia societária / grupo econômico — o caminho principal da demo |
| `backend/app/db/hierarchy.py` | hierarquia comercial e escopo de visibilidade |
| `backend/app/db/search.py` | Atlas Search sobre razão social e nome de sócio (entity resolution) |
| `backend/app/db/concentration.py` | Atlas Vector Search sobre descrição de atividade (concentração semântica) |
| `backend/app/db/credit_decision.py` | transação ACID multi-documento (abrir/fechar revisão de crédito) |
| `backend/app/services/alerts.py` | `AlertHub` — change stream em thread própria, alimenta SSE por gerador assíncrono (não prende thread do pool; teto de 64 assinantes, acima disso 503) |
| `backend/app/services/limits.py` | bulkhead: semáforo por classe de consulta analítica |
| `backend/app/services/credit_demo.py` | pontos de entrada da demo (`economic_groups`, ground truth) |

Detalhe de cada query, pipeline e índice está em [`queries.md`](queries.md).

## Fluxo de dados — caminho principal

1. Presenter escolhe um CNPJ de entrada (`GET /api/entry-points`) e uma
   profundidade no slider.
2. `GET /api/group/{cnpj}?depth=N` chega ao FastAPI; o `pydantic`/`Query`
   valida tipos, mas quem decide o valor final da profundidade é o backend
   (`ownership.clamp_depth`), nunca o valor bruto do cliente.
3. `app/db/ownership.economic_group()` roda **uma agregação** contendo dois
   `$graphLookup` (sobe até os controladores, desce das raízes encontradas) e
   quatro `$lookup` de hidratação (empresas, sócios, assessores, exposição de
   crédito).
4. O backend deduplica arestas, monta `nodes`/`edges` no formato que
   `vis-network` consome, calcula a exposição consolidada do grupo e devolve
   tudo — incluindo a `pipeline` executada, para o painel "query executada" da
   tela.
5. Frontend desenha o grafo (sweep de cor por hop, sem física), preenche o
   inspector, e os demais painéis (Search, Concentração, Visibilidade,
   Alertas) ficam disponíveis como abas ao lado, todos escopados ao grupo em
   tela.

## Decisões de design que atravessam o projeto

Estas onze regras vêm do `01-arquitetura.md` original e do código; cada uma
tem uma consequência prática se for esquecida.

1. **`_id` determinístico em todo dado gerado.** `det_id(kind, *parts)` é um
   `uuid5` sobre os atributos-chave. Rodar o gerador duas vezes reescreve os
   mesmos documentos — nenhuma escrita usa `ObjectId` aleatório. Isso é o que
   torna a carga idempotente e os grupos de vitrine reproduzíveis.
2. **O backend decide a profundidade, não o frontend.** `clamp_depth()` no
   backend limita a `GRAPH_MAX_DEPTH_CAP` (padrão 6), independente do que o
   cliente mandar. Uma expansão sem teto é o jeito mais rápido de travar uma
   demo ao vivo.
3. **Retry só para falha transitória de rede.** `with_retry()` (em
   `app/db/client.py`) repete `AutoReconnect`, `NetworkTimeout` e
   `ConnectionFailure` com backoff exponencial, no máximo 3 vezes. Erro de
   lógica ou de validação nunca é repetido — repetir esconderia bug.
4. **Degradação por feature, não por tela.** Um índice de Search/Vector
   ausente ou `BUILDING` vira `503` com `{feature, index, status}`; o
   frontend mostra um badge só naquele painel, e o traversal continua
   funcionando.
5. **Ground truth rastreável.** Cada grupo de vitrine é registrado em
   `economic_groups` — holding, membros, solicitante, "irmã" inadimplente,
   sócio-ponte e cross-holdings. A demo nunca depende de aleatoriedade ter
   cooperado, e nunca varre a base ao vivo na frente do cliente.
6. **Teto de nós devolvidos.** `GRAPH_MAX_NODES` (padrão 1200) trunca por
   distância de hop mais curta. Em profundidade alta o gargalo é o navegador,
   não o Atlas; truncar a partir da periferia preserva o que importa, e o
   payload reporta `truncated: true`.
7. **Teto de tempo, não só de tamanho.** Todo traversal roda sob
   `pymongo.timeout(GRAPH_MAX_TIME_MS)` (padrão 15 s), via
   `client.bounded_aggregate`. Com `timeoutMS` no cliente (CSOT) o driver
   ignora o `maxTimeMS` por operação; até 2026-10 o teto efetivo era o do
   cliente (25 s), e não os 15 s documentados. Prazo estourado não é repetido
   por `with_retry`. Medido: num grafo de 2,4 M de arestas,
   `$graphLookup` moeu **97 segundos** antes de estourar o limite de 100 MB do
   documento de saída. Sem o teto isso é uma tela travada por um minuto e
   meio terminando em erro; com o teto, `503` com `too_large: true` e uma
   dica do que fazer.
8. **O servidor decide o escopo de visibilidade.** O frontend manda *quem é o
   usuário*; `app/db/hierarchy.py` deriva o que essa pessoa alcança descendo
   `advisors.reports_to`. Nunca aceita lista de ids do cliente — a mesma regra
   da profundidade do traversal, aplicada a controle de acesso.
9. **Uma agregação por resposta, onde a árvore é rasa.** `economic_group` sobe,
   deriva as raízes do grupo dentro do próprio pipeline, desce de todas num
   `$lookup` correlacionado e hidrata em mais três `$lookup`. A versão anterior
   fazia o mesmo trabalho em até dez chamadas em série e media 4× mais lento —
   numa árvore rasa, ida e volta de rede custa mais que a travessia em si.
10. **Concorrência tem teto por classe, e o excesso é recusado.**
    `app/services/limits.py` dá 4 vagas simultâneas à carteira de um assessor
    e 2 à análise de concentração; quem não consegue vaga em 750 ms recebe
    `429` com `Retry-After`. Medido: sem isso, 64 clientes concorrentes
    levaram a p95 do caminho interativo de 308 ms para 2 s, porque uma
    consulta analítica pesada disputava a mesma fila. Sob saturação, um
    sistema honesto recusa cedo.
11. **Corpo de POST passa por schema, nunca checagem manual.** Os modelos
    `pydantic` no topo de `backend/main.py` existem porque dois bugs reais
    vieram de validação manual: `limit: -1` chegando ao `$vectorSearch` e
    virando 500, e `person_ids` chegando como string e sendo iterado
    caractere a caractere até a transação abortar com 409.

## Variáveis de ambiente

| Variável | Padrão | Papel |
|---|---|---|
| `MONGODB_URI` | — | obrigatória |
| `MONGODB_DB` | `graph_grupo_economico` | banco do projeto |
| `ATLAS_SEARCH_INDEX_NAME` | `companies_name_resolution` | índice de resolução de nome de empresa |
| `PEOPLE_SEARCH_INDEX_NAME` | `people_name_resolution` | índice de resolução de nome de sócio |
| `VECTOR_INDEX_NAME` | `activities_vector` | similaridade semântica de atividade |
| `VOYAGE_API_KEY` | — | sem ela, Vector Search degrada com `NO_EMBEDDING_KEY`. Embeddings vão direto à API da Voyage; esta PoV não tem LLM |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` | `voyage-3-lite` / `512` | ver `docs/adr/0002-vetores-em-512d-quantizados.md` |
| `GRAPH_MAX_DEPTH_CAP` | `6` | teto absoluto de profundidade |
| `GRAPH_DEFAULT_DEPTH` | `4` | definida em `config.py`, mas **não é lida** no traversal atual: `ownership.clamp_depth()` usa o literal `3` como padrão quando o cliente não manda profundidade. Config e código divergem hoje — se for citar o "padrão" em apresentação, o valor real é `3` |
| `HUB_FANOUT_THRESHOLD` | `50` | definida em `config.py`; não encontrei nenhuma leitura de `hub_threshold` em `backend/app/db/` ou `backend/app/services/` (`grep -rn hub_threshold backend/` não retorna uso). Parece configuração órfã — vale confirmar antes de citar como comportamento ativo |
| `GRAPH_MAX_NODES` | `1200` | teto de nós no payload |
| `GRAPH_MAX_TIME_MS` | `15000` | teto de tempo da agregação de traversal |
| `ANALYST_HOURS_PER_CASE` / `ANALYST_COST_PER_HOUR` / `CURRENCY` | `4` / `120` / `R$` | números do caso de negócio, entrada do apresentador — nunca medição |
| `BACKEND_PORT` | `8350` | porta do FastAPI |

## Endpoints

| Método | Rota | Papel |
|---|---|---|
| GET | `/health/live` | liveness pura, não toca o banco |
| GET | `/health` | checagem profunda: conexão, contagens, status dos índices de busca, latência de referência do `$graphLookup`, estado do change stream |
| GET | `/api/entry-points` | pontos de entrada da demo, a partir do ground truth em `economic_groups` |
| GET | `/api/group/{cnpj}` | **caminho principal**: cadeia societária e exposição consolidada, uma agregação (`depth`) |
| POST | `/api/search/companies` | Atlas Search sobre razão social e sócio, ciente do grupo em tela |
| POST | `/api/analysis/concentration` | Vector Search sobre descrição de atividade: exposição no bloco semelhante à atividade principal |
| GET | `/api/hierarchy/roster` | usuários de exemplo para a demo de visibilidade |
| GET | `/api/hierarchy/{advisor_id}/portfolio` | escopo derivado descendo `reports_to`, mais a carteira consolidada |
| GET | `/api/hierarchy/{advisor_id}/can-see/{cnpj}` | este usuário pode ver esta conta, e por quê |
| POST | `/api/credit/review` | transação ACID multi-documento sobre o grupo inteiro; recusa segunda revisão sobre empresas já em revisão |
| GET | `/api/credit/case/{case_id}` | o caso aberto, com o antes/depois do que a transação mudou |
| POST | `/api/credit/close/{case_id}` | encerra um caso |
| POST | `/api/demo/reset` | devolve o dataset ao estado pré-demo |
| GET | `/api/alerts/stream` | SSE alimentado pelo change stream; 503 acima de 64 conexões |
| GET | `/api/alerts/recent` | os alertas persistidos mais recentes |

## Ordem de operação (setup)

Dado → índices → backend → frontend → benchmarks. A topologia do dado
sintético foi decidida por medição antes de qualquer linha de backend existir
— ver `docs/adr/0001-topologia-do-dado-sintetico.md`. Sem essa ordem a demo
mostra o grupo inteiro na profundidade 1 ou um triângulo isolado na
profundidade 3.

`scripts/reset_demo.py` executa tudo em ordem: pessoas, base societária,
hierarquia, índices B-tree (`schema/indexes.js` com `MONGODB_DB` repassado),
estado de revisão, vetores (`embed_activities.py`, que só roda depois da base
societária — antes, o painel de concentração voltava quase vazio), índices
Atlas Search/Vector Search com espera por `READY` e uma conferência final.
Recusa banco que não termine em `_test` sem `ALLOW_DEMO_DB_WRITE=1`; a mesma
guarda vale para cada script de `data-generator/`. `run_all.sh` é atalho para
ele. A variável de volume dos grupos é `ECON_GROUPS`: `GROUPS` é especial no
bash e fazia o atalho antigo gerar sempre 20 grupos.

```bash
cp .env.example .env
python3 -m venv .venv && .venv/bin/pip install -r data-generator/requirements.txt
python3 -m venv backend/venv && backend/venv/bin/pip install -r backend/requirements.txt
(cd frontend && npm install)

ALLOW_DEMO_DB_WRITE=1 .venv/bin/python scripts/reset_demo.py
# validação sem tocar a demo:
MONGODB_DB=graph_grupo_economico_test .venv/bin/python scripts/reset_demo.py --scale small --drop

./start.sh                 # DEV=1 ./start.sh para HMR e --reload
```

## Testes

```bash
backend/venv/bin/python -m unittest discover -s tests -p 'test_*.py'  # offline
backend/venv/bin/python tests/http_adversarial.py     # API em execução, só leitura
backend/venv/bin/python tests/live_graph_adversarial.py  # topologias hostis em banco efêmero
.venv/bin/python tests/test_resilience.py           # suíte hostil completa
.venv/bin/python tests/test_resilience.py --quick    # sem change stream e carga
PYTHONPATH=backend .venv/bin/python queries/bench.py --runs 30
.venv/bin/python tests/stress.py                     # carga mista, rampa até 64 concorrentes
.venv/bin/python tests/stress.py --max 128 --seconds 30
cd frontend && node --test tests/*.test.mjs           # regressão de frontend
```

`tests/test_resilience.py` não é teste unitário: cada caso corresponde a uma
forma real de este projeto falhar na frente de um cliente, e o critério de
sucesso é **degradar com mensagem clara** — nunca 500, nunca travar, nunca
dado inconsistente. `tests/stress.py` pergunta uma coisa diferente: não "isso
quebra?", mas "o que degrada primeiro?".

## Armadilhas encontradas construindo (vale saber antes de mexer)

- **Raiz definida pelo que o traversal enxergou, não pelo banco.** Definir
  raiz como "empresa sem dono PJ *dentro das arestas já percorridas*" promove
  um nó do meio da árvore a raiz quando a profundidade acaba antes de chegar
  ao dono de verdade — o controle de profundidade parece quebrado porque cada
  nível mostra quase a mesma coisa. A correção é perguntar ao banco: raiz é
  quem não tem dono PJ **em `ownership`**, checado com um `$lookup` indexado
  dentro da mesma agregação (ver `queries.md`).
- **A versão oposta do mesmo bug.** Descartar qualquer entidade que apareça
  como *controlada* exclui a própria holding (os sócios PF dela produzem
  arestas em que ela é a controlada). O grupo volta com duas empresas em vez
  de oito, sem erro nenhum.
- **`_id` determinístico colidindo consigo mesmo.** Adicionar cross-holdings
  aos grupos de vitrine criou um par que o laço de níveis já tinha criado
  (`11 % 9 == 2`); como o `_id` da aresta é `uuid5(owner, owned)`, a carga
  limpa morreu em `E11000` depois de 1,9 M inserções. O gerador agora
  deduplica os pares antes de escrever.
- **Join por documento escondido atrás de um escopo pequeno.** A carteira do
  assessor partia de `companies` no escopo e buscava `credit_exposure` por
  empresa — 13 s para um regional. Denormalizar `advisor_id` em
  `credit_exposure` virou um `$match` indexado mais `$group`: 462 ms.
- **`ObjectId` no payload do alerta.** SSE serializa para JSON; o `_id` do
  alerta é string derivada do documento fonte, e a escrita é `upsert` para
  que um evento repetido não derrube a thread do listener com chave
  duplicada.
- **`depthField` devolve `NumberLong`.** No `mongosh`, `long + 1` concatena
  string em vez de somar — usar `$toInt`.
- **Escrita em lote afogando o change stream.** O listener reagia a qualquer
  escrita na coleção observada, então um backfill disparava milhares de
  eventos irrelevantes. Agora filtra por tipo de operação e campo relevante.
  Vale saber antes de uma demo: depois de um lote grande o listener drena o
  oplog a partir do `resume_token` guardado, e novos eventos só aparecem
  depois de alcançar o presente.

Detalhes completos, incluindo os bugs pegos pela suíte de resiliência, estão
no histórico de `01-arquitetura.md` (absorvido aqui) e nos comentários do
próprio código-fonte.

## Referências

- Modelagem detalhada, incluindo a comparação entre os dois padrões de grafo:
  [`../../schema/collections.md`](../../schema/collections.md).
- Decisões de arquitetura registradas como ADR: [`../adr/`](../adr/).
- Caso de negócio: [`../business-case.md`](../business-case.md).
- Roteiro de demo: [`../demo-script.md`](../demo-script.md).
