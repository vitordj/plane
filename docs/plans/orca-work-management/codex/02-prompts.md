# Prompts do Codex — Fase 2 (Fila da área e coordenador)

Plano da fase: [`../02-queue-and-coordinator.md`](../02-queue-and-coordinator.md).
Contexto obrigatório: [`00-context.md`](./00-context.md).

Pré-requisito: **Gate 1 fechado**. Ordem: 2.1 → 2.2 → 2.3 (parte mínima) →
**Gate 2-mínimo** → 2.4 → 2.3 (restante) → 2.5 → 2.6. O Gate 2-mínimo é o
que libera `ORCA_PUBLIC_API_ENABLED=1` em produção — não o antecipe.

> Esta fase é a primeira com muito frontend. Antes de despachar 2.3, decida
> você (humano) o desenho da aba: o prompt manda reutilizar `@plane/ui` e
> `@plane/propel` e proíbe CSS novo, mas não substitui uma decisão de UX.

| Item | Perfil | Risco |
| --- | --- | --- |
| 2.1 | `heavy` | alto (mexe no reconciliador de acesso) |
| 2.2 | `standard` | médio (matriz de permissões) |
| 2.3 | `standard` | médio, volumoso — despache em duas partes |
| 2.4 | `standard` | baixo |
| 2.5, 2.6 | `standard` | baixo |

---

## 2.1 — Migração 0140: coordenadores e acesso reconciliado

```text
Você vai implementar o item 2.1 do plano Orca. Só este item. Ele toca o
reconciliador de acesso, que é a parte do fork com maior risco de dar acesso
indevido a projeto — leia o reconciliador inteiro antes de mudar uma linha.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §5.2 (OrganizationalUnitCoordinator), §6.1 (I10),
   decisões F16 e F17 em §3.
3. docs/plans/orca-work-management/02-queue-and-coordinator.md, seção "2.1".
4. apps/api/plane/app/services/orca/org_unit_reconciler.py INTEIRO — baseline_role,
   last_applied_role, proveniência do grant, o piso de permissão.
5. apps/api/plane/db/models/organizational_unit.py (OrganizationalUnitGrant).
6. apps/api/plane/tests/unit/orca/test_org_unit_reconciler.py.

TAREFA
1. Modelo OrganizationalUnitCoordinator (RFC §5.2) em organizational_unit.py.
2. Campo grant_source em OrganizationalUnitGrant: choices membership | coordinator,
   default "membership". É ele que permite remover o coordenador sem tirar o acesso
   que a pessoa já tinha por ser membro da área.
3. Migração 0140_orca_unit_coordinator.py com o modelo e o campo, dependência
   explícita na última Orca.
4. org_unit_reconciler.py: coordenador ativo de uma área recebe ProjectMember
   (role Member, 15) em todos os projetos cobertos, com grant de
   grant_source="coordinator". Reaproveite TODA a lógica de baseline_role e
   last_applied_role que já existe: quem já é Admin manual não é rebaixado, e a
   remoção restaura o baseline, não o mínimo.
5. Testes em test_org_unit_reconciler.py:
   - coordenador ganha acesso aos projetos cobertos;
   - coordenador que já era membro manual Admin NÃO é rebaixado;
   - remover a coordenação restaura o baseline anterior;
   - coordenador que também é membro da área mantém acesso ao deixar a coordenação;
   - reconciliar duas vezes seguidas não muda nada (idempotência).

DEFINIÇÃO DE PRONTO
- A escrita em ProjectMember continua acontecendo SÓ dentro do reconciliador (I10).
- Nenhum caminho remove acesso que veio de outra proveniência.
- ruff limpo.

NÃO FAÇA
- Não crie endpoint de coordenador aqui (é 2.2).
- Não rode makemigrations/migrate; escreva à mão e avise.

AO TERMINAR
- Marque 2.1 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [2.1] add unit coordinators and reconcile their project access
- Responda no formato da seção 10 do 00-context.md, com a tabela cenário → efeito
  em ProjectMember (antes/depois).
```

---

## 2.2 — Permissão de coordenador e endpoints internos

```text
Você vai implementar o item 2.2 do plano Orca. Só este item. Depende de 2.1.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §8.1 (lista dos endpoints), §6.2, §10 (matriz
   de permissões — é o seu checklist).
3. docs/plans/orca-work-management/02-queue-and-coordinator.md, seção "2.2".
4. apps/api/plane/app/permissions/ — o decorator allow_permission e o estilo.
5. apps/api/plane/app/views/organizational_unit.py e urls/orca.py (D0.6).

TAREFA
1. apps/api/plane/app/permissions/organizational_unit.py (novo): is_unit_coordinator,
   is_unit_member e o decorator allow_unit_role(["coordinator", "member"],
   unit_kwarg="unit_id"), no espírito de allow_permission. Workspace Admin sempre passa.
2. Endpoints do RFC §8.1: claim/, reassign/, return/, transfer/, queue/, decisions/,
   PUT de política (por área e por área↔projeto) e CRUD de coordinators/.
   Todos delegam ao serviço D0.5 com o trigger correto: ui_claim, ui_coordinator,
   reassign, return_to_queue.
3. queue/ aceita filtros routing_state, overdue, project, executor; devolve também
   age_seconds e assignment_overdue; ordenação padrão: atrasados primeiro, depois
   queued_at ascendente. Devolve ainda as capacidades do ator na linha
   (can_claim, can_assign, can_return) — a UI de 2.3 depende disso.
4. decisions/ paginado, mais recentes primeiro, com supersedes expandido em um nível.
5. Testes: a matriz de permissões do RFC §10 para CADA endpoint, com todos os papéis:
   Admin do workspace, Member do projeto, Member de outro projeto, Guest, coordenador
   da área, coordenador de OUTRA área, lead sem coordenação, membro da área em política
   self_claim vs manual. Um teste por célula, nomeado com papel e endpoint.

DEFINIÇÃO DE PRONTO
- Nenhuma célula da matriz sem teste. Cole a matriz preenchida na resposta.
- Nenhuma escrita em ProjectMember (I10).
- Todas as rotas sob o kill switch (404 com ORCA_ORG_UNITS_ENABLED=0).
- ruff limpo.

NÃO FAÇA
- Não implemente UI aqui (é 2.3).
- Não duplique regra de negócio do serviço na view.

AO TERMINAR
- Marque 2.2 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [2.2] add coordinator permissions and the unit queue endpoints
- Responda no formato da seção 10 do 00-context.md.
```

---

## 2.3a — Interface: parte mínima (antes do Gate 2-mínimo)

```text
Você vai implementar a PARTE MÍNIMA do item 2.3 do plano Orca — o que precisa
existir para o Gate 2-mínimo. Não faça a parte completa (seções Atenção, Decisões,
política, coordenadores, página Minha Área): ela é um despacho separado.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §8.2 (frontend).
3. docs/plans/orca-work-management/02-queue-and-coordinator.md, seção "2.3",
   parágrafo "Parte mínima".
4. apps/web/core/components/orca/organizational-units/ — TODOS os componentes que já
   existem lá, especialmente unit-detail.tsx e issue-unit-property.tsx.
5. O store MobX: organizational-unit.store.ts e o service correspondente.
6. packages/ui e packages/propel — os componentes disponíveis. Nada de CSS novo.
7. packages/i18n/src/locales/en/workspace-settings.json, namespace organizational_units.

TAREFA
1. unit-detail.tsx ganha a terceira aba "work" → novo unit-work-tab.tsx, com duas
   seções: "Caixa de entrada" (routing_state queued e allocation_failed) e
   "Em execução" (agrupado por executor principal).
2. queue-list.tsx + queue-item-row.tsx: identificador, título com link para o item,
   estado nativo, queue_reason, idade, atraso de atribuição, executor.
3. Ações por linha, condicionadas às capacidades que a API devolve (can_claim,
   can_assign, can_return) — nunca inferidas no cliente: Assumir, Atribuir a…
   (assign-member-modal.tsx, listando candidatos do endpoint de ranking com a carga
   de cada um) e Devolver à fila.
4. Store: queueByUnit, fetchQueue, claim, assign, returnToQueue em
   organizational-unit.store.ts, mais o service. Siga o padrão de erro/loading dos
   stores vizinhos.
5. issue-unit-property.tsx: mostra routing_state e executor principal; o botão
   "atribuir" vira menu com as três ações.
6. i18n: todas as strings no catálogo, em TODAS as locales (skill translate;
   plurais CLDR; placeholders preservados). Nenhuma string literal no componente.
7. Testes: store (vitest) para fila e as três ações; um teste de componente para
   queue-list.tsx (linha renderiza, ação some quando a capacidade é false).

DEFINIÇÃO DE PRONTO
- Nenhuma classe CSS nova fora do tema; nenhum componente novo que duplique um de
  @plane/ui ou @plane/propel (diga na resposta quais reutilizou).
- Nenhum any novo.
- Comandos para o desenvolvedor: pnpm --filter web check:lint, check:types e o
  check:sync do i18n.

NÃO FAÇA
- Não faça a parte completa do 2.3.
- Não chame endpoint que não existe: se faltar campo na API, PARE e reporte.

AO TERMINAR
- No arquivo da fase, marque o item 2.3 como [~] (em andamento, parte mínima
  entregue) e explique na resposta o que falta.
- Commit: feat(orca): [2.3] add the unit work queue tab and row actions
- Responda no formato da seção 10 do 00-context.md.
```

---

## 2.3b — Interface: parte completa

```text
Você vai implementar a PARTE COMPLETA do item 2.3 do plano Orca, depois do Gate
2-mínimo. A parte mínima (aba de fila, ações de linha, store) já existe: leia o que
está lá e ESTENDA, não reescreva.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/plans/orca-work-management/02-queue-and-coordinator.md, seção "2.3",
   parágrafo "Parte completa".
3. Tudo que a parte mínima entregou em apps/web/core/components/orca/.
4. apps/web/app/routes/core.ts — como uma rota nova é registrada.

TAREFA
1. Seção "Atenção" na aba: target_date vencido, routing_state=suspended, executor
   indisponível (a Fase 3 preenche o dado; a seção já prevê o caso), item sem data.
2. decision-timeline.tsx: seção "Decisões", consumindo decisions/ paginado, com
   supersedes expandido em um nível.
3. policy-form.tsx (só Admin): default_mode, allowed_modes, assignment_sla_seconds,
   max_open_items_per_member, por área e por área↔projeto. Validação no cliente
   espelhando o clean() do modelo (default_mode dentro de allowed_modes), com a
   mensagem vindo do catálogo i18n.
4. coordinators-tab.tsx (só Admin): CRUD de coordenadores.
5. Página "Minha Área": rota :workspaceSlug/my-areas em apps/web/app/routes/core.ts,
   página em apps/web/app/(all)/[workspaceSlug]/(projects)/my-areas/page.tsx, listando
   as áreas de organizational-units/me/ e montando unit-work-tab.tsx para a
   selecionada. Entrada na sidebar do workspace, visível só quando o usuário tem ao
   menos uma área.
6. Transferir para outra área a partir do item: modal listando apenas áreas que
   cobrem o projeto do item.
7. i18n completo em todas as locales; testes de store e de componente para o que for novo.

DEFINIÇÃO DE PRONTO
- Nenhuma rota nova acessível a quem não deveria (a sidebar esconder não basta:
  a página checa).
- check:lint, check:types e check:sync limpos (comandos para o desenvolvedor).

NÃO FAÇA
- Não introduza biblioteca de UI nova.
- Não altere endpoint: se faltar dado, reporte.

AO TERMINAR
- Marque 2.3 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [2.3] complete the unit work surface and the My Areas page
- Responda no formato da seção 10 do 00-context.md.
```

---

## 2.4 — Alertas e varredura de SLA de atribuição

```text
Você vai implementar o item 2.4 do plano Orca. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §6.6 (SLA de atribuição) e §11.
3. docs/plans/orca-work-management/02-queue-and-coordinator.md, seção "2.4".
4. apps/api/plane/bgtasks/organizational_directory_task.py — o padrão de tarefa Orca,
   inclusive como ela sai cedo com o kill switch desligado.
5. apps/api/plane/celery.py e o include de settings/common.py.
6. O modelo Notification nativo e como outras tarefas criam notificação.

TAREFA
1. Campo last_alerted_at em IssueOrganizationalUnit, na migração desta fase.
2. Tarefa plane.bgtasks.organizational_queue_task.sweep_assignment_sla, agendada a
   cada 15 minutos, registrada em celery.py e no include de settings — com o mesmo
   comentário explicativo das tarefas Orca existentes.
3. Regra: para cada item queued/allocation_failed com assignment_due_at < now() e
   sem alerta nas últimas 4 h, cria Notification nativa para os coordenadores da
   área; se não houver coordenador, para o lead da área. Atualiza last_alerted_at.
4. Alerta imediato (dentro do serviço, não da tarefa) quando uma alocação termina em
   allocation_failed.
5. Testes: sweep cria notificação uma vez; repetição dentro de 4 h não duplica; sem
   coordenador cai para o lead; com ORCA_ORG_UNITS_ENABLED=0 a tarefa sai sem efeito.

DEFINIÇÃO DE PRONTO
- Nenhum caminho manda notificação em lote sem limite (uma varredura em base grande
  não pode gerar milhares de notificações por item repetido — o last_alerted_at é o
  que impede; prove com teste).
- ruff limpo.

NÃO FAÇA
- Não crie canal de notificação novo (e-mail, webhook): use a Notification nativa.

AO TERMINAR
- Marque 2.4 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [2.4] alert coordinators when assignment SLA expires
- Responda no formato da seção 10 do 00-context.md.
```

---

## 2.5 — i18n completo e documentação

```text
Você vai implementar o item 2.5 do plano Orca. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md, seção 4.
2. docs/i18n.md e o skill translate (packages/i18n).
3. docs/plans/orca-work-management/02-queue-and-coordinator.md, seção "2.5".
4. docs/organizational-units.md e docs/orca-public-api.md.

TAREFA
1. Varra todas as strings introduzidas na fase (2.3a e 2.3b) e garanta que existem
   em TODAS as locales de packages/i18n/src/locales, com plurais CLDR corretos por
   idioma e placeholders/tags preservados. Nada de tradução automática sem revisão:
   siga o fluxo do skill translate.
2. docs/organizational-units.md: seções novas "Fila da área", "Coordenador" e
   "Minha Área", escritas para quem administra, não para quem programa.
3. docs/orca-public-api.md: nota de que a API pública fica liberada em produção a
   partir deste gate, e como desligá-la.

DEFINIÇÃO DE PRONTO
- check:sync do i18n verde (comando para o desenvolvedor).
- Nenhuma locale com chave faltando ou sobrando.

NÃO FAÇA
- Não invente terminologia nova: reutilize a que já está no catálogo.

AO TERMINAR
- Marque 2.5 [x] e atualize a contagem no README do plano.
- Commit: docs(orca): [2.5] translate the queue surface and document coordinators
- Responda no formato da seção 10 do 00-context.md.
```

---

## 2.6 — Testes de fechamento

```text
Você vai implementar o item 2.6 do plano Orca. Só este item. Se um teste falhar,
reporte o defeito — não conserte fora do escopo.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/plans/orca-work-management/02-queue-and-coordinator.md, seções "2.6",
   "Gate 2-mínimo" e "Gate 2 completo".
3. docs/orca-work-management-rfc.md §6.1 e §10.

TAREFA
1. Teste de integração: um coordenador esvazia uma fila de 30 itens usando SOMENTE
   os endpoints da aba. Ao final, o conjunto de ProjectMember é IDÊNTICO ao inicial
   (compare values_list ordenado antes e depois). Esse teste é a prova viva de I10.
2. Matriz de permissões negativa completa (complemento do 2.2): para cada endpoint,
   cada papel que NÃO pode, com o status esperado.
3. Cada ação da aba gera EXATAMENTE uma AssignmentDecision (conte antes e depois de
   cada ação, uma asserção por ação).

DEFINIÇÃO DE PRONTO
- Os três blocos existem e passam (comando para o desenvolvedor).
- Nenhuma asserção frouxa.

NÃO FAÇA
- Não feche o gate: liste o que falta (área piloto, coordenador definido, semana de
  uso real, audit_organizational_routing limpo).

AO TERMINAR
- Marque 2.6 [x] e atualize a contagem no README do plano.
- Commit: test(orca): [2.6] prove the queue surface never touches project membership
- Responda no formato da seção 10 do 00-context.md.
```
