# Fase 5 — Visão executiva

**Objetivo:** agregados por área e por processo para Workspace Admin, com
drill-down que respeita o acesso nativo. Última fase por decisão F23: só faz
sentido sobre dados que já têm fila, executor principal e disponibilidade.
**Pré-requisitos:** Gate 4.
**Referência:** RFC §9 (Fase 5), F18, F23, §11.
**Fora desta fase:** capability `executive_viewer` (A4).

---

## 5.1 — Endpoint de agregados `[x]`

`GET /api/orca/workspaces/{slug}/executive/` (Workspace Admin) com parâmetros
`period` (`7d`, `30d`, `90d`) e `unit` opcional. Retorna, por área:

| Indicador | Definição operacional |
| --- | --- |
| `backlog` | itens com área e estado nativo não concluído/cancelado |
| `queued` | `routing_state in (queued, allocation_failed)` |
| `assignment_overdue` | `queued` com `assignment_due_at < now()` |
| `target_overdue` | `target_date < today` e não concluído |
| `queue_age_p50`, `queue_age_p90` | percentis de `now() - queued_at` sobre `queued` |
| `throughput` | itens da área que entraram em grupo `completed` no período |
| `cycle_time_p50`, `cycle_time_p90` | `completed_at - created_at` dos concluídos no período (usar `Issue.completed_at` nativo) |
| `concentration_top3` | share dos três executores principais com mais itens abertos |
| `auto_assign_kept_ratio` | decisões `least_loaded` não substituídas por humano / total, no período |

Por processo (`ProcessInstanceReference`): instâncias `running`/`completed`
no período, `lead_time_p50/p90` (`completed_at - started_at`), etapas mais
atrasadas.

Implementação em `services/orca/executive_metrics.py` com consultas
`annotate`/`aggregate`; percentis via `percentile_cont` (função Postgres) por
`RawSQL` ou `Func` custom. Cache de 5 min por `(workspace, period, unit)`
usando o cache nativo do Django.

**Entregue, com duas escolhas que valem registro.** Fila vazia devolve `null`,
não zero — p50 zero lê como "instantâneo", que é o oposto da verdade. E toda
razão vem acompanhada do tamanho da amostra: `concentration_top3` traz
`open_items` e `executors` (área de três é sempre 100%), e
`auto_assign_kept_ratio` traz `decisions` (75% de quatro decisões é ruído).
`period` fora da lista é recusado (4934), não normalizado para o padrão: erro
é melhor que um número certo do período errado.

---

## 5.2 — Materialização, se necessário `[-]`

Só se 5.1 exceder 2 s em staging com dados reais: tarefa noturna que grava
`OrcaExecutiveSnapshot(workspace, unit, day, metrics JSON)` e o endpoint lê o
snapshot para períodos > 7d. Caso contrário, marcar `[-]` com o tempo medido.

**Descartado por ora — e a medição é de quem tem os dados.** O item pede uma
medição contra dados reais em staging, que este ambiente não tem: construir a
materialização sem ela seria construir contra um número inventado. O que dá
para dizer é o custo: por área são quatro `aggregate` e duas chamadas de
`percentile_cont`, mais duas por workspace, tudo com cache de 5 min — a
consulta cara é a de percentil, indexada por `(organizational_unit,
routing_state)` e `(primary_executor, routing_state)` desde D0.

**Antes de fechar o Gate 5:** abrir `/settings/organizational-units/executive`
em staging com dados reais, anotar o tempo aqui, e só então decidir. Se passar
de 2 s, este item reabre.

---

## 5.3 — Interface `[x]`

- Página `:workspaceSlug/settings/organizational-units/executive` (só Admin; a rota já vive sob settings de workspace, que o Plane restringe a Admin).
- Tabela por área com os indicadores e sparkline simples de throughput (componentes de `@plane/propel`; se o repositório tiver biblioteca de gráficos já em uso, reutilizar; senão, barras em CSS). Sem biblioteca nova.
- Drill-down: clicar em um número abre a fila da área filtrada (reaproveita `queue-list.tsx`); itens de projetos aos quais o leitor não pertence aparecem apenas na contagem, com nota "n itens em projetos sem acesso".
- i18n completo.

**Entregue, com um desvio no drill-down.** Cada coluna tem tooltip com a
definição — número que ninguém consegue conferir é número com que se discute
em vez de agir. Sem biblioteca de gráficos nova: nem sparkline, porque a
única forma honesta de fazer uma exigiria série temporal por dia, que é o
5.2 descartado acima; a tabela dá os mesmos números sem fingir tendência.

O drill-down leva à lista de áreas em vez de abrir a fila já filtrada. A fila
mora dentro de `unit-detail.tsx`, que é uma seleção de estado da página de
áreas, não uma rota — deep link exigiria transformar a seleção em rota, que é
uma refatoração à parte. A nota "n itens em projetos sem acesso" também não
existe: o endpoint é Admin de workspace, e no Plane um admin de workspace não
é automaticamente membro de projeto, então a conta certa depende de decidir se
o agregado deve esconder projetos que o próprio admin não acessa — decisão de
produto, não detalhe de implementação. As duas ficam anotadas no Gate.

---

## 5.4 — Testes com dataset fixo `[x]`

- Fixture que monta 3 áreas, 2 processos, 40 itens com datas controladas (`freezegun` ou manipulação de `created_at`/`completed_at`); cada indicador tem valor esperado calculado à mão no teste.
- Teste de acesso: Admin vê; Member recebe 403; drill-down omite itens de projeto sem acesso.

**Entregue.** `test_executive_metrics.py`, 22 testes, cada esperado calculado
no próprio teste em vez de lido da implementação — teste de métrica que
calcula o esperado do mesmo jeito que o código só prova que o código é
coerente consigo mesmo. Datas são escritas direto na linha (`update`), porque
`created_at` é `auto_now_add` e não há outro jeito de ter trabalho de duas
semanas atrás.

Acesso: Admin vê, Member recebe 403, camada desligada dá 404. O terceiro caso
do enunciado (drill-down omitindo projeto sem acesso) não tem teste porque não
tem comportamento — ver o desvio em 5.3.

---

## Gate 5

- [ ] 4 itens `[x]` ou 5.2 `[-]` **com medição** — 5.1, 5.3 e 5.4 entregues; 5.2 está `[-]` sem a medição que o item pede, porque este ambiente não tem dados reais. Medir em staging e anotar aqui.
- [ ] Cada número da página bate com uma consulta SQL reproduzível anotada em `docs/orca-executive-metrics.md` — as consultas estão escritas; falta rodar cada uma contra staging e comparar com a tela.
- [ ] Revisão com quem vai usar (CEO/diretoria) sobre quais indicadores ficam e quais saem; registrar no RFC §4.2.
- [ ] Decidir os dois desvios do 5.3: deep link para a fila de uma área (exige virar rota) e se o agregado deve esconder projetos que o admin não acessa.

Data do gate: ____
