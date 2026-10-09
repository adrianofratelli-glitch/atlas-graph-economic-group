> Atualização: a revisão usa comprovante assinado e revalidação transacional; a concentração usa ENN e reporta apenas o bloco da atividade principal. Trechos abaixo são explicativos; o código é a fonte das pipelines vigentes.

# Queries, pipelines e índices

Referência rápida: "onde está a query X" e "por que esse índice existe".
Tudo extraído do código-fonte real em `backend/app/db/`, `backend/app/services/`,
`schema/indexes.js` e `schema/search_indexes.py`. Onde não achei um exemplo real
de algo, digo isso explicitamente em vez de inventar.

## Índice — queries por arquivo

| Arquivo | O que responde |
|---|---|
| `backend/app/db/ownership.py` | cadeia societária / grupo econômico (`$graphLookup` duplo) |
| `backend/app/db/hierarchy.py` | escopo de visibilidade comercial (`$graphLookup` sobre `advisors`) |
| `backend/app/db/search.py` | Atlas Search (razão social + nome de sócio) |
| `backend/app/db/concentration.py` | Atlas Vector Search (concentração semântica de atividade) |
| `backend/app/db/credit_decision.py` | transação ACID multi-documento |
| `backend/app/services/alerts.py` | Change Stream sobre `companies` |
| `backend/app/services/credit_demo.py` | agregação de pontos de entrada da demo |

---

## 1. Grupo econômico — `backend/app/db/ownership.py`

### 1.1 `_graph_lookup()` (linhas 72–91) — estágio `$graphLookup` reutilizável

**O que faz:** monta o estágio de travessia em uma das duas direções. A
aresta `ownership` é **dirigida** (`owner_id` participa de `owned_id`), e
subir/descer usam campos trocados:

- subir (quem é dono de mim): `connectToField: "owned_id"`,
  `connectFromField: "owner_id"`
- descer (de quem eu sou dono): `connectToField: "owner_id"`,
  `connectFromField: "owned_id"`

```js
// direção "up" (subir)
{
  $graphLookup: {
    from: "ownership",
    startWith: "$_id",
    connectFromField: "owner_id",
    connectToField: "owned_id",
    as: "cadeia",
    maxDepth: depth,
    depthField: "nivel"
  }
}
```

**Por que existe:** trocar os campos por engano devolve silenciosamente o
conjunto errado — sem erro, sem aviso. É por isso que o comentário no código
chama isso de "o detalhe que mais confunde" do projeto.

`restrictSearchWithMatch` **não** é usado aqui de propósito: filtrar por
`owner_type` cortaria o sócio pessoa física, que costuma ser exatamente a
ponte entre dois grupos que o cadastro trata como não relacionados.

### 1.2 `_pipeline_grupo()` / `economic_group()` (linhas 104–429) — a query principal da demo

**Onde:** `backend/app/db/ownership.py:271-429`, chamada por
`GET /api/group/{cnpj}` em `backend/main.py:160`.

**O que faz, em linguagem natural:** dado um CNPJ, monta a árvore societária
completa e a exposição de crédito consolidada — tudo em **uma única ida ao
cluster**. Passos:

1. `$match` no CNPJ + `$limit: 1` — encontra a empresa.
2. `$graphLookup` **para cima** até os controladores (profundidade `depth`).
3. Deriva as raízes do grupo **dentro do próprio pipeline**: pessoas
   jurídicas sem dono corporativo entre as arestas já percorridas.
4. Confirma cada raiz candidata com um `$lookup` indexado em `ownership`
   (por `owned_id` + `owner_type: corporate`) — sem isso, um nó do meio da
   árvore vira raiz sempre que a profundidade some antes de alcançar o dono
   real dele.
5. `$lookup` correlacionado que roda `$graphLookup` **para baixo**, a partir
   de todas as raízes de uma vez, no servidor.
6. Três `$lookup` de hidratação: `companies`, `people`, `credit_exposure` —
   mais um quarto (`advisors`) para trazer quem atende cada empresa do grupo.
7. `$project` final com só os campos que o frontend consome.

**Exemplo ilustrativo do formato da pergunta** (não é o pipeline completo —
esse está em `ownership.py:104-268` e é devolvido ao vivo em `query_details`
na resposta da API):

```js
db.companies.aggregate([
  { $match: { cnpj: "12.345.678/0001-90" } },
  { $limit: 1 },
  { $graphLookup: {
      from: "ownership", startWith: "$_id",
      connectFromField: "owner_id", connectToField: "owned_id",
      as: "cadeia", maxDepth: 3, depthField: "nivel"
  }},
  // ... derivação de raízes, descida correlacionada, hidratação ...
])  // prazo: pymongo.timeout(GRAPH_MAX_TIME_MS) em client.bounded_aggregate
```

**Por que existe / motivação de performance:** a versão anterior fazia o
mesmo trabalho em até dez chamadas em série — `find` do CNPJ, subida, uma
descida **por raiz num laço Python**, três `find` de hidratação. Com RTT de
8 ms ao cluster, isso media ~52 ms contra ~13 ms da versão de uma agregação
só. Numa árvore rasa o traversal em si custa milissegundos de banco; a
latência é decidida por quantas vezes a aplicação fala com o cluster, não
pela complexidade do `$graphLookup`.

`MAX_RAIZES = 5` (linha 52): um grupo com mais de 5 controladores no topo é
processamento em lote, não consulta interativa.

`clamp_depth()` (linhas 55–60): profundidade default `3`, teto
`settings.depth_cap` (`GRAPH_MAX_DEPTH_CAP`, padrão 6) — decidido no
backend, nunca aceito cru do cliente.

**Tratamento de erro:** se `$graphLookup` atinge limites de memória/tamanho
ou o prazo `GRAPH_MAX_TIME_MS` (servidor `ExecutionTimeout` ou cliente
`NetworkTimeout` do CSOT), a função devolve `too_large: true` em
vez de deixar a exceção subir (`_falha()`, linhas 63–69).

---

## 2. Escopo de visibilidade — `backend/app/db/hierarchy.py`

### 2.1 `_subordinados_pipeline()` / `team()` / `portfolio()` (linhas 37–186)

**Onde:** `backend/app/db/hierarchy.py:81-186`, chamada por
`GET /api/hierarchy/{advisor_id}/portfolio` em `backend/main.py:183`.

**O que faz:** dado um `advisor_id`, desce a árvore de `advisors.reports_to`
(auto-referente) via `$graphLookup`, une o escopo alcançado, soma a exposição
de crédito de todo o escopo e devolve as maiores posições.

```js
db.advisors.aggregate([
  { $match: { _id: "advisor_123" } },
  { $graphLookup: {
      from: "advisors", startWith: "$_id",
      connectFromField: "_id", connectToField: "reports_to",
      as: "equipe", maxDepth: 4, depthField: "nivel_relativo"
  }},
  { $set: { escopo: { $setUnion: [["$_id"], "$equipe.id"] } } },
  { $lookup: {
      from: "credit_exposure",
      let: { escopo: "$escopo" },
      pipeline: [
        { $match: { $expr: { $in: ["$advisor_id", "$$escopo"] } } },
        { $group: {
            _id: null,
            empresas: { $sum: 1 },
            limite: { $sum: "$limite" },
            utilizado: { $sum: "$utilizado" },
            vencido: { $sum: "$vencido" },
            top: { $topN: { n: 50, sortBy: { utilizado: -1 }, output: { /* ... */ } } }
        }}
      ],
      as: "carteira"
  }}
  // + $lookup de hidratação em companies, só para o top N
])
```

**Por que existe:** a visibilidade é derivada da árvore **no momento da
consulta**, não uma lista pré-calculada por usuário. Se fosse pré-calculada,
toda troca de carteira ou mudança de gestor exigiria recálculo, e na janela
entre o evento e o recálculo alguém veria o que não devia. `MAX_NIVEIS = 4`
(linha 34) dá folga de um nível sobre a árvore atual (superintendente →
regional → gerente → assessor) sem abrir traversal sem teto.

**Motivação de performance (denormalização):** a soma passa por
`credit_exposure`, não por `companies`. A versão anterior partia das
empresas do escopo e fazia `$lookup` de exposição documento a documento — 13
segundos para um regional (129 assessores, 51 mil exposições). Só ~32% da
base tem crédito, e é a exposição que carrega o número; `advisor_id` foi
denormalizado para `credit_exposure` (pelo gerador, não por um listener — ver
`architecture.md`), e a carteira virou `$match` indexado + `$group`: 462 ms.

### 2.2 `can_see()` (linhas 189–255)

**Onde:** `backend/app/db/hierarchy.py:189-255`, chamada por
`GET /api/hierarchy/{advisor_id}/can-see/{cnpj}` em `backend/main.py:204`.

**O que faz:** parte da empresa, salta para o assessor dono da conta via
`$lookup`, e sobe a cadeia de comando dele com `$graphLookup` (`startWith:
"$reports_to"`) verificando se `advisor_id` está no caminho.

```js
db.companies.aggregate([
  { $match: { cnpj: "12.345.678/0001-90" } },
  { $limit: 1 },
  { $lookup: {
      from: "advisors",
      let: { dono: "$advisor_id" },
      pipeline: [
        { $match: { $expr: { $eq: ["$_id", "$$dono"] } } },
        { $graphLookup: {
            from: "advisors", startWith: "$reports_to",
            connectFromField: "reports_to", connectToField: "_id",
            as: "superiores", maxDepth: 4
        }}
      ],
      as: "dono"
  }}
])
```

**Por que existe / motivação de performance:** subir da folha (a conta) até
o topo custa no máximo `MAX_NIVEIS` saltos; descer a árvore inteira do
gerente para checar se ele alcança a conta custaria muito mais. A versão
anterior fazia isso em duas idas ao cluster (`find_one` + agregação); com
piso de rede alto, a segunda ida custava mais que todo o trabalho de banco
somado — hoje é uma única agregação.

---

## 3. Busca por nome (Atlas Search) — `backend/app/db/search.py`

### 3.1 `resolve_company()` (linhas 129–254)

**Onde:** `backend/app/db/search.py:129`, chamada por
`POST /api/search/companies` em `backend/main.py:269`.

**O que faz:** busca difusa em `razao_social` (e, em paralelo, no nome de
sócios pessoa física via `_busca_socios()`), escopada por padrão ao grupo em
tela.

```js
db.companies.aggregate([
  { $search: {
      index: "companies_name_resolution",
      compound: {
        should: [
          { text: { query: "Construtora Alfa", path: "razao_social", score: { boost: { value: 3 } } } },
          { text: { query: "Construtora Alfa", path: "razao_social", fuzzy: { maxEdits: 2, prefixLength: 1 } } },
          { autocomplete: { query: "Construtora Alfa", path: "razao_social" } }
        ],
        minimumShouldMatch: 1,
        filter: [ { in: { path: "_id", value: ["comp_id_1", "comp_id_2"] } } ]  // só quando scope_only=true
      }
  }},
  { $limit: 10 },
  { $lookup: { from: "credit_exposure", localField: "_id", foreignField: "company_id", as: "cred" } },
  { $project: { razao_social: 1, cnpj: 1, uf: 1, limite: { $first: "$cred.limite" }, score: { $meta: "searchScore" } /* ... */ } }
])
```

**Por que existe:** cadastro de empresa é escrito de N formas ("Construtora
Alfa S.A." vs "CONSTRUTORA ALPHA SA"). Um `$graphLookup`/`$match` por
igualdade nunca liga as duas grafias, e a esteira de crédito trataria como
empresas distintas — exatamente como um grupo econômico passa despercebido.
A busca roda **no mesmo cluster e motor** que guarda a cadeia societária, sem
um segundo sistema para sincronizar.

**Por que o padrão é escopado (`scope_only=True`):** a tela mostra um grupo.
Buscar sem filtro devolve dezenas de resultados sem relação nenhuma com o
grafo desenhado — ruído, não busca. `compound.filter` no próprio índice
restringe ao grupo em tela (usa `_id` mapeado como `token`). `scope_only=false`
é o gesto deliberado de entity resolution: acha uma empresa que ainda não
está no grafo, com resultado marcado `in_group: false`.

### 3.2 `_busca_socios()` (linhas 65–126)

**O que faz:** a mesma busca fuzzy/autocomplete, mas sobre `people.name`.

**Por que existe:** quem descobre um grupo econômico frequentemente parte do
nome de uma **pessoa** (o sócio que aparece em empresas de grupos
diferentes), não do CNPJ. Buscar só razão social deixava metade do grafo
inalcançável. Se o índice de pessoas não está pronto, essa busca degrada
sozinha (devolve lista vazia) sem derrubar a busca de empresas.

---

## 4. Concentração semântica (Atlas Vector Search) — `backend/app/db/concentration.py`

**Onde:** `backend/app/db/concentration.py:57-187`, chamada por
`POST /api/analysis/concentration` em `backend/main.py:286`.

**O que faz, em linguagem natural:** dado o grupo em tela, pergunta "este
grupo é tão diversificado quanto os códigos CNAE sugerem, ou os CNAEs
diferentes escondem o mesmo negócio?".

1. Agrupa `companies` do grupo por `cnae_descricao`, somando a exposição de
   crédito de cada atividade (agregação normal, sem busca):

```js
db.companies.aggregate([
  { $match: { _id: { $in: companyIds }, is_holding: { $ne: true } } },
  { $lookup: { from: "credit_exposure", localField: "_id", foreignField: "company_id", as: "cred" } },
  { $group: {
      _id: "$cnae_descricao",
      companies: { $sum: 1 },
      limite: { $sum: { $ifNull: [{ $first: "$cred.limite" }, 0] } },
      vencido: { $sum: { $ifNull: [{ $first: "$cred.vencido" }, 0] } }
  }},
  { $sort: { limite: -1 } }
], { allowDiskUse: true })
```

2. Pega o vetor da atividade dominante (maior exposição) e roda
   `$vectorSearch` em `activities` (não em `companies`) para achar quais
   outras descrições do próprio grupo dizem a mesma coisa:

```js
db.activities.aggregate([
  { $vectorSearch: {
      index: "activities_vector",
      path: "embedding",
      queryVector: [ /* vetor 512d da atividade dominante */ ],
      exact: true,
      limit: 50
  }},
  { $set: { score: { $meta: "vectorSearchScore" } } },
  { $project: { _id: 1, score: 1 } },
  { $sort: { score: -1 } }
])
```

3. Atividades com `score >= LIMIAR_EQUIVALENCIA` (`0.80`, calibrado no dado
   desta PoV, linha 49) compõem o bloco semelhante à atividade principal; soma sua exposição.
   Não estima todos os negócios nem o maior bloco global.

**Por que existe:** comparar código CNAE não encontra que "construção de
edifícios residenciais", "obras de alvenaria e acabamento" e "serviços de
engenharia e projeto de obras" (as descrições reais de `generate_ownership.py`)
são três códigos e um negócio só. Comparar palavras também falha: a primeira
não divide termo nenhum, além de preposição, com as outras duas (a segunda e a
terceira dividem só "obras"). Comparar **significado** encontra.

**Por que o índice vive em `activities` e não em `companies` (motivação de
performance):** `activities` tem ~32 documentos, um por descrição distinta.
O índice vetorial já esteve em `companies.activity_embedding` (1,2 M
documentos repetindo os mesmos 32 textos) e a consulta levava **29,3
segundos**. Movido para `activities`, a mesma resposta sai em milissegundos,
o índice cabe em qualquer tier, e a carga não escreve mais um vetor
512-dimensional em cada empresa.

**Trap de `numCandidates` com filtro seletivo:** `$vectorSearch` percorre o
grafo HNSW da coleção inteira e aplica o filtro durante a caminhada. Um
filtro muito seletivo (poucas dezenas de empresas de um grupo dentro de 1,2
milhão) descarta quase todo candidato e a busca esgota a lista antes de
juntar resultado — o painel volta quase vazio com `numCandidates` baixo.
`numCandidates: 10000` era necessário nesse cenário porque é o **teto do
servidor** (`"numCandidates" must be within bounds [1..10000]`). Hoje o índice vive em `activities` (~32 documentos) e a consulta usa busca exata (`exact: true`), sem `numCandidates`. O resultado descreve a equivalência com a atividade de maior exposição, não uma partição de todos os negócios do grupo.

O campo `setor` está mapeado como `filter` no índice vetorial (ver seção de
índices abaixo), mas não localizei um `$vectorSearch` no código atual que
passe `filter` por `setor` ou por `company_ids` — o filtro por escopo de
grupo, hoje, acontece filtrando os `company_ids` **antes** da etapa 1
(agregação normal), não dentro do `$vectorSearch`. Se for citar "filtro
seletivo em `$vectorSearch`" na conversa técnica, a evidência real no código
é o comentário de trap acima (`app/db/concentration.py:188-202` do
`02-mongodb.md` original), não uma chamada ativa — vale confirmar se essa
otimização está noutro branch antes de apresentar como comportamento atual.

---

## 5. Transação ACID — `backend/app/db/credit_decision.py`

**Onde:** `open_review()` (linhas 48–152) e `close_review()` (linhas
155–182), chamadas por `POST /api/credit/review` e
`POST /api/credit/close/{case_id}` em `backend/main.py:302` e `:331`.

**O que faz:** não é leitura, é escrita transacional — vale documentar
porque é a peça mais citada em conversa de arquitetura. Dentro de uma
`client.start_session()` com `session.with_transaction(...)`:

```python
session.with_transaction(
    txn,
    read_concern=ReadConcern("snapshot"),
    write_concern=WriteConcern("majority"),
    read_preference=ReadPreference.PRIMARY,
)
```

Dentro de `txn`: checa se já existe revisão aberta sobre alguma dessas
empresas (**dentro** da mesma transação — auditoria de 2026-09 moveu essa
checagem para dentro, corrigindo uma race condition real entre requisições
concorrentes), depois `update_many` em `companies.credit_status`,
`update_many` em `credit_exposure.review_flag`, e `insert_one` em
`credit_decisions`.

**Por que existe:** ou o grupo inteiro entra em revisão, ou nenhuma empresa
entra. Um estado intermediário (metade bloqueada, metade livre, sem registro
coerente) é pior que não ter decidido — a mesa de crédito aprovaria pela
porta que ficou aberta. `readConcern: snapshot` garante que a decisão é
tomada sobre uma fotografia consistente do grupo; `writeConcern: majority`
garante que só é considerada tomada quando a maioria do replica set
confirmou.

**Por que a primeira escrita é no documento de controle:** `txn` começa com
`find_one_and_update` em `demo_control/review_lock` (`$inc`), e só segue se
`reset_until` não estiver no futuro. `reset_all()` (o `POST /api/demo/reset`)
não é transacional: toma esse lease antes de limpar e o solta no fim. Como a
transação **escreve** no mesmo documento, as duas ordens são seguras — se o
reset tomou o lease depois do snapshot, a escrita sofre conflito e
`with_transaction` repete, agora vendo o lease; se a transação escreveu
primeiro, a escrita do lease espera o commit e o reset limpa o caso junto.
Sem isso, uma abertura comitada entre a limpeza de `companies` e o
`delete_many` de `credit_decisions` deixava 43 empresas bloqueadas sem caso
(reproduzido em 2026-10-08). O reset ainda termina varrendo marcas sem caso.
`close_review()` usa a mesma guarda.

**Por que recusa uma segunda revisão sobre o mesmo grupo:** abrir duas
sobrescreveria `case_id` e deixaria a primeira como casca (`status: "open"`,
zero empresas apontando para ela) — pior que erro, porque a auditoria não
consegue reconstruir o que foi decidido.

---

## 6. Change Stream — `backend/app/services/alerts.py`

**Onde:** `AlertHub._run()` (linhas 116–162), thread própria, ligada no
`lifespan` de `backend/main.py:32`.

**O que faz:** observa `companies` com um pipeline de `$match` **rodando no
servidor**, filtrando por campo alterado:

```python
db.companies.watch(
    pipeline=[{
        "$match": {
            "operationType": "update",
            "$or": [
                {"updateDescription.updatedFields.credit_status": {"$exists": True}},
                {"updateDescription.removedFields": "case_id"},
            ],
        }
    }],
    full_document="updateLookup",
    max_await_time_ms=250,
    resume_after=resume_token,  # se houver
)
```

**Por que existe:** abrir revisão marca dezenas de empresas numa transação;
quem precisa saber não é só a tela que disparou. O caminho comum seria uma
rotina noturna varrendo `companies` — o change stream acorda por evento,
agrupa por `case_id` (`JANELA_COALESCE_S = 0.5s`, coalescendo `update_many`
sobre 40 empresas em **um** evento) e publica em segundos, sem polling.

O `$match` no servidor é o que permite observar uma coleção de 1,2 M
documentos com segurança: uma carga em lote toca `companies` sem tocar
`credit_status`, e não produz ruído nenhum no SSE.

**Não é** uma coleção de aresta derivada — a coleção `ownership_alerts`
guarda só os eventos publicados (upsert por `_id: f"alert_{case_id}"`, para
que um evento repetido não quebre a thread do listener). A ownership edge em
si nunca é materializada de novo por esse mecanismo.

---

## 7. Agregações auxiliares — `backend/app/services/credit_demo.py`

**Onde:** `_compute_entry_points()`, linhas 46–154, chamada por
`GET /api/entry-points`.

**O que faz:** monta a lista de pontos de entrada da demo (um grupo de
vitrine por profundidade societária) e o "controle" — empresas sem dono
corporativo, o caso comum sem grupo.

```js
// achar empresas "controle" (sem dono corporativo) numa única ida
db.companies.aggregate([
  { $match: { is_holding: { $ne: true }, situacao: "ATIVA" } },
  { $limit: 400 },
  { $lookup: {
      from: "ownership",
      let: { cid: "$_id" },
      pipeline: [
        { $match: { $expr: { $and: [
            { $eq: ["$owned_id", "$$cid"] },
            { $eq: ["$owner_type", "corporate"] }
        ]}}},
        { $limit: 1 }
      ],
      as: "dono_corporativo"
  }},
  { $match: { dono_corporativo: { $size: 0 } } },
  { $limit: 3 },
  { $project: { cnpj: 1, razao_social: 1 } }
])
```

**Por que existe (motivação de performance):** a versão anterior fazia isso
num laço Python de até 400 `count_documents` separados — até 400 idas ao
cluster no pior caso, e o custo reaparecia a cada `POST /api/demo/reset`,
justamente quando o cluster já está sob carga de reconstrução do dataset.
Uma agregação com `$lookup` correlacionado resolve no servidor, no mesmo
padrão de `ownership.py`.

O resultado inteiro (`entry_points()`) é cacheado em memória de processo
(`_cache`, protegido por `threading.Lock` cobrindo checagem + cálculo, não só
a checagem — corrigido em auditoria de 2026-09 para evitar cálculo duplicado
sob concorrência).

---

## Índices MongoDB

Fonte: `schema/indexes.js` (B-tree, script idempotente) e
`schema/search_indexes.py` (Atlas Search / Vector Search, ciclo de vida
próprio `PENDING → BUILDING → READY`, por isso fora do script de índice de
banco).

### B-tree (`schema/indexes.js`)

| Coleção | Índice | Tipo | Por quê |
|---|---|---|---|
| `companies` | `{cnpj: 1}` | single, **unique** | lookup pontual por CNPJ — o caminho principal da demo |
| `companies` | `{razao_social: 1}` | single | apoio à busca por nome |
| `companies` | `{is_holding: 1}` | single, sparse | seleção de casos na demo |
| `companies` | `{credit_status: 1}` | single, sparse | empresas sob revisão de crédito |
| `companies` | `{case_id: 1}` | single, sparse | empresas de um caso aberto |
| `companies` | `{seed_index: 1}` | single | seleção determinística de casos |
| `companies` | `{advisor_id: 1}` | single | carteira de um assessor |
| `ownership` | `{owner_id: 1}` | single | `connectToField` ao descer / `connectFromField` ao subir |
| `ownership` | `{owned_id: 1}` | single | `connectToField` ao subir / `connectFromField` ao descer |
| `ownership` | `{simulated: 1}` | single, sparse | reset da demo remove só arestas simuladas |
| `credit_exposure` | `{company_id: 1}` | single, **unique** | exposição por empresa |
| `credit_exposure` | `{advisor_id: 1}` | single | soma da carteira sem join por documento |
| `credit_exposure` | `{case_id: 1}` | single, sparse | exposições de um caso aberto |
| `credit_exposure` | `{review_flag: 1}` | single, sparse | fila de exposições sob revisão e reset da demo |
| `credit_exposure` | `{vencido: -1}` | single | encontrar inadimplência relevante |
| `advisors` | `{reports_to: 1}` | single | `connectToField` ao descer a hierarquia |
| `advisors` | `{papel: 1}` | single | seleção de gerente/assessor na demo |
| `advisors` | `{matricula: 1}` | single, **unique** | login por matrícula |
| `people` | `{seed_index: 1}` | single | seleção determinística |
| `ownership_alerts` | `{created_at: -1}` | single | `GET /api/alerts/recent` ordena pelo mais novo (adicionado na auditoria de 2026-09) |

**Duas decisões deliberadas de índice, documentadas no código:**

- **Sem índice composto em `ownership`.** `{owned_id: 1, owner_type: 1}` só
  pagaria com `restrictSearchWithMatch` filtrando por `owner_type`, e o
  traversal não filtra assim de propósito — cortaria exatamente o sócio PF
  que costuma ser a ponte entre dois grupos. Também não existe índice isolado
  em `owner_type`: 3 valores em milhões de arestas não seleciona nada, e toda
  consulta já entra por `owner_id`/`owned_id`.
- **`advisor_id` denormalizado em `credit_exposure`.** Dado derivado, com
  fonte de verdade no gerador (não mantido por listener). Existe porque a
  carteira consolidada, partindo de `companies` no escopo com `$lookup` por
  empresa, levava 13 s para um regional; como fonte do número
  (`credit_exposure`) só cobre ~32% das empresas, a mesma resposta virou
  `$match` indexado + `$group`: 462 ms.

### Atlas Search (`schema/search_indexes.py`)

| Índice | Coleção | Campos | Por quê |
|---|---|---|---|
| `companies_name_resolution` (`ATLAS_SEARCH_INDEX_NAME`) | `companies` | `razao_social` (string padrão + `autocomplete` minGrams 3/maxGrams 15/edgeGram), `cnpj` (keyword), `uf`/`situacao` (token), `_id` (token) | resolve grafia divergente de razão social; `_id` como token permite `compound.filter` para escopar a busca ao grupo em tela |
| `people_name_resolution` (`PEOPLE_SEARCH_INDEX_NAME`) | `people` | `name` (string padrão + autocomplete), `document_id` (keyword), `occupation` (token), `_id` (token) | mesmo tratamento para sócio pessoa física — quem investiga um grupo frequentemente parte de um nome de pessoa, não do CNPJ |

### Atlas Vector Search (`schema/search_indexes.py`)

| Índice | Coleção | Campos | Por quê |
|---|---|---|---|
| `activities_vector` (`VECTOR_INDEX_NAME`) | `activities` | `embedding` (vector, 512 dimensões, `cosine`, `quantization: scalar`), `setor` (filter) | vive na coleção de ~32 descrições distintas de atividade, não em `companies` (1,2 M documentos) — ver seção 4 acima. quantização preservada do ADR; com apenas 32 vetores, não é requisito de capacidade |

Modelo de embedding: `voyage-3-lite`, 512 dimensões — decisão registrada em
`docs/adr/0002-vetores-em-512d-quantizados.md`.

### O que não encontrei no código (evitando invenção)

- Nenhum índice TTL (`expireAfterSeconds`) em nenhuma coleção do projeto.
- Nenhum uso ativo de `HUB_FANOUT_THRESHOLD`/poda de hub por grau nas
  queries atuais (ver nota em `architecture.md`).
- `$vectorSearch` com `filter` por `company_ids`/`setor` não aparece
  efetivamente chamado no `concentration.py` atual — o escopo ao grupo
  acontece antes, na agregação normal sobre `companies` (seção 4).
