# Plano da Madrugada — Revisão R2 (2026-11-21)

**Objetivo:** transformar os 23 achados (`R2.01`–`R2.23`) e as 4 decisões de
desenho (`D1`–`D4`) registrados em [R2-review-findings.md](./R2-review-findings.md)
em frentes independentes, com branches prontas, testadas e enviadas para
`origin` sem abrir PRs nem tocar em `stage` (conforme
[HANDOFF-PROMPT.md](./HANDOFF-PROMPT.md)).

---

## 0. Fatos verificados antes de começar

- **Base do repositório:** `origin/stage` em `3d894760` (Plane CE v1.4.3 pós-sync).
- **Registro R2:** commitado em `origin/claude/laughing-pasteur-uhje1u` (`c9556234`),
  sem código alterado.
- **Zero migrações novas em F1–F5:** todas as correções desta madrugada usam o
  schema já migrado em `stage`. Apenas **D1** (caso aprovada a remoção de
  unicidade do índice de líder único) exigirá migração futura.
- **Próximo código de erro Orca livre após F4:** `4945` (`4944` foi alocado em
  F4 para `ORG_UNIT_HAS_ACTIVE_WORK_ITEMS`).
- **Ambiente de verificação usado na sessão:** PostgreSQL 15 local, servidor
  Redis-compatível (`fakeredis`), virtualenv Python 3.11 com `pytest` e `ruff`,
  e workspace `pnpm` com `turbo` (`pnpm check:types --filter=web`, `oxlint`,
  `oxfmt`, `@plane/i18n check:sync`).

---

## 1. Mapa de Frentes (F0–F6) e cobertura integral

Todos os 23 achados de R2 estão mapeados. Nenhuma decisão de produto controversa
(`D1`, `D4`) foi codificada sem aprovação; `D2` foi isolada numa branch própria
(`F2`) para que possa ser mergeada ou ajustada de forma independente.

| Frente | Branch                                  | Status                               | Achados / Decisões                                                                           | Escopo resumido                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| :----- | :-------------------------------------- | :----------------------------------- | :------------------------------------------------------------------------------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **F0** | `docs/orca-r2-madrugada-plan`           | **Pronta (push)**                    | Planejamento R2                                                                              | Este plano de execução, matriz de conflitos, checklist e especificação de F6/F7.                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| **F1** | `fix/orca-r2-access-and-scim`           | **Pronta (push)**                    | `R2.01`, `R2.03`, `R2.04`, `R2.18`, `R2.20` (views), `R2.21`, `R2.22`                        | Acesso herdado em linha inativa, filtro de projetos em `ProcessInstanceEndpoint` e `UnitProjects`, SCIM `PUT`/`PATCH replace`, escopo de método+rota na idempotência, guard de feature flag em ausências, `IssueCandidatesEndpoint` restrito a coordenador, e hardening de `is_unit_coordinator`.                                                                                                                                                                                                                |
| **F2** | `fix/orca-r2-02-coordinator-gate`       | **Pronta (push) — aguarda ok em D2** | `R2.02`, `D2`                                                                                | Portão de coordenador/admin em `POST/DELETE organizational-unit/`, `POST organizational-unit-assign/`, e rotas públicas `reassign/` e `transfer/`. Qualquer Member do projeto continua podendo atribuir área a item sem área.                                                                                                                                                                                                                                                                                    |
| **F3** | `fix/orca-r2-queue-native-sync`         | **Pronta (push)**                    | `R2.05`, `R2.06` (`D3`), `R2.07`, `R2.08`, `R2.11`, `R2.17`, `R2.19`, `R2.20` (`signals.py`) | Sincronização de troca nativa de responsável + varredura horária `sweep_routing_drift`; exclusão de itens concluídos/cancelados/arquivados de fila, auditoria e varreduras; respeito a override humano posterior à janela de ausência; atividade + notificação nativa em `_ensure_assignee`; reset de SLA e `already_assigned` em devolução; lock de unidade em `allocate()`; filtro de coordenador ativo em alertas e marcação por item alertado.                                                               |
| **F4** | `fix/orca-r2-member-and-unit-lifecycle` | **Pronta (push)**                    | `R2.09`, `R2.10`                                                                             | Limpeza automática de participação e coordenação quando um membro sai do workspace; guarda `409 ORG_UNIT_HAS_ACTIVE_WORK_ITEMS` (4944) ao apagar área com trabalho aberto + registro de `IssueResponsibilityEvent(to_unit=None)` para links fechados; proteção de `AppendOnlyModel` contra `SET_NULL` em cascata.                                                                                                                                                                                                |
| **F5** | `fix/orca-r2-web-quick-wins`            | **Pronta (push)**                    | `R2.12`, `R2.13`, `R2.14`, `R2.15` (parcial), `R2.16` (maioria), `R2.23`                     | Coordenador fora da área passa a ver a área em `/organizational-units/me/`; botões da linha da fila atualizados imediatamente após Assumir/Atribuir/Devolver; 12 métodos do serviço web passando `error?.response?.data` para `resolveOrcaErrorKey`; refresh de assignees ao escolher área; correções de `age_seconds`, datas localizadas, `work.reason.new_item` em pt-BR, erro de carga da fila com retry, ocultação de timeline para 403, badge da aba Trabalho e ausência de um único dia (`00:00`–`23:59`). |
| **F6** | `feat/orca-r2-web-area-everywhere`      | **Especificada (§4)**                | `R2.15` (restante), `R2.16` (2 miúdos restantes)                                             | Campo Área no modal de criação, peek overview, opção "Nenhuma" (limpar), badge de estado de roteamento, ocultar label órfão quando nenhuma área cobre o projeto; modais de confirmação (`AlertModalCore`) nas remoções e input de SLA em horas/unidade.                                                                                                                                                                                                                                                          |
| **F7** | _(após decisão)_                        | **Bloqueada por D1 / D4**            | `D1`, `D4`                                                                                   | Fusão Líder + Coordenador (e revisão do índice de líder único) + política de Convidado em área.                                                                                                                                                                                                                                                                                                                                                                                                                  |

---

## 2. Inventário das branches prontas e evidência de verificação

Todas as 5 branches de código foram cortadas diretamente de `origin/stage`
(`3d894760`), testadas contra PostgreSQL 15 + Redis-compatível local e enviadas
para `origin`.

### F1 — `fix/orca-r2-access-and-scim` · SHA `2e7181e1`

- **Achados:** `R2.01`, `R2.03`, `R2.04`, `R2.18`, `R2.20` (rotas de ausência),
  `R2.21`, `R2.22`.
- **Arquivos principais:**
  - `apps/api/plane/app/services/orca/org_unit_reconciler.py` (`ACTION_RELINQUISH_CLAIM` rebaixa linha inativa para `baseline_role` ou papel de auto-entrada)
  - `apps/api/plane/api/views/orca/work_items.py` (`ProcessInstanceEndpoint` filtra via `visible_project_ids_for`; `run_operation` passa `request_scope`)
  - `apps/api/plane/app/services/orca/automation_operation.py` (`_existing` valida `operation_type`; `request_scope` opcional no hash)
  - `apps/api/plane/app/views/orca_scim/groups.py` (`PUT` sem `members` preserva grupo; `PATCH replace` sem/nulo responde 400 `invalidValue`)
  - `apps/api/plane/app/views/organizational_availability.py` (`AvailabilityFeatureMixin` exige `organizational_units_enabled()`)
  - `apps/api/plane/app/views/organizational_queue.py` (`IssueCandidatesEndpoint` protegido com `UNIT_COORDINATOR`)
  - `apps/api/plane/app/views/organizational_unit.py` (`_visible_unit_projects` esconde projetos secretos de Guests/não-membros)
  - `apps/api/plane/app/permissions/organizational_unit.py` (`is_unit_coordinator` e `may_manage_member_availability` exigem área ativa e `role >= MEMBER`)
  - `apps/api/plane/tests/unit/orca/test_r2_access_and_scim.py` (**17 testes novos**)
- **Verificação:** `pytest` **244 passed, 0 failed** (17 novos + 227 existentes);
  `ruff check` e `ruff format --check` limpos.

### F2 — `fix/orca-r2-02-coordinator-gate` · SHA `d7cf94e3`

- **Achados:** `R2.02` (implementando a proposta recomendada de **D2**).
- **Arquivos principais:**
  - `apps/api/plane/app/views/organizational_unit.py` (`IssueOrganizationalUnitEndpoint.post`/`delete` e `IssueOrganizationalUnitAssignEndpoint.post`)
  - `apps/api/plane/api/views/orca/work_items.py` (`WorkItemReassignEndpoint.post` e `WorkItemTransferEndpoint.post` verificam coordenador/admin antes de abrir recibo de idempotência)
  - `apps/api/plane/tests/unit/orca/test_r2_02_coordinator_gate.py` (**11 testes novos** cobrindo os 5 vetores de contorno + permissão de Member em item sem área + não-consumo de `Idempotency-Key` em 403)
- **Verificação:** `pytest` **85 passed, 0 failed** (11 novos + 74 existentes em
  `test_issue_organizational_unit_http.py`, `test_issue_unit_coverage.py` e
  `test_public_reassign_transfer.py`); `ruff check` e `ruff format --check` limpos.

### F3 — `fix/orca-r2-queue-native-sync` · SHA `8d7db27f`

- **Achados:** `R2.05`, `R2.06` (`D3`), `R2.07`, `R2.08`, `R2.11`, `R2.17`,
  `R2.19`, `R2.20` (`signals.py`).
- **Arquivos principais:**
  - `apps/api/plane/app/services/orca/queue.py` (helper compartilhado `open_work_links` excluindo `completed`/`cancelled`, arquivados, soft-deleted, drafts e projetos arquivados)
  - `apps/api/plane/app/services/orca/routing_audit.py`, `apps/api/plane/bgtasks/organizational_queue_task.py`, `apps/api/plane/bgtasks/organizational_availability_task.py` (aplicação uniforme de `open_work_links`, `sweep_routing_drift` agendado no `celery.py` no minuto `:50`, e `last_alerted_at` gravado apenas para itens efetivamente notificados)
  - `apps/api/plane/app/services/orca/alerts.py` (filtra membros e usuários ativos + coordenador `role >= MEMBER`)
  - `apps/api/plane/app/services/orca/signals.py`, `apps/api/plane/app/serializers/issue.py`, `apps/api/plane/app/views/issue/bulk_operations.py` (reconciliação em troca nativa e em massa de `assignee_ids` + guarda `organizational_units_enabled()`)
  - `apps/api/plane/app/services/orca/assignment_service.py` (notificação e atividade nativas em `_ensure_assignee` — incluindo caminho seguro para `actor=None` sem crash em `notification_task.py`; reset de SLA e `last_alerted_at`; revalidação sob lock em `allocate()`)
  - `apps/api/plane/app/views/organizational_unit.py` (`IssueOrganizationalUnitAssignEndpoint` não retorna `already_assigned` quando o link voltou para `queued`/`allocation_failed`)
  - `apps/api/plane/tests/unit/orca/test_r2_queue_native_sync.py` (**13 testes novos**) e atualização de `test_celery_task_registration.py`
- **Nota de engenharia (bug real pego pelos testes):** em `R2.07`, a comparação
  de override humano usa `decision.created_at >= max(window.unavailable_from, window.created_at)`
  para que ausências registradas retroativamente (ex.: atestado médico lançado
  depois da atribuição) continuem sendo recolhidas pela varredura, preservando
  todos os 25 testes de `test_availability_sweep.py`.
- **Verificação:** `pytest` **133 passed, 0 failed** nas suítes de fila,
  disponibilidade, auditoria, alertas, transições, bulk e registro Celery (além
  de `test_assignment_service.py` e `test_assignment_concurrency.py`); `ruff` limpo.

### F4 — `fix/orca-r2-member-and-unit-lifecycle` · SHA `b615d7d8`

- **Achados:** `R2.09`, `R2.10`.
- **Arquivos principais:**
  - `apps/api/plane/app/services/orca/signals.py` (receivers `pre_save`/`post_save`/`post_delete` em `WorkspaceMember` desativando participação/coordenação e disparando reconciliação)
  - `apps/api/plane/app/views/organizational_unit.py` e `organizational_queue.py` (filtragem `workspace_member__is_active=True` em contagens/rosters; `destroy` retorna `409 ORG_UNIT_HAS_ACTIVE_WORK_ITEMS` se houver itens abertos e grava `IssueResponsibilityEvent(to_unit=None)` para links fechados)
  - `apps/api/plane/bgtasks/deletion_task.py` (skip de subclasses `AppendOnlyModel` no ramo `SET_NULL`)
  - `apps/api/plane/utils/orca_error_codes.py`, `packages/constants/src/orca/error-codes.ts` e 19 locales `packages/i18n/src/locales/*/workspace-settings.json` (código `4944`)
  - `apps/api/plane/tests/unit/orca/test_r2_member_and_unit_lifecycle.py` (**3 testes novos**)
- **Nota de engenharia (bug real pego pelos testes):** `deletion_task.py`
  executava `related_queryset.update(field=None)` em `SET_NULL` inclusive sobre
  tabelas `AppendOnlyModel`, apagando `IssueResponsibilityEvent.from_unit` ao
  soft-deletar uma área. Corrigido nesta branch.
- **Verificação:** `pytest` **68 passed, 0 failed** (`test_r2_member_and_unit_lifecycle.py`,
  `test_orca_error_codes.py`, `test_upstream_member_seams.py`);
  `@plane/i18n check:sync` 19/19 (100%); `oxfmt`, `oxlint` e `ruff` limpos.

### F5 — `fix/orca-r2-web-quick-wins` · SHA `a2eeab07`

- **Achados:** `R2.12`, `R2.13`, `R2.14`, `R2.15` (refresh de assignees),
  `R2.16` (7 de 9 miúdos), `R2.23`.
- **Arquivos principais:**
  - `apps/api/plane/app/views/organizational_unit.py` (`UserOrganizationalUnitsEndpoint` inclui unidades coordenadas e retorna `is_coordinator`)
  - `apps/api/plane/tests/unit/orca/test_r2_12_coordinator_my_areas.py` (**5 testes novos**)
  - `packages/types/src/organizational-unit.ts`
  - `apps/web/core/services/orca/organizational-unit.service.ts` (12x `throw error?.response?.data`)
  - `apps/web/core/store/orca/organizational-unit.store.ts` (`mergeRoutingIntoRow` recalcula `permissions` + `age_seconds` e dispara refresh em background)
  - `apps/web/core/components/orca/organizational-units/` (`availability-form.tsx`, `decision-timeline.tsx`, `issue-unit-property.tsx`, `queue-item-row.tsx`, `unit-detail.tsx`, `unit-form-modal.tsx`, `unit-projects-tab.tsx`, `unit-work-tab.tsx`)
  - 19 locales `packages/i18n/src/locales/*/workspace-settings.json` (nova chave `work.load_failed` + correção pt-BR `work.reason.new_item` -> `"Novo na fila"`)
- **Verificação:** `pytest` **61 passed, 0 failed** (5 novos + 56 existentes);
  `pnpm check:types --filter=web` **11/11 tasks**; `pnpm --filter web check:lint`
  **0 errors**; `oxfmt --check` limpo em `web`, `@plane/types` e `@plane/i18n`;
  `@plane/i18n check:sync` **4.347 chaves, 19/19 (100%)**; `ruff` limpo.

---

## 3. Matriz de conflitos e ordem recomendada de merge

Testado via `git merge-tree --write-tree` em todos os 10 pares de branches:

- **8 de 10 pares fazem merge com ZERO conflitos**, incluindo todos os 19
  arquivos de locale entre F4 e F5, `signals.py` entre F3 e F4, e
  `work_items.py` entre F1 e F2.
- Apenas **2 trechos triviais** em `apps/api/plane/app/views/organizational_unit.py`
  se sobrepõem:
  1. **F1 + F4** (`OrganizationalUnitViewSet.get_queryset`): manter ambos os
     filtros — `memberships__workspace_member__is_active=True` (F4) e
     `_visible_unit_projects` (F1).
  2. **F3 + F2** (`IssueOrganizationalUnitAssignEndpoint.post`): manter o
     portão de coordenador acima (F2) e a guarda
     `and (existing is None or existing.routing_state == RoutingState.ASSIGNED)`
     no bloco `already_assigned` (F3).

### Ordem recomendada de PR / merge em `stage`

1. `claude/laughing-pasteur-uhje1u` (registro R2 no quadro) + `docs/orca-r2-madrugada-plan` (F0)
2. **F1** (`fix/orca-r2-access-and-scim`) — fecha acesso/SCIM/API pública
3. **F3** (`fix/orca-r2-queue-native-sync`) — fecha fila vs Plane nativo e notificações
4. **F4** (`fix/orca-r2-member-and-unit-lifecycle`) — resolve o hunk trivial com F1 em `organizational_unit.py`
5. **F5** (`fix/orca-r2-web-quick-wins`) — sem conflitos com F1/F3/F4
6. **F2** (`fix/orca-r2-02-coordinator-gate`) — após confirmar **D2**; resolve o hunk trivial com F3 em `organizational_unit.py`
7. **F6** (`feat/orca-r2-web-area-everywhere`) — próxima sessão de UI após merge de F5 (e F2)

---

## 4. Especificação da Frente F6 (`feat/orca-r2-web-area-everywhere`)

Frente focada em experiência de interface que ficou deliberadamente separada de
F5 por tocar superfícies nativas do Plane (modal de criação e peek overview) e
por se beneficiar do merge prévio de F5 e F2.

### 4.1 `R2.15` — Área presente no fluxo diário do item

- **Remover área ("Nenhuma") em `issue-unit-property.tsx`:** adicionar opção de
  limpeza (`DELETE organizational-unit/` via `store.clearIssueUnit`) visível
  apenas quando `isWorkspaceAdmin || isCoordinator` (alinhado com D2/F2), além
  de desabilitar a troca de área no dropdown quando o item já tem área e o
  usuário não é coordenador/admin.
- **Badge de estado de roteamento em `issue-unit-property.tsx`:** exibir estado
  compacto ao lado do seletor (`Na fila` / `Atribuído a <nome>` / `Falha na
alocação`) usando os dados de `routing` já carregados.
- **Ocultar label órfão "Área" na página do item:** no wrapper de propriedades
  da página cheia do item (`apps/web/core/components/issues/issue-detail/issue-properties.tsx`
  ou equivalente), só renderizar a linha do rótulo "Área" quando o projeto tiver
  ao menos uma área ativa vinculada (hoje `IssueOrganizationalUnitProperty`
  retorna `null`, deixando o rótulo sozinho).
- **Modal de criação de item (`create-update-issue-modal`):** adicionar seletor
  opcional de Área (filtrado por `projectId`) que, após a criação nativa do
  item, chama `store.setIssueUnit(workspaceSlug, projectId, created.id, unitId)`
  em sidecar — mantendo compatibilidade com `FORK.md` (sem alterar o payload
  nativo de `POST issues/`).
- **Peek overview (`issue-peek-overview`):** incluir a mesma linha de
  propriedade `IssueOrganizationalUnitProperty` no painel lateral de peek.
- _(Opcional / fase seguinte)_ Coluna e filtro de Área nas views de projeto
  (requer contrato de listagem ou enriquecimento em lote para evitar N+1).

### 4.2 `R2.16` — 2 miúdos restantes

- **Modais de confirmação em ações destrutivas (`AlertModalCore`):**
  - `apps/web/core/components/orca/organizational-units/unit-members-tab.tsx` (remover pessoa da área)
  - `apps/web/core/components/orca/organizational-units/unit-coordinators-tab.tsx` (remover coordenador)
  - `apps/web/core/components/orca/organizational-units/unit-projects-tab.tsx` (desvincular projeto da área)
- **SLA amigável na aba Política:** em
  `apps/web/core/components/orca/organizational-units/unit-policy-tab.tsx`,
  permitir editar `assignment_sla_seconds` em minutos/horas (ou seletor de
  unidade `min` / `h` / `d`) convertendo para segundos apenas no envio à API.

---

## 5. Decisões de desenho pendentes (F7)

| ID     | Questão                                                                                                                                                                                            | Recomendação R2                                                                                                                       | Status                                                                                         |
| :----- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------ | :--------------------------------------------------------------------------------------------- |
| **D1** | Fundir **Líder** e **Coordenador** em um papel único que opera a fila e gerencia pessoas/ausências da própria área, permitindo mais de um por área (mantendo vínculo de projeto apenas com Admin). | Aprovar fusão (reabre RFC §2.1 e remove/ajusta índice único `org_unit_membership_single_active_lead_per_unit`).                       | **Aguarda decisão** — não codificado.                                                          |
| **D2** | Quem pode definir/trocar/remover a área de um item.                                                                                                                                                | Qualquer Member do projeto dá área a item sem área; trocar ou remover exige coordenador da área atual ou Admin.                       | **Implementado isolado na branch F2** (`fix/orca-r2-02-coordinator-gate`) para revisão rápida. |
| **D3** | O roteamento termina quando o item fecha (`completed`/`cancelled`).                                                                                                                                | Sim — sai da fila, para de alertar e fica fora das varreduras.                                                                        | **Implementado na branch F3** (`fix/orca-r2-queue-native-sync`).                               |
| **D4** | Convidado (`Guest`) em área: recusar inclusão na área ou manter inclusão ocultando ações indisponíveis na tela.                                                                                    | Escolher entre recusar Guest na entrada da área (mais simples e sem surpresas) vs aceitar só para acesso herdado a projetos sem fila. | **Aguarda decisão** — não codificado.                                                          |

---

## 6. Checklist da manhã

1. **Revisar e decidir D1, D2 e D4.**
2. **Conferir rapidamente os diffs** antes de abrir PRs contra `stage`:
   ```bash
   git fetch origin
   git log --oneline origin/stage..origin/fix/orca-r2-access-and-scim
   git log --oneline origin/stage..origin/fix/orca-r2-queue-native-sync
   git log --oneline origin/stage..origin/fix/orca-r2-member-and-unit-lifecycle
   git log --oneline origin/stage..origin/fix/orca-r2-web-quick-wins
   git log --oneline origin/stage..origin/fix/orca-r2-02-coordinator-gate
   ```
3. **Abrir os PRs contra `stage` na ordem da §3** (lembrando que o job
   `Ensure Release Candidate PR` no `stage` é pré-existente e não relaciona com
   as frentes R2).
4. **Atualizar os checkboxes** em [R2-review-findings.md](./R2-review-findings.md)
   conforme cada PR for mergeado em `stage`.
5. **Confirmar migrações:** nenhuma branch F1–F5 cria migrações (`manage.py makemigrations --check --dry-run` deve continuar limpo).
6. **Rodar F6** (`feat/orca-r2-web-area-everywhere`) e, após decisão de **D1/D4**,
   abrir **F7**.
