# Fase 5 — Visão executiva

**Objetivo:** agregados por área e por processo para Workspace Admin, com
drill-down que respeita o acesso nativo. Última fase por decisão F23: só faz
sentido sobre dados que já têm fila, executor principal e disponibilidade.
**Pré-requisitos:** Gate 4.
**Referência:** RFC §9 (Fase 5), F18, F23, §11.
**Fora desta fase:** capability `executive_viewer` (A4).

---

### Executado em 06/09 — antes do gate, e o que saiu diferente

**A fase foi implementada com os Gates 2, 3 e 4 abertos**, como as anteriores,
pelo mesmo motivo: foi o que se pediu. E o Gate 5 tem um critério que só uma
pessoa fecha — a revisão com quem vai usar a página sobre quais indicadores
ficam e quais saem. Os dez que estão aqui são os do enunciado; nenhum foi
escolhido por conta própria.

Quatro desvios, nenhum reabrindo decisão fechada (RFC §4.2, rev. 8):

1. **População vazia responde `null`, não `0`.** Uma área que não segura nada
   não tem mediana de espera nem concentração. Zero leria como atendimento
   instantâneo e distribuição perfeita, que é o oposto do que a linha diz.
2. **O `auto_assign_kept_ratio` só conta reversão feita por pessoa.** Trabalho
   devolvido pela varredura de disponibilidade não é o ranking errando —
   contá-lo pioraria o número toda vez que a Fase 3 fizesse seu trabalho.
3. **O detalhamento não reaproveita `queue-list.tsx`.** É uma lista somente
   leitura própria (`executive-drilldown.tsx`): quem lê a página está
   respondendo "quais?", e oferecer reatribuir dali colocaria a decisão da área
   na mão errada. A fila continua sendo onde trabalho se move.
4. **A barra de throughput compara áreas, não é sparkline.** Um sparkline
   precisa de série diária, que é exatamente o 5.2 — desenhar tendência a
   partir de um número só seria inventá-la.

E o **5.2 continua aberto por medição, não por trabalho**: o gatilho do
enunciado é "só se 5.1 exceder 2 s em staging com dados reais". Não há staging
com dados reais alcançável daqui, e medir contra o dataset de teste
responderia outra pergunta.

---

## 5.1 — Endpoint de agregados `[x]`

`GET /api/orca/workspaces/{slug}/executive/` (Workspace Admin) com parâmetros
`period` (`7d`, `30d`, `90d`) e `unit` opcional. Retorna, por área:

| Indicador                          | Definição operacional                                                                    |
| ---------------------------------- | ---------------------------------------------------------------------------------------- |
| `backlog`                          | itens com área e estado nativo não concluído/cancelado                                   |
| `queued`                           | `routing_state in (queued, allocation_failed)`                                           |
| `assignment_overdue`               | `queued` com `assignment_due_at < now()`                                                 |
| `target_overdue`                   | `target_date < today` e não concluído                                                    |
| `queue_age_p50`, `queue_age_p90`   | percentis de `now() - queued_at` sobre `queued`                                          |
| `throughput`                       | itens da área que entraram em grupo `completed` no período                               |
| `cycle_time_p50`, `cycle_time_p90` | `completed_at - created_at` dos concluídos no período (usar `Issue.completed_at` nativo) |
| `concentration_top3`               | share dos três executores principais com mais itens abertos                              |
| `auto_assign_kept_ratio`           | decisões `least_loaded` não substituídas por humano / total, no período                  |

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

**Aberto, e o que falta é a medição.** O item é condicional por desenho, e a
condição é um número que só staging com dados reais produz — o dataset dos
testes tem 40 itens e responderia outra pergunta. Enquanto ninguém mede, criar
a tabela de snapshot seria otimizar um número que ninguém viu, e ela traria de
volta exatamente o que a Fase 5 evitou: uma linha que pode discordar da fila.

O que a implementação deixou pronto para a decisão: seis consultas por leitura
(contagens, idades, concluídos, concentração, ranking mantido, processos),
cache de cinco minutos por `(workspace, período, área)` e `?refresh=1` para
medir sem cache. `EXPLAIN ANALYZE` das consultas do
`docs/orca-executive-metrics.md` é o que responde.

---

## 5.3 — Interface `[x]`

- Página `:workspaceSlug/settings/organizational-units/executive` (só Admin; a rota já vive sob settings de workspace, que o Plane restringe a Admin).
- Tabela por área com os indicadores e sparkline simples de throughput (componentes de `@plane/propel`; se o repositório tiver biblioteca de gráficos já em uso, reutilizar; senão, barras em CSS). Sem biblioteca nova.
- Drill-down: clicar em um número abre a fila da área filtrada (reaproveita `queue-list.tsx`); itens de projetos aos quais o leitor não pertence aparecem apenas na contagem, com nota "n itens em projetos sem acesso".
- i18n completo.

---

## 5.4 — Testes com dataset fixo `[x]`

- Fixture que monta 3 áreas, 2 processos, 40 itens com datas controladas (`freezegun` ou manipulação de `created_at`/`completed_at`); cada indicador tem valor esperado calculado à mão no teste.
- Teste de acesso: Admin vê; Member recebe 403; drill-down omite itens de projeto sem acesso.

---

## Gate 5

- [ ] 3 dos 4 itens `[x]`; **5.2 aberto por falta de medição em staging**, que é a condição do próprio item. Verificado nesta sessão: 24 testes novos de dataset fixo, `check:types`/`check:lint`/`check:format` limpos e i18n 100% em 19 locales.
- [x] Cada número da página bate com uma consulta SQL reproduzível anotada em [`docs/orca-executive-metrics.md`](../../orca-executive-metrics.md) — dez indicadores por área e três de processo, cada um com a sua consulta e o motivo da definição.
- [ ] Revisão com quem vai usar (CEO/diretoria) sobre quais indicadores ficam e quais saem; registrar no RFC §4.2. Os dez implementados são os do enunciado desta fase; tirar ou acrescentar um é decisão de quem lê a página.

Data do gate: \_\_\_\_
