# Interface e fluxos

A interface é em **português do Brasil**: é apresentada a bancos brasileiros,
e CNPJ, razão social, sócio e rating são as palavras que esses times usam. A
documentação do repositório fica em inglês (exceto este conjunto de
arquivos, que segue o padrão do resto de `docs/briefing/`).

O design visual segue um conjunto de tokens compartilhado entre os projetos
de demonstração do workspace: fundo escuro, verde para ação/sucesso, azul
para informação, âmbar para aviso, vermelho para risco.
`frontend/src/pov-signature.css` carrega essa assinatura e é importado depois
do stylesheet da aplicação — é **byte a byte idêntico** ao das outras PoVs do
workspace; alterar aqui obriga sincronizar todas (ver `POV_UI_DESIGN_SYSTEM.md`
na raiz do workspace).

## Componentes principais (frontend/src/)

| Arquivo | Papel |
|---|---|
| `App.jsx` (1058 linhas) | componente raiz — estado de toda a tela, orquestra as chamadas à API, monta as abas do inspector |
| `GraphCanvas.jsx` (730 linhas) | o canvas `vis-network`: layout, interação, hop sweep, drag/reacomodação |
| `QueryDetails.jsx` (20 linhas) | painel `<details>` que mostra a pipeline/query executada |
| `api.js` | wrapper de chamadas HTTP ao backend |
| `theme.js` | tokens de tema |
| `pov-signature.css` | assinatura visual compartilhada entre PoVs — não editar isoladamente |

## Modo palco (stage mode)

Uma tela só. O primeiro viewport carrega **uma tese** (uma empresa sozinha
parece pequena e limpa), **uma ação** (a profundidade do traversal) e **uma
evidência** (o grafo do grupo com a exposição consolidada e o tempo de
resposta medido).

Tudo que não decide nada para o apresentador fica fora do caminho: a query
executada vive num `<details>` (`QueryDetails.jsx`), e os painéis de Busca,
Concentração, Visibilidade e Alertas ficam ao lado do grafo, em abas, na
ordem do roteiro.

## Layout — uma tela, sem scroll

```
┌─ topbar 52px: marca · tese em uma linha · saúde ────────────────────────┐
├──────────┬────────────────────────────────────────┬────────────────────┤
│ controles│ métricas: nós · arestas · no anel ·     │ inspector (abas)   │
│          │           bloqueados · tempo · hop      │ ┌────────────────┐ │
│ entrada  │ ┌────────────────────────────────────┐  │ │Empresa│Busca│Concentração│Visibilidade│Alertas│ │
│ profund. │ │                                    │  │ ├────────────────┤ │
│ arestas  │ │   canvas (ocupa a altura restante) │  │ │ detalhe do nó  │ │
│ ação     │ │            [+] [−] [⤢]              │  │ │ chips vizinhos │ │
│          │ └────────────────────────────────────┘  │ └────────────────┘ │
│          │                                         │ ▸ query executada  │
└──────────┴────────────────────────────────────────┴────────────────────┘
```

`body { overflow: hidden }` e `.app { height: 100dvh }`: a página nunca rola.
Só o trilho de controles e o corpo do inspector rolam internamente, quando
precisam. As abas do inspector, confirmadas em `App.jsx` (linha 35 e
461-755): `company` (Empresa), `search` (Busca), `semantic` (Concentração),
`visibility` (Visibilidade), `alerts` (Alertas).

Uma versão anterior empilhava grafo, busca, vetorial e alertas numa coluna
de 2.600 px — o apresentador rolava para achar cada evidência e perdia o
grafo de vista. As três evidências viraram abas do inspector, ao lado do
grafo, que fica visível o tempo todo.

## Interação com o grafo (`GraphCanvas.jsx`)

Quatro padrões emprestados de ferramentas de investigação de grafo:

- **Destaque de vizinhos.** Passar o mouse num nó acende seus vizinhos
  diretos e escurece o resto a ~16% de opacidade. O nó em foco **mantém a
  cor do tipo** e ganha borda azul — trocar o preenchimento apagaria a
  informação "isto está num anel".
- **Revelação progressiva.** Sem tooltip nativo: o detalhe do nó (hops,
  grau, contas e quando foram abertas, flags de risco, `_id`) vive na aba
  **Empresa** do inspector, junto com os vizinhos diretos como atalhos
  clicáveis.
- **Sweep de hop, de uma vez só.** No primeiro desenho, a cor viaja para
  fora a partir da empresa consultada, um hop topológico de cada vez. Os
  nós nunca se movem, o sweep termina em ~meio segundo e qualquer interação
  do usuário cancela. Explica o traversal sem transformar um gráfico
  determinístico de propriedade em demo de força física — deliberadamente
  **não** apresentado como um trace de execução interna do `$graphLookup`.
- **Reacomodação local no arrasto.** Física permanentemente desligada. Uma
  passada de colisão determinística curta move só os nós que se sobrepõem
  ao nó largado; os níveis societários e toda posição não relacionada ficam
  estáveis.

Tamanho da empresa segue o limite de crédito, não o grau do nó: é uma
decisão de crédito, então o peso visual segue o dinheiro. Empresas são
caixas; sócios pessoa física são círculos. O sujeito da consulta tem borda
verde, holdings borda azul, empresas inadimplentes borda vermelha, empresas
em revisão borda tracejada.

Arestas ficam discretas em repouso: 1,8 px para participação societária e
1,5 px para participação individual, ambas a 50% de opacidade. Os rótulos de
percentual só aparecem quando a relação está em contexto (hover, nó fixado
ou destaque vindo do inspector). Foco aumenta opacidade e espessura; a cor
segue distinguindo corporativo (azul) de individual (âmbar).

Controles de enquadramento (`+`, `−`, `⤢`) ficam sobre o canvas: depois de
arrastar ou dar zoom, o apresentador volta ao enquadramento sem recarregar.

## Arrastar move o que você arrastou, e só isso

A primeira versão ligava o solver de física no `dragStart` e congelava
quando o motor assentava. Lê bem como descrição e se comporta mal na mão:
mover um nó colocava o grafo inteiro em movimento, e o analista perdia o
mapa espacial que tinha acabado de construir — em demo isso lê como
instabilidade.

Física agora fica desligada permanentemente. O nó arrastado fica exatamente
onde foi largado, e uma passada local empurra **só** os nós em que ele caiu
em cima, pela distância mínima, com cascata curta (dois níveis) e teto de
deslocamento por nó. Medido no grupo de demonstração: arrastar uma empresa
move 4 de 35 nós — o arrastado e os três que precisaram sair do caminho.

Uma segunda passada roda uma vez após o layout inicial, e só move nós no
**eixo X**, dentro de cada nível. Posição vertical é o nível societário;
empurrar um nó para cima ou para baixo para corrigir sobreposição trocaria
um problema cosmético por uma afirmação falsa sobre quem controla quem.

`window.__grafo` em `GraphCanvas` é um gancho de teste (só leitura de
posições) — é o que permite afirmar "só o nó arrastado saiu do lugar" sem
inspeção visual humana.

## Fixar um nó, e por que hover não pode vencer

O painel da direita resolvia o nó como `hovered ?? picked`: hover **sempre**
vencia o clique. Na prática, clicar num nó era inútil — mover o mouse até o
painel trocava o conteúdo, e o analista nunca conseguia ler o nó escolhido.

A prioridade agora é `picked ?? hovered`. Clicar fixa; hover só preenche o
painel quando nada está fixado, e continua isolando vizinhos no canvas de
qualquer jeito. O nó fixado ganha borda branca grossa no grafo e um chip
`pinned ✕` no painel — sem uma marca permanente, ao olhar de volta para o
grafo o analista não saberia mais o que estava lendo.

`pinned` vive num `useRef` dentro de `GraphCanvas`, fora das dependências do
`useEffect` que cria o `Network`. Colocá-lo na lista de dependências
reconstruiria o grafo a cada clique, jogando fora o layout que a física
tinha acabado de assentar.

## O caso, sem uma nova aba

O resultado da transação ACID vive na **coluna esquerda**, sob o botão que a
disparou. Uma nova aba seria mais uma tela para o apresentador lembrar de
abrir no meio da demo, e o efeito da ação precisa aparecer onde a ação
aconteceu.

O card mostra contas bloqueadas, pessoas sinalizadas, tempo de commit,
`snapshot`/`majority` e o antes/depois das primeiras contas
(`active` → bloqueada). No canvas, nós sinalizados ganham **borda
tracejada**, e uma métrica "Bloqueados" aparece na barra superior.

O relatório de compliance (COAF / MED / LGPD) fica num `<details>` fechado
que só chama a API quando aberto — responde "e o que o banco faz com isso?",
pergunta que nem toda audiência faz.

Se já existe um caso aberto sobre esses nós, o backend recusa com `409`
carregando o `case_id` aberto. A tela não mostra erro e para: traz esse caso
para frente, com o botão de encerrar ao lado.

## O A/B do alerta

`Encerrar caso` e `Reiniciar demo` são botões, não só endpoints. Provam algo
que antes era difícil de mostrar: o mesmo mecanismo produzindo o alerta **e**
sua contrapartida, para o silêncio ter forma.

O gatilho é a própria revisão de crédito. Abrir uma marca todas as empresas
do grupo numa única transação; o change stream em `companies` pega esses
documentos, coalesce por `case_id`, e a aba Alertas recebe um evento com a
contagem real de empresas e a exposição agora sob revisão. Encerrar o caso
emite `review_closed` pelo mesmo caminho.

Esse pareamento é o argumento: o listener lê estado em vez de disparar
sozinho, e o apresentador prova isso ao vivo em cerca de um segundo de cada
lado. O evento carrega `stream_ms` — do commit à chegada, incluindo a
janela de coalescência — porque mostrar um número menor do que o vivido
seria ficção de demo.

## O número de negócio, sem tela nova

Duas adições, ambas dentro de superfícies que já existiam:

- uma tile **"Em risco"** na barra de métricas, mostrando o volume que a
  rede em tela movimentou. Aparece só quando a expansão contém membros do
  anel;
- um bloco **"O que este caso vale"** dentro do card do caso, recolhido por
  padrão.

`POST /api/exposure` é uma chamada separada feita *depois* que o grafo já
desenhou. A agregação sobre `transactions` custa cerca de um segundo, e
prendê-la ao traversal desaceleraria exatamente o passo sendo demonstrado ao
vivo. A metade medida e a metade assumida são separadas visualmente: volume,
operações e janela vêm do dado; horas e custo por caso vêm do `.env`
(`ANALYST_HOURS_PER_CASE`, `ANALYST_COST_PER_HOUR`) e são números do banco.
Perda evitada é deliberadamente não estimada — ver
[`../business-case.md`](../business-case.md).

## Manter o trilho livre de scroll

O card do caso cresceu três blocos recolhíveis (o que a transação mudou, o
que o caso vale, o que o banco precisa fazer agora), e com todos expandidos
o trilho de controles chegava a 1.687 px contra um viewport de 922 px.

Duas mudanças corrigiram, ambas medidas contra a altura de scroll do
próprio trilho: o card mostra só o resumo (contas bloqueadas, pessoas, tempo
de commit, garantia) num grid de duas colunas, com o resto atrás de um
`<summary>`; e o botão **Sinalizar** desaparece enquanto um caso está aberto
(o backend já recusa um segundo caso sobre os mesmos nós, então o botão só
ocupava altura à toa). Medido depois: 922 px com caso aberto e tudo
recolhido — exatamente o viewport, sem scroll.

## Canal de eventos fechado não finge reconexão

O backend aceita até 64 conexões SSE simultâneas; acima disso responde `503`.
O `EventSource` não tenta de novo depois de uma resposta que não é
`text/event-stream`, então a tela mostra "Canal de eventos: indisponível" em vez
de "reconectando". Os alertas persistidos seguem em `/api/alerts/recent`.

## Nada além de um alerta rouba a aba

Todo evento SSE chamava `setTab('alerts')`. Durante um `update_many` grande a
interface pulava para Alertas a cada poucos segundos e ficava impossível
digitar numa caixa de busca. Hoje só `review_opened` troca de aba (`App.jsx`
linha 104); `review_closed` fica na lista sem sequestrar a tela. O `$match`
do lado do servidor fecha o mesmo buraco pelo outro lado: o listener só
publica quando `credit_status` muda de verdade, então uma carga em lote
nunca chega ao SSE.

## Escopo de busca

Buscar "Diego" numa base de 150 mil pessoas devolvia dez Diegos aleatórios, e
o Diego que estava em tela não aparecia entre eles. Não era problema de
ordenação: `$search` corta para o mais relevante **antes** de qualquer
anotação, e reordenar não conserta o que nunca chegou.

Os dois painéis ganharam um segmento de escopo, com padrões diferentes por
motivos diferentes:

| Painel | Padrão | Por quê |
|---|---|---|
| Atlas Search | `base` | o gêmeo com grafia errada **não** está na rede; escopar sempre mataria o passo 7 do roteiro |
| Vector Search | `rede` | solto sobre a base inteira o painel é curiosidade; escopado, responde "qual desculpa essas contas usam?" |

Em modo `base`, `resolve_entity` roda uma segunda passagem escopada à rede e
a coloca primeiro. Todo resultado carrega `in_group`/`na_rede`, e a lista
mostra a tag.

## O painel de visibilidade

O segundo cenário não precisou de tela nova: é uma aba ao lado das outras, e
tem três coisas — lista de usuários de exemplo, escopo e carteira de quem
está selecionado, e o veredito para a empresa atualmente no grafo.

**A lista de usuários é uma lista de botões, não um dropdown.** O ponto é o
*contraste* entre gerente e assessor, e um dropdown esconde a outra opção
atrás de um clique. Cada linha é um `<button>` de verdade, acessível por
teclado/leitor de tela.

**O veredito segue a empresa já em tela.** Selecionar um usuário reavalia o
CNPJ em análise, então a fronteira é demonstrada na mesma empresa que o
cliente vem olhando há cinco minutos, não num exemplo abstrato.

## Toda lista aponta para o grafo

Os painéis do trilho não são listas somente-leitura. Clicar numa linha
define um destaque: os nós referidos ganham borda em ênfase, o resto
escurece, e o canvas centraliza no conjunto. Clicar de novo na mesma linha
libera; trocar empresa, profundidade ou aba limpa o destaque.

Dois detalhes de segunda passada:

- **Zoom é limitado.** `fit({nodes})` num único nó dá zoom máximo e o
  contexto desaparece — agora centraliza no conjunto e segura a escala
  entre 0,55 e 0,9.
- **Resultado fora do grafo não faz nada** no canvas — piscar a tela para um
  resultado sem nó correspondente seria pior que não reagir.

## A fronteira de visibilidade é desenhada no grafo

Selecionar um usuário na aba Visibilidade escurece as empresas do grupo que
ficam fora do escopo desse usuário. Escurecidas, não escondidas: a empresa
**está** no grupo econômico — o que muda é quem pode vê-la. Esconder
responderia uma pergunta diferente e distorceria o dado silenciosamente.

## Estados

| Estado | O que a tela faz |
|---|---|
| Backend fora do ar | badge `✕ backend offline`; o canvas diz o que aconteceu |
| Índice Search/Vector `BUILDING`/`MISSING` | badge no painel daquele recurso e aviso explicando; o grafo continua funcionando |
| Sem `VOYAGE_API_KEY` | mesmo caminho, com status `NO_EMBEDDING_KEY` |
| Ponto de entrada sem vínculos | canvas vazio com texto, não um spinner infinito |
| Resultado truncado | aviso apontando para `LIMITATIONS.md §4`, com a contagem real de arestas encontradas |
| Sem alertas ainda | texto dizendo qual ação produz um, não um vazio mudo |
| Revisão em profundidade rasa | aviso dizendo quantas empresas essa expansão de fato sinalizaria |
| Busca sem resultado dentro do grupo | avisa e oferece a caixa de seleção que abre a busca para a base inteira |
| Consulta analítica saturada (429) | painel diz "saturado, tente de novo", **nunca** "indisponível" — confundir backpressure com queda manda o apresentador debugar a coisa errada |
| Conta fora do escopo do usuário | painel de visibilidade diz **não visível** e dá a razão, nomeando o assessor dono da conta |

Nenhum estado é comunicado só por cor: empresa em revisão tem borda
tracejada além da métrica, o sujeito da consulta é rotulado no painel do nó,
e o veredito de visibilidade é uma frase, não uma cor.

## Streaming

Alertas chegam via `EventSource` em `/api/alerts/stream`. O backend manda um
heartbeat a cada 15 s para o navegador não considerar a conexão ociosa
morta; `EventSource` reconecta sozinho. A UI mantém os 20 eventos mais
recentes.

## Screenshots referenciados (`docs/screenshots/`)

| Arquivo | Passo do roteiro |
|---|---|
| `01-empresa-sozinha.png` | profundidade 1 — a empresa sozinha, nada de errado à vista |
| `02-grupo-economico.png` | profundidade elevada, revelando o grupo e a inadimplência num ramo distante |
| `03-entity-resolution.png` | Atlas Search resolvendo um sócio com grafia divergente |
| `04-concentracao-semantica.png` | painel de concentração — CNAEs distintos, negócio único |
| `05-visibilidade-gerente.png` | escopo do gerente na aba Visibilidade |
| `06-visibilidade-assessor.png` | escopo do assessor, e a recusa de ver conta de outro ramo |
| `07-acid-e-alerta.png` | card do caso aberto (transação ACID) e o alerta chegando pelo change stream |

## Roteiro de demo (resumo)

Versão completa, com checklist pré-demo, em
[`../demo-script.md`](../demo-script.md):

1. escolher solicitante, começar na profundidade 1 (nada de errado);
2. subir a profundidade no grupo de seis níveis — cada passo revela empresa
   nova, até 43 na profundidade 6, com inadimplência num ramo distante;
3. painel de exposição consolidada;
4. Atlas Search com nome de sócio escrito diferente, escopado ao grupo;
5. Concentração — equivalência semântica com a atividade de maior exposição;
6. o sócio-ponte entre dois grupos que o cadastro trata como não
   relacionados;
7. **Abrir revisão** — transação ACID, card do caso, nós tracejados;
8. aba Alertas — o change stream já entregou o evento coalescido;
9. **Encerrar caso** — o evento de contrapartida chega pelo mesmo caminho;
10. Visibilidade: gerente, depois assessor, depois a recusa na mesma
    empresa;
11. fechamento em arquitetura: um sistema a menos para operar, sincronizar
    e proteger.

## Resiliência integrada na chamada HTTP

Chamada HTTP completa limitada a 120 s; o timer é liberado e a falha é
sinalizada sem reenviar a operação. Está integrada em `main` — o estado
vigente de validação está em `../../REVIEW.md`, e mudanças de resiliência de
frontend têm regressão em `cd frontend && node --test tests/*.test.mjs`
(`frontend/tests/http-deadline.test.mjs`).
