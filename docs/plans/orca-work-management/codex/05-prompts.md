# Prompts do Codex — Fase 5 (Visão executiva)

Plano da fase: [`../05-executive-view.md`](../05-executive-view.md).
Contexto obrigatório: [`00-context.md`](./00-context.md).

Pré-requisito: **Gate 4**. Ordem: 5.1 → 5.2 (talvez descartado) → 5.3 → 5.4.
Por decisão F23, esta fase é a última: agregado sobre dado que ainda não tem
fila, executor principal e disponibilidade mede o nada.

| Item | Perfil | Risco |
| --- | --- | --- |
| 5.1 | `heavy` | médio (SQL de percentil e cache) |
| 5.2 | `standard` | baixo — só existe se 5.1 for lento |
| 5.3 | `standard` | baixo |
| 5.4 | `standard` | médio (é o que prova os números) |

---

## 5.1 — Endpoint de agregados

```text
Você vai implementar o item 5.1 do plano Orca. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §9 (Fase 5), §11 (observabilidade), decisões
   F18 e F23 em §3.
3. docs/plans/orca-work-management/05-executive-view.md, seção "5.1" — a tabela de
   indicadores com a DEFINIÇÃO OPERACIONAL de cada um. Implemente exatamente essas
   definições; se alguma for ambígua no seu entendimento, pergunte antes de escolher.
4. apps/api/plane/app/views/organizational_unit.py e as permissões de workspace.

TAREFA
1. GET /api/orca/workspaces/{slug}/executive/, só Workspace Admin, com parâmetros
   period (7d, 30d, 90d) e unit opcional.
2. Por área, os indicadores da tabela do plano: backlog, queued, assignment_overdue,
   target_overdue, queue_age_p50/p90, throughput, cycle_time_p50/p90,
   concentration_top3, auto_assign_kept_ratio.
   Por processo (ProcessInstanceReference): instâncias running/completed no período,
   lead_time_p50/p90 e as etapas mais atrasadas.
3. Implementação em apps/api/plane/app/services/orca/executive_metrics.py, com
   annotate/aggregate. Percentis via percentile_cont do Postgres (Func custom ou
   RawSQL parametrizado — nunca interpolação de string com valor vindo do request).
4. Cache de 5 minutos por (workspace, period, unit) com o cache nativo do Django.
   Invalidação: só expiração. Documente isso na docstring.
5. Cada consulta com um comentário dizendo qual linha da tabela ela implementa.
6. Testes de forma (a prova numérica é o item 5.4): permissão (Admin 200, Member 403),
   parâmetros inválidos → 400, cache é usado na segunda chamada.

DEFINIÇÃO DE PRONTO
- Nenhuma consulta N+1; nenhum cálculo em Python sobre queryset inteiro que caberia
  no banco. Diga na resposta quantas queries o endpoint faz (assertNumQueries).
- Nenhuma SQL construída por concatenação de entrada do usuário.
- ruff limpo.

NÃO FAÇA
- Não materialize nada aqui (isso é 5.2, e só se for necessário).
- Não invente indicador fora da tabela.

AO TERMINAR
- Marque 5.1 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [5.1] add the executive metrics endpoint
- Responda no formato da seção 10 do 00-context.md, com o tempo medido por consulta
  no dataset de teste.
```

---

## 5.2 — Materialização, se necessário

```text
Você vai avaliar o item 5.2 do plano Orca. ATENÇÃO: este item só é implementado se
o 5.1 exceder 2 s em staging com dados reais. Comece medindo; se estiver abaixo,
o entregável é a MEDIÇÃO e o descarte do item.

LEIA ANTES
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/plans/orca-work-management/05-executive-view.md, seção "5.2".
3. O código de 5.1.

TAREFA
1. Meça o endpoint com o dataset disponível (peça ao desenvolvedor a medição em
   staging se você não puder rodar; diga o comando exato).
2. Se < 2 s: marque o item como [-] no arquivo da fase, com o tempo medido, a data e
   o dataset. Não implemente nada. Esse é um resultado legítimo e é o desejado.
3. Se >= 2 s: tarefa noturna que grava OrcaExecutiveSnapshot(workspace, unit, day,
   metrics JSON); o endpoint lê o snapshot para períodos > 7d e calcula ao vivo para
   7d. Migração própria, flag para desligar, teste de que snapshot e cálculo ao vivo
   dão o MESMO número para o mesmo período.

DEFINIÇÃO DE PRONTO
- A decisão está registrada com número medido, não com impressão.

NÃO FAÇA
- Não implemente a materialização "por precaução".

AO TERMINAR
- Marque 5.2 [x] ou [-] com o motivo e atualize a contagem no README do plano.
- Commit: docs(orca): [5.2] record the executive endpoint latency and skip materialization
  (ou feat(orca): [5.2] materialize executive metrics nightly)
- Responda no formato da seção 10 do 00-context.md.
```

---

## 5.3 — Interface da visão executiva

```text
Você vai implementar o item 5.3 do plano Orca. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/plans/orca-work-management/05-executive-view.md, seção "5.3".
3. queue-list.tsx e a aba de fila (2.3) — o drill-down reaproveita esses componentes.
4. packages/propel — o que existe de gráfico/sparkline. Se não houver biblioteca de
   gráficos já em uso no repositório, use barras em CSS. NÃO adicione biblioteca nova.

TAREFA
1. Página :workspaceSlug/settings/organizational-units/executive, só Admin (a rota
   vive sob settings de workspace, que o Plane já restringe — mas a página confere
   de novo).
2. Tabela por área com os indicadores e uma sparkline simples de throughput.
3. Drill-down: clicar em um número abre a fila da área filtrada, reaproveitando
   queue-list.tsx. Itens de projetos aos quais o LEITOR não pertence aparecem apenas
   na contagem, com a nota "n itens em projetos sem acesso" — nunca com título,
   identificador ou executor. Essa é uma regra de privacidade, não de layout.
4. i18n completo em todas as locales, incluindo formato de número e data por locale.

DEFINIÇÃO DE PRONTO
- Nenhum dado de item inacessível vaza para o drill-down (teste isso, não só olhe).
- Nenhuma dependência nova no package.json.
- check:lint/check:types/check:sync — comandos para o desenvolvedor.

NÃO FAÇA
- Não crie a capability executive_viewer (decisão A4, aberta): por ora, só Admin.

AO TERMINAR
- Marque 5.3 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [5.3] add the executive view with unit drill-down
- Responda no formato da seção 10 do 00-context.md.
```

---

## 5.4 — Testes com dataset fixo

```text
Você vai implementar o item 5.4 do plano Orca. Só este item. Ele é o que dá direito
de alguém tomar decisão olhando esses números.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/plans/orca-work-management/05-executive-view.md, seções "5.4" e "Gate 5".
3. O serviço de 5.1 e a tabela de definições operacionais.

TAREFA
1. Fixture com dataset FIXO: 3 áreas, 2 processos, 40 itens com datas controladas
   (freezegun, ou created_at/completed_at manipulados). Nada aleatório.
2. Para CADA indicador, o valor esperado é calculado à mão no teste, com o cálculo
   escrito em comentário (ex.: "queued = 7: itens 3,5,9,12,18,22,31"). Se o código e
   a conta divergirem, o defeito pode estar na conta: investigue antes de mudar o código.
3. Teste de acesso: Admin vê; Member recebe 403; o drill-down omite itens de projeto
   sem acesso e a contagem continua correta.
4. docs/orca-executive-metrics.md: para cada indicador, a consulta SQL reproduzível
   que um humano pode rodar no banco para conferir o número da tela.

DEFINIÇÃO DE PRONTO
- Cada número da página tem, no documento, uma SQL que o reproduz.
- Nenhum valor esperado no teste veio de rodar o código e copiar a saída.

NÃO FAÇA
- Não feche o Gate 5 (é humano): ele exige revisão com quem vai usar os indicadores.

AO TERMINAR
- Marque 5.4 [x] e atualize a contagem no README do plano.
- Commit: test(orca): [5.4] pin the executive metrics to a hand-computed dataset
- Responda no formato da seção 10 do 00-context.md.
```
