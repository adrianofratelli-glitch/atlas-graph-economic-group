# Validação de melhorias — 29 e 30/09/2026

A POV mantém os dois objetivos: cadeia societária rasa consultada por CNPJ com exposição consolidada e hierarquia comercial consultada em tempo de execução. Não foi introduzido agente generativo nem detecção de anéis de fraude.

## Referência e decisões

O repositório [Mongo-LangGraph-Demo](https://github.com/jeegarghodasara/Mongo-LangGraph-Demo), revisão `90bc5849172daab1fcb86b3099fa42164aed0cd8`, foi consultado somente para leitura. A inspiração aproveitada foi tornar as etapas e evidências da investigação visíveis. O percurso determinístico existente continua mais adequado ao objetivo desta POV; não há justificativa para adicionar LLMs ao caminho de decisão.

- Resumo apresenta exposição, vínculos com empresas em atraso, alcance da consulta e etapas da revisão. Interface mantém a identidade MongoDB, com abas responsivas e navegação por teclado.
- A API deriva composição e exposição no servidor. Um comprovante assinado, válido por 15 minutos, associa a revisão à consulta apresentada; a transação revalida os fatos antes de escrever. Consultas parciais, alteradas ou expiradas são recusadas.
- Reenvios do mesmo comprovante são idempotentes. Sobreposição com outro caso aberto é recusada. Escritas com resposta incerta orientam atualização da consulta, sem reenvio automático.
- Busca de sócios substitui contagens individuais por um `$lookup` no pipeline. Escopo vazio permanece vazio. Falhas de busca de pessoas são informadas sem eliminar resultados de empresas.
- Concentração usa busca vetorial exata no catálogo pequeno. Compara com a atividade de maior exposição individual; não afirma contar todos os negócios distintos.
- Alertas distinguem abertura e encerramento na mesma janela, preservam pendências em falhas transitórias e usam a exposição registrada no caso. Respostas atrasadas não sobrescrevem a seleção atual da interface.

## Evidências reproduzíveis

| Camada | Resultado | Evidência |
|---|---:|---|
| Backend offline: comprovantes, transações simuladas, limites, erros, eventos | 35 testes aprovados | `tests/test_hardening.py` |
| Inicialização concorrente do pool | 1 teste aprovado | `tests/test_client_concurrency.py` |
| Frontend: prazos HTTP, escrita incerta, caminho de evidência e relatório | 12 testes aprovados | `frontend/tests/*.test.mjs` |
| Navegador com falhas simuladas | 11 cenários aprovados | `tests/browser-offline-results.json` |
| API real: entradas hostis, consistência, busca, hierarquia, saúde | 43 verificações aprovadas | `tests/test_resilience.py --quick` |
| Atlas: transações e Change Streams em base isolada | 22 verificações aprovadas | `tests/live-hardening-results.json` |
| Navegador com Atlas: 1440, 768 e 360 px, respostas atrasadas e reconexão | 15 cenários aprovados | `tests/browser-results.json` |
| Instrumentos de medição: centavos, percentis, fixtures e planos | 4 testes aprovados | `tests/test_evidence_measurement.py` |
| Página de evidências: auditoria, planos, falhas e telas estreitas | 10 cenários aprovados | `tests/browser-evidence-results.json` |

A base `graph_resilience_test_<uuid>` foi criada exclusivamente para o teste e removida no bloco de limpeza. O dataset da demonstração não foi reiniciado nem alterado por esses testes. Foram exercitados seis pedidos concorrentes com o mesmo comprovante, mudança de exposição antes da revisão, caso sobreposto, fechamento repetido, ciclo societário e limite de nós.

Execução local:

```bash
backend/venv/bin/python -m unittest discover -s tests -p 'test_hardening.py'
backend/venv/bin/python -m unittest discover -s tests -p 'test_client_concurrency.py'
node --test frontend/tests/*.test.mjs
npm --prefix frontend run build
backend/venv/bin/python tests/test_resilience.py --quick
backend/venv/bin/python tests/live_hardening.py
backend/venv/bin/python tests/stress.py --max 64 --seconds 8 --out tests/stress-hardening-results.json
# Playwright e seu Chromium devem estar disponíveis:
node tests/browser-offline.cjs
node tests/browser-resilience.cjs
```

O build de produção passou. Há aviso de bundle JavaScript grande (~812 kB sem gzip, ~252 kB com gzip), principalmente pela biblioteca de grafo. Não foi feita uma mudança de biblioteca durante esta revisão.

## Carga e latência

A rodada inicial de 8 segundos por nível teve zero 5xx e zero falhas de conexão, mas **reprovou** o critério de visibilidade: p95 de 3.052,3 ms para teto de 3.000 ms, em 37 chamadas na concorrência 64. O resultado está preservado em `tests/stress-hardening-first-results.json`.

Sem alterar código nem limites, repetimos com 20 segundos por nível (1, 8, 32 e 64 clientes): **2.142 requisições**, zero 5xx, zero falhas de conexão e 276 respostas 429 de proteção sob saturação. Todos os critérios passaram. A rodada de 64 clientes teve 1.039 requisições, das quais 871 responderam 200 e 168 responderam 429; vazão total de 47,7 req/s, incluindo recusas.

| Caminho | p95 com 64 clientes |
|---|---:|
| Grupo | 1.331,8 ms |
| Visibilidade | 1.000,3 ms |
| Busca | 2.224,6 ms |
| Carteira | 2.248,1 ms |
| Concentração | 3.108,2 ms |

Percentis dos caminhos analíticos incluem respostas 429; não representam somente consultas concluídas. Grupo e visibilidade não têm essa recusa de admissão. Os totais consolidados permaneceram iguais e a saúde e o listener continuaram ativos após a carga. Evidência: `tests/stress-hardening-results.json`. Reprodução da repetição: trocar `--seconds 8` por `--seconds 20` no comando acima.

A variação entre as rodadas não demonstra causalidade nem elimina a primeira falha. O critério passou na amostra maior, mas a latência de visibilidade deve continuar sendo observada em apresentações com esta conexão e cluster compartilhado.

## Carga após a publicação — 30/09/2026

A versão servida nas portas 5350/8350 passou uma nova rodada de 20 segundos por nível de concorrência. Foram **2.589 requisições**, com 2.293 respostas 200 e 296 respostas 429. Não houve 5xx nem falha de conexão. Todos os critérios de latência passaram; os totais do grupo permaneceram iguais, `/health` continuou saudável e o Change Stream permaneceu ativo.

Na concorrência 64: 1.354 requisições, p95 do grupo de **933,3 ms**, visibilidade de **745,2 ms**, busca de **1.651,3 ms**, carteira de **1.940,0 ms** e concentração de **2.326,0 ms**. A vazão total foi 62,3 req/s, incluindo recusas. O mesmo cuidado sobre percentis analíticos que incluem 429 se aplica aqui.

Evidência: `tests/stress-release-results.json`. Essa rodada confirma os critérios do teste naquele ambiente; a diferença para a medição anterior não é atribuída a uma otimização de código.

## Limites da evidência

- Esses testes demonstram os cenários exercitados; não garantem resiliência total. Não foram provocados failover real do Atlas, exaustão de disco, interrupção do processo durante commit nem indisponibilidade prolongada.
- A validação transacional usa uma fotografia consistente. Não bloqueia edições concorrentes na topologia de `ownership` posteriores à fotografia. Produção com edição societária simultânea exige estratégia de versionamento/serialização.
- O comprovante garante integridade, não autenticação. O seletor de perfil continua uma simulação de alcance comercial, sem autorização global da API. Multiworker exige `INVESTIGATION_SIGNING_KEY` compartilhada.
- O resume token e os acumuladores dos alertas ficam em memória. Reinício pode perder eventos pendentes; não há entrega durável exatamente uma vez nem replay SSE por assinante. Assinantes lentos podem perder notificações; o estado do caso permanece consultável.
- Não foram validados transactions com `ownership` fragmentada; `$graphLookup` sobre coleção fragmentada dentro de transação é uma restrição do servidor.
- Latências incluem internet e aplicação, em M20 compartilhado. Resultados históricos de agosto não são uma comparação antes/depois destas mudanças. A eliminação de N+1 reduz viagens ao banco, mas não equivale a um percentual de ganho medido.


## Investigação auditável e curva publicada

A página `/evidence` apresenta uma fotografia de seis grupos, escolhidos com o mesmo critério do seletor da investigação. Cada um passou sete verificações: composição, arestas corporativas, exposição, cobertura, existência das entidades do manifesto, unicidade da exposição por empresa e caminho até a empresa em atraso. A referência usa leituras diretas e o manifesto do gerador, sem reaproveitar o resultado do traversal para calcular o esperado.

A curva contém 13 cenários, com dez execuções aquecidas em cada um. As árvores variam entre um e seis níveis e um a três filhos por empresa. O cenário adicional contém 1.201 empresas e 1.200 arestas em um nível. A resposta exibiu 1.200 empresas e 1.199 arestas, marcou alcance parcial e impediu a revisão. Isso verifica o teto de apresentação; não estabelece o limite físico do MongoDB.

Os planos preservam métricas por estágio. O contador do cursor inicial não é apresentado como total do pipeline, e métricas ausentes não são tratadas como zero. As amostras incluem rede e aplicação. Dez observações por ponto são uma exploração inicial da curva, não uma estimativa estável da cauda de produção.

Dados, amostras, hashes e planos: `frontend/public/evidence/results.json`. Reprodução: `backend/venv/bin/python queries/build_evidence.py --runs 10`. A base temporária da curva foi removida; a base da demonstração recebeu apenas leituras.

A comparação com PostgreSQL foi adiada por decisão do responsável pela POV. O protocolo está em `docs/comparison-protocol.md`; não há resultado comparativo publicado.

## Publicação local e mapa do código

O build de produção está servido em `localhost:5350`, com API em `localhost:8350` e Atlas como banco. A bateria de navegador foi executada sobre esse build, incluindo respostas inválidas do relatório, recuperação de falhas e os fluxos de investigação.

