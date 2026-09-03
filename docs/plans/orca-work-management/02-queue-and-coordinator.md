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

## 2.1 — Migração 0140: coordenadores e acesso reconciliado `[x]`

- Modelo `OrganizationalUnitCoordinator` (RFC §5.2) em `organizational_unit.py`.
- `org_unit_reconciler.py`: coordenadores ativos de uma área recebem `ProjectMember` (role Member, 15) em todos os projetos cobertos, com `OrganizationalUnitGrant` de origem própria. Adicionar campo `grant_source` (`membership` | `coordinator`) em `OrganizationalUnitGrant` na mesma migração, default `membership`, para que a remoção do coordenador retire só o que ele ganhou por isso e respeite o piso/proveniência já existentes. Reaproveitar toda a lógica de `baseline_role`/`last_applied_role`.
- Testes em `test_org_unit_reconciler.py`: coordenador ganha acesso; coordenador que já era membro manual Admin não é rebaixado; remoção do coordenador restaura baseline; coordenador que também é membro da área mantém acesso após deixar a coordenação.

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

---

## 2.3 — Interface `[~]`

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
- [ ] `pnpm --filter web check:lint` e `check:types` limpos (local).
- [ ] `check:sync` do i18n verde.
- [-] Teste de store (vitest) e teste de componente: **`apps/web` não tem
  runner de teste configurado** (sem vitest, sem config, sem dependência).
  Introduzir um é uma decisão própria, não um detalhe deste item — abrir como
  item separado se a equipe quiser. A cobertura da fila hoje é de backend
  (`test_queue_endpoints.py`), que é onde as regras vivem.

---

## 2.4 — Alertas e varredura de SLA de atribuição `[x]`

- Tarefa Celery `plane.bgtasks.organizational_queue_task.sweep_assignment_sla` a cada 15 min (registrar em `plane/celery.py` e no `include` de `settings/common.py`, com o mesmo comentário explicativo das tarefas Orca existentes).
- Para cada item `queued`/`allocation_failed` com `assignment_due_at < now()` sem alerta nas últimas 4 h (guardar `last_alerted_at` em `IssueOrganizationalUnit`, campo novo na mesma fase, migração `0140`), criar notificação nativa (`Notification`) para os coordenadores da área e, se não houver coordenador, para o `lead`.
- Alerta imediato (no serviço) quando uma alocação termina em `allocation_failed`.

**Testes:** sweep cria notificação uma vez; repetição dentro de 4 h não
duplica; sem coordenador cai para o lead; `ORCA_ORG_UNITS_ENABLED=0` faz a
tarefa sair sem efeito (padrão da `organizational_directory_task`).

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

- [ ] 2.1, 2.2 e a parte mínima de 2.3 mescladas em `stage` e implantadas em staging.
- [ ] Área piloto com coordenador definido (pendência de negócio no README do plano).
- [ ] Coordenador piloto consegue, em staging: ver a fila, receber alerta de `allocation_failed` (2.4 pode ser entregue junto ou logo após; sem ele, o alerta imediato do serviço basta para o gate), atribuir manualmente, devolver à fila.
- [ ] Runbook: como desligar a API (`ORCA_PUBLIC_API_ENABLED=0`) e o que acontece com operações em voo.

Data: ____ · Quem verificou: ____

## Gate 2 completo

- [ ] 6 itens `[x]`.
- [ ] Teste de 2.6 verde.
- [ ] Uma semana de uso real da fila pela área piloto sem violação apontada por `audit_organizational_routing` (rodar diariamente em dry-run).

Data do gate: ____
