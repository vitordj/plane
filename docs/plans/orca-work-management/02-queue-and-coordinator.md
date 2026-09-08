# Fase 2 — Fila da área e coordenador

**Objetivo:** dar à área uma superfície para tratar o trabalho que a API e a
UI colocam nela: caixa de entrada, ações de assumir/atribuir/reatribuir/
devolver, papel de coordenador com permissões próprias e histórico de
decisões. O **Gate 2-mínimo** é o que libera a API pública em produção.
**Pré-requisitos:** Gate 1.
**Referência:** RFC §5.2 (`OrganizationalUnitCoordinator`), §6.2, §8, §9
(Fase 2), F16, F17.
**Ordem:** 2.1 → 2.2 → 2.3 (parcial: fila + atribuir + devolver) → **Gate
2-mínimo** → 2.4 → 2.3 (restante) → 2.5 → 2.6.

---

## 2.1 — Migração 0139: coordenadores e acesso reconciliado `[x]`

- Modelo `OrganizationalUnitCoordinator` (RFC §5.2) em `organizational_unit.py`.
- `org_unit_reconciler.py`: coordenadores ativos de uma área recebem `ProjectMember` (role Member, 15) em todos os projetos cobertos, com `OrganizationalUnitGrant` de origem própria. Adicionar campo `grant_source` (`membership` | `coordinator`) em `OrganizationalUnitGrant` na mesma migração, default `membership`, para que a remoção do coordenador retire só o que ele ganhou por isso e respeite o piso/proveniência já existentes. Reaproveitar toda a lógica de `baseline_role`/`last_applied_role`.
- Testes em `test_org_unit_reconciler.py`: coordenador ganha acesso; coordenador que já era membro manual Admin não é rebaixado; remoção do coordenador restaura baseline; coordenador que também é membro da área mantém acesso após deixar a coordenação.

**Entregue (07/09).** A migração saiu **`0139`**, não `0140`: `0139` estava
livre e a Fase 3, que a reservava, passou a `0140` — Django liga migrações por
dependência, e a convenção do repositório é depender explicitamente da última
Orca (`0138_orca_automation_binding`).

O item era maior do que este arquivo dizia. `OrganizationalUnitGrant.membership`
era FK **obrigatória** e o reconciliador iterava pares `(membership,
unit_project)`; um coordenador pode não ser membro da área (F16, RFC §5.2),
então não existe membership para o grant apontar. Um campo `grant_source` não
bastava. O que foi feito: `membership` anulável, FK `coordinator` própria,
`grant_source` (`membership` | `coordinator`), CHECK de exclusividade entre as
duas FKs e constraint parcial única `(coordinator, unit_project)`. No
reconciliador, "fonte" deixou de ser o par e passou a carregar a sua origem;
`baseline_role`, `last_applied_role`, `cap_role_to_workspace_role` e a detecção
de drift ficaram intocados.

---

## 2.2 — Permissão de coordenador e endpoints internos `[x]`

- `apps/api/plane/app/permissions/organizational_unit.py` (novo): `is_unit_coordinator(user, unit)`, `is_unit_member(user, unit)`; decorator `allow_unit_role(["coordinator", "member"], unit_kwarg="unit_id")` no espírito de `allow_permission`, que também aceita Workspace Admin sempre.
- Endpoints (RFC §8.1): `claim/`, `reassign/`, `return/`, `transfer/`, `queue/`, `decisions/`, `policy PUT` (área e projeto), `coordinators/` CRUD. Todos usam o serviço D0.5 com `trigger` correto (`ui_claim`, `ui_coordinator`, `reassign`, `return_to_queue`).
- `queue/` aceita filtros `routing_state`, `overdue`, `project`, `executor`; retorna também `age_seconds` e `assignment_overdue: bool`; ordenação padrão: atrasados primeiro, depois `queued_at` asc.
- `decisions/` paginado, mais recentes primeiro, com `supersedes` expandido em um nível.

**Testes:** matriz de permissões do RFC §10 para cada endpoint (Admin ws,
Member do projeto, Member de outro projeto, Guest, coordenador da área,
coordenador de outra área, lead sem coordenação, membro da área em
`self_claim` vs `manual`).

**Entregue (07/09).** Todos os endpoints, inclusive os dois que a ordem de
corte permitia sacrificar (`transfer` e `decisions`): `claim`, `return`,
`reassign`, `transfer`, `queue`, `decisions`, `coordinators` CRUD e `policy
PUT`. Arquivos novos: `app/permissions/organizational_unit.py` (helpers
`is_unit_coordinator`, `is_unit_member`, `may_see_queue` e os decorators
`allow_unit_role`, `allow_issue_unit_role`) e
`app/views/organizational_queue.py`. A view pública
`UnitQueueEndpoint._may_see_queue` passou a chamar o helper compartilhado,
então "quem vê a fila" tem uma definição só.

Cinco códigos de erro novos, 4932–4936, nos três lugares e nas 19 locales.
`policy PUT` é Admin do workspace e **não** herdou o decorator do `policy GET`,
que aceita Guest — a revisão adversarial da mesma noite sinalizou esse risco de
copiar-e-colar e o código já estava certo. O `return` é o único cuja permissão
não é fixa: coordenador, Admin, ou quem detém o item.

**Testes:** 55 em `test_organizational_queue_http.py` e 27 em
`test_orca_unit_permissions.py`, incluindo a matriz do RFC §10 por endpoint, a
flag desligada respondendo 404, duas claims sequenciais (200 e 409), o
`expected_decision_id` velho (409) e o fluxo `claim → return → reassign` com
`ProjectMember.values_list` idêntico antes e depois.

---

## 2.3 — Interface `[~]` (parte mínima entregue)

Padrão: reutilizar componentes de `@plane/ui` e `@plane/propel`; nenhum CSS
novo fora do tema. Todas as strings no catálogo i18n
(`packages/i18n/src/locales/*/workspace-settings.json`, namespace
`organizational_units`), em todas as locales, via skill `translate`.

**Parte mínima (antes do Gate 2-mínimo):**

- `unit-detail.tsx`: terceira aba `work` → `unit-work-tab.tsx` com seções "Caixa de entrada" (`queued`, `allocation_failed`) e "Em execução" (agrupado por executor).
- `queue-list.tsx` + `queue-item-row.tsx`: linha com identificador, título (link para o item), estado nativo, `queue_reason`, idade, atraso na atribuição, executor.
- Ações por linha, condicionais ao papel devolvido pela API (`can_claim`, `can_assign`, `can_return`): **Assumir**, **Atribuir a…** (`assign-member-modal.tsx` listando candidatos do endpoint de ranking com carga), **Devolver à fila**.
- Store: `queueByUnit`, `fetchQueue`, `claim`, `assign`, `returnToQueue` em `organizational-unit.store.ts`; service correspondente.
- `issue-unit-property.tsx`: mostra `routing_state` e executor principal; botão "atribuir" vira menu com as três ações.

**Parte completa:**

- Seção "Atenção": `target_date` vencido, `suspended`, executor indisponível (Fase 3 preenche), sem data.
- Seção "Decisões": `decision-timeline.tsx`.
- `policy-form.tsx` (Admin): `default_mode`, `allowed_modes`, `assignment_sla_seconds`, `max_open_items_per_member`, por área e por projeto.
- `coordinators-tab.tsx` (Admin).
- Página **Minha Área**: rota `:workspaceSlug/my-areas` em `apps/web/app/routes/core.ts`, página em `apps/web/app/(all)/[workspaceSlug]/(projects)/my-areas/page.tsx` que lista as áreas de `organizational-units/me/` e monta `unit-work-tab.tsx` para a selecionada; entrada na sidebar do workspace visível quando o usuário tem ao menos uma área.
- Transferir para outra área a partir do item (modal com áreas que cobrem o projeto).

**Aceite.**

- [x] `pnpm --filter web check:lint` e `check:types` limpos (local) — `check:types` roda como `pnpm check:types --filter=web`, pelo turbo; isolado, falha por falta do build dos pacotes.
- [x] `check:sync` do i18n verde (19 locales, 100%).
- [ ] Teste de store para fila e ações (vitest) e um teste de componente para `queue-list.tsx`. **Aberto por decisão** (plano da madrugada, M8): `apps/web` não tem vitest configurado — só `vite.config.ts`, e o vitest do monorepo vive em `packages/codemods` e `apps/live`. Montar a configuração dentro do mesmo PR que entrega a aba foi julgado risco maior que o benefício. Item próprio, antes do Gate 2 completo.

**Parte mínima entregue (07/09).** `packages/types/src/organizational-unit.ts`
com as formas da fila; service e store (`queueByUnit`, `fetchQueue`, `claim`,
`assign`, `returnToQueue`); `queue-item-row.tsx`, `queue-list.tsx`,
`assign-member-modal.tsx`, `unit-work-tab.tsx`; terceira aba em
`unit-detail.tsx`; menu de ações em `issue-unit-property.tsx`; strings novas no
bloco `organizational_units.work` das 19 locales.

Verificado na sessão, na árvore integrada: `pnpm check:types --filter=web`,
`pnpm --filter web check:lint`, `pnpm --filter web check:format` e
`pnpm --filter @plane/i18n check:sync` — todos exit 0.

**Falta para a parte completa:** seção "Atenção", `decision-timeline.tsx`,
`policy-form.tsx`, `coordinators-tab.tsx`, a página "Minha Área" e a
transferência entre áreas a partir do item.

---

## 2.4 — Alertas e varredura de SLA de atribuição `[x]`

- Tarefa Celery `plane.bgtasks.organizational_queue_task.sweep_assignment_sla` a cada 15 min (registrar em `plane/celery.py` e no `include` de `settings/common.py`, com o mesmo comentário explicativo das tarefas Orca existentes).
- Para cada item `queued`/`allocation_failed` com `assignment_due_at < now()` sem alerta nas últimas 4 h (guardar `last_alerted_at` em `IssueOrganizationalUnit`, campo novo na mesma fase, migração `0140`), criar notificação nativa (`Notification`) para os coordenadores da área e, se não houver coordenador, para o `lead`.
- Alerta imediato (no serviço) quando uma alocação termina em `allocation_failed`.

**Testes:** sweep cria notificação uma vez; repetição dentro de 4 h não
duplica; sem coordenador cai para o lead; `ORCA_ORG_UNITS_ENABLED=0` faz a
tarefa sair sem efeito (padrão da `organizational_directory_task`).

**Entregue (08/09).** O campo `last_alerted_at` já existia na `0139`, então
este item não precisou de migração. Arquivos novos:
`app/services/orca/alerts.py` (destinatários e a escrita da `Notification`) e
`bgtasks/organizational_queue_task.py` (a varredura). O gancho imediato mora em
`_apply_queued`: `transaction.on_commit` + `notify_allocation_failed_safely`,
que captura qualquer exceção para um broker fora do ar não transformar
`allocation_failed` em 500. Beat a cada 15 min, `CELERY_IMPORTS` inclui o
módulo. O alerta imediato **não** grava `last_alerted_at` — essa coluna é o
cooldown da varredura de SLA, não do "ninguém pôde pegar".

---

## 2.5 — i18n completo e documentação `[ ]`

- Todas as strings novas em todas as locales; revisar plurais com CLDR (skill `translate`).
- `docs/organizational-units.md`: seções "Fila da área", "Coordenador", "Minha Área".
- `docs/orca-public-api.md`: nota de que a API está liberada em produção a partir deste gate.

---

## 2.6 — Testes de fechamento `[ ]`

- Teste de integração: coordenador esvazia uma fila de 30 itens só pelos endpoints da aba; ao final, `ProjectMember` idêntico ao início (comparar `values_list` antes/depois).
- Matriz de permissões negativa completa (2.2).
- Cada ação da aba gera exatamente uma `AssignmentDecision`.

---

## Gate 2-mínimo (libera `ORCA_PUBLIC_API_ENABLED=1` em produção)

- [~] 2.1, 2.2 e a parte mínima de 2.3 **entregues e verificadas**; 2.4 entregue (08/09). Falta implantação em staging e a área piloto.
- [ ] Área piloto com coordenador definido (pendência de negócio no README do plano).
- [ ] Coordenador piloto consegue, em staging: ver a fila, receber alerta de `allocation_failed`, atribuir manualmente, devolver à fila. **O código do alerta existe**; o critério continua aberto até alguém exercitar em staging.
- [ ] Runbook: como desligar a API (`ORCA_PUBLIC_API_ENABLED=0`) e o que acontece com operações em voo.

Data: \_**\_ · Quem verificou: \_\_**

## Gate 2 completo

- [ ] 6 itens `[x]`.
- [ ] Teste de 2.6 verde.
- [ ] Uma semana de uso real da fila pela área piloto sem violação apontada por `audit_organizational_routing` (rodar diariamente em dry-run).

Data do gate: \_\_\_\_
