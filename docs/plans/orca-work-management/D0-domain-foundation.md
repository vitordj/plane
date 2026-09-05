# Fase D0 — Fundação do domínio

**Objetivo:** fechar os quatro defeitos conhecidos do alocador (RFC §2.2,
D1–D4), introduzir o estado de fila, o executor principal, a política de
alocação e a auditoria de decisões, e reescrever o engine com lock e
determinismo. Tudo atrás do kill switch existente; nenhuma capacidade nova
exposta a robôs ainda.
**Pré-requisitos:** nenhum. Corre em paralelo com P0.
**Referência:** RFC §5.1, §5.2 (três primeiras tabelas), §6 inteiro, §9
(Fase D0), §10.
**Ordem recomendada:** D0.1 → D0.2 → D0.3 → D0.4 → D0.5 → D0.6 → D0.7 →
D0.8 → D0.9 (os testes acompanham cada item; D0.9 é o fechamento da matriz)
→ D0.10.

Todo item que toca alocação termina com teste de concorrência. Comandos são
para o desenvolvedor rodar; a sessão de agente não executa migrações nem
suíte completa (AGENTS.md).

---

## D0.1 — Área precisa cobrir o projeto (defeito D1) `[x]`

**Onde estava o defeito.**
- `apps/api/plane/app/views/organizational_unit.py`, `IssueOrganizationalUnitEndpoint.post`: validava só `workspace_id`.
- `apps/web/core/components/orca/organizational-units/issue-unit-property.tsx` l.67: filtrava só `unit.is_active` — com um comentário logo acima afirmando que só áreas que cobrem o projeto apareciam. O comentário descrevia a intenção; o código não a implementava.
- `apps/api/plane/app/services/orca/assignment_engine.py` ~l.105: acrescentava `project_id` a `unit_project_ids` quando não estava lá, o que transformava "não coberto" em "coberto" **e** fazia trabalho de um projeto que a área não possui contar na carga dos membros dela.

**Mudança** (entregue em `feat/orca-unit-project-coverage`).
- `services/orca/coverage.py` (novo): `unit_covers_project(unit, project_id)` — área ativa, link vivo (o manager padrão já exclui soft-deleted) e projeto não arquivado. Projeto arquivado não concede nada, então uma área ligada só a projetos arquivados não cobre nenhum.
- `IssueOrganizationalUnitEndpoint.post` e `IssueOrganizationalUnitAssignEndpoint.post` chamam o helper; falha → `ORG_UNIT_NOT_COVERING_PROJECT` (4916). A checagem é feita **de novo** na rota de atribuição, não só na criação do vínculo: o projeto pode ser desvinculado ou arquivado depois que o item já estava marcado como da área.
- Engine: o `append` saiu. Coberto passou a ser pré-condição, verificada logo no começo de `candidates_for`, que devolve `[]` quando não é o caso.
- Código novo nos três lugares (`orca_error_codes.py`, `error-codes.ts`, catálogo i18n nas 19 locales).
- API: `OrganizationalUnitSerializer` ganha `project_ids` (read-only, sem projetos arquivados). O endpoint de lista alimenta o campo com um `Prefetch(..., to_attr=...)`, então listar áreas continua sendo uma consulta e não uma por área; a serialização de uma única área cai numa consulta filtrada.
- UI: o dropdown filtra `unit.is_active && unit.project_ids.includes(projectId)` — o comentário passou a ser verdade.
- `docs/organizational-units.md` §Assignment descreve a regra, o código de erro e o campo novo.

**Testes.** `test_issue_unit_coverage.py` (novo): a regra em si (ligado, não
ligado, ligado a outro projeto, área inativa, projeto arquivado, link
removido, argumentos nulos); a recusa nas duas rotas com o código certo,
inclusive o caso em que a cobertura some depois de o vínculo existir; o engine
sem candidatos e `no_eligible_member`; e o serializer, incluindo que trabalho
num projeto não coberto **deixou** de inflar a carga. Em
`test_issue_organizational_unit_http.py`, os testes que marcavam uma área
responsável passaram a ligar o projeto antes (fixture `covering_unit`) — o
comportamento mudou, e é isso que eles agora afirmam.

**Aceite.**
- [x] Ruff limpo nos arquivos tocados; paridade dos quatro lugares do código de erro conferida fora do pytest (17 códigos, mapa TS e catálogo en batem). Testes escritos; a sessão não roda pytest (AGENTS.md) — confirmar no CI de `stage`.
- [ ] `check:sync` do i18n verde (a sessão não tem `node_modules`; a paridade de chaves das 19 locales foi conferida com uma comparação equivalente de conjuntos de chaves).

**Migração.** Nenhuma — o defeito era de validação, não de esquema.

**Efeito em dados existentes.** Vínculos já gravados em projetos não cobertos
continuam no banco e não são apagados por este item: a partir de agora eles
recusam atribuição (com o código novo) em vez de atribuir errado. Se aparecer
algum em produção, o caminho é vincular o projeto à área ou trocar a área do
item — vale um levantamento antes de ligar a camada num workspace que já usa
áreas.

---

## D0.2 — Remover herança implícita de assignees na API pública (defeito D2) `[x]`

**Onde estava.** `apps/api/plane/api/serializers/issue.py` (~l.188) e o mesmo
bloco em `apps/api/plane/app/serializers/issue.py`: sem `assignees`, o item
copiava os assignees do último item criado pela mesma pessoa naquele projeto.

**Mudança** (entregue em `feat/orca-unit-project-coverage`).
- O bloco upstream foi restaurado **literalmente** (copiado de `5662b7610`) nos dois serializers: sem `assignees`, usa o `default_assignee` do projeto se ele ainda for um membro ativo com papel ≥ 15; nada além disso.
- **Decisão tomada nesta sessão:** removido também do serializer interno, não só do público. O plano deixava a alternativa de manter na UI atrás de `ProjectCustomSettings.remember_last_assignees`; ninguém reivindicou o comportamento, e o próprio enunciado manda remover nesse caso. Um toggle que ninguém pediu é código morto com migração junto. Se aparecer demanda, a lógica está no histórico e vira item próprio.

**Por que era defeito e não feature.** Para um robô que posta um item sem
assignees, a escolha dependia de histórico invisível: o mesmo request produzia
resultados diferentes conforme o que aquela conta tivesse criado antes. O
contrato do `/api/v1` também divergia em silêncio do upstream, o que é
exatamente o que um fork não deve fazer numa rota pública.

**Testes.** `test_issue_serializer_orca_features.py`: a classe de herança deu
lugar a `TestDefaultAssignee` (endpoint interno — sem default, lista
explícita, default aplicado, default que saiu do projeto, default rebaixado a
guest) e a `TestPublicApiDefaultAssignee`, que exercita
`plane.api.serializers.IssueSerializer.create` direto (a rota pública
autentica por API key; o que importa aqui é quais linhas o `create` grava).
Os dois lados têm um teste que **fixa a ausência** da herança — é o que pega
o comportamento voltando.

**Aceite.**
- [x] Criar item via `/api/v1` sem `assignees` em projeto sem `default_assignee` → zero assignees (teste direto do serializer).
- [x] Criar item via `/api/v1` sem `assignees` em projeto com `default_assignee` → esse assignee.
- [ ] Registrar a mudança de comportamento nas notas do próximo release. O `CHANGELOG` é gerado pelo Release Please a partir das mensagens de commit, e o corpo do commit deste item descreve a mudança; **não** foi marcado `BREAKING CHANGE:` de propósito — com a versão em 1.x isso dispararia um bump major.
- [ ] README: não há linha na tabela de features anunciando a herança (nunca houve), então não há o que corrigir lá. Fica registrado para o caso de alguém procurar.

**Arquivos:** `apps/api/plane/api/serializers/issue.py`, `apps/api/plane/app/serializers/issue.py`, `apps/api/plane/tests/unit/orca/test_issue_serializer_orca_features.py`.

---

## D0.3 — Migração 0135: estado de fila e executor principal `[ ]`

**Modelo.** `apps/api/plane/db/models/organizational_unit.py`,
`IssueOrganizationalUnit`, campos e constraints do RFC §5.1:
`routing_state`, `queue_reason`, `queued_at`, `assignment_due_at`,
`primary_executor` (FK `User`, `SET_NULL`, `related_name="orca_primary_executions"`),
`current_assignment_decision` (FK para modelo do D0.4; usar string
`"db.AssignmentDecision"` e criar a FK em `0137`, ou criar `0135` sem esse
campo e adicioná-lo em `0137` — preferir a segunda opção para evitar
dependência circular).

Choices como `TextChoices` no mesmo arquivo: `RoutingState`, `QueueReason`.

**Migração.**
1. `python3 apps/api/manage.py makemigrations db -n orca_issue_routing_state` → revisar; dependência `("db", "0134_orca_user_language_preference")`.
2. `RunPython` idempotente: para cada `IssueOrganizationalUnit` sem `deleted_at`: se existe `IssueAssignee(issue, deleted_at=null)` → `routing_state=assigned`, `primary_executor` = assignee com menor `created_at`; senão `queued`, `queue_reason=new_item`, `queued_at=now`.
3. `AddConstraint` para os dois CHECKs e `AddIndex` para os dois índices do RFC §5.1. O CHECK que exige `primary_executor` em `assigned` deve ser adicionado **depois** do `RunPython`.
4. Reverso do `RunPython`: no-op (campos são removidos pelo reverso dos `AddField`).

**Testes** (`test_routing_state.py`):
- CHECKs: salvar `assigned` sem executor → `IntegrityError`; `queued` com executor → `IntegrityError`.
- Data migration: usar `django_test_migrations` se disponível; senão, testar a função de migração diretamente com dados montados.

**Aceite.**
- [ ] `makemigrations --check` limpo após a migração.
- [ ] Migração aplicada e revertida com sucesso num banco local com dados (`migrate db 0134` e volta).
- [ ] Testes verdes.

---

## D0.4 — Migrações 0136 e 0137: política, decisão e evento de responsabilidade `[ ]`

**Modelos.** Novo arquivo `apps/api/plane/db/models/organizational_assignment.py`
(exportar em `db/models/__init__.py`, na seção Orca, com header de copyright):
- `OrganizationalUnitAssignmentPolicy` — RFC §5.2, incluindo as duas
  constraints parciais de unicidade e `version` incrementado em `save()`.
- `AssignmentDecision` — RFC §5.2. Bloquear `update`: sobrescrever `save()`
  para levantar `ValueError` se `self.pk` já existe e o objeto veio do banco
  (append-only), e cobrir por teste.
- `IssueResponsibilityEvent` — RFC §5.2, mesmo tratamento append-only.

`0137` também adiciona `current_assignment_decision` em
`IssueOrganizationalUnit` (ver D0.3).

Choices como `TextChoices`: `AssignmentMode` (`manual`, `self_claim`,
`least_loaded`, `explicit`), `RequestedAssignmentMode` (os anteriores +
`default`), `PolicySource`, `DecisionTrigger`, `DecisionOutcome`,
`ResponsibilitySource`.

**Testes** (`test_assignment_models.py`):
- unicidade: duas políticas padrão na mesma área → erro; duas para o mesmo `unit_project` → erro; uma padrão e uma por projeto → ok.
- `allowed_modes` sem `default_mode` → `ValidationError` no `clean()`.
- `version` incrementa a cada save.
- decisão e evento não aceitam update.

**Aceite.**
- [ ] `makemigrations --check` limpo.
- [ ] Testes verdes.

---

## D0.5 — `assignment_service.py`: resolução, ranking `lb-1`, alocação, claim, reatribuição, devolução, transferência `[ ]`

**Arquivo novo.** `apps/api/plane/app/services/orca/assignment_service.py`.
`assignment_engine.py` passa a delegar (`assign_from_unit` chama
`allocate(..., requested_mode="least_loaded")`) e recebe docstring "legado,
mantido para compatibilidade dos chamadores atuais; remover na Fase 2".

**Funções e contratos** (docstrings no formato `@description/@param/@returns`):

```python
resolve_policy(unit, project_id, requested_mode: str | None) -> PolicyResolution
    # RFC §6.3. Levanta AssignmentModeNotAllowed (mapeada para ORG_ASSIGNMENT_MODE_NOT_ALLOWED).

rank_candidates(unit, project_id, policy: OrganizationalUnitAssignmentPolicy | None) -> RankedCandidates
    # RFC §6.4 algoritmo "lb-1". Retorna eleitos ordenados + excluídos com excluded_reason.
    # Só executor principal conta (IssueOrganizationalUnit.primary_executor, routing_state=assigned,
    # state.group not in CLOSED_STATE_GROUPS). Desempate final por user_id.

allocate(issue, unit, *, requested_mode=None, explicit_executor=None, collaborators=(),
         actor=None, trigger, operation=None, assignment_due_at=None) -> AllocationResult
    # Transação: advisory lock por unidade (só para least_loaded), select_for_update no link,
    # valida I2 e I4, cria/atualiza IssueAssignee, atualiza routing_state/queue_reason/queued_at/
    # primary_executor/current_assignment_decision, grava AssignmentDecision. Nunca remove
    # IssueAssignee existentes.

claim(issue, user, *, actor) -> AllocationResult
    # select_for_update; exige routing_state in (queued, allocation_failed) e política efetiva
    # self_claim (ou actor coordenador/admin); perdedor → AlreadyClaimed com vencedor.

reassign(issue, new_executor, *, actor, reason, expected_decision_id) -> AllocationResult
    # If-Match semântico: expected_decision_id deve ser current_assignment_decision; senão DecisionStale.
    # Executor anterior permanece como colaborador (IssueAssignee mantido); decisão com supersedes.

return_to_queue(issue, *, actor, reason, queue_reason="manually_returned") -> AllocationResult
    # Remove primary_executor (mantém IssueAssignee), routing_state=queued, decisão.

transfer_unit(issue, to_unit, *, actor, source, reason) -> TransferResult
    # RFC §6.8: valida I2 em to_unit, evento from→to, devolve à fila se executor não pertence a
    # to_unit, resolve política em to_unit e aplica como criação.

set_responsibility(issue, unit, *, actor, source, requested_mode=None, ...) -> AllocationResult
    # Caminho de "marcar área" (POST organizational-unit): cria o link se não existe (evento
    # from=None), ou delega a transfer_unit se já existe outra área, e então allocate.
```

**Lock.** Helper `unit_allocation_lock(unit_id)` que executa
`SELECT pg_advisory_xact_lock(hashtext(%s))` dentro de `transaction.atomic()`.
Em SQLite (não usado nos testes: o runner é Postgres) o helper é no-op.

**Erros.** Exceções de domínio em `services/orca/errors.py`
(`AssignmentModeNotAllowed`, `UnitNotCoveringProject`, `ExecutorNotEligible`,
`AlreadyClaimed`, `DecisionStale`, `InvalidTransition`) com atributo
`error_code` apontando para o nome em `ORCA_ERROR_CODES`. As views convertem
com um único `except OrcaDomainError as e: return orca_error(e.error_code,
e.http_status)`.

**Códigos novos** (três lugares): `ORG_ASSIGNMENT_MODE_NOT_ALLOWED`,
`ORG_EXECUTOR_NOT_ELIGIBLE`, `ORG_WORK_ITEM_ALREADY_CLAIMED`,
`ORG_DECISION_STALE`, `ORG_INVALID_ROUTING_TRANSITION`.

**Métricas/logs.** Logger `plane.orca.assignment` com `extra=` contendo
`workspace_id, unit_id, issue_id, decision_id, mode, outcome, trigger`.

**Testes** (`test_assignment_service.py`, `test_assignment_concurrency.py`):
toda a linha "Resolução de política", "Ranking lb-1", "Estados", "Decisões",
"Encaminhamento" e "Concorrência" da matriz do RFC §10. Concorrência com
`@pytest.mark.django_db(transaction=True)`, `ThreadPoolExecutor`, uma
conexão por thread (`connection.close()` no fim de cada), 4 membros e 20
alocações → 5/5/5/5; 10 claims → 1 sucesso e 9 `AlreadyClaimed`.

**Aceite.**
- [ ] Testes verdes, incluindo concorrência no runner Docker (`docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/orca/test_assignment_concurrency.py -q`).
- [ ] `assignment_engine.py` não contém mais lógica própria de ranking.
- [ ] Nenhuma escrita em `ProjectMember` no módulo (grep).

---

## D0.6 — Endpoints internos passam a usar o serviço; GET de política `[ ]`

**Mudança** em `apps/api/plane/app/views/organizational_unit.py` e
`apps/api/plane/app/urls/orca.py`:
- `IssueOrganizationalUnitEndpoint.post` → `set_responsibility(...)`; resposta inclui `routing_state`, `queue_reason`, `primary_executor`, `decision`.
- `IssueOrganizationalUnitEndpoint.get` → inclui os mesmos campos.
- `IssueOrganizationalUnitEndpoint.delete` → grava `IssueResponsibilityEvent(to_unit=None)` antes de apagar.
- `IssueOrganizationalUnitAssignEndpoint.post` → `allocate(..., requested_mode=request.data.get("mode","least_loaded"), trigger="internal_api")`; aceita ainda `mode=append` por compatibilidade mapeando para colaborador adicional (documentar como deprecated).
- Novo `OrganizationalUnitPolicyEndpoint` (`GET .../organizational-units/{unit_id}/policy/` e `GET .../projects/{pk}/policy/`) devolvendo a política efetiva resolvida (sem `requested_mode`) — permissão Admin/Member/Guest do workspace, como os demais GETs.
- Serializers em `apps/api/plane/app/serializers/organizational_unit.py`: `AssignmentPolicySerializer`, `AssignmentDecisionSerializer`, `IssueRoutingSerializer`.
- Frontend mínimo para não quebrar: `packages/types/src/organizational-unit.ts` ganha os tipos; `issue-unit-property.tsx` exibe `routing_state` e executor principal (a UI completa é Fase 2).

**Testes.** Atualizar `test_issue_organizational_unit_http.py` e
`test_organizational_unit_http.py` para o novo payload; adicionar caso de
`delete` gerando evento.

**Aceite.**
- [ ] Todos os testes Orca verdes.
- [ ] `pnpm --filter web check:types` limpo (rodar localmente).

---

## D0.7 — Comando `audit_organizational_routing` `[ ]`

**Arquivo.** `apps/api/plane/db/management/commands/audit_organizational_routing.py`,
mesmo esqueleto de `reconcile_organizational_access.py` (flags `--workspace`,
`--write`, saída tabular).

**Verificações** (RFC §6.1 I3, I4): `assigned` sem `IssueAssignee` ativo do
executor; `assigned` com executor que não é mais membro ativo da área ou do
projeto; `queued`/`allocation_failed` com `IssueAssignee` ativo mas sem
executor principal (mostrar; não corrigir automaticamente — pode ser
colaborador); política com `default_mode` fora de `allowed_modes`.

`--write`: para os dois primeiros casos, `return_to_queue(...,
queue_reason="executor_unavailable", trigger="command")`.

**Aceite.**
- [ ] Teste `test_audit_routing_command.py` com um caso de cada violação, em dry-run e write.
- [ ] Documentado em `docs/organizational-units.md` ao lado do reconcile.

---

## D0.8 — Observabilidade mínima `[ ]`

**Mudança.** Módulo `services/orca/metrics.py` com funções
`record_assignment_outcome(mode, outcome, trigger)`,
`record_no_candidate(unit_id)`, `record_decision_superseded(unit_id,
previous_mode)`; implementação inicial = log estruturado em nível INFO com
nomes do RFC §11. Se o projeto adotar Prometheus/StatsD depois, só este
módulo muda. Chamado por `assignment_service.py`.

**Aceite.**
- [ ] Teste que captura logs (`caplog`) e confere os campos.

---

## D0.9 — Fechar a matriz de testes da fase `[ ]`

Percorrer RFC §10 linhas: I2 cobertura, Resolução de política, Ranking,
Estados, Decisões, Concorrência, Encaminhamento, Kill switches, Permissões
(para os endpoints internos alterados). Cada linha tem pelo menos um teste
nomeado com o identificador (`test_i2_unit_not_covering_project_rejected`).
Adicionar em `apps/api/tests/TESTING_GUIDE.md` a seção "Testes de
concorrência" com o padrão usado.

**Aceite.**
- [ ] `pytest plane/tests/unit/orca/ -q` verde no runner Docker.
- [ ] Cobertura das linhas listadas registrada em uma tabela ao final deste arquivo.

---

## D0.10 — Documentação `[ ]`

- `docs/organizational-units.md` §Assignment reescrita: estados, políticas,
  executor principal, decisões, comando de auditoria, o que mudou em relação
  à v1 (modo `append`).
- RFC §2.1 tabela atualizada (linhas "Estado de fila", "Executor principal",
  "Alocar ao menos carregado" → Sim).
- Este arquivo e o `README.md` do plano com estado e data do gate.

---

## D0.11 — Arquivar projeto dispara reconciliação `[ ]`

**Problema** (pendência do PR #6). `_active_sources` já ignora projetos
arquivados e `projects_in_workspace` só devolve não arquivados, então o
acesso herdado a um projeto arquivado deixa de ter fonte — mas nenhuma
reconciliação é disparada no arquivamento, e a passagem de workspace não
inclui o projeto. O `ProjectMember` herdado fica ativo até alguém reconciliar
aquele projeto explicitamente.

**Mudança.**
- `ProjectArchiveUnarchiveEndpoint` (`views/project/base.py`): após gravar `archived_at`, chamar `dispatch_reconciliation(workspace_id, project_ids=[project_id])`; ao desarquivar, idem (o acesso volta).
- `reconcile_access` com `project_ids` explícitos deve aceitar projeto arquivado como alvo (só a coleta de *fontes* o ignora), para que a reconciliação possa desativar o que ele herdou.

**Testes** (`test_org_unit_reconciler.py`): arquivar → `ProjectMember` herdado
desativado, baseline manual preservado; desarquivar → restaurado.

**Aceite.**
- [ ] Testes acima verdes; `ORCA_ORG_UNITS_ENABLED=0` faz o arquivamento não reconciliar (o guard de `reconcile_access` cobre).

---

## D0.12 — Roster SCIM de grupo exclui memberships removidos logicamente `[ ]`

**Problema** (pendência do PR #6). `members_of(unit)` em
`views/orca_scim/groups.py` atravessa `group_memberships__organizational_unit_id`
sem filtrar `deleted_at`; um membership soft-deleted pode reaparecer na
resposta de `GET /Groups/{id}`, e o Entra passa a considerar a pessoa membro.

**Mudança.**
- `members_of` filtra `group_memberships__deleted_at__isnull=True`; revisar `remove_members`/`replace_members` para que a remoção seja consistente com o filtro.

**Testes** (`test_scim_endpoints.py`): membership removido não aparece no
`GET`; `PATCH remove` seguido de `GET` devolve roster vazio.

**Aceite.**
- [ ] Testes acima verdes.

---

## Gate D0

- [ ] Todos os 12 itens `[x]`.
- [ ] Invariantes I1–I7 com teste positivo e negativo (listar nomes dos testes abaixo).
- [ ] Concorrência: 20 alocações → 5/5/5/5; 10 claims → 1 vencedor.
- [ ] `audit_organizational_routing` sem violações num dump do banco de `stage`.
- [ ] `pytest plane/tests/unit/orca/` verde no CI.
- [ ] `ORCA_ORG_UNITS_ENABLED=0` continua respondendo 404 em todas as rotas novas e antigas.

Data do gate: ____

### Testes por invariante (preencher)

| Invariante | Testes |
| --- | --- |
| I1 | |
| I2 | |
| I3 | |
| I4 | |
| I5 | |
| I6 | |
| I7 | |
