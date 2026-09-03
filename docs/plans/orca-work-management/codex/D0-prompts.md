# Prompts do Codex — Fase D0 (Fundação do domínio)

Plano da fase: [`../D0-domain-foundation.md`](../D0-domain-foundation.md).
Contexto obrigatório: [`00-context.md`](./00-context.md).

D0 é o coração do trabalho: fecha os quatro defeitos conhecidos do alocador
(RFC §2.2, D1–D4) e monta fila, executor principal, política, decisão e
auditoria. **Ordem é obrigatória**: D0.1 → D0.2 → D0.3 → D0.4 → D0.5 → D0.6 →
D0.7 → D0.8 → D0.9 → D0.10. Só D0.1 e D0.2 são independentes entre si.

| Item | Perfil | Risco | Observação |
| --- | --- | --- | --- |
| D0.1 | `standard` | baixo | toca API, engine, UI e i18n |
| D0.2 | `standard` | médio | muda comportamento de API pública |
| D0.3, D0.4 | `heavy` | alto | migrações e constraints |
| D0.5 | `heavy` | **o item mais crítico do plano** | concorrência, lock, transação |
| D0.6 | `standard` | médio | endpoints + tipos do front |
| D0.7, D0.8 | `standard` | baixo | comando e observabilidade |
| D0.9, D0.10 | `standard` | baixo | fechamento |

> Antes de despachar D0.5, leia você mesmo o diff de D0.3 e D0.4. Um erro de
> constraint só aparece três itens depois, e sai caro.

---

## D0.1 — Área precisa cobrir o projeto (defeito D1)

```text
Você vai implementar o item D0.1 do plano "Gestão de trabalho por área (Orca)"
neste fork do Plane CE. Faça apenas este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md — inteiro.
2. docs/orca-work-management-rfc.md — §1, §2 (com atenção a §2.2, defeito D1),
   §6.1 (invariante I2). Não reabra decisão fechada de §3.
3. docs/plans/orca-work-management/D0-domain-foundation.md, seção "D0.1".
4. apps/api/plane/app/views/organizational_unit.py — IssueOrganizationalUnitEndpoint
   e IssueOrganizationalUnitAssignEndpoint.
5. apps/api/plane/app/services/orca/assignment_engine.py (~l.105).
6. apps/api/plane/db/models/organizational_unit.py — OrganizationalUnitProject.
7. apps/web/core/components/orca/organizational-units/issue-unit-property.tsx (~l.67).
8. apps/api/plane/utils/orca_error_codes.py e packages/constants/src/orca/error-codes.ts.

DEFEITO
Hoje é possível marcar um item com uma área que não cobre o projeto do item: o
endpoint valida só workspace_id, o dropdown filtra só is_active, e o engine, pior,
"conserta" o problema acrescentando project_id a unit_project_ids quando ele não
está lá. Resultado: alocação para gente que não é membro do projeto. Isso viola a
invariante I2 do RFC.

TAREFA
1. Helper unit_covers_project(unit, project_id) -> bool em
   apps/api/plane/app/services/orca/coverage.py (arquivo novo, com header de
   copyright e docstring @description/@param/@returns). Verdadeiro quando existe
   OrganizationalUnitProject(organizational_unit=unit, project_id=..., deleted_at=null)
   E unit.is_active E project.archived_at is null.
2. IssueOrganizationalUnitEndpoint.post e IssueOrganizationalUnitAssignEndpoint.post
   chamam o helper. Falso → orca_error("ORG_UNIT_NOT_COVERING_PROJECT") com 400.
3. Código de erro novo nos TRÊS lugares (00-context §4): utils/orca_error_codes.py,
   packages/constants/src/orca/error-codes.ts e o catálogo i18n em todas as locales.
4. assignment_engine.py: REMOVA o append de project_id em unit_project_ids. Projeto
   não coberto ⇒ candidates_for retorna lista vazia ⇒ resultado no_eligible_member.
5. UI: issue-unit-property.tsx passa a filtrar unit.is_active && unit.project_ids.includes(projectId).
   Verifique se OrganizationalUnitSerializer já serializa project_ids; se não,
   acrescente como campo read-only (sem N+1: use prefetch, e diga na resposta o que fez).
   Ajuste o tipo em packages/types/src/organizational-unit.ts se necessário.
6. Testes em apps/api/plane/tests/unit/orca/test_issue_unit_coverage.py (novo):
   - área cobre o projeto → 200;
   - área não cobre → 400 com ORG_UNIT_NOT_COVERING_PROJECT;
   - área inativa → 400;
   - projeto arquivado → 400;
   - engine com projeto não coberto → no_eligible_member.
   Nomeie o primeiro negativo test_i2_unit_not_covering_project_rejected.

DEFINIÇÃO DE PRONTO
- test_orca_error_codes.py continua coerente (paridade dos três lugares).
- Nenhum caminho do engine "conserta" cobertura ausente.
- ruff check/format limpos em apps/api.
- Strings i18n em todas as locales; nenhuma locale ficou para trás.

NÃO FAÇA
- Não escreva em ProjectMember (invariante I10).
- Não altere o modelo OrganizationalUnitProject.
- Não rode pnpm check/build nem a suíte; liste os comandos ao desenvolvedor.

AO TERMINAR
- Marque D0.1 [x] no arquivo da fase e atualize a contagem no README do plano.
- Commit: fix(orca): [D0.1] require the unit to cover the work item's project
- Responda no formato da seção 10 do 00-context.md.
```

---

## D0.2 — Remover herança implícita de assignees na API pública (defeito D2)

```text
Você vai implementar o item D0.2 do plano Orca. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §2.2 (defeito D2) e §3 (decisões fechadas).
3. docs/plans/orca-work-management/D0-domain-foundation.md, seção "D0.2".
4. apps/api/plane/api/serializers/issue.py, ~l.191, bloco marcado
   "# ORCA CUSTOM FEATURE: Default to assignees of user's last created issue".
5. O mesmo trecho no commit upstream base:
   git show 5662b7610:apps/api/plane/api/serializers/issue.py
6. apps/api/plane/tests/unit/orca/test_issue_serializer_orca_features.py.

DEFEITO
Um item criado pela API pública sem assignees herda os assignees do último item
criado por aquele usuário. Para um robô, isso significa que todo item criado
cai em cima de quem quer que tenha recebido o item anterior — invisível,
não determinístico, e incompatível com a alocação por área que estamos montando.

TAREFA
1. No serializer PÚBLICO (plane/api/serializers/issue.py): restaure o
   comportamento upstream — sem assignees no payload, usar project.default_assignee
   se válido, e nada além disso. Copie literalmente do commit 5662b7610.
2. Decida e execute UMA das duas saídas para a UI (o plano permite as duas):
   a) Se a herança for desejada na interface, mova a lógica para o serializer
      INTERNO (plane/app/serializers/issue.py) atrás de um flag
      ProjectCustomSettings.remember_last_assignees (boolean, default False,
      migração própria, dependência explícita na última Orca) e exponha o toggle
      em apps/web/core/components/project/settings/features-list.tsx.
   b) Se ninguém reivindicar, apenas remova.
   NA DÚVIDA, FAÇA (b) e diga na resposta que (a) continua disponível — é a
   opção reversível e de menor superfície.
3. Atualize test_issue_serializer_orca_features.py: o teste que hoje espera a
   herança passa a esperar default_assignee no caminho público.
4. Registre a mudança de comportamento: linha no README.md do fork (tabela de
   features) e uma linha em docs/orca-work-management-rfc.md §4.2 dizendo o que
   mudou e por quê.

DEFINIÇÃO DE PRONTO
- POST /api/v1 sem assignees em projeto sem default_assignee → zero assignees.
- POST /api/v1 sem assignees em projeto com default_assignee → esse assignee.
- Nenhuma consulta ao "último item criado" sobrou no caminho público (grep).
- ruff limpo.

NÃO FAÇA
- Não altere o comportamento de criação pela UI sem o flag (se escolher (a)).
- Não toque em apps/api/plane/app/views/issue/ (00-context §2.7).

AO TERMINAR
- Marque D0.2 [x] e atualize a contagem no README do plano.
- Commit: fix(orca): [D0.2] stop inheriting assignees in the public API
- Responda no formato da seção 10 do 00-context.md, dizendo qual saída escolheu.
```

---

## D0.3 — Migração 0135: estado de fila e executor principal

```text
Você vai implementar o item D0.3 do plano Orca. Só este item. É uma migração:
leia duas vezes antes de escrever.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md — com atenção à seção 5.
2. docs/orca-work-management-rfc.md §5.1 (campos, CHECKs e índices exatos),
   §6.1 (invariantes I3, I5) e §6.2 (máquina de estados).
3. docs/plans/orca-work-management/D0-domain-foundation.md, seção "D0.3".
4. apps/api/plane/db/models/organizational_unit.py — modelo IssueOrganizationalUnit.
5. apps/api/plane/db/migrations/0134_orca_user_language_preference.py e
   0131_issue_organizational_unit.py — o padrão a seguir.

TAREFA
1. Em IssueOrganizationalUnit, acrescente os campos do RFC §5.1: routing_state,
   queue_reason, queued_at, assignment_due_at, primary_executor
   (FK User, on_delete=SET_NULL, null=True, related_name="orca_primary_executions").
   NÃO crie current_assignment_decision agora: ele entra na 0137 (item D0.4), para
   evitar dependência circular. Deixe um comentário dizendo isso.
2. Choices como TextChoices no mesmo arquivo: RoutingState e QueueReason, com os
   valores exatos do RFC §6.2. Docstring @description em cada uma.
3. Migração apps/api/plane/db/migrations/0135_orca_issue_routing_state.py,
   dependencies=[("db", "0134_orca_user_language_preference")], nesta ordem:
   a. AddField dos cinco campos (nullable/default seguros);
   b. RunPython idempotente: para cada IssueOrganizationalUnit com deleted_at null,
      se existe IssueAssignee(issue, deleted_at=null) → routing_state="assigned" e
      primary_executor = o assignee de menor created_at; senão routing_state="queued",
      queue_reason="new_item", queued_at=now(). Use o padrão de migração de dados do
      repositório (apps.get_model, iteração em lotes com iterator()/bulk_update).
      Reversa: no-op documentado;
   c. AddConstraint dos dois CHECKs do RFC §5.1 — o que exige primary_executor em
      routing_state="assigned" DEPOIS do RunPython;
   d. AddIndex dos dois índices do RFC §5.1.
4. Testes em apps/api/plane/tests/unit/orca/test_routing_state.py:
   - assigned sem executor → IntegrityError;
   - queued com executor → IntegrityError;
   - a função de migração de dados, chamada diretamente com dados montados
     (use django_test_migrations se já estiver disponível; se não, teste a função).
   @pytest.mark.unit e, para os IntegrityError, transação própria.

DEFINIÇÃO DE PRONTO
- A migração é reversível: migrate db 0134 e voltar funciona (o desenvolvedor
  confirma; diga o comando).
- Nenhum campo novo em tabela core — só em IssueOrganizationalUnit, que é do fork.
- ruff limpo.

NÃO FAÇA
- Não rode makemigrations nem migrate (00-context §6). Escreva o arquivo à mão,
  seguindo o padrão, e AVISE na resposta que ele precisa de makemigrations --check.
- Não edite migração já existente.
- Não invente campo que o RFC §5.1 não pede.

AO TERMINAR
- Marque D0.3 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [D0.3] add routing state and primary executor to the unit link
- Responda no formato da seção 10 do 00-context.md, com o SQL que os CHECKs geram.
```

---

## D0.4 — Migrações 0136 e 0137: política, decisão e evento

```text
Você vai implementar o item D0.4 do plano Orca. Só este item. Depende de D0.3
mesclado: confirme que 0135 existe antes de começar.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md, seção 5.
2. docs/orca-work-management-rfc.md §5.2 — os três modelos, campo a campo, com as
   constraints; e §6.1 (I5, I6).
3. docs/plans/orca-work-management/D0-domain-foundation.md, seção "D0.4".
4. apps/api/plane/db/models/organizational_unit.py e db/models/__init__.py
   (seção Orca dos exports).

TAREFA
1. Arquivo novo apps/api/plane/db/models/organizational_assignment.py (header de
   copyright), com:
   - OrganizationalUnitAssignmentPolicy — RFC §5.2, incluindo as DUAS constraints
     parciais de unicidade (uma política padrão por área; uma por área↔projeto) e
     version incrementado em save().
   - AssignmentDecision — RFC §5.2, append-only: save() levanta ValueError se o
     objeto já existe no banco (self.pk definido e não _state.adding). Nada de
     update, nunca.
   - IssueResponsibilityEvent — RFC §5.2, mesmo tratamento append-only.
   TextChoices no mesmo arquivo: AssignmentMode (manual, self_claim, least_loaded,
   explicit), RequestedAssignmentMode (os anteriores + default), PolicySource,
   DecisionTrigger, DecisionOutcome, ResponsibilitySource — valores exatos do RFC.
2. Exporte os três em db/models/__init__.py, na seção Orca.
3. clean() da política: default_mode precisa estar em allowed_modes, senão
   ValidationError com mensagem clara.
4. Migração 0136_orca_assignment_policy.py (política) e 0137_orca_assignment_decision.py
   (decisão, evento e o campo current_assignment_decision em IssueOrganizationalUnit,
   FK SET_NULL para AssignmentDecision). dependencies explícitas e encadeadas.
5. Testes em apps/api/plane/tests/unit/orca/test_assignment_models.py:
   - duas políticas padrão na mesma área → erro; duas para o mesmo unit↔projeto →
     erro; uma padrão + uma por projeto → ok;
   - allowed_modes sem default_mode → ValidationError no clean();
   - version incrementa a cada save;
   - decisão e evento recusam update (assertRaises ValueError);
   - AssignmentDecision aceita supersedes apontando para outra decisão.

DEFINIÇÃO DE PRONTO
- Os três modelos batem campo a campo com o RFC §5.2. Liste na resposta qualquer
  divergência que você tenha julgado necessária, com a justificativa.
- Nenhuma alteração em tabela core.
- ruff limpo.

NÃO FAÇA
- Não implemente serviço nem view aqui: este item é só schema (o serviço é D0.5).
- Não rode makemigrations/migrate; escreva à mão e avise.

AO TERMINAR
- Marque D0.4 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [D0.4] add assignment policy, decision and responsibility event
- Responda no formato da seção 10 do 00-context.md.
```

---

## D0.5 — `assignment_service.py` *(item mais crítico do plano)*

```text
Você vai implementar o item D0.5 do plano Orca: o serviço de alocação. É o item
mais crítico de toda a especificação — todos os outros dependem de ele estar
correto sob concorrência. Faça só este item, e não tenha pressa.

LEIA ANTES DE EDITAR (tudo, sem pular)
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md: §6 INTEIRO (invariantes, máquina de estados,
   resolução de política, ranking lb-1, concorrência, SLA, encaminhamento) e §10
   (matriz de testes — é o seu checklist de saída).
3. docs/plans/orca-work-management/D0-domain-foundation.md, seção "D0.5", com as
   assinaturas exatas das funções.
4. apps/api/plane/app/services/orca/assignment_engine.py (o que vai virar legado),
   org_unit_reconciler.py (estilo de serviço do repositório) e coverage.py (D0.1).
5. apps/api/plane/db/models/organizational_assignment.py (D0.4) e
   organizational_unit.py (D0.3).
6. apps/api/plane/tests/unit/orca/conftest.py e test_assignment_engine.py.

TAREFA
1. Arquivo novo apps/api/plane/app/services/orca/assignment_service.py com as
   funções da seção D0.5 do plano, cada uma com docstring
   @description/@param/@returns e as assinaturas EXATAS lá descritas:
   resolve_policy, rank_candidates, allocate, claim, reassign, return_to_queue,
   transfer_unit, set_responsibility.
2. Regras que não podem ser violadas em nenhum caminho:
   - allocate roda dentro de transaction.atomic() com select_for_update() no link
     IssueOrganizationalUnit;
   - para requested_mode=least_loaded, e SÓ para ele, tome o advisory lock por
     unidade: helper unit_allocation_lock(unit_id) executando
     SELECT pg_advisory_xact_lock(hashtext(%s)) dentro da transação. Fora do
     Postgres, no-op (o runner é Postgres; o no-op é só para não quebrar import);
   - valida I2 (cobertura, via coverage.py) e I4 (executor é membro ativo da área
     E ProjectMember ativo do projeto) antes de decidir;
   - NUNCA remove IssueAssignee existente. Reatribuir mantém o anterior como
     colaborador;
   - toda mudança de primary_executor ou routing_state grava uma AssignmentDecision
     (I5) e atualiza current_assignment_decision;
   - NENHUMA escrita em ProjectMember (I10). Isso é verificável por grep e será
     verificado.
3. Ranking lb-1 (RFC §6.4), determinístico: carga conta APENAS onde a pessoa é
   primary_executor com routing_state=assigned e o estado nativo não está em grupo
   fechado. Desempate final por user_id, sempre. Retorne eleitos ordenados E
   excluídos com excluded_reason — o snapshot é o que torna a decisão auditável.
4. Erros de domínio em apps/api/plane/app/services/orca/errors.py: OrcaDomainError
   base com atributo error_code e http_status; AssignmentModeNotAllowed,
   UnitNotCoveringProject, ExecutorNotEligible, AlreadyClaimed, DecisionStale,
   InvalidTransition. As views convertem com um único
   except OrcaDomainError as e: return orca_error(e.error_code, e.http_status).
5. Códigos novos nos três lugares (00-context §4): ORG_ASSIGNMENT_MODE_NOT_ALLOWED,
   ORG_EXECUTOR_NOT_ELIGIBLE, ORG_WORK_ITEM_ALREADY_CLAIMED, ORG_DECISION_STALE,
   ORG_INVALID_ROUTING_TRANSITION.
6. reassign implementa If-Match semântico: expected_decision_id precisa ser o
   current_assignment_decision atual; divergente → DecisionStale. A decisão nova
   aponta supersedes para a anterior.
7. claim: exige routing_state em (queued, allocation_failed) e política efetiva
   self_claim (ou ator coordenador/admin). Sob corrida, um vence e os demais
   recebem AlreadyClaimed com o vencedor no erro.
8. assignment_engine.py passa a delegar: assign_from_unit chama
   allocate(..., requested_mode="least_loaded"). Docstring nova: "legado, mantido
   para compatibilidade dos chamadores atuais; remover na Fase 2". Nenhuma lógica
   de ranking sobra nele.
9. Logger plane.orca.assignment em cada decisão, com extra= contendo
   workspace_id, unit_id, issue_id, decision_id, mode, outcome, trigger.
10. Testes:
   - apps/api/plane/tests/unit/orca/test_assignment_service.py: todas as linhas
     "Resolução de política", "Ranking lb-1", "Estados", "Decisões" e
     "Encaminhamento" da matriz do RFC §10, uma função por linha, nomeada com o
     identificador quando houver (test_i4_executor_not_project_member_rejected);
   - apps/api/plane/tests/unit/orca/test_assignment_concurrency.py:
     @pytest.mark.django_db(transaction=True), ThreadPoolExecutor, UMA conexão por
     thread com connection.close() no finally de cada worker.
     Cenário A: 4 membros, 20 alocações least_loaded simultâneas → 5/5/5/5, exato.
     Cenário B: 10 claims simultâneos no mesmo item → 1 sucesso, 9 AlreadyClaimed.
     Cenário C: 2 reassign simultâneos com o mesmo expected_decision_id → 1 sucesso,
     1 DecisionStale.

DEFINIÇÃO DE PRONTO
- grep -rn "ProjectMember" apps/api/plane/app/services/orca/assignment_service.py
  não retorna nenhuma escrita (só leitura para validar I4).
- assignment_engine.py não contém mais ranking próprio.
- Os três cenários de concorrência existem e são determinísticos (sem sleep
  arbitrário como sincronização; use barreira/evento).
- ruff check/format limpos.

NÃO FAÇA
- Não crie endpoint nenhum aqui (isso é D0.6).
- Não altere as migrações de D0.3/D0.4 — se faltar campo, PARE e reporte.
- Não "simplifique" o lock para um lock em Python: precisa ser no banco, porque
  há mais de um worker.
- Não rode a suíte; passe o comando ao desenvolvedor:
  docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/orca/test_assignment_concurrency.py -q

AO TERMINAR
- Marque D0.5 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [D0.5] add the assignment service with policy resolution and locking
- Responda no formato da seção 10 do 00-context.md, com uma seção extra
  "Mapa RFC §10 → teste" (uma linha por linha da matriz, com o nome do teste).
```

---

## D0.6 — Endpoints internos passam a usar o serviço

```text
Você vai implementar o item D0.6 do plano Orca. Só este item. Depende de D0.5.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §8.1 (endpoints internos) e §6.2.
3. docs/plans/orca-work-management/D0-domain-foundation.md, seção "D0.6".
4. apps/api/plane/app/views/organizational_unit.py, urls/orca.py,
   serializers/organizational_unit.py.
5. apps/api/plane/tests/unit/orca/test_issue_organizational_unit_http.py e
   test_organizational_unit_http.py.
6. packages/types/src/organizational-unit.ts e
   apps/web/core/components/orca/organizational-units/issue-unit-property.tsx.

TAREFA
1. IssueOrganizationalUnitEndpoint.post → chama set_responsibility(...) do serviço.
   A resposta passa a incluir routing_state, queue_reason, primary_executor e a
   decisão.
2. .get → mesmos campos.
3. .delete → grava IssueResponsibilityEvent(to_unit=None) ANTES de apagar o link,
   e não remove IssueAssignee (RFC §6.2, último parágrafo).
4. IssueOrganizationalUnitAssignEndpoint.post → allocate(...,
   requested_mode=request.data.get("mode", "least_loaded"), trigger="internal_api").
   O modo antigo "append" continua aceito, mapeado para "acrescentar colaborador",
   com comentário e docstring dizendo DEPRECATED e desde quando.
5. Novo OrganizationalUnitPolicyEndpoint:
   GET .../organizational-units/{unit_id}/policy/ e
   GET .../organizational-units/{unit_id}/projects/{pk}/policy/ devolvendo a
   política efetiva resolvida (sem requested_mode). Permissão: Admin/Member/Guest
   do workspace, igual aos outros GETs do arquivo. Registre em urls/orca.py.
6. Serializers novos em serializers/organizational_unit.py: AssignmentPolicySerializer,
   AssignmentDecisionSerializer, IssueRoutingSerializer.
7. Todas as views convertem erro de domínio com o único
   except OrcaDomainError as e: return orca_error(e.error_code, e.http_status).
8. Frontend mínimo (a UI completa é Fase 2): tipos em
   packages/types/src/organizational-unit.ts; issue-unit-property.tsx exibe
   routing_state e o executor principal. Strings novas em todas as locales.
9. Atualize test_issue_organizational_unit_http.py e test_organizational_unit_http.py
   para o payload novo, e acrescente o caso de delete gerando evento.

DEFINIÇÃO DE PRONTO
- Nenhuma view refaz lógica que o serviço já tem (grep por ranking/carga nas views).
- Toda rota nova continua sob OrganizationalUnitFeatureMixin: com
  ORCA_ORG_UNITS_ENABLED=0 responde 404. Teste isso.
- i18n em todas as locales; ruff limpo.

NÃO FAÇA
- Não escreva em ProjectMember.
- Não construa a aba de fila nem modais (Fase 2).

AO TERMINAR
- Marque D0.6 [x] e atualize a contagem no README do plano. Liste os comandos de
  verificação do front (pnpm --filter web check:types, check:lint) para o desenvolvedor.
- Commit: feat(orca): [D0.6] route the internal endpoints through the assignment service
- Responda no formato da seção 10 do 00-context.md.
```

---

## D0.7 — Comando `audit_organizational_routing`

```text
Você vai implementar o item D0.7 do plano Orca. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §6.1 (I3 e I4).
3. docs/plans/orca-work-management/D0-domain-foundation.md, seção "D0.7".
4. apps/api/plane/db/management/commands/reconcile_organizational_access.py —
   esqueleto, flags e formato de saída a imitar.
5. apps/api/plane/tests/unit/orca/test_reconcile_command.py — o padrão de teste.

TAREFA
1. Comando apps/api/plane/db/management/commands/audit_organizational_routing.py
   com as flags --workspace, --write (dry-run é o default) e saída tabular, igual
   ao reconcile.
2. Verificações:
   a. routing_state=assigned sem IssueAssignee ativo do primary_executor (viola I3);
   b. assigned com executor que não é mais membro ativo da área ou do projeto (I4);
   c. queued/allocation_failed com IssueAssignee ativo mas sem primary_executor —
      APENAS mostrar; pode ser colaborador legítimo, não corrigir;
   d. política com default_mode fora de allowed_modes.
3. --write: só para (a) e (b), chamando
   return_to_queue(..., queue_reason="executor_unavailable", trigger="command").
   (c) e (d) nunca são corrigidos automaticamente.
4. Saída: uma linha por violação com workspace, área, item, tipo de violação e o
   que seria feito; resumo por tipo no fim. Código de saída 0 mesmo com violações
   em dry-run (é relatório), diferente de zero só em erro de execução.
5. Teste apps/api/plane/tests/unit/orca/test_audit_routing_command.py com um caso
   de cada violação, em dry-run (nada muda) e em --write (só a e b mudam).
6. docs/organizational-units.md: documente o comando ao lado do reconcile, com
   exemplo de uso e periodicidade sugerida (diário, dry-run).

DEFINIÇÃO DE PRONTO
- Dry-run não escreve nada — teste prova (contagem de AssignmentDecision antes/depois).
- ruff limpo.

NÃO FAÇA
- Não escreva em ProjectMember (o reconcile é quem faz isso).
- Não corrija (c) nem (d) automaticamente, mesmo parecendo óbvio.

AO TERMINAR
- Marque D0.7 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [D0.7] add the routing audit management command
- Responda no formato da seção 10 do 00-context.md.
```

---

## D0.8 — Observabilidade mínima

```text
Você vai implementar o item D0.8 do plano Orca. Só este item. É pequeno.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §11 (nomes das métricas — use exatamente esses).
3. docs/plans/orca-work-management/D0-domain-foundation.md, seção "D0.8".
4. apps/api/plane/app/services/orca/assignment_service.py (D0.5).

TAREFA
1. Módulo novo apps/api/plane/app/services/orca/metrics.py com
   record_assignment_outcome(mode, outcome, trigger),
   record_no_candidate(unit_id) e
   record_decision_superseded(unit_id, previous_mode).
   Implementação inicial: log estruturado nível INFO, com os nomes de métrica do
   RFC §11 e os campos em extra=. Docstring explicando que este é o único ponto a
   trocar quando o projeto adotar Prometheus/StatsD.
2. assignment_service.py chama as três nos pontos certos, sem duplicar o log que
   já existe (se o log de decisão de D0.5 cobre o mesmo evento, unifique).
3. Teste apps/api/plane/tests/unit/orca/test_orca_metrics.py usando caplog:
   confere nome do evento e cada campo esperado.

DEFINIÇÃO DE PRONTO
- Nenhum caminho de alocação fica sem registro de outcome.
- ruff limpo.

NÃO FAÇA
- Não adicione dependência nova (nada de prometheus_client aqui).

AO TERMINAR
- Marque D0.8 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [D0.8] record assignment outcomes through a metrics module
- Responda no formato da seção 10 do 00-context.md.
```

---

## D0.9 — Fechar a matriz de testes da fase

```text
Você vai implementar o item D0.9 do plano Orca: fechar a matriz de testes. Não é
para escrever código de produção — se um teste revelar defeito, REPORTE, não conserte.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §10 INTEIRO e §6.1.
3. docs/plans/orca-work-management/D0-domain-foundation.md, seções "D0.9" e "Gate D0".
4. Todos os testes já existentes em apps/api/plane/tests/unit/orca/.

TAREFA
1. Percorra as linhas do RFC §10: I2 cobertura, resolução de política, ranking,
   estados, decisões, concorrência, encaminhamento, kill switches, permissões dos
   endpoints internos alterados.
2. Para cada linha, localize o teste que já a cobre. Se não houver, escreva —
   nomeado com o identificador (test_i2_..., test_i5_...).
3. Kill switch: teste explícito de que ORCA_ORG_UNITS_ENABLED=0 devolve 404 em
   TODAS as rotas novas e antigas de organizational unit.
4. Preencha a tabela "Testes por invariante" ao final de
   docs/plans/orca-work-management/D0-domain-foundation.md com os nomes reais
   dos testes, I1 a I7.
5. Acrescente a apps/api/tests/TESTING_GUIDE.md a seção "Testes de concorrência"
   com o padrão usado em test_assignment_concurrency.py (transaction=True, conexão
   por thread, barreira, por que não usar sleep).

DEFINIÇÃO DE PRONTO
- Nenhuma linha do RFC §10 fica sem pelo menos um teste nomeado.
- A tabela de invariantes do arquivo da fase está preenchida com nomes reais.
- ruff limpo.

NÃO FAÇA
- Não mude código de produção para fazer teste passar. Um teste que falha é um
  achado: registre em "Riscos e decisões" com arquivo, linha e o que observou.
- Não marque o Gate D0 como fechado: isso é decisão humana.

AO TERMINAR
- Marque D0.9 [x] e atualize a contagem no README do plano.
- Commit: test(orca): [D0.9] close the D0 test matrix
- Responda no formato da seção 10 do 00-context.md, com a tabela linha do §10 →
  teste, e a lista de achados (se houver).
```

---

## D0.10 — Documentação da fase

```text
Você vai implementar o item D0.10 do plano Orca: documentação. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/organizational-units.md inteiro (é o que você vai reescrever em parte).
3. docs/orca-work-management-rfc.md §2.1 (tabela do que existe) e §6.
4. O diff acumulado da fase: git log --oneline origin/stage..HEAD e
   git diff origin/stage...HEAD --stat.

TAREFA
1. docs/organizational-units.md, seção "Assignment" reescrita: estados de fila,
   política e sua resolução, executor principal, decisões e auditoria, o comando
   audit_organizational_routing, e o que mudou em relação à v1 (o modo append
   virou colaborador e está DEPRECATED).
   Escreva para quem administra o workspace, não para quem escreveu o código:
   cada conceito com uma frase do que é e uma do que muda na prática.
2. docs/orca-work-management-rfc.md §2.1: as linhas "Estado de fila", "Executor
   principal" e "Alocar ao menos carregado" passam a "Sim", com a referência do item.
3. docs/plans/orca-work-management/D0-domain-foundation.md e o README.md do plano:
   estado final dos itens e a contagem correta.
4. Se algo do desenho mudou durante a fase, uma linha por mudança em
   docs/orca-work-management-rfc.md §4.2, com data.

DEFINIÇÃO DE PRONTO
- Nenhuma afirmação da doc contradiz o código entregue na fase. Verifique as
  afirmações que você escrever contra o código, não contra o plano.
- Nenhum trecho da doc antiga que ainda vale foi apagado.

NÃO FAÇA
- Não escreva a data do Gate D0 nem marque o gate como fechado (é humano).

AO TERMINAR
- Marque D0.10 [x] e atualize a contagem no README do plano.
- Commit: docs(orca): [D0.10] document queue state, policies and the routing audit
- Responda no formato da seção 10 do 00-context.md.
```
