# Fase 5 — Visão executiva

**Objetivo:** agregados por área e por processo para Workspace Admin, com
drill-down que respeita o acesso nativo. Última fase por decisão F23: só faz
sentido sobre dados que já têm fila, executor principal e disponibilidade.
**Pré-requisitos:** Gate 4.
**Referência:** RFC §9 (Fase 5), F18, F23, §11.
**Fora desta fase:** capability `executive_viewer` (A4).

---

## 5.1 — Endpoint de agregados `[ ]`

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

---

## 5.2 — Materialização, se necessário `[ ]`

Só se 5.1 exceder 2 s em staging com dados reais: tarefa noturna que grava
`OrcaExecutiveSnapshot(workspace, unit, day, metrics JSON)` e o endpoint lê o
snapshot para períodos > 7d. Caso contrário, marcar `[-]` com o tempo medido.

---

## 5.3 — Interface `[ ]`

- Página `:workspaceSlug/settings/organizational-units/executive` (só Admin; a rota já vive sob settings de workspace, que o Plane restringe a Admin).
- Tabela por área com os indicadores e sparkline simples de throughput (componentes de `@plane/propel`; se o repositório tiver biblioteca de gráficos já em uso, reutilizar; senão, barras em CSS). Sem biblioteca nova.
- Drill-down: clicar em um número abre a fila da área filtrada (reaproveita `queue-list.tsx`); itens de projetos aos quais o leitor não pertence aparecem apenas na contagem, com nota "n itens em projetos sem acesso".
- i18n completo.

---

## 5.4 — Testes com dataset fixo `[ ]`

- Fixture que monta 3 áreas, 2 processos, 40 itens com datas controladas (`freezegun` ou manipulação de `created_at`/`completed_at`); cada indicador tem valor esperado calculado à mão no teste.
- Teste de acesso: Admin vê; Member recebe 403; drill-down omite itens de projeto sem acesso.

---

## Gate 5

- [ ] 4 itens `[x]` ou 5.2 `[-]` com medição.
- [ ] Cada número da página bate com uma consulta SQL reproduzível anotada em `docs/orca-executive-metrics.md`.
- [ ] Revisão com quem vai usar (CEO/diretoria) sobre quais indicadores ficam e quais saem; registrar no RFC §4.2.

Data do gate: ____
