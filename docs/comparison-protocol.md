# MongoDB e PostgreSQL: protocolo de comparação

**Estado em 2026-11-12:** primeira rodada executada com os dois motores locais, no mesmo computador e sem rede (MongoDB 8.2 e PostgreSQL 16, 1 GB de cache cada). As 19 células devolveram resultado idêntico. Com 1 cliente e com 8, o PostgreSQL teve p95 menor nas mesmas 15 células (grupos de até 31 empresas e todas as topologias hostis) e o MongoDB nas quatro maiores (121 a 1.201 empresas), com distância de cerca de duas vezes sob 8 clientes. A máquina estava compartilhada com outros processos (carga registrada no JSON). O MongoDB local é 8.2.7 Community; a demo roda Atlas 9.0. Decisão, números e ressalvas: [ADR 0004](adr/0004-first-postgresql-comparison-on-local-engines.md). Dados brutos: `frontend/public/evidence/comparison.json`. Atlas, PostgreSQL gerenciado, carga fria, a base de 1,2 milhão de empresas e as concorrências 32/64 continuam pendentes, nas condições abaixo.

```bash
python3.12 -m venv /tmp/cmp && /tmp/cmp/bin/pip install -r queries/requirements-compare.txt
/tmp/cmp/bin/python queries/compare_postgres.py --runs 100 --concurrency 1,8   # sobe mongod e PostgreSQL temporários
```

Sem `--mongo-uri`/`--pg-uri`, o script precisa de `mongod` no PATH e sobe um PostgreSQL embutido (`pgserver`, wheels até Python 3.12); ambos são encerrados no fim. Com URIs, o banco PostgreSQL precisa terminar em `_test`. O job `compare` do CI roda a mesma checagem de correção com containers `mongo:8.2` e `postgres:16`, informativo (`continue-on-error`) até o primeiro verde no GitHub; os tempos de lá não são publicados.

Não publicamos tempos estimados nem atribuímos vantagem a um motor sem medir os dois.

## Pergunta comum

Dado o identificador de uma empresa, encontrar seus controladores corporativos até o limite contratado, identificar raízes válidas, percorrer suas participações descendentes, eliminar repetições e devolver as empresas e a exposição consolidada. Consultas incompletas devem declarar o motivo. A hierarquia comercial será uma segunda carga, medida separadamente.

O contrato deve ser idêntico: conjunto de empresas, conjunto de arestas, valores em centavos e indicação de alcance. Comparar apenas uma CTE que soma descendentes com a investigação completa da POV não seria equivalente.

## Dados e implementação

Usar o mesmo manifesto sintético exportado, com SHA-256 e contagens por entidade. Carregar as mesmas participações cruzadas, ciclos e empresas sem crédito nos dois motores. Não usar apenas as árvores mais favoráveis.

No MongoDB, executar o pipeline versionado da POV. No PostgreSQL, usar tabelas de empresas, participações e exposições, índices para os dois sentidos da relação e `WITH RECURSIVE`, com controle explícito de ciclos, deduplicação e profundidade. A equipe que conhece cada motor deve revisar seu plano antes da medição. Não impor ao PostgreSQL uma sequência de consultas quando uma única consulta pode resolver a pergunta.

Fontes de semântica: [MongoDB $graphLookup](https://www.mongodb.com/docs/manual/reference/operator/aggregation/graphlookup/) e [PostgreSQL recursive queries](https://www.postgresql.org/docs/17/queries-with.html).

## Condições equivalentes

- Mesma classe de CPU, memória e armazenamento, documentando versões e configurações.
- Cliente na mesma região e com caminho de rede comparável. Atlas remoto versus PostgreSQL em notebook não será tratado como comparação de desempenho.
- Mesma durabilidade, timeout, payload e concorrência. Reportar recusas e erros à parte dos tempos de consultas concluídas.
- Separar cargas frias e aquecidas. Alternar a ordem dos cenários e motores; repetir rodadas, guardar todas as amostras e o plano real de cada consulta.
- Medir carga inicial, tamanho dos dados e índices, tempo no servidor, latência ponta a ponta e custo operacional separadamente.

## Matriz e critérios

Profundidades 1, 2, 4 e 6; ramificações 1, 2 e 3; participações cruzadas; ciclos; hub com 1.200 filhos; controle isolado. Repetir sobre uma base de fundo de tamanho equivalente antes de extrapolar as pequenas fixtures. Testar concorrências 1, 8, 32 e 64, com no mínimo 100 amostras concluídas por célula para avaliar caudas, além de aquecimento.

Uma célula com resultados divergentes reprova por correção e não entra em ranking de velocidade. O limiar interativo inicial é p95 de 3 segundos, acordado antes da execução. Esse limiar é uma condição da apresentação, não um SLA de produção.

O relatório final deve apresentar também onde cada alternativa deixa de atender. A decisão considera a localização atual dos dados, operação, atualização de relações e necessidade de algoritmos de grafo; não apenas a menor barra de latência.
