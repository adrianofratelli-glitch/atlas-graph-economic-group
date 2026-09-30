# MongoDB e PostgreSQL: protocolo de comparação

A execução foi adiada por não haver um ambiente PostgreSQL preparado. Não publicamos tempos estimados nem atribuímos vantagem a um motor sem medir os dois.

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
