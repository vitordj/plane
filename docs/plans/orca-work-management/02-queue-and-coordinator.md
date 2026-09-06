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

### Executado em 06/09 — o que saiu diferente do enunciado

Três desvios, todos registrados aqui em vez de no RFC porque nenhum reabre
decisão fechada:

1. **A migração é a `0139`, não a `0140`.** A Fase 3 previa `0139`; a Fase 2
   entra antes, e o número é posição na sequência, não nome. A Fase 3, quando
   vier, usa o número seguinte.
2. **`suspend` ganhou função de serviço, endpoint e um `outcome` novo.** A
   tabela de endpoints do 2.2 lista as seis rotas que a API pública já tinha;
   a máquina de estados do RFC §6.2, porém, dá ao coordenador duas transições
   (`qualquer → suspended` e `suspended → queued`) que **não tinham como
   acontecer**: não existia função de serviço, e a coluna só se mexia à mão em
   teste. Entregues: `suspend()` no serviço, `POST .../suspend/`,
   `DecisionOutcome.SUSPENDED` (na mesma migração) e a parada do relógio da
   fila (`queued_at`/`assignment_due_at` zerados) — um item parado não pode
   acumular idade contra um prazo que ninguém consegue cumprir. Retomar é o
   `return/`, que é a transição que o RFC desenha.
3. **`GET .../candidates/`** entrou junto: o 2.3 pede um modal que lista
   "candidatos do endpoint de ranking com carga", e esse endpoint é da Fase 3
   no RFC (§3.5). Sem ele o modal teria de reimplementar o ranking no cliente.
   É leitura pura — o ranking é recalculado dentro do lock na hora da decisão,
   então uma lista velha na tela nunca atribui a pessoa errada.

Também novo: cinco códigos de erro (4932–4936) nos três lugares e nas 19
locales, e o `grant_source` no `OrganizationalUnitGrant`, que é o que permite
o coordenador ter acesso sem ter membership.

---

## 2.1 — Migração 0139: coordenadores e acesso reconciliado `[x]`

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

## 2.3 — Interface `[x]`

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

- [x] `pnpm --filter web check:lint` e `check:types` limpos (rodados nesta sessão: lint 739 avisos e 0 erros — a linha de base do repositório, teto `--max-warnings=11957`; `check:types` exit 0 via `pnpm turbo run check:types --filter=web`, que constrói os pacotes antes).
- [x] `check:sync` do i18n verde: 4.274 chaves em 19 locales, 100%.
- [x] Teste de store para fila e ações: `apps/web/core/store/orca/organizational-unit.store.test.ts`, 11 casos, vitest adicionado ao `apps/web` (config em `vitest.config.ts`, ambiente `node`) e rodando no CI (`Run Web Unit Tests` no `stage.yml`).
- [x] Teste de componente para `queue-list.tsx`: `queue-list.test.tsx`, 5 casos (uma linha por item, estado vazio, contagem no título, ordem do servidor preservada, capabilities repassadas a toda linha). **A infraestrutura entrou junto, e é menor do que a nota anterior previa:** duas entradas de catálogo (`@testing-library/react`, `jsdom`), um segundo projeto no `vitest.config.ts` com `environment: "jsdom"` e um `vitest.setup.ts` de quatro linhas que desmonta o que o teste anterior renderizou. `@testing-library/jest-dom` ficou de fora: os matchers dele resolvem asserções que `expect(...).toBeTruthy()` já responde, e uma dependência a menos é uma dependência a menos. O `queue-item-row` é mockado de propósito — ele puxa store, router e toast, e renderizá-lo faria um teste de "a seção desenha suas linhas" falhar por motivos que não são da seção.

---

## 2.4 — Alertas e varredura de SLA de atribuição `[x]`

- Tarefa Celery `plane.bgtasks.organizational_queue_task.sweep_assignment_sla` a cada 15 min (registrar em `plane/celery.py` e no `include` de `settings/common.py`, com o mesmo comentário explicativo das tarefas Orca existentes).
- Para cada item `queued`/`allocation_failed` com `assignment_due_at < now()` sem alerta nas últimas 4 h (guardar `last_alerted_at` em `IssueOrganizationalUnit`, campo novo na mesma fase, migração `0140`), criar notificação nativa (`Notification`) para os coordenadores da área e, se não houver coordenador, para o `lead`.
- Alerta imediato (no serviço) quando uma alocação termina em `allocation_failed`.

**Testes:** sweep cria notificação uma vez; repetição dentro de 4 h não
duplica; sem coordenador cai para o lead; `ORCA_ORG_UNITS_ENABLED=0` faz a
tarefa sair sem efeito (padrão da `organizational_directory_task`).

---

## 2.5 — i18n completo e documentação `[x]`

- Todas as strings novas em todas as locales; revisar plurais com CLDR (skill `translate`).
- `docs/organizational-units.md`: seções "Fila da área", "Coordenador", "Minha Área".
- `docs/orca-public-api.md`: nota de que a API está liberada em produção a partir deste gate.

---

## 2.6 — Testes de fechamento `[x]`

- Teste de integração: coordenador esvazia uma fila de 30 itens só pelos endpoints da aba; ao final, `ProjectMember` idêntico ao início (comparar `values_list` antes/depois).
- Matriz de permissões negativa completa (2.2).
- Cada ação da aba gera exatamente uma `AssignmentDecision`.

---

## Gate 2-mínimo (libera `ORCA_PUBLIC_API_ENABLED=1` em produção)

- [~] 2.1, 2.2 e a parte mínima de 2.3 mescladas em `stage` e implantadas em staging. **Código pronto e verde na branch** (`claude/implementacao-ponta-a-ponta-vfbofq`); falta o merge e o deploy, que a sessão de agente não faz.
- [ ] Área piloto com coordenador definido (pendência de negócio no README do plano).
- [ ] Coordenador piloto consegue, em staging: ver a fila, receber alerta de `allocation_failed` (2.4 pode ser entregue junto ou logo após; sem ele, o alerta imediato do serviço basta para o gate), atribuir manualmente, devolver à fila.
- [x] Runbook: como desligar a API (`ORCA_PUBLIC_API_ENABLED=0`) e o que acontece com operações em voo — `docs/release-runbook.md` §6b.

Data: \_**\_ · Quem verificou: \_\_**

## Gate 2 completo

- [~] 6 itens `[x]` — cinco fechados; o 2.3 fica `[~]` só pelo teste de componente (ver o item).
- [x] Teste de 2.6 verde: `test_queue_closing.py`, 22 casos, executados nesta sessão.
- [ ] Uma semana de uso real da fila pela área piloto sem violação apontada por `audit_organizational_routing` (rodar diariamente em dry-run).

Data do gate: \_\_\_\_
