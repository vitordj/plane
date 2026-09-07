# Plano da madrugada 07/09/2026 — do núcleo pronto ao Gate 2-mínimo

**O que este arquivo é.** Um plano de execução para **uma noite** (≈ 01:20 →
07:00 BRT) com sessões de agente em paralelo, escrito depois de reler a
revisão externa do Codex de 06/09 e de verificar cada premissa dela contra a
árvore atual. Não implementa nada: fixa **o que** cada sessão faz, **em que
arquivos**, **em que ordem se mescla** e **como se prova**, e deixa para a
manhã a lista do que só um humano com acesso a staging consegue fechar.

**O que se quer ao amanhecer.** Três coisas prontas para revisão, nesta ordem
de merge: (1) o PR #15 já aberto; (2) um PR de saneamento de documentação;
(3) **um** PR de bloco com 2.1 + 2.2 + 2.3-mínimo + 2.4 — a fila operável
pela interface, o coordenador e o alerta. Mais um arquivo de revisão
adversarial da `stage`, sem código. Com isso, o Gate 2-mínimo passa a
depender só de staging, área piloto e coordenador piloto — que são decisões
de negócio e operação, não de código.

**Leitura obrigatória antes deste arquivo:** [`README.md`](./README.md)
(quadro), [`02-queue-and-coordinator.md`](./02-queue-and-coordinator.md)
(a fase), [`HANDOFF-PROMPT.md`](./HANDOFF-PROMPT.md) (ambiente local) e RFC
§§5.2, 6.2, 6.6, 8, 10.

---

## 0. Fatos verificados nesta sessão que mudam o plano

Cada item abaixo foi checado contra a árvore em `f490a2d7` (`stage` após o
merge do PR #14) e contra o GitHub, em 07/09 ≈ 04:10 UTC.

| #   | Fato                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | Consequência                                                                                                                                                                                                                                                                                                                            |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| F-a | **O PR #15 está aberto** (`claude/pendencias-implementacao-auto-7a2rsp`, `31d35e2b`, criado 04:04 UTC): P0.19 e P0.20. O P0.19 descobriu que `ORCA_PUBLIC_API_ENABLED` e `ORCA_PUBLIC_API_RATE_LIMIT` **não eram encaminhados** pelo `docker-compose-orca.yml` — ligar a flag na plataforma não fazia nada. Toca `settings/common.py`, `celery.py`, `docker-compose-orca.yml`, `stage.yml`, README, RFC §4.2 (rev. 6), `P0-platform-hardening.md` e o README do plano (quadro em 18/21).                                                                                                                    | Toda branch desta noite nasce **da ponta do #15**, não de `stage` (precedente: o bloco 1.4 → 1.8 nasceu da ponta do #12). A revisão do Codex não conhecia o #15; o quadro que ela cita (16/19) já está defasado. O P0.19 é também o que torna o Gate 2-mínimo _alcançável_ — antes dele, "liberar a flag em produção" não teria efeito. |
| F-b | **`pnpm install --frozen-lockfile` funciona na sessão**: exit 0 em 18 s (store do pnpm quente em `/root/.local/share/pnpm/store/v11`). `pnpm --filter @plane/i18n check:sync` → 19 locales em 100 %, 1,7 s. `pnpm --filter web check:types` **sozinho falha** (5 126 erros `Cannot find module '@plane/…'`) porque `check:types` depende de `^build`; **`pnpm check:types --filter=web` via turbo passa**: exit 0 em 1m09s.                                                                                                                                                                                 | O `HANDOFF-PROMPT.md` ("o que continua fora: pnpm check/build/check:types, sem node_modules"), o AGENTS.md §Token Efficiency e o RFC §13 afirmam uma impossibilidade que é premissa, não fato. A Fase 2.3 pode ser **validada dentro da sessão** (tipos, lint, sync). Corrigir os três textos é tarefa da Sessão S0.                    |
| F-c | Sem daemon Docker (`docker info` falha); PostgreSQL 16 e Redis disponíveis — a receita do `HANDOFF-PROMPT.md` §Ambiente local continua válida.                                                                                                                                                                                                                                                                                                                                                                                                                                                              | pytest, `makemigrations --check` e `migrate` de ida e volta rodam na sessão. "Banco com dados" e dump de `stage` continuam fora do alcance.                                                                                                                                                                                             |
| F-d | `apps/web` **não tem vitest configurado** (só `vite.config.ts`; vitest existe em `packages/codemods` e `apps/live`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | O critério "teste de store (vitest) e teste de componente para `queue-list.tsx`" do 2.3 exige criar a configuração. **Não nesta noite** (decisão M8); o checkbox fica aberto.                                                                                                                                                           |
| F-e | O RFC §5.2 e o arquivo da fase dizem "migração `0140`" para coordenadores, mas a última Orca é `0138` e a Fase 3 reservou `0139`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Django liga migrações por dependência, não por número; a convenção do repositório é "dependência explícita na última Orca". Coordenadores vão em **`0139`** e a Fase 3 passa a `0140` (decisão M2).                                                                                                                                     |
| F-f | `OrganizationalUnitGrant.membership` é **FK obrigatória**, e o reconciliador (`_active_sources`, `_sync_grants`) itera pares `(membership, unit_project)`. O item 2.1 pede "campo `grant_source`" como se bastasse — mas um coordenador **pode não ser membro** da área (F16, RFC §5.2), logo não tem membership para o grant apontar.                                                                                                                                                                                                                                                                      | O 2.1 é maior do que o arquivo da fase sugere: exige `membership` anulável, FK `coordinator` anulável, CHECK de exclusividade e generalizar a noção de "fonte" no reconciliador (decisão M3). É o item de maior risco técnico da noite e por isso vai primeiro na Sessão SA.                                                            |
| F-g | `queue_row()` público (`api/serializers/orca/units.py`) já devolve `issue_id, sequence_id, name, project_id, routing_state, queue_reason, queued_at, assignment_due_at, assignment_overdue, age_seconds, primary_executor{id,email,display_name}`; `IssueRoutingSerializer`, `AssignmentDecisionSerializer`, `AssignmentPolicySerializer` existem em `app/serializers/organizational_unit.py`; `TRoutingState`, `TQueueReason`, `IAssignmentDecision`, `IIssueRouting` existem em `packages/types/src/organizational-unit.ts`; `queue_queryset()` em `services/orca/queue.py` já ordena atrasados-primeiro. | O 2.2 e o 2.3 têm menos trabalho de forma do que o plano da fase previa. A fila interna reaproveita `queue_queryset` e o serializer público (movido ou importado), acrescentando só o que a UI precisa (M6).                                                                                                                            |
| F-h | `UnitQueueEndpoint._may_see_queue` (público): membro da área **ou** Admin do workspace. Não existe `apps/api/plane/app/permissions/organizational_unit.py`.                                                                                                                                                                                                                                                                                                                                                                                                                                                 | O helper de permissão do 2.2 nasce compartilhado pelas duas APIs; a pública passa a chamá-lo (uma linha), para que "quem vê a fila" tenha uma só definição.                                                                                                                                                                             |
| F-i | `assignment_service.py` já expõe `claim`, `reassign(expected_decision_id, trigger)`, `return_to_queue(queue_reason, trigger)`, `transfer_unit`, `set_responsibility` com os `trigger` que o 2.2 precisa (`ui_claim`, `ui_coordinator`, `reassign`, `return_to_queue`).                                                                                                                                                                                                                                                                                                                                      | **Os endpoints do 2.2 não alteram o serviço.** A única edição em `assignment_service.py` nesta noite é o gancho do alerta imediato do 2.4 (M10), feita por uma única sessão.                                                                                                                                                            |
| F-j | Códigos de erro Orca: último número em uso **4931** (`ORG_INTERNAL_ERROR`). Catálogo i18n dos códigos: `workspace_settings.settings.organizational_units.errors.*` em `workspace-settings.json` — **o mesmo arquivo** onde entram as strings de UI, em 19 locales. `test_orca_error_codes.py` exige paridade dos três lugares.                                                                                                                                                                                                                                                                              | Duas sessões editando os mesmos 19 JSON é conflito certo. Regra de propriedade: SA escreve só dentro de `errors.*`; SB escreve só blocos novos (`work.*`, `coordinators.*`) inseridos **antes** de `errors`. Hunks distintos mesclam limpo; a Sessão SI resolve o que não mesclar (M7).                                                 |
| F-k | 27 heads remotas; a lista de apagáveis está em P0.12. `git push --delete` continua barrado para a sessão.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | P0.12 fica para a manhã, com o comando pronto (§6).                                                                                                                                                                                                                                                                                     |
| F-l | `plane/tests/unit/orca/` tem 885 funções de teste em 45 arquivos; último run completo registrado: 1017 passed (Orca + contrato). O PR #15 reporta 767 passed na suíte Orca (22m32s) na sua ponta.                                                                                                                                                                                                                                                                                                                                                                                                           | Baseline da noite = a suíte Orca na ponta do #15. Cada sessão roda **só os arquivos que toca** durante o trabalho e a suíte Orca inteira **uma vez** antes do push final; a suíte upstream inteira (≈ 10 min no CI) fica para o CI do PR.                                                                                               |

---

## 1. Decisões desta noite (M1–M12)

Registrar em RFC §4.2 como **rev. 7** (Sessão SI). Nenhuma decisão F1–F24
é reaberta; são mecanismos.

| #   | Decisão                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | Por quê                                                                                                                                                                                                              |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| M1  | **Base de todas as branches = ponta do PR #15** (`origin/claude/pendencias-implementacao-auto-7a2rsp`). Se o #15 já estiver em `stage` quando a sessão começar (`git merge-base --is-ancestor origin/claude/pendencias-implementacao-auto-7a2rsp origin/stage`), a base é `origin/stage`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | F-a. Evita refazer o merge de `settings/common.py`/`celery.py`, que o 2.4 toca, e dos docs do plano.                                                                                                                 |
| M2  | **Migração da Fase 2 é `0139_orca_unit_coordinator`**, dependendo de `0138_orca_automation_binding`. Contém: `OrganizationalUnitCoordinator`, as alterações em `OrganizationalUnitGrant` (M3) e `IssueOrganizationalUnit.last_alerted_at` (que o 2.4 precisa — assim o 2.4 não gera migração própria). A Fase 3 passa a `0140`; SI corrige `03-availability.md`, RFC §5.2 e `02-queue-and-coordinator.md`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | F-e. Uma migração por bloco, como no 1.1.                                                                                                                                                                            |
| M3  | **Proveniência do coordenador no grant:** `OrganizationalUnitGrant.membership` passa a `null=True`; nova FK `coordinator` (`OrganizationalUnitCoordinator`, `null=True`, `on_delete=CASCADE`, `related_name="grants"`); novo `grant_source` (`membership` \| `coordinator`, default `membership`); CHECK `(membership IS NOT NULL) <> (coordinator IS NOT NULL)` e CHECK `grant_source` coerente com a FK preenchida; constraint parcial única `(coordinator, unit_project) WHERE deleted_at IS NULL` ao lado da existente. No reconciliador, "fonte" deixa de ser o par `(membership, unit_project)` e vira uma tupla/dataclass `Source(kind, source_id, workspace_member_id, unit_project, role)`; `_active_sources` acrescenta uma fonte `coordinator` com role **15 (Member)** por coordenador ativo × projeto coberto; `_sync_grants` passa a chavear por `(kind, source_id, unit_project_id)`. Todo o resto (`baseline_role`, `last_applied_role`, `cap_role_to_workspace_role`, drift) fica intocado. | F-f, F17. É a única forma de "remover o coordenador retira só o que ele ganhou por isso" sem inventar um segundo ledger.                                                                                             |
| M4  | **Coordenador precisa ser Member ou Admin do workspace.** `POST coordinators/` com um Guest responde 400 `ORG_COORDINATOR_MUST_BE_MEMBER`. Coordenador **não** precisa ser membro da área (RFC §5.2).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | Um Guest com `ProjectMember` Member seria o cap do workspace jogando contra a intenção; melhor recusar do que degradar em silêncio (mesmo espírito do I7).                                                           |
| M5  | **Candidatos do "Atribuir a…" vêm do `workload/` já existente**, não de um endpoint novo de ranking. A elegibilidade por projeto é imposta pelo `reassign/` (403 `ORG_EXECUTOR_NOT_ELIGIBLE`), e a UI mostra o toast.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | Reduz o 2.2; o ranking `lb-1` continua sendo do serviço, não da tela.                                                                                                                                                |
| M6  | **A fila interna devolve, por linha, `permissions: {can_claim, can_assign, can_return}` e, no topo da página, `viewer: {is_admin, is_coordinator, is_member}`**, além dos campos do `queue_row` público e de `project{id, identifier, name}`, `state{id, name, color, group}`, `priority`, `target_date`. `can_claim` = estado em espera ∧ política efetiva do projeto permite `self_claim` ∧ viewer é membro da área com acesso ao projeto; `can_assign` = viewer é coordenador ou Admin; `can_return` = estado `assigned` ∧ (viewer é o executor ∨ coordenador ∨ Admin). A política é resolvida **uma vez por projeto por página**, não por linha.                                                                                                                                                                                                                                                                                                                                                         | A UI filtra, a API rejeita (RFC §1.2). As flags evitam quatro chamadas por linha e mantêm a autoridade no backend.                                                                                                   |
| M7  | **Códigos novos 4932–4936**, chaves em `organizational_units.errors.*`: `4932 ORG_NOT_UNIT_COORDINATOR` (403), `4933 ORG_COORDINATOR_ALREADY_SET` (409), `4934 ORG_COORDINATOR_NOT_FOUND` (404), `4935 ORG_NOT_THE_EXECUTOR` (403), `4936 ORG_COORDINATOR_MUST_BE_MEMBER` (400). Se a ponta do #15 já usar algum destes números, SA desloca para o próximo livre e avisa no commit. Propriedade dos 19 locales: **SA só dentro de `errors.*`; SB só em blocos novos antes de `errors`.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | F-j.                                                                                                                                                                                                                 |
| M8  | **Vitest em `apps/web` não entra nesta noite.** O checkbox "teste de store e de componente" do 2.3 fica `[ ]`, com a justificativa (F-d) no arquivo da fase. A cobertura da noite no frontend é `check:types` + `check:lint` + `check:format` + `check:sync`, todos executáveis na sessão (F-b).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Montar vitest às 3 h da manhã dentro do mesmo PR que entrega a aba é como um bug nasce.                                                                                                                              |
| M9  | **Só a Sessão SI abre PRs, e em draft**: um PR de docs (S0) e um PR de bloco (SA + SC + SB integrados). As sessões SA, SB, SC **só empurram branch**; não abrem PR nem editam `02-queue-and-coordinator.md`/README do plano — SI faz. A revisão SR não gera PR: gera um arquivo.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Evita cinco PRs com conflito entre si e cinco edições concorrentes no quadro. Desvia do `HANDOFF-PROMPT.md` ("não abra PR") de propósito, para que ao amanhecer exista uma fila de merge, não uma fila de perguntas. |
| M10 | **Alerta imediato de `allocation_failed`** é um gancho em `assignment_service._apply_queued` (quando `state == ALLOCATION_FAILED`): `transaction.on_commit(lambda: _notify_allocation_failed(link_id))`, onde a função **registra em log e nunca propaga** falha do broker — a lição do PR #13. **Só a Sessão SC edita `assignment_service.py`**, e só nesse ponto.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | F-i; RFC §12 "efeitos assíncronos só após commit"; PR #13 achado (2).                                                                                                                                                |
| M11 | **Linha de corte do 2.3-mínimo:** a aba Trabalho (caixa de entrada + em execução + Assumir/Atribuir/Devolver) **não é cortável**; o menu em `issue-unit-property.tsx` é o **último passo de SB** e, se não couber, vai para 2.3-completo com registro. **Linha de corte do 2.2:** `claim`, `reassign`, `return`, `queue`, `coordinators` não são cortáveis (são o Gate 2-mínimo); `transfer` e `decisions` são cortáveis para a manhã; `policy PUT` fica por último entre os não-cortáveis, porque sem ele o piloto não consegue ligar `self_claim`/`least_loaded` sem SQL.                                                                                                                                                                                                                                                                                                                                                                                                                                  | Gate 2-mínimo = fila visível, alerta, atribuir, devolver (F24).                                                                                                                                                      |
| M12 | **Título do PR de bloco:** `feat(orca): [2.1][2.2][2.3-min][2.4] the area's queue, its coordinator, and the alert` — um PR, como o #13 foi para 1.4 → 1.8.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Precedente e rastreabilidade por item no título.                                                                                                                                                                     |

---

## 2. Regras de operação da noite

1. **Uma sessão, uma branch, um conjunto exclusivo de arquivos** (matriz em
   §4). Quem precisar tocar arquivo de outra sessão **não toca**: anota no
   relatório final e SI decide.
2. **Ler este arquivo pela branch em que ele está**, porque as sessões clonam
   `stage`:
   `git fetch origin claude/fork-architecture-review-pmszir && git show origin/claude/fork-architecture-review-pmszir:docs/plans/orca-work-management/MADRUGADA-2026-09-07.md`
3. **Base (M1):**
   `git fetch origin claude/pendencias-implementacao-auto-7a2rsp stage && git checkout -b <branch> origin/claude/pendencias-implementacao-auto-7a2rsp`
   (ou `origin/stage` se o #15 já for ancestral dela).
4. **Ambiente local primeiro**, para quem roda pytest: `HANDOFF-PROMPT.md`
   §Ambiente local, tal como está (≈ 5 min). Para quem roda frontend:
   `pnpm install --frozen-lockfile` na raiz (F-b).
5. **Prova antes de marcar.** Um item só conta como feito com o teste
   **executado** na sessão; o relatório final diz o comando e o resultado
   (`-q`, saída para arquivo, `tail -20`). `ruff check` e `ruff format
--check` em `apps/api` antes de cada commit; `pnpm --filter web
fix:format` antes de cada commit que toque `apps/web`.
6. **Commits** com Conventional Commit e escopo `orca`, identificador do item
   no título, um commit por passo da tabela da sessão. Push com
   `git push -u origin <branch>` ao fim de **cada passo**, não só ao fim —
   SC e SI dependem de ver a ponta de SA.
7. **Header de copyright** em todo arquivo novo (`COPYRIGHT_CHECK.md`);
   docstrings `@description/@param/@returns`; comentário do porquê em cada
   override do core.
8. **Nenhuma escrita em `ProjectMember` fora do reconciliador** — critério de
   revisão de todos os PRs Orca (HANDOFF §Sessão de revisão). O 2.6 vai
   provar isso com `values_list` antes/depois; nesta noite, SA inclui essa
   asserção no teste do fluxo `claim → return → reassign`.
9. **Relatório final de cada sessão** (última mensagem): o que entregou, o
   que verificou e como, o que **não** verificou, o que ficou fora e por quê,
   SHA da ponta empurrada.
10. Ninguém liga `ORCA_PUBLIC_API_ENABLED` em lugar nenhum. Ninguém faz
    merge. Ninguém apaga branch.

---

## 3. Cronograma (BRT = UTC−3)

```text
01:20  S0 docs ────────────► 02:20  (push; SI mescla depois)
01:20  SR revisão adversarial ─────────────────────► 04:30  (arquivo, sem código)
01:20  SG gates verificáveis ─────────────► 03:45  (evidências + runbook de desligar a API)
01:20  SA backend 2.1 ──► ~03:00 push 2.1 ──► 2.2 ─────────────► 05:00
                                 │
                                 └─► 03:00  SC 2.4 (corta da ponta de SA) ────► 05:00
01:20  SB frontend 2.3-mín (tipos/service/store → componentes) ──► 04:45 mescla SA ──► 05:30
                                                                                  │
05:30  SI integração: S0 + SA + SC + SB → suíte, ruff, check:types/lint/format/sync,
       docs da fase, quadro, RFC §4.2 rev. 7, dois PRs em draft ──────────► 06:45
07:00  Manhã (humano): §6
```

Dependências duras: SC depende do commit 2.1 de SA (modelo
`OrganizationalUnitCoordinator` e `last_alerted_at`); SB depende de SA só na
**integração** (antes disso trabalha contra o contrato de §5); SI depende de
todos. S0, SR e SG são independentes.

Se SA atrasar o push do 2.1 além de 03:30, SC começa mesmo assim escrevendo
a tarefa contra o **contrato de modelo** de §5.1 (nomes de campo fixados aqui)
e integra depois; o que não pode é SC inventar outro nome.

---

## 4. Sessões

### S0 — Saneamento da documentação de progresso (≈ 1 h)

**Por quê primeiro.** É o defeito de engenharia mais perigoso que a revisão
achou: o RFC diz no cabeçalho que nada está implementado e em §2.2 que
"nenhum defeito tem teste hoje", quando D1–D4 fecharam na D0 com testes
nomeados; o README diz que o #12 está aberto quando #12, #13, #14 já
mesclaram e o #15 está aberto; o `HANDOFF-PROMPT.md` diz que a sessão não
roda `check:types`. Uma sessão de agente que leia isso "corrige" o que já foi
corrigido.

**Branch:** `docs/orca-plan-sanitize-2026-09-07`. **Base:** M1.

**Arquivos (exclusivos):** `docs/orca-work-management-rfc.md`,
`docs/plans/orca-work-management/README.md`,
`docs/plans/orca-work-management/HANDOFF-PROMPT.md`, `AGENTS.md`.
**Não tocar:** `02-queue-and-coordinator.md`, `03-availability.md`,
`P0-platform-hardening.md`, `D0-*`, `01-*` (SG e SI).

**Passos:**

1. **RFC cabeçalho:** "Revisão 7 (07/09/2026). Base analisada: `stage` em
   `<sha da base>`. O que está implementado é a tabela de §2.1; as seções
   marcadas 'proposta' que restam são as das Fases 2–5." Remover "Nenhuma
   seção marcada como proposta está implementada".
2. **RFC §2.1:** conferir cada linha da tabela contra o código (a linha
   "Tela da área — só membros e projetos" continua verdadeira até o PR de
   bloco; deixar) e acrescentar `AutomationOperation`/binding (Fase 1) e
   retenção (P0.20) onde faltar.
3. **RFC §2.2:** trocar "Confirmados por leitura. Nenhum tem teste hoje." por
   "Fechados na D0 (05/09/2026); cada um está pinado por teste — ver
   `D0-domain-foundation.md` §Testes por invariante." e passar D1–D4 para o
   passado, mantendo a descrição (é histórico útil) e citando o teste que
   pina cada um: D1 → `test_issue_unit_coverage.py`; D2 → remoção da herança
   em `api/serializers/issue.py` + `test_public_work_items.py`; D3 →
   `test_assignment_concurrency.py` (20 → 5/5/5/5; 10 claims → 1); D4 →
   `test_assignment_service.py` (carga = itens abertos como executor
   principal).
4. **RFC §13** e **AGENTS.md §Token Efficiency:** substituir "não rodar
   `check:types`/pnpm na sessão" por: pode rodar, com saída redirecionada e
   `tail`; o que continua vetado é despejar a saída no contexto. Citar os
   tempos medidos (F-b).
5. **HANDOFF-PROMPT.md:** (a) corrigir "O que continua fora: pnpm
   check/build/check:types (sem node_modules)"; (b) nova subseção "Ambiente
   local — frontend" com os quatro comandos e tempos:
   `pnpm install --frozen-lockfile` (18 s com store quente; minutos a frio),
   `pnpm check:types --filter=web` (turbo constrói os pacotes; 1m09s),
   `pnpm --filter web check:lint`, `pnpm --filter @plane/i18n check:sync`
   (1,7 s); (c) "Contexto que você não precisa redescobrir": D1–D4 são
   **fechados**, não "defeitos conhecidos".
6. **README do plano §Próximo item recomendado:** reescrever inteiro:
   `stage` em `<sha>`; #12, #13, #14 mesclados; **#15 aberto** (P0.19,
   P0.20 — e o que o P0.19 significa para o Gate 2-mínimo); próximo bloco =
   Fase 2 conforme este arquivo; o parágrafo "o que a sessão pode e não pode"
   atualizado por F-b/F-c. Linha nova no §Histórico (07/09) apontando para
   este arquivo. **Não** mexer na tabela do quadro (SI faz).
7. `pnpm --filter @plane/i18n check:sync` não se aplica; nenhum teste. Prova:
   `git diff --stat` e leitura cruzada de cada afirmação nova com o código
   (citar arquivo:linha no commit).

**Commit:** `docs(orca): the progress record catches up with the code`
(um só). Push. Relatório.

---

### SR — Revisão adversarial da `stage`, sem RFC (≈ 3 h, só leitura)

**Objetivo.** A revisão que o Codex ofereceu: ignorar os planos e procurar
**bugs, races, permissões e caminhos de escalada** em: `org_unit_reconciler.py`,
`assignment_service.py`, `automation_operation.py`, `queue.py`,
`routing_audit.py`, `api/views/orca/*`, `app/views/organizational_unit.py`,
`throttles/orca_public.py`, autenticação por API key nas rotas Orca, SCIM
(`views/scim*`), e o Compose/workflows depois do #15.

**Branch:** `docs/orca-stage-review-2026-09-07`. **Base:** M1.
**Arquivo único:** `docs/plans/orca-work-management/reviews/2026-09-07-stage-adversarial-review.md`.

**Roteiro mínimo (cada linha vira uma seção "verificado / achado"):**

- Escrita em `ProjectMember` fora dos reconciliadores (grep + leitura).
- `select_for_update` cobre **todos** os caminhos que mudam `routing_state`
  (inclui `routing_audit --write` e `transfer_unit`)? Deadlock possível entre
  lock de área e lock de linha em ordens diferentes?
- Idempotência: `all_objects` em todas as leituras de recibo; janela de
  retenção do P0.20 × `transfer` sem `If-Match` (o PR #15 reconhece o caso).
- Escalada: Guest com API key nas rotas `/api/v1/orca/`; Member de outro
  projeto lendo a fila pelo `unit_slug`; `workload/` e `effective-access/`
  vazando e-mails para Guest; coordenador (quando existir) alcançando
  `policy PUT`.
- Throttle por token: chave derivada do id (não do segredo) — confirmar; e o
  que acontece sem Redis.
- SCIM: kill switch depois da autenticação; rate limit; `Groups` PATCH
  removendo membership de lead.
- `QuerySet.update()` contornando o append-only de `AssignmentDecision` e
  `IssueResponsibilityEvent` — listar **onde** no código há `.update(` sobre
  essas tabelas (deve ser zero) e propor a trigger PostgreSQL como item novo
  de fase (não implementar).
- Compose pós-#15: alguma variável lida por `settings/common.py` que o
  Compose ainda não encaminha? (o job `compose_env_forwarding` cobre a tabela
  do README; conferir o que não está na tabela).

**Formato:** achados ordenados por severidade (S1 bloqueia Gate 2-mínimo →
S4 cosmético), cada um com arquivo:linha, cenário concreto de falha, teste
que faltaria, e proposta de correção **sem** implementar. Fechar com
"veredito de gate" para P0, D0, 1 e 2-mínimo. **Não editar código.**

**Commit:** `docs(orca): adversarial review of stage, 2026-09-07`. Push.

---

### SG — Gates verificáveis e runbook de desligar a API (≈ 2,5 h)

**Objetivo.** Fechar tudo do Gate D0, Gate 1 e Gate 2-mínimo que **não**
precisa de staging, e deixar evidência escrita; escrever o runbook do Gate
2-mínimo que é documento, não código.

**Branch:** `docs/orca-gates-evidence-2026-09-07`. **Base:** M1.
**Arquivos (exclusivos):** `D0-domain-foundation.md` (só o bloco Gate D0),
`01-public-contract.md` (só o bloco Gate 1), `docs/orca-public-api.md`,
`apps/api/tests/RUNNING_TESTS.md`.

**Passos:**

1. Ambiente local (HANDOFF). `pnpm install --frozen-lockfile`.
2. **Baseline da noite** na base: `pytest plane/tests/unit/orca -q -m unit
-p no:cacheprovider > /tmp/orca-baseline.log; tail -3` → registrar
   contagem e tempo. Este número é o que SI compara ao fim.
3. `python manage.py makemigrations --check --dry-run` → limpo.
   Ida e volta: `migrate`, `migrate db 0134`, `migrate` → registrar.
4. `pnpm check:types --filter=web`, `pnpm --filter web check:lint`,
   `pnpm --filter @plane/i18n check:sync` na base → registrar exit e tempo.
   **Isto fecha o critério "`check:types`" do quadro D0** — que estava aberto
   por premissa errada (F-b).
5. `python manage.py audit_organizational_routing --workspace <slug>` no
   banco vazio → "0 violações em 0 itens" (não fecha o gate, mas prova o
   comando). Escrever no bloco Gate D0 o comando exato para a manhã rodar
   sobre um dump (§6).
6. **Medição local p50/p95 (prévia do Gate 1):** com `live_server` ou o
   `runserver` local + `tools/orca-client/`, 200 criações sequenciais na
   mesma área com `least_loaded` e 4 membros; anotar p50/p95 **rotulado como
   local, não staging**. Script fica em `tools/orca-client/` só se já houver
   lugar natural; senão, comando inline no doc.
7. **Runbook "Desligar a API pública"** em `docs/orca-public-api.md`
   (critério do Gate 2-mínimo): o que acontece ao trocar
   `ORCA_PUBLIC_API_ENABLED=1→0` (404 em toda rota; recibos `in_progress`
   ficam como estão e a retomada de §6.7 os resolve ao religar; o que a
   retenção do P0.20 faz com eles; efeito zero sobre `/api/orca/` interno e
   sobre a fila). Ler `automation_operation.py` antes de afirmar cada frase.
8. `RUNNING_TESTS.md`: parágrafo "o que a sessão de agente consegue rodar",
   com os tempos medidos.

**Commit:** `docs(orca): gate evidence that a session can produce, and the
switch-off runbook`. Push.

---

### SA — Backend: 2.1 coordenadores e 2.2 endpoints (≈ 3,5 h)

**Branch:** `feat/orca-phase2-backend`. **Base:** M1.

**Arquivos (exclusivos):** `apps/api/plane/db/models/organizational_unit.py`,
`apps/api/plane/db/models/__init__.py`,
`apps/api/plane/db/migrations/0139_orca_unit_coordinator.py`,
`apps/api/plane/app/services/orca/org_unit_reconciler.py`,
`apps/api/plane/app/permissions/organizational_unit.py` (novo) e o
`__init__.py` do pacote, `apps/api/plane/app/views/organizational_queue.py`
(novo) + export em `views/__init__.py`, `apps/api/plane/app/urls/orca.py`,
`apps/api/plane/app/serializers/organizational_unit.py`,
`apps/api/plane/api/views/orca/units.py` (só `_may_see_queue`),
`apps/api/plane/utils/orca_error_codes.py`,
`packages/constants/src/orca/error-codes.ts`, os 19
`packages/i18n/src/locales/*/workspace-settings.json` **só dentro de
`organizational_units.errors`**, testes novos em `plane/tests/unit/orca/`.
**Não tocar:** `assignment_service.py` (SC), qualquer arquivo de
`apps/web` (SB), docs do plano (SI).

**Passos e prova:**

| #   | Passo                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | Commit                                                                                                                                                                                                                                                     | Prova (executada)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A1  | Modelo `OrganizationalUnitCoordinator` (`organizational_unit`, `workspace_member`, `workspace`, `is_active`; único `(organizational_unit, workspace_member) WHERE deleted_at IS NULL`; `db_table = organizational_unit_coordinators`). Alterações de M3 em `OrganizationalUnitGrant`. `IssueOrganizationalUnit.last_alerted_at` (`DateTimeField null`). Migração `0139` **escrita à mão** no padrão da `0138`, com `makemigrations --check` provando que bate.                                                                                                                                                                     | `feat(orca): [2.1] the coordinator, and where a grant comes from`                                                                                                                                                                                          | `makemigrations --check --dry-run` limpo; `migrate` → `migrate db 0138` → `migrate`; `pytest plane/tests/unit/orca/test_org_unit_reconciler.py test_organizational_unit_api.py -q` (regressão: nada muda sem coordenadores).                                                                                                                                                                                                                                                                                                                                                                            |
| A2  | Reconciliador: `Source` generalizada (M3); coordenadores ativos → role 15 em cada projeto coberto; grants com `grant_source=coordinator`; remoção restaura baseline; coordenador que também é membro mantém acesso pela membership ao deixar a coordenação. Quatro testes do arquivo da fase + **um** de proveniência (grant do coordenador não tem `membership`, tem `coordinator`).                                                                                                                                                                                                                                              | `feat(orca): [2.1] a coordinator reaches the area's projects, and only through that`                                                                                                                                                                       | `pytest .../test_org_unit_reconciler.py -q` verde, incluindo os 18 existentes. **Push aqui (≈ 03:00): sinal para SC.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| A3  | `permissions/organizational_unit.py`: `is_workspace_admin(user, workspace_id)`, `is_unit_coordinator(user, unit)`, `is_unit_member(user, unit)`, `may_see_queue(user, unit)`, `unit_for_issue(issue_id)`; decorators `allow_unit_role(roles, unit_kwarg="unit_id")` e `allow_issue_unit_role(roles)` no espírito de `allow_permission` (Admin de workspace sempre passa; 404 quando o item não tem área — `ORG_WORK_ITEM_HAS_NO_UNIT`). `_may_see_queue` público passa a chamar `may_see_queue`.                                                                                                                                   | `feat(orca): [2.2] who may act on an area's work, in one place`                                                                                                                                                                                            | Teste unitário do módulo (matriz papel × helper) + `test_public_units.py` continua verde.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| A4  | Códigos 4932–4936 nos três lugares (M7) + `errors.*` nos 19 locales via skill `translate` (são cinco frases curtas; se faltar tempo, inglês nos 18 restantes é aceitável nesta noite, registrado no relatório — SB/SI não tocam `errors.*`).                                                                                                                                                                                                                                                                                                                                                                                       | `feat(orca): [2.2] five error codes, in the three places`                                                                                                                                                                                                  | `pytest .../test_orca_error_codes.py -q`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| A5  | `views/organizational_queue.py`: `IssueClaimEndpoint`, `IssueReassignEndpoint`, `IssueReturnEndpoint`, `IssueTransferEndpoint`, `OrganizationalUnitQueueEndpoint`, `OrganizationalUnitDecisionsEndpoint`, `OrganizationalUnitCoordinatorViewSet`, e `put` em `OrganizationalUnitPolicyEndpoint` — contrato em §5.2. Serializers: `QueueRowSerializer` (ou função, reaproveitando `queue_row`), `OrganizationalUnitCoordinatorSerializer`. Todos com `OrganizationalUnitFeatureMixin`. Ordem de implementação = ordem de corte inversa (M11): claim → return → reassign → queue → coordinators → policy PUT → decisions → transfer. | um commit por endpoint ou par: `feat(orca): [2.2] claim and return, from the interface`, `... [2.2] the coordinator reassigns`, `... [2.2] the area's inbox`, `... [2.2] coordinators, and the policy an admin writes`, `... [2.2] decisions and transfer` | `plane/tests/unit/orca/test_organizational_queue_http.py` (novo): por endpoint, a matriz do RFC §10 — Admin ws, Member do projeto, Member de outro projeto, Guest, coordenador da área, coordenador de outra área, lead sem coordenação, membro em `self_claim` × `manual`; flag off → 404. Fluxo `claim → return → reassign` com `ProjectMember.values_list` idêntico antes e depois (regra 8). Duas claims sequenciais → 200 e 409 `ORG_WORK_ITEM_ALREADY_CLAIMED`. `reassign` com `expected_decision_id` velho → 409 `ORG_DECISION_STALE`. Cada ação → exatamente **uma** `AssignmentDecision` nova. |
| A6  | Suíte Orca inteira uma vez; `ruff check . && ruff format --check .` em `apps/api`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | —                                                                                                                                                                                                                                                          | `pytest plane/tests/unit/orca -q -m unit -p no:cacheprovider > /tmp/sa-final.log; tail -3`. Push final.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |

**Cuidado conhecido:** `routing_state` é `varchar(16)` — nada novo aqui, mas
não invente estado com mais de 16 caracteres. `DecisionStale` carrega 409 na
API interna (RFC §4.2 rev. 5): **não** mapear para 412 aqui.

---

### SB — Frontend: 2.3-mínimo, a aba Trabalho (≈ 4 h)

**Branch:** `feat/orca-phase2-work-tab`. **Base:** M1. Trabalha contra o
contrato de §5.2 **antes** de SA existir; integra SA às ~04:45.

**Arquivos (exclusivos):** `packages/types/src/organizational-unit.ts`,
`apps/web/core/services/orca/organizational-unit.service.ts`,
`apps/web/core/store/orca/organizational-unit.store.ts`,
`apps/web/core/components/orca/organizational-units/{unit-detail,unit-work-tab,queue-list,queue-item-row,assign-member-modal,issue-unit-property,index}.tsx`,
os 19 `workspace-settings.json` **só em blocos novos `organizational_units.work`**
inseridos antes de `errors`. **Não tocar:** `errors.*`, `apps/api`, docs do plano.

**Padrão:** componentes de `@plane/propel`/`@plane/ui` já usados em
`unit-members-tab.tsx` (ler antes); classes `text-custom-*`/`bg-custom-*`
como nos irmãos; nenhum CSS novo; `observer` do mobx-react; toasts com
`setToast`/`TOAST_TYPE` como em `issue-unit-property.tsx`; erros mapeados por
código via `ORCA_ERROR_CODE_KEYS` (ver como `unit-members-tab.tsx` faz).

**Passos e prova:**

| #   | Passo                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | Commit                                                             | Prova                                                       |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ | ----------------------------------------------------------- |
| B1  | Tipos: `IQueueRow`, `IQueueRowPermissions`, `IQueueViewer`, `IQueuePage`, `IOrganizationalUnitCoordinator` (§5.2). Service: `getQueue(slug, unitId, params)`, `claimIssue`, `reassignIssue`, `returnIssue`, `getCoordinators`, `addCoordinator`, `removeCoordinator`, `updatePolicy`.                                                                                                                                                                                                              | `feat(orca): [2.3] the shapes and calls of the area's work`        | `pnpm check:types --filter=web` (turbo) exit 0.             |
| B2  | Store: `queueByUnit: Record<unitId, {waiting: IQueueRow[]; inProgress: IQueueRow[]; viewer: IQueueViewer; loader}>`; `fetchQueue(slug, unitId)` (duas chamadas: default e `routing_state=assigned`), `claim`, `assign`, `returnToQueue` — cada ação recebe a linha, chama o service, **substitui a linha pela resposta** (o `IIssueRouting` devolvido) movendo-a entre `waiting`/`inProgress`, e em erro relança para o componente mostrar o toast. Hook `useOrganizationalUnit` já expõe o store. | `feat(orca): [2.3] the queue in the store`                         | `check:types`; leitura cruzada com §5.2.                    |
| B3  | `queue-item-row.tsx`: identificador `PROJ-123` (link para o item, mesma rota que a UI nativa usa), título, estado nativo (cor), `queue_reason` traduzido, idade (`age_seconds` humanizado), badge "atrasado" quando `assignment_overdue`, executor (avatar/nome). Ações condicionais às flags: **Assumir**, **Atribuir a…**, **Devolver à fila**. `queue-list.tsx`: lista + estado vazio + loader.                                                                                                 | `feat(orca): [2.3] one row of the inbox`                           | `check:types`, `check:lint`.                                |
| B4  | `assign-member-modal.tsx`: lista os membros da área via `fetchWorkload` (M5) com carga (`open_items`), busca por nome, confirma → `assign`. Erro 403 `ORG_EXECUTOR_NOT_ELIGIBLE` → toast específico.                                                                                                                                                                                                                                                                                               | `feat(orca): [2.3] choosing who takes it`                          | idem.                                                       |
| B5  | `unit-work-tab.tsx`: seção "Caixa de entrada" (`waiting`, atrasados no topo já vêm ordenados do backend), seção "Em execução" agrupada por executor (`primary_executor.id`), contadores. `unit-detail.tsx`: terceira aba `work`, label `t(\`${OU}.work.tab\`)`, contador = `waiting.length` depois do fetch.                                                                                                                                                                                       | `feat(orca): [2.3] the Work tab`                                   | idem.                                                       |
| B6  | i18n: bloco `organizational_units.work.*` em `en` e nos outros 18 via skill `translate` (plurais CLDR onde houver contagem).                                                                                                                                                                                                                                                                                                                                                                       | `feat(orca): [2.3] the Work tab speaks every locale`               | `pnpm --filter @plane/i18n check:sync` 100 %.               |
| B7  | **Cortável (M11).** `issue-unit-property.tsx`: mostra `routing_state` + executor; o botão "atribuir" vira menu com Atribuir automático (já existe), Assumir, Escolher pessoa (reusa o modal), Devolver à fila. As ações derivam do `IIssueRouting` que `GET organizational-unit/` do item já devolve (estado + executor) e do papel do usuário no workspace; não há chamada extra à fila. A API rejeita o que o usuário não pode, e o toast mostra o código.                                       | `feat(orca): [2.3] the work item's routing, and the three actions` | idem.                                                       |
| B8  | Integração: `git merge origin/feat/orca-phase2-backend`; ajustar tipos ao que SA realmente devolveu (SA tem a palavra final nos nomes de campo **se** divergirem do §5.2, e anota a divergência); `pnpm --filter web fix:format`; `check:types`, `check:lint`, `check:sync`.                                                                                                                                                                                                                       | `fix(orca): [2.3] align with the backend as shipped` (se preciso)  | os quatro comandos, exit 0, registrados no relatório. Push. |

Sem servidor de desenvolvimento para clicar (não há API rodando com dados),
a prova visual fica para staging (§6). Por isso o `check:types` completo é
obrigatório, não opcional.

---

### SC — 2.4 Alerta imediato e varredura de SLA (≈ 2 h; começa ≈ 03:00)

**Branch:** `feat/orca-phase2-alerts`, cortada da **ponta de SA depois do
commit A2** (`git fetch origin feat/orca-phase2-backend && git checkout -b
feat/orca-phase2-alerts origin/feat/orca-phase2-backend`).

**Arquivos (exclusivos):** `apps/api/plane/bgtasks/organizational_queue_task.py`
(novo), `apps/api/plane/celery.py` (uma entrada no beat), `apps/api/plane/settings/common.py`
(uma linha em `CELERY_IMPORTS` — o PR #15 mostra o porquê: módulo `*_task.py`
fora de `CELERY_IMPORTS` = "Received unregistered task" em silêncio),
`apps/api/plane/app/services/orca/assignment_service.py` (**só** o gancho em
`_apply_queued`, M10), `apps/api/plane/app/services/orca/alerts.py` (novo:
quem recebe e como se escreve a `Notification`), testes novos.

**Passos e prova:**

| #   | Passo                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Commit                                                                    | Prova                                                                                                                                                                         |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| C1  | `alerts.py`: `recipients_for(unit) -> list[user_id]` = coordenadores ativos; sem coordenador → `lead`; sem lead → vazio (log). `notify(link, kind)` cria `Notification(workspace, sender="in_app:orca:<kind>", triggered_by_id=None, receiver_id, entity_identifier=issue_id, entity_name="issue", project, title, message, data={"issue": {...}, "organizational_unit": {...}, "routing_state", "queue_reason"})` — mesmo formato de `notification_task.py`.                                                    | `feat(orca): [2.4] who the area's alerts reach`                           | teste: coordenador; fallback lead; nenhum.                                                                                                                                    |
| C2  | `organizational_queue_task.sweep_assignment_sla` (`@shared_task`): sai sem efeito com a flag desligada (padrão de `organizational_directory_task`); para cada `IssueOrganizationalUnit` em `queued`/`allocation_failed` com `assignment_due_at < now()` e (`last_alerted_at IS NULL` ou `< now() − 4h`): notifica e grava `last_alerted_at` (um `update()` **nesta** tabela é legítimo — não é append-only). Beat: a cada 15 min, com o comentário explicativo como nas tarefas Orca vizinhas. `CELERY_IMPORTS`. | `feat(orca): [2.4] the sweep that notices an assignment deadline passed`  | testes: cria uma vez; repetição dentro de 4 h não duplica; depois de 4 h volta a alertar; flag off → nada; `test_celery_task_registration.py` estendido para a tarefa nova.   |
| C3  | Gancho M10 em `_apply_queued`: `if state == RoutingState.ALLOCATION_FAILED: transaction.on_commit(lambda: notify_allocation_failed_safely(link.id))`, onde a função captura **qualquer** exceção do broker/DB e registra em log (`logger.exception`), nunca propaga. Se a tarefa for `.delay()`, o teste usa `CELERY_TASK_ALWAYS_EAGER`/mock; o teste com broker "fora" prova que a alocação **ainda** termina em `allocation_failed` sem 500.                                                                   | `feat(orca): [2.4] an area hears at once that nobody could take the item` | `pytest .../test_assignment_service.py .../test_assignment_concurrency.py -q` continuam verdes (não mudou semântica) + testes novos com `django_capture_on_commit_callbacks`. |
| C4  | Suíte Orca inteira; ruff. Push.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | —                                                                         | `tail -3` do log.                                                                                                                                                             |

---

### SI — Integração, docs da fase e os dois PRs (≈ 1h15; começa ≈ 05:30)

**Branch:** `feat/orca-phase2-minimum` = base M1 + merges, nesta ordem:
`docs/orca-plan-sanitize-2026-09-07` (S0, primeiro, para que as edições de
docs desta sessão se apoiem no texto já saneado) →
`docs/orca-gates-evidence-2026-09-07` → `feat/orca-phase2-backend` →
`feat/orca-phase2-alerts` → `feat/orca-phase2-work-tab`. SR **não** entra:
é PR próprio, só leitura. S0 **também** ganha PR próprio (passo 6); como o
repositório mescla com merge commit, o PR de bloco mostra só o delta depois
que o de S0 mesclar — o mesmo arranjo do #12/#13.

**Passos:**

1. Merges na ordem; conflitos esperados só nos 19 JSON (F-j) — resolver
   mantendo os dois blocos. Se SB não terminou B8, SI faz o B8.
2. Verificação completa **na árvore integrada**:
   `pytest plane/tests/unit/orca -q -m unit -p no:cacheprovider` (comparar
   com o baseline de SG: nenhuma queda, N novos); `pytest
plane/tests/contract/test_orca_public_contract.py -q`;
   `makemigrations --check`; `migrate` ida e volta `0138 ↔ 0139`; `ruff`;
   `pnpm check:types --filter=web`; `pnpm --filter web check:lint`;
   `pnpm --filter web check:format`; `pnpm --filter @plane/i18n check:sync`.
   Tudo em arquivo, `tail`, resultado no PR.
3. **`02-queue-and-coordinator.md`:** 2.1 `[x]`, 2.2 `[x]` (ou `[~]` com o
   que ficou cortado por M11), 2.3 `[~]` (parte mínima entregue; lista do que
   falta para "completa"; checkbox vitest aberto com F-d), 2.4 `[x]`, Gate
   2-mínimo: os critérios que **este PR fecha** marcados; os de staging/piloto
   abertos; renumeração `0140→0139` registrada.
4. `03-availability.md`: "Migração 0139" → "0140". RFC §5.2: `0139` para
   coordenador, `0140` para disponibilidade, `0141` mantém-se. RFC §5.1/§5.2:
   tabela do `OrganizationalUnitGrant` com `coordinator`/`grant_source`.
   RFC §4.2: **rev. 7** com M1–M12 em uma linha cada.
5. README do plano: quadro (linha Fase 2 → `[~] 4/6`; D0 → "`check:types`
   fechado; falta só o dump"), linha do §Histórico (07/09) com os números da
   noite, e "Próximo item recomendado" = §6 deste arquivo — por cima do
   texto que S0 já saneou (S0 foi o primeiro merge do passo 1).
6. Push. **Dois PRs em draft** contra `stage`, com o template do
   repositório (`create-pull-request` skill): o de S0 (branch
   `docs/orca-plan-sanitize-2026-09-07`, título
   `docs(orca): the progress record catches up with the code`) e o de bloco
   (título de M12), este listando: o que cada item entregou, os comandos e
   resultados de (2), o que ficou cortado, a ordem de merge (#15 → S0 →
   bloco) e a lista de §6 como "próximos passos para quem mesclar". Link do
   arquivo de SR no corpo dos dois.
7. Relatório final com os SHAs e os links.

---

## 5. Contratos fixados para o trabalho paralelo

### 5.1 Modelo (nomes que SA, SC e SB usam sem negociar)

```text
OrganizationalUnitCoordinator   db_table organizational_unit_coordinators
  organizational_unit FK -> OrganizationalUnit  related_name="coordinators"
  workspace_member    FK -> db.WorkspaceMember   related_name="coordinated_units"
  workspace           FK -> db.Workspace
  is_active           bool default True
  UNIQUE (organizational_unit, workspace_member) WHERE deleted_at IS NULL

OrganizationalUnitGrant (alterações)
  membership      FK null=True  (era obrigatória)
  coordinator     FK -> OrganizationalUnitCoordinator null=True related_name="grants"
  grant_source    CharField(16) choices membership|coordinator default membership
  CHECK exatamente uma de (membership, coordinator) preenchida, coerente com grant_source
  UNIQUE (coordinator, unit_project) WHERE deleted_at IS NULL

IssueOrganizationalUnit (alteração)
  last_alerted_at DateTimeField null=True
```

### 5.2 API interna `/api/orca/workspaces/<slug>/…` (sessão; `OrganizationalUnitFeatureMixin`)

Todas as respostas de ação devolvem o **`IssueRoutingSerializer`** do item
(já existe: `id, organizational_unit, routing_state, queue_reason, queued_at,
assignment_due_at, primary_executor, current_assignment_decision, …`).
Erros no envelope Orca já usado (`{"error_code": <n>, "error": <msg>, ...}`).

| Rota                                                                         | Método | Corpo                                                                                                                                | Permissão (helper)                                                            | Sucesso                                                                                                              | Erros                                                                                                                                        |
| ---------------------------------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `projects/<project_id>/issues/<issue_id>/organizational-unit/claim/`         | POST   | `{}`                                                                                                                                 | Member/Admin do projeto (nativo); elegibilidade pelo serviço                  | 200 routing                                                                                                          | 404 `ORG_WORK_ITEM_HAS_NO_UNIT`; 409 `ORG_WORK_ITEM_ALREADY_CLAIMED`; 400 `ORG_ASSIGNMENT_MODE_NOT_ALLOWED`; 403 `ORG_EXECUTOR_NOT_ELIGIBLE` |
| `…/organizational-unit/reassign/`                                            | POST   | `{"executor_id": uuid, "reason"?: str, "expected_decision_id"?: uuid}`                                                               | coordenador da área do item ∨ Admin ws → senão 403 `ORG_NOT_UNIT_COORDINATOR` | 200 routing                                                                                                          | 409 `ORG_DECISION_STALE`; 403 `ORG_EXECUTOR_NOT_ELIGIBLE`; 404                                                                               |
| `…/organizational-unit/return/`                                              | POST   | `{"reason"?: str, "expected_decision_id"?: uuid}`                                                                                    | coordenador ∨ Admin ∨ **executor atual** → senão 403 `ORG_NOT_THE_EXECUTOR`   | 200 routing (`queued`, `manually_returned`)                                                                          | 409 `ORG_INVALID_ROUTING_TRANSITION` se não estava `assigned`; 409 `ORG_DECISION_STALE`                                                      |
| `…/organizational-unit/transfer/`                                            | POST   | `{"unit_id": uuid, "reason"?: str}`                                                                                                  | coordenador da área **de origem** ∨ Admin                                     | 200 routing (nova área)                                                                                              | 400 `ORG_UNIT_NOT_COVERING_PROJECT`; 403                                                                                                     |
| `organizational-units/<unit_id>/queue/`                                      | GET    | query `routing_state` (estado \| `all`), `overdue` (`true`\|`false`), `project`, `executor`, paginação nativa (`cursor`, `per_page`) | `may_see_queue` (membro ∨ coordenador ∨ Admin) → senão 403                    | página nativa; `results[]` = §5.3; **mais** `viewer` no topo                                                         | 400 `ORG_INVALID_ROUTING_TRANSITION` para estado desconhecido                                                                                |
| `organizational-units/<unit_id>/decisions/`                                  | GET    | `issue`?, paginação                                                                                                                  | coordenador ∨ Admin                                                           | página de `AssignmentDecisionSerializer` + `issue{id,sequence_id,name,project_id}` + `supersedes` expandido um nível | 403                                                                                                                                          |
| `organizational-units/<unit_id>/policy/` e `…/projects/<project_id>/policy/` | PUT    | `{"default_mode", "allowed_modes": [..], "assignment_sla_seconds"?: int, "max_open_items_per_member"?: int}`                         | Admin ws                                                                      | 200 `AssignmentPolicySerializer` (cria ou atualiza; `version` += 1)                                                  | 400 `ORG_INVALID_ASSIGNMENT_MODE` (modo desconhecido ou `default_mode ∉ allowed_modes`)                                                      |
| `organizational-units/<unit_id>/coordinators/`                               | GET    | —                                                                                                                                    | membro ∨ coordenador ∨ Admin                                                  | `[{id, workspace_member, member{id,display_name,email,avatar_url}, is_active, created_at}]`                          | —                                                                                                                                            |
| idem                                                                         | POST   | `{"workspace_member_id": uuid}` (ou `member_id` = user id; aceitar um dos dois, documentar)                                          | Admin ws                                                                      | 201 coordenador; **dispara `dispatch_reconciliation`** para o membro × projetos da área                              | 400 `ORG_COORDINATOR_MUST_BE_MEMBER`; 409 `ORG_COORDINATOR_ALREADY_SET`; 400 `ORG_UNIT_MEMBERS_NOT_IN_WORKSPACE`                             |
| `organizational-units/<unit_id>/coordinators/<pk>/`                          | DELETE | —                                                                                                                                    | Admin ws                                                                      | 204; reconcilia (grants `coordinator` revogados; baseline restaurado)                                                | 404 `ORG_COORDINATOR_NOT_FOUND`                                                                                                              |

Triggers gravados na `AssignmentDecision`: claim → `ui_claim`; reassign →
`ui_coordinator`; return → `return_to_queue`; transfer → `ui_coordinator`
(RFC §8.1 / arquivo da fase). `decided_by` = `request.user` sempre.

### 5.3 Linha da fila (`results[]`) e `viewer`

```json
{
  "issue_id": "uuid",
  "sequence_id": 123,
  "name": "…",
  "project": { "id": "uuid", "identifier": "PROJ", "name": "…" },
  "state": { "id": "uuid", "name": "…", "color": "#…", "group": "started" },
  "priority": "high",
  "target_date": "2026-09-10",
  "routing_state": "queued",
  "queue_reason": "awaiting_coordinator",
  "queued_at": "…",
  "assignment_due_at": "…",
  "assignment_overdue": false,
  "age_seconds": 3600,
  "primary_executor": { "id": "uuid", "display_name": "…", "email": "…", "avatar_url": "…" },
  "current_decision_id": "uuid",
  "permissions": { "can_claim": true, "can_assign": false, "can_return": false }
}
```

`viewer`: `{"is_admin": bool, "is_coordinator": bool, "is_member": bool}`
ao lado de `results` na resposta paginada. `current_decision_id` é o que a
UI manda de volta em `expected_decision_id` (If-Match interno).

### 5.4 Chaves i18n reservadas

- SA: `organizational_units.errors.{not_unit_coordinator, coordinator_already_set, coordinator_not_found, not_the_executor, coordinator_must_be_member}`.
- SB: `organizational_units.work.{tab, inbox, in_progress, empty_inbox, empty_in_progress, claim, assign, return_to_queue, overdue, age, executor, unassigned, reason.<queue_reason>, assign_modal.*, toast.*}`.
- Ninguém renomeia chave existente.

---

## 6. Manhã — o que só o humano fecha (ordem recomendada)

Tudo abaixo depende de staging, de dados reais ou de decisão de negócio.
Comandos assumem o `docker-compose-orca.yml` implantado e as variáveis do
P0.18/P0.19 definidas.

1. **Mesclar em ordem:** #15 (conferir que todos os checks terminaram
   verdes; às 04:10 UTC ainda não tinham rodado) → PR de docs (S0) → PR de
   bloco. Antes do bloco: ler
   `reviews/2026-09-07-stage-adversarial-review.md`; qualquer S1 ali
   **segura o merge**.
2. **Deploy em staging** (o `migrator` aplica `0139`). Conferir
   `GET /api/orca/build-info/` com o SHA do merge (P0.15) e
   `printenv ORCA_ORG_UNITS_ENABLED`/`ORCA_PUBLIC_API_ENABLED` iguais em
   api, worker e beat (P0.14/P0.19).
3. **Gate D0 — dump:** `pg_dump` de staging → banco local →
   `python manage.py migrate db 0134 && python manage.py migrate` (ida e
   volta com dados) e `python manage.py audit_organizational_routing
--workspace <slug>` (dry-run) → zero violações. Preencher data e nome no
   bloco Gate D0.
4. **Gate 1 — em staging apenas, `ORCA_PUBLIC_API_ENABLED=1`:** alguém que
   não escreveu o código executa os `curl` de `docs/orca-public-api.md`;
   200 criações `least_loaded` na mesma área com `tools/orca-client/`
   (SG deixa o comando) → p50/p95 no arquivo da fase. Registrar quem
   verificou que produção continua `0`.
5. **Área piloto e coordenador** (pendência de negócio no README): criar a
   área, ligar ao projeto piloto, `PUT policy/` com `default_mode` escolhido,
   `POST coordinators/` com o coordenador — pela API interna (sessão) até a
   aba de coordenadores existir (2.3-completo).
6. **Gate 2-mínimo em staging:** o coordenador piloto vê a fila na aba
   Trabalho; um item criado pela API pública numa área sem membros elegíveis
   gera `allocation_failed` **e** a notificação; ele atribui manualmente;
   devolve à fila. Preencher data e nome no arquivo da fase. Runbook de
   desligar (SG) lido por quem opera.
7. **P0.12:** `git push origin --delete <branches>` com a lista do item.
   **P0.17:** registrar o alvo de implantação. **P0.13:** primeiro ensaio do
   runbook com a RC.
8. **Só depois de 6:** `ORCA_PUBLIC_API_ENABLED=1` em produção — o P0.19 é o
   que faz a variável chegar aos quatro serviços.

O que **não** é desta noite nem desta manhã: 2.3-completo (Minha Área,
`policy-form`, `coordinators-tab`, `decision-timeline`), 2.5, 2.6, Fases
3–5, trigger PostgreSQL para o append-only (SR propõe como item).

---

## 7. Riscos da noite e o que fazer

| Risco                                                           | Sinal                                 | Resposta                                                                                                                                                                                                                               |
| --------------------------------------------------------------- | ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| M3 maior que o previsto (reconciliador resiste à generalização) | A2 não pronto às 03:30                | SA reduz: coordenador **precisa ser membro da área** nesta noite (grant via membership existente, `grant_source=coordinator` só rotula), registra a limitação em §4.2 e o 2.1 fica `[~]`. SC começa pelo contrato de §5.1 mesmo assim. |
| Conflito nos 19 JSON                                            | merge de SB em SI                     | Manter os dois blocos; `check:sync` decide.                                                                                                                                                                                            |
| `check:types` quebra por tipo que SA devolveu diferente do §5.2 | B8                                    | SA manda no nome; SB ajusta; divergência anotada no PR.                                                                                                                                                                                |
| Suíte Orca cai na árvore integrada                              | passo 2 de SI                         | Não "arrumar o teste": achar qual sessão quebrou o quê pelo `git bisect` entre as quatro pontas; se não der em 30 min, o PR sai **sem** a branch culpada e o corpo diz qual e por quê.                                                 |
| Store do pnpm frio numa sessão nova                             | `pnpm install` > 5 min                | Deixar rodar em background e seguir lendo código; só SB e SI precisam dele de fato.                                                                                                                                                    |
| Sessão excede ~4 h ou perde contexto                            | —                                     | O push por passo (regra 6) garante que o trabalho parcial está na branch; SI integra o que houver e lista o resto em §6.                                                                                                               |
| PR #15 mesclado no meio da noite                                | `merge-base --is-ancestor` verdadeiro | Nada muda: as branches continuam válidas; SI rebase-a a integração sobre `stage` com merge, não rebase (repositório mescla com merge commit).                                                                                          |

---

## 8. Prompts prontos (copiar como primeira mensagem de cada sessão)

Cada prompt começa igual; só o bloco **Sessão** muda.

```text
Você vai executar UMA sessão do plano da madrugada de 07/09 do projeto
"Gestão de trabalho por área (Orca)" neste fork do Plane CE. Respostas em
português; código, commits e docs técnicas nas convenções do repositório
(inglês, escopo `orca`).

Primeiro, leia o plano pela branch em que ele está:
  git fetch origin claude/fork-architecture-review-pmszir
  git show origin/claude/fork-architecture-review-pmszir:docs/plans/orca-work-management/MADRUGADA-2026-09-07.md
Depois AGENTS.md, FORK.md, docs/plans/orca-work-management/HANDOFF-PROMPT.md
(§Ambiente local), e do RFC docs/orca-work-management-rfc.md as seções que a
sua sessão cita. As decisões M1–M12 do plano estão tomadas; não as reabra —
se algo as contradiz no código, registre no relatório final.

Base da branch (M1): a ponta do PR #15, salvo se ela já for ancestral de stage:
  git fetch origin claude/pendencias-implementacao-auto-7a2rsp stage
  git merge-base --is-ancestor origin/claude/pendencias-implementacao-auto-7a2rsp origin/stage && BASE=origin/stage || BASE=origin/claude/pendencias-implementacao-auto-7a2rsp
  git checkout -b <BRANCH DA SESSÃO> $BASE

Regras (plano §2): só os arquivos da sua sessão; um commit por passo da sua
tabela; teste EXECUTADO antes de marcar (saída em arquivo, `tail -20`); ruff
antes de cada commit em apps/api; `pnpm --filter web fix:format` antes de
cada commit em apps/web; push com `git push -u origin <branch>` ao fim de
CADA passo; não abra PR, não faça merge, não edite arquivo fora da lista da
sua sessão (o quadro do plano e o arquivo da Fase 2 são só de SI).
Relatório final: o que entregou, o que verificou e como, o que não verificou,
o que ficou fora e por quê, SHA da ponta.

Sessão: <COLE AQUI UM DOS BLOCOS ABAIXO>
```

Blocos por sessão:

```text
S0 — Saneamento da documentação. Branch docs/orca-plan-sanitize-2026-09-07.
Siga o plano §4 "S0" passo a passo. Nenhum código. Cada afirmação nova nos
docs precisa de arquivo:linha verificado nesta sessão.
```

```text
SR — Revisão adversarial da stage. Branch docs/orca-stage-review-2026-09-07.
Siga o plano §4 "SR". Só leitura de código e UM arquivo novo em
docs/plans/orca-work-management/reviews/. Ignore os RFCs ao procurar
defeitos; use-os só para saber o que o código PRETENDE fazer. Não corrija
nada.
```

```text
SG — Gates verificáveis. Branch docs/orca-gates-evidence-2026-09-07.
Suba o ambiente local (HANDOFF §Ambiente local) e rode `pnpm install
--frozen-lockfile`. Siga o plano §4 "SG": baseline da suíte Orca, migrações
ida e volta, check:types via turbo, check:lint, check:sync, auditoria no
banco vazio, medição local p50/p95, runbook de desligar a API em
docs/orca-public-api.md. Registre os números nos blocos de gate indicados e
em nenhum outro lugar.
```

```text
SA — Backend 2.1 + 2.2. Branch feat/orca-phase2-backend. Suba o ambiente
local. Siga o plano §4 "SA" na ordem A1→A6 e os contratos de §5.1, §5.2, §5.3,
§5.4. Faça push logo após A2 — outra sessão espera por ele. Ordem de corte
M11: transfer e decisions saem primeiro se faltar tempo. Não toque em
assignment_service.py nem em apps/web.
```

```text
SB — Frontend 2.3-mínimo. Branch feat/orca-phase2-work-tab. Rode `pnpm
install --frozen-lockfile`. Siga o plano §4 "SB" B1→B8 contra o contrato de
§5.2/§5.3/§5.4, ANTES de o backend existir. Prova de cada passo: `pnpm
check:types --filter=web` (via turbo, não `pnpm --filter web check:types`).
Strings novas só no bloco organizational_units.work, em todas as 19 locales
via skill translate. B7 é cortável (M11); B1–B6 não. Em B8, mescle
origin/feat/orca-phase2-backend e alinhe os tipos ao que o backend devolve.
```

```text
SC — 2.4 alertas. Espere `git fetch origin feat/orca-phase2-backend` mostrar
o commit "[2.1] a coordinator reaches the area's projects" e corte a branch
feat/orca-phase2-alerts DALI (não da base). Suba o ambiente local. Siga o
plano §4 "SC" C1→C4 e a decisão M10: você é a única sessão que toca
assignment_service.py, e só no gancho de _apply_queued; falha do broker vira
log, nunca exceção. Não esqueça CELERY_IMPORTS.
```

```text
SI — Integração. Branch feat/orca-phase2-minimum a partir da base M1. Siga o
plano §4 "SI" 1→7: merges na ordem (plan-sanitize → gates-evidence →
backend → alerts → work-tab), verificação completa na árvore
integrada, docs da fase, quadro, RFC §4.2 rev. 7 com M1–M12, renumeração
0139/0140, e dois PRs EM DRAFT contra stage com o template do repositório
(skill create-pull-request). O corpo do PR de bloco lista comandos e
resultados de verificação, o que ficou cortado, e a seção §6 do plano como
próximos passos. Você é a única sessão autorizada a abrir PR.
```

---

## 9. Como saber, às 07:00, se a noite deu certo

- [ ] Dois PRs em draft contra `stage`, verdes ou com CI rodando: docs (S0) e bloco (M12).
- [ ] `reviews/2026-09-07-stage-adversarial-review.md` existe, com veredito de gate.
- [ ] Na árvore do bloco: suíte Orca ≥ baseline de SG sem queda; `check:types`, `check:lint`, `check:format`, `check:sync` exit 0; `makemigrations --check` limpo; `0138 ↔ 0139` ida e volta.
- [ ] Quadro: Fase 2 `[~] 4/6` (ou o que M11 permitiu, dito com precisão); D0 sem "`check:types`" na lista de faltas.
- [ ] RFC cabeçalho, §2.2 e HANDOFF não afirmam mais coisas falsas.
- [ ] §6 deste arquivo copiado para "Próximo item recomendado" do README.

Se só metade disso existir, o que existe está em branches empurradas, com
push por passo, e a manhã começa de onde a noite parou — não do zero.
