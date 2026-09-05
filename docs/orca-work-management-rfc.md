# Especificação — Gestão de trabalho por área (Orca)

**Status:** especificação para implementação. Revisão 2 (03/09/2026).
**Base analisada:** `stage` em `79221cb6` (Plane CE v1.4.1). Nenhuma seção
marcada como "proposta" está implementada.
**Leitura obrigatória antes:** [FORK.md](../FORK.md), [AGENTS.md](../AGENTS.md),
[organizational-units.md](./organizational-units.md),
[entra-directory-sync.md](./entra-directory-sync.md).

**Plano de execução:** [`docs/plans/orca-work-management/`](./plans/orca-work-management/README.md)
(quadro de estado, um arquivo por fase com itens marcáveis, prompt de handoff).

Este documento foi escrito para ser entregue a outra sessão de trabalho sem
contexto prévio. A seção 0 diz como usá-lo. A seção 13 lista as convenções do
repositório que qualquer implementação precisa seguir. O apêndice A mapeia
cada dúvida conceitual do debate para a decisão tomada.

---

## 0. Como usar este documento

1. Leia as seções 1 a 3 inteiras. Elas definem o vocabulário e as decisões já
   fechadas. Não reabra uma decisão fechada sem registrar o motivo na seção 4.
2. Escolha uma fase (seção 9). As fases P0 e D0 podem correr em paralelo; as
   demais são sequenciais e cada uma tem um gate objetivo.
3. Dentro da fase, cada item de trabalho aponta arquivos existentes. Antes de
   criar um arquivo novo, procure o equivalente Orca já existente e siga o
   padrão dele.
4. Todo item que toca alocação termina com um teste de concorrência (seção
   10). Isso faz parte da definição de pronto, não é opcional.
5. Ao terminar um item, atualize a tabela de estado da seção 2 e o changelog
   de decisões da seção 4. Este documento é a fonte da verdade do desenho.

### Glossário

| Termo | Significado neste documento |
| --- | --- |
| **Área** (`OrganizationalUnit`) | Unidade organizacional que responde institucionalmente por trabalho. Já existe. |
| **Responsabilidade** | Vínculo item ↔ área em `IssueOrganizationalUnit`. Uma área ativa por item. Já existe. |
| **Executor principal** | A pessoa accountable pela execução do item. Proposta: referência lateral que deve coincidir com um `IssueAssignee` nativo. |
| **Colaborador** | Qualquer outro `IssueAssignee` do item. |
| **Fila** | Conjunto de itens de uma área com `routing_state = queued`. É uma consulta, não uma tabela. |
| **Política de alocação** | Regra que decide o que acontece com um item quando ele recebe uma área: `manual`, `self_claim`, `least_loaded`. |
| **Decisão de alocação** | Registro append-only de cada escolha (automática ou humana) de executor. |
| **Operação de automação** | Requisição idempotente de um cliente externo, identificada por chave própria. |
| **Binding externo** | Vínculo item ↔ objeto de um sistema externo (`external_source` + `external_id`). |
| **Coordenador** | Pessoa que opera a fila de uma área. Papel novo, distinto de `lead`. |
| **Orquestrador** | Serviço sidecar (fora do Plane) que instancia processos e chama a API pública. |
| **Instância de processo** | Uma execução concreta de um template (ex.: onboarding do cliente 123). Projeção mínima dentro do Orca. |

---

## 1. Conceito e princípios

### 1.1 O conceito

> A **área** responde por uma pendência; a **pessoa** a executa num dado
> momento. A responsabilidade não evapora porque alguém saiu de férias, mudou
> de área ou deixou a empresa.

Consequências diretas:

- Executor vazio é legítimo **apenas** quando o item está explicitamente em
  fila. Um item "sem ninguém" sem estado de fila é violação de invariante.
- Fila é **estado**, não política. As políticas dizem como um item sai da
  fila.
- Toda decisão sobre quem executa fica registrada, é auditável e pode ser
  substituída por outra decisão sem apagar a anterior.
- Nada automático desfaz uma escolha humana posterior.

### 1.2 Princípios de implementação

1. **Autoridades nativas intocadas.** `ProjectMember` governa acesso;
   `IssueAssignee` diz quem executa. Orca só escreve nesses modelos por
   reconciliadores explícitos, idempotentes e com proveniência. Nenhuma coluna
   nova em `Issue`, `Project`, `Workspace`, `User`, `ProjectMember` ou
   `IssueAssignee`.
2. **Invariantes no backend.** A UI filtra; a API rejeita. Toda invariante da
   seção 6 tem um teste negativo.
3. **Kill switch por capacidade.** `ORCA_ORG_UNITS_ENABLED` já existe e
   continua sendo o interruptor-mestre. Novas chaves, todas com default
   desligado até o gate da fase correspondente fechar:
   `ORCA_ASSIGNMENT_AUTO_ENABLED`, `ORCA_PUBLIC_API_ENABLED`,
   `ORCA_AVAILABILITY_ENABLED`, `ORCA_PROCESS_PROJECTION_ENABLED`. Mesmo
   padrão do `OrganizationalUnitFeatureMixin`: enforce por requisição,
   responde 404.
4. **Toda decisão automática é registrada** em `AssignmentDecision` antes de
   qualquer efeito. Sem registro, não há alocação automática.
5. **Dry-run antes de write** em todo comando ou endpoint que redistribui
   trabalho, no padrão de `reconcile_organizational_access`.
6. **Replay-safe, não desired-state.** Repetir uma operação retorna o mesmo
   resultado sem repetir efeitos de alocação nem desfazer alterações humanas
   posteriores. Mudar estado exige nova operação identificada.
7. **Público e interno separados.** Rotas de sessão ficam em `/api/orca/`.
   Rotas para robôs ficam em `/api/v1/orca/` com `APIKeyAuthentication`,
   serializers próprios e contrato versionado.
8. **Nada automático e irreversível na v1.** Reatribuição por saída ou férias
   é assistida: o item volta à fila com marcação e o coordenador decide.
9. **Determinismo.** Todo ranking termina em desempate por identificador
   estável. Dois nós com o mesmo estado produzem a mesma escolha.
10. **Observabilidade desde o dia um.** Contadores de falha de alocação, fila
    sem candidato, idade da fila, decisões revertidas e conflitos de
    idempotência existem a partir da fase D0.

---

## 2. Estado atual — verificado no código

### 2.1 O que existe

| Capacidade | Estado | Onde |
| --- | --- | --- |
| Área responsável persistente por work item, uma por item | Sim | `IssueOrganizationalUnit` em `apps/api/plane/db/models/organizational_unit.py` |
| Área liga pessoas a projetos e materializa `ProjectMember` | Sim | `apps/api/plane/app/services/orca/org_unit_reconciler.py` |
| Acesso manual preservado, papel herdado como piso | Sim | idem; documentado em `organizational-units.md` |
| Alocar ao integrante menos carregado | Sim | `apps/api/plane/app/services/orca/assignment_service.py` (`lb-1`, lock por área, decisão registrada); disparo manual; `fill_empty`/`append` deprecados em favor de `assignment_mode` |
| Papel `lead` na área | Só rótulo | `OrganizationalUnitMemberRole`; nenhuma permissão decorre disso |
| "Minhas áreas" e carga por integrante | Sim | `UserOrganizationalUnitsEndpoint`, `OrganizationalUnitWorkloadEndpoint` em `apps/api/plane/app/views/organizational_unit.py` |
| Tela da área | Só membros e projetos | `apps/web/core/components/orca/organizational-units/unit-detail.tsx` |
| Rotas Orca por API key | Não | tudo em `apps/api/plane/app/urls/orca.py`, autenticação de sessão |
| Códigos de erro Orca traduzíveis | Sim | `apps/api/plane/utils/orca_error_codes.py` + `packages/constants/src/orca/error-codes.ts` + catálogo i18n |
| Kill switch | Sim | `OrganizationalUnitFeatureMixin` |
| Rate limit dedicado | Só SCIM | `apps/api/plane/throttles/scim.py` |
| Disponibilidade, férias, capacidade | Não | — |
| Estado de fila | Sim | `IssueOrganizationalUnit.routing_state`/`queue_reason`/`queued_at`/`assignment_due_at`; máquina de estados em §6.2 |
| Executor principal | Sim | `IssueOrganizationalUnit.primary_executor`; auditado por `audit_organizational_routing` |
| Reatribuição quando alguém sai | Parcial | `audit_organizational_routing --write` devolve à fila quem perdeu elegibilidade; automático no evento é Fase 3 |
| Política automática na criação | Parcial | `OrganizationalUnitAssignmentPolicy` existe e resolve (§6.3); criar item já com área é da API pública, Fase 1 |
| Dashboard da área / executivo | Não | — |

### 2.2 Defeitos que precisam fechar antes de qualquer automação

Confirmados por leitura. Nenhum tem teste hoje. Identificadores D1 a D4 são
usados na fase D0.

**D1 — Área sem cobertura do projeto.**
`IssueOrganizationalUnitEndpoint.post` (views/organizational_unit.py, ~l.525)
só confere `workspace_id`. Não exige `OrganizationalUnitProject` ativo ligando
área e projeto. A UI (`issue-unit-property.tsx`, l.67) filtra apenas por
`is_active`. O engine (`assignment_engine.py`, ~l.105) acrescenta o projeto do
item à lista de projetos da área quando ele não está lá, escondendo a
inconsistência.

**D2 — Herança implícita de assignees na API pública.**
`apps/api/plane/api/serializers/issue.py` (~l.188) copia os assignees do
último item criado pelo mesmo usuário no projeto quando `assignees` vem vazio
ou omitido. O upstream usa `default_assignee` do projeto. Para um robô, isso
torna o resultado dependente de histórico e conflita com a fila.

**D3 — Ranking e gravação sem lock.**
`assign_from_unit` calcula o ranking e depois faz `IssueAssignee.objects.create`
sem `select_for_update` nem lock por área. N criações simultâneas podem
escolher a mesma pessoa.

**D4 — Carga é "total da pessoa nos projetos da área", não declarada.**
O engine conta todo `IssueAssignee` aberto nos projetos da área, inclusive
itens de outras áreas ou pessoais, e não distingue principal de colaborador.
A seção 6.4 fixa a regra.

### 2.3 Correções factuais ao debate

- **Plane CE v1.4.1 não tem work item types nem custom properties.** São
  recurso da edição comercial. Qualquer "propriedade Área" via YAML é
  inviável nesta base. `IssueOrganizationalUnit` é a única fonte da verdade.
- **Branches antigos ainda existem no remoto:**
  `claude/azure-aad-integration-review-5if6pz`,
  `claude/sync-remote-azure-auth-m6618f`, `claude/aad-end-to-end-egj4dm`.
  Contêm abordagens `oidc-free`/`azuread` superadas pelo PR #2. Apagar.
- **Pipeline de imagens:** `build-push` em `.github/workflows/stage.yml` roda
  em `pull_request` com `push: true` incondicional para a tag mutável
  `:stage`; `prod.yml` promove puxando `:stage`. Pré-requisito P0.
- **Namespace das imagens (04/09, corrigido):** `docker-compose-orca.yml`
  apontava para `ghcr.io/prospect-development-team/plane-orca/*`, enquanto
  `stage.yml` publica em `ghcr.io/<este repositório>/*`. O Compose que o
  README manda o Coolify usar podia implantar imagens do repositório-pai.
  Corrigido em P0.0 (variável `ORCA_IMAGE_REPOSITORY` com default no fork e
  job `compose_provenance` que falha no drift). A promoção por digest
  (P0.2/P0.3) e a exposição do commit no runtime (P0.15) completam a
  proveniência.
- **Plane Compose:** a doc oficial não pôde ser lida deste ambiente. As
  afirmações do debate (PAT/workspace token, `.plane/state.json`, update por
  id local, ausência de campo de área) vêm de fonte externa e devem ser
  reconfirmadas por quem tiver acesso antes da Fase 4. Nada da Fase 1 depende
  disso: o contrato é REST.

---

## 3. Decisões fechadas

Estas decisões saíram do debate e valem para a implementação. Reabrir exige
registrar na seção 4 o motivo e o impacto.

| # | Decisão |
| --- | --- |
| F1 | **Uma área accountable por item.** Duas áreas com entrega própria são dois itens ligados por `blocked_by`/`blocking`. Outra área "consultada" não é responsabilidade. |
| F2 | **Fila é estado.** `IssueOrganizationalUnit` ganha `routing_state` e `queue_reason`. Executor vazio é válido apenas com `routing_state = queued` ou `allocation_failed`. |
| F3 | **Políticas v1:** `manual`, `self_claim`, `least_loaded`. Não existe política `queue` nem `specific_member`. Atribuição a pessoa específica é `assignment.mode = explicit`. |
| F4 | **Política mora em área↔projeto, com fallback na área, com fallback `manual`.** A requisição pode solicitar uma política, mas só entre as permitidas pelo vínculo área↔projeto. Fora disso, rejeita. Sem política nenhuma, o default é `manual` e qualquer modo pode ser solicitado (§6.3). |
| F5 | **Executor principal existe na camada lateral** e deve coincidir com um `IssueAssignee` ativo do mesmo item. Nunca coluna em `Issue`. |
| F6 | **Carga v1 é contagem simples de itens abertos como executor principal**, ordenando por total no workspace, depois na área, depois última atribuição automática, depois id estável. Não configurável na v1. |
| F7 | **Encaminhamento entre áreas troca a área ativa no mesmo item e grava evento append-only** em `IssueResponsibilityEvent`. Nova entrega é novo item. |
| F8 | **`AssignmentDecision` é append-only e versionada.** Reversão é nova decisão com `supersedes`. |
| F9 | **Idempotência em duas camadas:** `ExternalWorkItemBinding` (objeto externo ↔ item, único por workspace) e `AutomationOperation` (chave de idempotência + hash do payload). `external_source`/`external_id` nativos continuam preenchidos para compatibilidade, mas não são o mecanismo. |
| F10 | **Operação composta é uma transação:** criar/localizar item, binding, área, política, decisão, executor principal. Efeitos assíncronos só após commit (`transaction.on_commit`). |
| F11 | **Replay retorna o mesmo resultado** sem recalcular `least_loaded` nem desfazer reatribuição humana posterior. Mesma chave com payload diferente responde 409. |
| F12 | **Contrato da Fase 1 é REST.** Compose é ferramenta de definição/sincronização, não motor de instâncias. Não bloqueia a Fase 1. |
| F13 | **O contrato não sobrecarrega `assignees`.** Um objeto `assignment` explícito (`default` / `explicit`) decide. `assignees` da API nativa continua funcionando fora do namespace Orca. |
| F14 | **Disponibilidade em dois níveis, ambos simples na v1:** `WorkspaceMemberAvailability` (intervalos globais) e `MembershipAllocationSettings.accepts_new_work` (por área). Fonte manual; campo `source` já preparado para `hr`/`directory`. |
| F15 | **Saída ou indisponibilidade do executor devolve o item à fila** com `queue_reason = executor_unavailable` e alerta o coordenador. Não redistribui automaticamente na v1. |
| F16 | **Papel `coordinator` separado de `lead`.** Vários por área, delegável. `lead` continua institucional e único. Políticas, memberships e cobertura continuam com Workspace Admin. |
| F17 | **Coordenador não tem acesso lateral.** Se a área cobre um projeto, o reconciliador garante `ProjectMember` para os coordenadores. Sem exceção ao modelo de acesso do Plane. |
| F18 | **Dashboard executivo v1 só para Workspace Admin.** Capability `executive_viewer` é evolução posterior, e drill-down sempre respeita acesso nativo. |
| F19 | **Templates de processo ficam fora do Plane** (orquestrador sidecar em Git). Dentro do Orca existe só a projeção `ProcessInstanceReference` + `ProcessInstanceItem`. |
| F20 | **Identidade canônica da instância é `ProcessInstanceReference`.** Módulo, label, ciclo e item pai são projeções visuais opcionais. |
| F21 | **Fechamento automático é por etapa:** `automatic`, `automatic_with_review`, `manual`, decidido no template. Toda conclusão automática registra origem, evento, timestamp, evidência e versão da regra. |
| F22 | **SLA é lateral e auditado** em `IssueServiceLevel`. `target_date` nativo é projeção visual. Editar `target_date` não altera o SLA. |
| F23 | **Observabilidade operacional desde D0; dashboard de gestão na Fase 5.** |
| F24 | **A API pública não é habilitada em produção antes de existir fila mínima utilizável** (Fase 2 parcial: fila, alerta sem candidato, atribuição manual, devolução à fila). |

---

## 4. Decisões abertas e changelog de decisões

### 4.1 Abertas (não bloqueiam Fase 1)

| # | Questão | Fase em que precisa fechar |
| --- | --- | --- |
| A1 | Habilidades/especialidades por membership | Fase 3, só se `least_loaded` se mostrar insuficiente |
| A2 | `round_robin` | Fase 3, só com evidência |
| A3 | Sincronizar férias com Entra/RH | Fase 3, exige política organizacional e tenant |
| A4 | Capability `executive_viewer` | Pós-Fase 5 |
| A5 | Verificar comportamento real do Plane Compose em re-push | Antes da Fase 4 |
| A6 | Módulo por instância quando a instância atravessa projetos | Fase 4 |
| A7 | Peso de carga por estimativa, prioridade e SLA | Pós-Fase 3 |
| A8 | Tabela `IssueSupportingUnit` para áreas consultadas | Só com demanda real |

### 4.2 Changelog

| Data | Mudança |
| --- | --- |
| 2026-09-03 | Rev. 1: RFC inicial com 23 dúvidas e 5 fases. |
| 2026-09-03 | Rev. 2: 24 decisões fechadas (F1–F24); fila vira estado; executor principal; binding + operação; `AssignmentDecision` append-only; Fase 0 dividida em P0 e D0; Compose retirado dos bloqueios; contrato REST detalhado. |
| 2026-09-04 | Rev. 3: revisão externa do commit `3a4c769` verificada. Compose volta aos bloqueios pelo namespace errado (P0.0, corrigido); parser estrito do kill switch, guard em `reconcile_access` e paridade de variáveis no Compose (P0.14, corrigido); novos itens P0.15 (commit no runtime), P0.16 (MinIO/PostgreSQL), D0.11 (arquivamento reconcilia), D0.12 (roster SCIM sem soft-deleted). Nenhuma decisão F1–F24 reaberta. |

---

## 5. Modelo de dados

Todas as tabelas são laterais, em `apps/api/plane/db/models/`, herdam de
`BaseModel` (que já traz `id` UUID, `created_at`, `updated_at`, `created_by`,
`updated_by`, `deleted_at`) e seguem o padrão de constraint condicional
`... WHERE deleted_at IS NULL` já usado em `organizational_unit.py`. Migrações
numeradas a partir de `0135`, cada uma com dependência explícita na anterior.

### 5.1 Alterações em tabela existente

**`IssueOrganizationalUnit`** (estender, migração `0135`)

| Campo | Tipo | Regras |
| --- | --- | --- |
| `routing_state` | `CharField(16)`, choices `queued`, `assigned`, `allocation_failed`, `suspended` | default `queued` para linhas novas; migração de dados: linhas existentes com `IssueAssignee` ativo viram `assigned`, demais `queued` com `queue_reason = new_item` |
| `queue_reason` | `CharField(32)`, choices `new_item`, `awaiting_coordinator`, `awaiting_claim`, `no_eligible_member`, `executor_unavailable`, `manually_returned`, blank | obrigatório quando `routing_state in (queued, allocation_failed)`; vazio quando `assigned` |
| `queued_at` | `DateTimeField(null)` | setado ao entrar em `queued`/`allocation_failed`; limpo ao sair |
| `assignment_due_at` | `DateTimeField(null)` | SLA de atribuição efetivo (seção 6.6) |
| `primary_executor` | `FK(User, null, on_delete=SET_NULL)` | obrigatório quando `routing_state = assigned`; deve existir `IssueAssignee(issue, assignee=primary_executor, deleted_at=null)` |
| `current_assignment_decision` | `FK(AssignmentDecision, null, SET_NULL)` | última decisão vigente |

Constraints:

- `CHECK (routing_state <> 'assigned' OR primary_executor_id IS NOT NULL)`
- `CHECK (routing_state = 'assigned' OR primary_executor_id IS NULL)`
- Índice `(workspace, organizational_unit, routing_state)` para a fila.
- Índice `(primary_executor, routing_state)` para carga.

A coincidência com `IssueAssignee` não é expressável em CHECK; é invariante de
serviço (seção 6.1) verificada por teste e por comando de auditoria.

### 5.2 Tabelas novas

**`OrganizationalUnitAssignmentPolicy`** (migração `0136`)

| Campo | Tipo | Regras |
| --- | --- | --- |
| `organizational_unit` | FK | obrigatório |
| `unit_project` | FK `OrganizationalUnitProject`, null | null = política padrão da área; não null = política daquele projeto |
| `default_mode` | choices `manual`, `self_claim`, `least_loaded` | default `manual` |
| `allowed_modes` | `JSONField` lista de modos | deve conter `default_mode`; default `["manual"]` |
| `assignment_sla_seconds` | `PositiveIntegerField(null)` | SLA de atribuição padrão |
| `max_open_items_per_member` | `PositiveIntegerField(null)` | limite rígido para `least_loaded` |
| `is_active` | bool | |
| `version` | `PositiveIntegerField`, default 1 | incrementa em cada save; congelado em `AssignmentDecision.policy_version` |

Constraints: único `(organizational_unit, unit_project)` com `deleted_at IS
NULL` (Postgres trata NULL como distinto; usar duas constraints parciais:
uma para `unit_project IS NULL`, outra para `unit_project IS NOT NULL`).
`workspace` desnormalizado, como nas demais tabelas Orca.

**`AssignmentDecision`** (migração `0137`) — append-only

| Campo | Tipo |
| --- | --- |
| `issue`, `organizational_unit`, `project`, `workspace` | FKs |
| `automation_operation` | FK `AutomationOperation`, null |
| `trigger` | choices `public_api`, `internal_api`, `ui_claim`, `ui_coordinator`, `reassign`, `availability`, `return_to_queue`, `command` |
| `requested_mode` | choices `default`, `explicit`, `manual`, `self_claim`, `least_loaded`, null |
| `effective_mode` | choices `manual`, `self_claim`, `least_loaded`, `explicit` |
| `policy_source` | choices `request`, `unit_project`, `unit`, `fallback` |
| `policy` | FK `OrganizationalUnitAssignmentPolicy`, null |
| `policy_version` | int, null |
| `algorithm_version` | `CharField(16)`; v1 = `"lb-1"` |
| `outcome` | choices `assigned`, `queued`, `allocation_failed`, `rejected` |
| `candidates_snapshot` | `JSONField`: lista de `{user_id, total_open, unit_open, last_auto_at, excluded_reason?}`; sem dados pessoais além do id |
| `chosen_assignee` | FK User, null |
| `previous_primary_executor` | FK User, null |
| `decided_by` | FK User, null (null = sistema) |
| `supersedes` | FK self, null |
| `reason` | `TextField`, blank |

Sem `updated_at` semântico: a linha nunca muda depois de criada. Índices
`(issue, created_at)` e `(organizational_unit, created_at)`.

**`IssueResponsibilityEvent`** (migração `0137`) — append-only

| Campo | Tipo |
| --- | --- |
| `issue`, `workspace` | FKs |
| `from_unit` | FK `OrganizationalUnit`, null (null = primeira atribuição de área) |
| `to_unit` | FK `OrganizationalUnit`, null (null = remoção) |
| `actor` | FK User, null |
| `source` | choices `public_api`, `internal_api`, `ui`, `command` |
| `reason` | `TextField`, blank |

**`ExternalWorkItemBinding`** (migração `0138`)

| Campo | Tipo |
| --- | --- |
| `workspace` | FK |
| `external_source` | `CharField(255)` |
| `external_id` | `CharField(255)` |
| `issue` | FK `Issue` |

Constraints: único `(workspace, external_source, external_id)` com
`deleted_at IS NULL`; único `(issue)` com `deleted_at IS NULL` (um item tem
no máximo um binding). Ao criar o binding, o serviço também preenche
`Issue.external_source`/`external_id` nativos, para que a busca nativa da
API v1 continue funcionando.

**`AutomationOperation`** (migração `0138`)

| Campo | Tipo |
| --- | --- |
| `workspace` | FK |
| `api_token` | FK `APIToken`, null (quem chamou) |
| `idempotency_key` | `CharField(255)` |
| `request_hash` | `CharField(64)` SHA-256 do payload canônico (JSON com chaves ordenadas, sem campos voláteis) |
| `operation_type` | choices `create_work_item`, `reassign`, `transfer_unit`, `complete` |
| `status` | choices `in_progress`, `succeeded`, `failed` |
| `issue` | FK, null |
| `response_snapshot` | `JSONField` (a resposta devolvida ao cliente) |
| `error_code` | `CharField(64)`, blank |
| `completed_at` | datetime, null |

Constraint: único `(workspace, idempotency_key)` sem condição de
`deleted_at` (operações não são soft-deleted). `status = in_progress` com
`created_at` mais antigo que 60 s é considerado abandonado e pode ser
retomado (seção 6.7).

**`WorkspaceMemberAvailability`** (migração `0139`, Fase 3)

| Campo | Tipo |
| --- | --- |
| `workspace_member` | FK `WorkspaceMember` |
| `workspace` | FK |
| `unavailable_from`, `unavailable_until` | datetimes; `until` null = indefinido |
| `reason` | choices `vacation`, `leave`, `other` |
| `source` | choices `manual`, `hr`, `directory`; v1 só `manual` |
| `external_id` | `CharField`, blank |
| `created_by` | já vem do `BaseModel` |

Constraint: `CHECK (unavailable_until IS NULL OR unavailable_until > unavailable_from)`.

**`MembershipAllocationSettings`** (migração `0139`, Fase 3)

| Campo | Tipo |
| --- | --- |
| `membership` | OneToOne `OrganizationalUnitMembership` |
| `accepts_new_work` | bool, default true |
| `max_open_items` | int, null |

**`OrganizationalUnitCoordinator`** (migração `0140`, Fase 2)

| Campo | Tipo |
| --- | --- |
| `organizational_unit`, `workspace_member`, `workspace` | FKs |
| `is_active` | bool |

Único `(organizational_unit, workspace_member)` com `deleted_at IS NULL`.
Alternativa descartada: adicionar `coordinator` a
`OrganizationalUnitMemberRole`, porque um coordenador pode não ser membro
executor da área e porque o SCIM escreve o `role` da membership.

**`IssueServiceLevel`** (migração `0141`, Fase 4)

| Campo | Tipo |
| --- | --- |
| `issue` | OneToOne |
| `assignment_due_at`, `completion_due_at` | datetimes, null |
| `original_assignment_due_at`, `original_completion_due_at` | datetimes, null; nunca alterados |
| `source` | choices `unit_project`, `unit`, `process`, `manual` |
| `source_version` | `CharField`, blank |
| `changed_by`, `change_reason` | FK null, texto |

**`ProcessInstanceReference`** e **`ProcessInstanceItem`** (migração `0142`, Fase 4)

```text
ProcessInstanceReference
    workspace, external_source, external_instance_id  UNIQUE(workspace, source, instance_id)
    template_name, template_version
    status: running | completed | cancelled
    started_at, completed_at
ProcessInstanceItem
    process_instance, issue UNIQUE(issue)
    step_key
    completion_mode: automatic | automatic_with_review | manual
```

### 5.3 Diagrama de relações

```text
OrganizationalUnit ──< OrganizationalUnitMembership ──1 MembershipAllocationSettings
        │                        │
        │                        └──> WorkspaceMember ──< WorkspaceMemberAvailability
        │
        ├──< OrganizationalUnitProject ──< OrganizationalUnitAssignmentPolicy (por projeto)
        ├──< OrganizationalUnitAssignmentPolicy (padrão da área)
        ├──< OrganizationalUnitCoordinator
        │
        └──< IssueOrganizationalUnit ──1 Issue ──< IssueAssignee
                    │                    │
                    │                    ├──1 ExternalWorkItemBinding
                    │                    ├──1 IssueServiceLevel
                    │                    ├──1 ProcessInstanceItem ──> ProcessInstanceReference
                    │                    └──< IssueResponsibilityEvent
                    │
                    └──> AssignmentDecision (current) ──< AssignmentDecision (supersedes)
                                 │
                                 └──> AutomationOperation
```

---

## 6. Semântica

### 6.1 Invariantes

Cada uma tem teste positivo e negativo (seção 10).

| # | Invariante | Onde se aplica |
| --- | --- | --- |
| I1 | Uma área ativa por item. | constraint existente |
| I2 | A área de um item deve estar ativa e ter `OrganizationalUnitProject` ativo com o projeto do item. | `POST organizational-unit`, API pública, encaminhamento |
| I3 | `routing_state = assigned` ⇔ `primary_executor` não nulo ⇔ existe `IssueAssignee` ativo para ele. | serviço; comando de auditoria |
| I4 | `primary_executor` é membro ativo da área e `ProjectMember` ativo do projeto no momento da decisão. | serviço |
| I5 | Toda mudança de `primary_executor` ou de `routing_state` gera uma `AssignmentDecision`. | serviço |
| I6 | Toda mudança de `organizational_unit` gera um `IssueResponsibilityEvent`. | serviço |
| I7 | Uma política solicitada fora de `allowed_modes` é rejeitada, nunca degradada. | resolução |
| I8 | Um binding externo aponta para exatamente um item, e um item tem no máximo um binding. | constraint |
| I9 | Uma `idempotency_key` reexecutada com hash diferente responde 409. | serviço |
| I10 | Nenhuma escrita em `ProjectMember` fora dos reconciliadores existentes. | revisão de código |

### 6.2 Máquina de estados de `routing_state`

```text
                 ┌──────────────────────────────────────────────┐
                 │                                              │
   criar item    ▼          least_loaded ok / claim / atribuir  │
  com área ──> queued ─────────────────────────────────────> assigned
                 ▲  ▲                                          │  │
                 │  │ devolver à fila / executor indisponível  │  │
                 │  └──────────────────────────────────────────┘  │
                 │                                                │
   least_loaded sem candidato                                     │
                 │                                                │
        allocation_failed ──── atribuir manual / claim ───────────┘
                 ▲
                 │  suspender (coordenador)
             suspended ◄─────── queued | assigned
                 │
                 └── retomar ──> queued (queue_reason = manually_returned)
```

Transições e quem pode acioná-las:

| De | Para | Gatilho | Quem |
| --- | --- | --- | --- |
| — | `queued` | item recebe área com política `manual` (`awaiting_coordinator`) ou `self_claim` (`awaiting_claim`) | API pública, UI, sistema |
| — | `assigned` | política `least_loaded` encontra candidato; ou `assignment.mode = explicit` | idem |
| — | `allocation_failed` | `least_loaded` sem candidato (`no_eligible_member`) | sistema |
| `queued`/`allocation_failed` | `assigned` | claim (self), atribuir (coordenador), reexecutar alocação | membro elegível, coordenador |
| `assigned` | `queued` | devolver à fila (`manually_returned`); executor indisponível/desativado (`executor_unavailable`) | coordenador, executor, sistema (Fase 3) |
| `assigned` | `assigned` | reatribuir (nova decisão com `supersedes`) | coordenador |
| qualquer | `suspended` | coordenador suspende (item bloqueado externamente) | coordenador |
| `suspended` | `queued` | retomar | coordenador |

Remover a área do item (`DELETE`) apaga a linha lateral, gera
`IssueResponsibilityEvent(to_unit=null)` e **não** remove `IssueAssignee`:
o item volta a ser um item Plane comum.

### 6.3 Resolução de política

Entrada: `unit`, `project`, `requested_mode` (opcional). Saída:
`effective_mode`, `policy`, `policy_source`, `policy_version`.

```text
1. policy_project = AssignmentPolicy(unit, unit_project=(unit,project), is_active)
   policy_unit    = AssignmentPolicy(unit, unit_project=null, is_active)
   allowed = policy_project.allowed_modes if policy_project
             else policy_unit.allowed_modes if policy_unit
             else ["manual", "self_claim", "least_loaded"]
2. se requested_mode em (manual, self_claim, least_loaded):
       se requested_mode ∉ allowed: REJEITAR (ORG_ASSIGNMENT_MODE_NOT_ALLOWED)
       senão: effective = requested_mode; source = request
   senão (default ou omitido):
       se policy_project: effective = policy_project.default_mode; source = unit_project
       senão se policy_unit: effective = policy_unit.default_mode; source = unit
       senão: effective = manual; source = fallback
3. policy = policy_project or policy_unit or null; policy_version = policy.version or null
```

Sem nenhuma política, `allowed` é a lista inteira: uma área que não
configurou nada não proibiu nada, e a versão original desta linha
(`["manual"]`) deixava o botão "atribuir automaticamente" recusando em toda
área recém-criada, já que a UI de política é da Fase 2. O que a ausência de
política decide é o **default** — `manual`, passo 2 —, então uma área não
configurada continua sem distribuir trabalho sozinha. I7 vale para a área que
declarou `allowed_modes`.

`assignment.mode = explicit` não passa pela resolução: valida I4 para o
`primary_executor` informado e grava decisão com `effective_mode = explicit`,
`policy_source = request`.

### 6.4 Ranking `least_loaded` (algoritmo `lb-1`)

```text
Elegíveis(unit, project):
    membership ativa na unit
    ∧ WorkspaceMember ativo
    ∧ ProjectMember ativo no project com role ≥ 15
    ∧ user.is_bot = false
    ∧ (Fase 3) sem WorkspaceMemberAvailability cobrindo now()
    ∧ (Fase 3) MembershipAllocationSettings.accepts_new_work ≠ false
    ∧ (se policy.max_open_items_per_member) total_open < limite
Carga por elegível:
    total_open = count IssueOrganizationalUnit(primary_executor=u, routing_state=assigned,
                 issue.state.group ∉ {completed, cancelled}, workspace=ws)
    unit_open  = idem, filtrando organizational_unit = unit
    last_auto  = max AssignmentDecision.created_at where chosen_assignee=u
                 and effective_mode=least_loaded and outcome=assigned (null = nunca)
Ordenar por (total_open ASC, unit_open ASC, last_auto ASC NULLS FIRST, user_id ASC)
Escolher o primeiro; se vazio → allocation_failed / no_eligible_member
```

Diferenças em relação ao engine atual: conta só executor principal (F6), conta
no workspace inteiro antes da área, usa a última **automática**, exclui o
"acrescentar o projeto à lista" (D1), e o desempate final é determinístico.
Excluídos entram no `candidates_snapshot` com `excluded_reason`.

### 6.5 Concorrência

Toda transição de `routing_state` roda dentro de `transaction.atomic()` e
começa com `SELECT ... FOR UPDATE` na linha de `IssueOrganizationalUnit`.
Para `least_loaded`, adicionalmente um advisory lock por área:

```python
with transaction.atomic():
    cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [f"orca-alloc-{unit.id}"])
    link = IssueOrganizationalUnit.objects.select_for_update().get(issue=issue)
    ...ranking, IssueAssignee.create, link.save, AssignmentDecision.create
```

O lock por área serializa as alocações automáticas daquela área, o que é o
comportamento desejado: a carga que a segunda requisição lê já inclui a
primeira. Claim usa só o `FOR UPDATE` da linha; o perdedor recebe
`ORG_WORK_ITEM_ALREADY_CLAIMED` com o vencedor no corpo.

### 6.6 SLA de atribuição

Precedência: `assignment_due_at` explícito na requisição > `policy_project.
assignment_sla_seconds` > `policy_unit.assignment_sla_seconds` > null.
Gravado em `IssueOrganizationalUnit.assignment_due_at` ao entrar em
`queued`/`allocation_failed`; a Fase 4 espelha em `IssueServiceLevel`.
Item com `assignment_due_at < now()` e `routing_state ∈ (queued,
allocation_failed)` é "atrasado na atribuição" na fila.

### 6.7 Idempotência e replay

```text
receber POST com header Idempotency-Key (obrigatório em /api/v1/orca/ mutações)
canonical = json.dumps(payload, sort_keys=True, separators=(",",":"))
hash = sha256(canonical)
with atomic():
    op, created = AutomationOperation.get_or_create(workspace, key,
                    defaults={request_hash: hash, status: in_progress, ...})
    se não created:
        se op.request_hash ≠ hash: 409 ORG_IDEMPOTENCY_PAYLOAD_MISMATCH
        se op.status = succeeded: retornar op.response_snapshot (200, header Idempotent-Replay: true)
        se op.status = failed: retornar op.response_snapshot com o status original
        se op.status = in_progress e created_at > now()-60s: 409 ORG_OPERATION_IN_PROGRESS
        se in_progress abandonada: retomar (continuar como created)
    executar operação (seção 7), gravar response_snapshot, status
```

Replay nunca re-executa a seção de alocação. Se o item foi reatribuído por um
humano entre a primeira chamada e o replay, a resposta do replay é a
**original** (snapshot), não o estado atual; o cliente que quer o estado
atual faz `GET`.

Binding: dentro da mesma transação, `ExternalWorkItemBinding.get_or_create`.
Se já existe para outro item, `409 ORG_EXTERNAL_BINDING_CONFLICT` e a
operação fecha como `failed`.

### 6.8 Encaminhamento (transfer)

`transfer_unit(issue, to_unit, actor, reason)`:

1. validar I2 para `to_unit`;
2. `FOR UPDATE` na linha; gravar `IssueResponsibilityEvent(from, to)`;
3. se `routing_state = assigned` e `primary_executor` não é membro de
   `to_unit`: devolver à fila (`manually_returned`), decisão com
   `previous_primary_executor`; `IssueAssignee` do executor anterior é
   mantido como colaborador (o Plane continua mostrando a pessoa) até o
   coordenador decidir;
4. resolver política em `to_unit` e aplicar como em criação.

### 6.9 Indisponibilidade (Fase 3)

Tarefa Celery horária `orca_availability_sweep` (registrar em
`plane/celery.py` e listar em `settings/common.py` como as demais tarefas
Orca): para cada `primary_executor` que ficou indisponível ou cuja membership
/ `WorkspaceMember` / `ProjectMember` foi desativada, devolver os itens
`assigned` à fila com `executor_unavailable`, gravando decisão com
`trigger = availability`. Dry-run por padrão via comando
`orca_availability_sweep --write`. O beat só roda em modo write quando
`ORCA_AVAILABILITY_ENABLED=1`.

---

## 7. Contrato da API pública `/api/v1/orca/`

### 7.1 Regras gerais

- Namespace: `apps/api/plane/api/urls/orca.py` incluído em
  `apps/api/plane/api/urls/__init__.py`; views em
  `apps/api/plane/api/views/orca/`; serializers em
  `apps/api/plane/api/serializers/orca/`. Herdam de `BaseAPIView` da API
  pública (`APIKeyAuthentication`).
- Mixin de kill switch: `OrcaPublicApiFeatureMixin` (mesmo padrão do
  `OrganizationalUnitFeatureMixin`) exigindo `ORCA_ORG_UNITS_ENABLED` **e**
  `ORCA_PUBLIC_API_ENABLED`.
- Autorização: o `APIToken` pertence a um usuário; a permissão efetiva é a
  desse usuário no workspace/projeto, como no resto da API v1. Criar item com
  área exige papel Member ou Admin no projeto.
- Header `Idempotency-Key` obrigatório em toda mutação. Ausência: `400
  ORG_IDEMPOTENCY_KEY_REQUIRED`.
- Throttle próprio `orca_public` (novo arquivo
  `apps/api/plane/throttles/orca_public.py`, scope por token), configurável
  por `ORCA_PUBLIC_API_RATE_LIMIT`, default `300/minute`.
- Erros: mesmo envelope de `orca_error` (`error_code`, `error_message`),
  com os códigos novos registrados nos **três** lugares:
  `apps/api/plane/utils/orca_error_codes.py`,
  `packages/constants/src/orca/error-codes.ts` e o catálogo i18n.
- Versionamento: o prefixo `/api/v1/orca/` é a versão. Mudanças
  incompatíveis abrem `/api/v2/orca/`.

### 7.2 Endpoints

**`GET /api/v1/orca/workspaces/{slug}/units/`**
Lista áreas ativas: `id`, `slug`, `name`, `projects: [{project_id,
identifier, default_role, policy: {default_mode, allowed_modes}}]`. Paginado
como a API v1.

**`GET /api/v1/orca/workspaces/{slug}/units/{unit_slug}/queue/`**
Fila da área: filtros `routing_state`, `overdue=true`, `project`. Retorna
itens com `issue_id`, `sequence_id`, `name`, `routing_state`, `queue_reason`,
`queued_at`, `assignment_due_at`, `primary_executor`.

**`POST /api/v1/orca/workspaces/{slug}/projects/{project_id}/work-items/`**
Operação composta. Corpo:

```json
{
  "external": { "source": "espo-onboarding", "id": "cliente-123:validacao-cadastral" },
  "work_item": {
    "name": "Validar documentação cadastral",
    "description_html": "<p>...</p>",
    "state": "uuid-do-estado",
    "priority": "high",
    "labels": ["uuid"],
    "start_date": "2026-09-03",
    "target_date": "2026-09-05",
    "parent": null
  },
  "responsibility": {
    "unit": "compliance",
    "assignment": { "mode": "default" },
    "assignment_due_at": "2026-09-03T12:00:00-03:00",
    "completion_due_at": "2026-09-05T18:00:00-03:00"
  },
  "process": {
    "source": "espo-onboarding",
    "instance_id": "cliente-123",
    "template_name": "onboarding-cliente",
    "template_version": "3",
    "step_key": "compliance.kyc",
    "completion_mode": "automatic_with_review"
  }
}
```

`assignment` aceita:

- `{"mode": "default"}` — resolução da seção 6.3;
- `{"mode": "manual" | "self_claim" | "least_loaded"}` — solicitada, sujeita a
  `allowed_modes`;
- `{"mode": "explicit", "primary_executor": "user-uuid", "collaborators":
  ["user-uuid"]}` — atribuição direta, valida I4 para o principal e
  `ProjectMember` ativo para colaboradores.

`process` é opcional e só aceito com `ORCA_PROCESS_PROJECTION_ENABLED=1`
(Fase 4); antes disso, presença do bloco responde `400
ORG_PROCESS_PROJECTION_DISABLED`.

Transação (ordem fixa):

1. `AutomationOperation` (seção 6.7);
2. `ExternalWorkItemBinding.get_or_create` → localizar ou criar `Issue` via o
   serializer nativo da API v1 **com** `assignees=[]` explícito e o fallback
   D2 desligado para este caminho;
3. `IssueOrganizationalUnit` (I2) + `IssueResponsibilityEvent(from=null)`;
4. resolução de política; alocação ou fila; `IssueAssignee` para principal e
   colaboradores; `AssignmentDecision`;
5. `assignment_due_at`; (Fase 4) `IssueServiceLevel`, `ProcessInstance*`;
6. `response_snapshot`, `status = succeeded`;
7. `transaction.on_commit`: `issue_activity` nativo, webhooks nativos,
   contadores.

Resposta `201` (ou `200` em replay):

```json
{
  "work_item": { "id": "...", "sequence_id": 128, "identifier": "ONB-128", "url": "..." },
  "binding": { "source": "espo-onboarding", "id": "cliente-123:validacao-cadastral", "created": true },
  "responsibility": {
    "unit": { "id": "...", "slug": "compliance" },
    "routing_state": "assigned",
    "queue_reason": "",
    "primary_executor": { "id": "...", "email": "maria@..." },
    "assignment_due_at": "2026-09-03T15:00:00Z"
  },
  "decision": {
    "id": "...", "requested_mode": "default", "effective_mode": "least_loaded",
    "policy_source": "unit_project", "policy_version": 3, "algorithm_version": "lb-1",
    "outcome": "assigned"
  },
  "operation": { "idempotency_key": "...", "replay": false }
}
```

Se a operação já existia, retorna o snapshot com `operation.replay = true`.

**`POST /api/v1/orca/workspaces/{slug}/projects/{project_id}/work-items/{issue_id}/reassign/`**
Corpo `{"primary_executor": "user-uuid", "reason": "..."}` ou
`{"return_to_queue": true, "reason": "..."}`. Requer `If-Match` com o
`decision.id` vigente (controle otimista); divergência responde `412
ORG_DECISION_STALE`. Grava decisão com `supersedes`.

**`POST /api/v1/orca/workspaces/{slug}/projects/{project_id}/work-items/{issue_id}/transfer/`**
Corpo `{"unit": "juridico", "reason": "..."}`. Seção 6.8.

**`POST /api/v1/orca/workspaces/{slug}/projects/{project_id}/work-items/{issue_id}/complete/`** (Fase 4)
Corpo `{"evidence": {...}, "rule_version": "..."}`. Respeita
`completion_mode` do `ProcessInstanceItem`: `automatic` move para o estado
`completed` do projeto; `automatic_with_review` move para o estado marcado
como revisão (configuração da área↔projeto; default: mantém estado e adiciona
label `aguardando-validacao`); `manual` responde `409
ORG_COMPLETION_MANUAL_ONLY`.

**`GET /api/v1/orca/workspaces/{slug}/work-items/by-external/{source}/{id}/`**
Localiza o item pelo binding; devolve o mesmo envelope da criação com o
estado atual.

### 7.3 Códigos de erro novos

| Código | HTTP | Quando |
| --- | --- | --- |
| `ORG_UNIT_NOT_COVERING_PROJECT` | 400 | I2 |
| `ORG_ASSIGNMENT_MODE_NOT_ALLOWED` | 400 | I7 |
| `ORG_EXECUTOR_NOT_ELIGIBLE` | 400 | I4 em `explicit` ou reassign |
| `ORG_WORK_ITEM_ALREADY_CLAIMED` | 409 | claim perdido |
| `ORG_DECISION_STALE` | 412 | `If-Match` divergente |
| `ORG_IDEMPOTENCY_KEY_REQUIRED` | 400 | header ausente |
| `ORG_IDEMPOTENCY_PAYLOAD_MISMATCH` | 409 | I9 |
| `ORG_OPERATION_IN_PROGRESS` | 409 | seção 6.7 |
| `ORG_EXTERNAL_BINDING_CONFLICT` | 409 | I8 |
| `ORG_PROCESS_PROJECTION_DISABLED` | 400 | bloco `process` sem flag |
| `ORG_COMPLETION_MANUAL_ONLY` | 409 | completar item manual via API |
| `ORG_PUBLIC_API_DISABLED` | 404 | via mixin (mesmo comportamento do kill switch) |

---

## 8. API interna `/api/orca/` e UI

### 8.1 Endpoints internos novos ou alterados

Todos em `apps/api/plane/app/views/organizational_unit.py` (ou um novo
`organizational_queue.py` importado em `views/__init__.py`) e registrados em
`apps/api/plane/app/urls/orca.py`.

| Rota | Método | Permissão | Fase |
| --- | --- | --- | --- |
| `.../issues/{issue_id}/organizational-unit/` | POST | Admin/Member do projeto; passa a validar I2 e a resolver política; grava evento | D0 |
| `.../issues/{issue_id}/organizational-unit-assign/` | POST | Admin/Member; passa a usar `lb-1` com lock; grava decisão | D0 |
| `.../issues/{issue_id}/organizational-unit/claim/` | POST | membro elegível da área (I4) | 2 |
| `.../issues/{issue_id}/organizational-unit/reassign/` | POST | coordenador da área ou Admin do workspace | 2 |
| `.../issues/{issue_id}/organizational-unit/return/` | POST | coordenador, ou o próprio executor | 2 |
| `.../issues/{issue_id}/organizational-unit/transfer/` | POST | coordenador da área de origem ou Admin | 2 |
| `.../organizational-units/{unit_id}/queue/` | GET | membro da área, coordenador, Admin | 2 |
| `.../organizational-units/{unit_id}/decisions/` | GET | coordenador, Admin | 2 |
| `.../organizational-units/{unit_id}/policy/` | GET/PUT | GET: membro; PUT: Admin | D0 (GET), 2 (PUT) |
| `.../organizational-units/{unit_id}/projects/{pk}/policy/` | GET/PUT | idem | D0/2 |
| `.../organizational-units/{unit_id}/coordinators/` | GET/POST/DELETE | Admin | 2 |
| `.../availability/me/` | GET/POST/DELETE | o próprio | 3 |
| `.../organizational-units/{unit_id}/members/{pk}/allocation/` | PUT | coordenador, Admin | 3 |
| `.../organizational-units/{unit_id}/executive/` | GET | Workspace Admin | 5 |

Permissão "coordenador da área": novo helper
`is_unit_coordinator(user, unit)` em `apps/api/plane/app/permissions/` usado
por um decorator `allow_unit_coordinator`, no espírito de `allow_permission`.

### 8.2 Frontend

- **Store:** estender `apps/web/core/store/orca/organizational-unit.store.ts`
  com `queueByUnit`, `decisionsByUnit`, `policyByUnit`, `coordinatorsByUnit`;
  service correspondente em
  `apps/web/core/services/orca/organizational-unit.service.ts`.
- **Tipos:** `packages/types/src/organizational-unit.ts` ganha
  `TRoutingState`, `TQueueReason`, `TAssignmentPolicy`, `TAssignmentDecision`,
  `TQueueItem`.
- **Propriedade do item:** `issue-unit-property.tsx` passa a listar só áreas
  que cobrem o projeto (I2) e a mostrar `routing_state` + executor principal.
  Botão "atribuir" vira menu: atribuir automático, assumir, escolher pessoa,
  devolver à fila.
- **Tela da área:** `unit-detail.tsx` ganha aba `work` (`Trabalho`) com quatro
  seções: caixa de entrada (`queued`/`allocation_failed`), em execução
  (agrupado por executor), atenção (atrasados na atribuição, `target_date`
  vencido, `suspended`, executor indisponível), decisões (histórico). Novos
  componentes em `apps/web/core/components/orca/organizational-units/`:
  `unit-work-tab.tsx`, `queue-list.tsx`, `queue-item-row.tsx`,
  `assign-member-modal.tsx`, `decision-timeline.tsx`, `policy-form.tsx`,
  `coordinators-tab.tsx`, `availability-form.tsx` (Fase 3).
- **Rota "Minha Área":** `apps/web/app/routes/core.ts` recebe
  `:workspaceSlug/my-areas` apontando para nova página que lista as áreas de
  `organizational-units/me/` e reaproveita `unit-work-tab.tsx`. Entrada na
  sidebar do workspace (`apps/web/core/components/sidebar/`), visível só
  quando o usuário tem ao menos uma área.
- **i18n:** todas as strings novas no catálogo (`packages/i18n/src/locales/
  */workspace-settings.json`, namespace `organizational_units`), em todos os
  locais, seguindo o skill `translate`. O `check:sync` do CI falha se um
  locale ficar para trás.
- **Códigos de erro:** cada código novo entra em
  `packages/constants/src/orca/error-codes.ts` e no catálogo.

---

## 9. Fases, itens de trabalho e gates

Convenção: `[Pn.m]` identifica o item; cada item vira um PR pequeno contra
`stage` com prefixo `feat(orca)`, `fix(orca)`, etc. P0 e D0 correm em
paralelo. As demais são sequenciais.

### Fase P0 — Segurança da plataforma

| Item | Entrega | Arquivos |
| --- | --- | --- |
| P0.0 | `docker-compose-orca.yml` puxa de `${ORCA_IMAGE_REPOSITORY:-ghcr.io/vitordj/plane}`; job `compose_provenance` falha se o default divergir do namespace que `stage.yml` publica. **Entregue 04/09.** | `docker-compose-orca.yml`, `stage.yml`, `README.md`, `.env.example` |
| P0.1 | `build-push` não publica em `pull_request`: `push: ${{ github.event_name != 'pull_request' }}`; em PR usa tag `pr-<n>-<sha>` só para build (sem push) | `.github/workflows/stage.yml` |
| P0.2 | Em push para `stage`, publicar `sha-<commit>` além de `:stage`; gravar os seis digests em artefato `image-digests.json` e como output do job | `stage.yml` |
| P0.3 | `prod.yml` promove por `sha-<commit>` do merge de `stage` em `prod` (ou pelos digests do artefato), nunca por `:stage` | `.github/workflows/prod.yml` |
| P0.4 | Job `promote-rc` falha se não encontrar nem criar a PR; valida status HTTP; sem `\|\| true` nos passos críticos | `stage.yml` |
| P0.5 | `pnpm install --frozen-lockfile` no CI; `permissions:` global reduzido a `contents: read`, escrita só nos jobs que precisam | `stage.yml`, `prod.yml` |
| P0.6 | `create_users.py`: sem senha fixa; `set_unusable_password()` + `is_password_autoset=True`; README orienta primeiro acesso por Entra ou magic link; invalidar contas já criadas com a senha antiga | `tools/migration/create_users.py`, `tools/migration/README.md` |
| P0.7 | `TRUSTED_PROXIES` sem default `0.0.0.0/0`: Caddyfile usa `{$TRUSTED_PROXIES}` sem fallback; `docker-compose-orca.yml` e README exigem a faixa do Coolify | `apps/proxy/Caddyfile.ce`, `docker-compose-orca.yml`, `.env.example`, `README.md` |
| P0.8 | Job `api_tests` roda também `plane/tests/unit` (exceto `orca/`, já coberto) com lista explícita de exclusões justificadas em `apps/api/tests/RUNNING_TESTS.md` | `stage.yml`, `apps/api/tests/RUNNING_TESTS.md` |
| P0.9 | Job `ruff check` + `ruff format --check` em `apps/api` (migrações já excluídas no `pyproject.toml`); corrigir os findings existentes nos arquivos do fork | `stage.yml`, arquivos apontados por `ruff` |
| P0.10 | Entra: validar `iss`, `aud`, `exp`, `nbf`, `nonce` e assinatura via JWKS com cache, usando biblioteca mantida; timeouts explícitos nas chamadas ao token endpoint e ao Graph | `apps/api/plane/authentication/provider/oauth/entra.py`, `adapter/oauth.py` |
| P0.11 | Sync com Plane CE 1.4.2 via branch `sync/upstream-merge-<data>` | fluxo do FORK.md §Phase 5 |
| P0.12 | Apagar os três branches remotos obsoletos | remoto |
| P0.13 | Alinhar `package.json`, manifest do Release Please e template de RC para `1.5.0`; documentar o fluxo real de duas etapas (merge em `prod` + commit `chore(prod): release`) | `package.json`, `.release-please-manifest.json`, `.github/PULL_REQUEST_TEMPLATE/release_candidate.md`, `FORK.md` |
| P0.14 | Kill switch com parser estrito (`1/true/yes/on`, `0/false/no/off`, outro valor falha no boot); `reconcile_access` recusa quando desligado; Compose encaminha `ORCA_*` e `SCIM_*` a api, worker, beat e migrator. **Entregue 04/09.** | `plane/utils/orca_env.py`, `settings/common.py`, `org_unit_reconciler.py`, `docker-compose-orca.yml` |
| P0.15 | Runtime expõe commit e versão (`GET /api/orca/build-info/`, build-arg `GIT_SHA`) | `stage.yml`, Dockerfiles, `views/orca_build_info.py` |
| P0.16 | Fixar `minio/minio` em tag imutável; alinhar PostgreSQL do CI (16) com o do Compose (15.7) ou documentar a matriz | `docker-compose-orca.yml`, `stage.yml`, `RUNNING_TESTS.md` |

**Gate P0:** CI verde com suíte upstream e ruff; um ensaio completo de RC
(criação da PR, promoção por digest, deploy em stage, rollback para os seis
digests anteriores) documentado em `docs/release-runbook.md`.

### Fase D0 — Fundação do domínio

| Item | Entrega | Arquivos |
| --- | --- | --- |
| D0.1 | **D1:** validar I2 em `IssueOrganizationalUnitEndpoint.post`; UI lista só áreas que cobrem o projeto; engine deixa de acrescentar o projeto | `views/organizational_unit.py`, `issue-unit-property.tsx`, `assignment_engine.py`, novo código `ORG_UNIT_NOT_COVERING_PROJECT` |
| D0.2 | **D2:** fallback "último assignee do criador" sai da API pública; volta o comportamento upstream (`default_assignee`); se a UI quiser manter, fica atrás de `ProjectCustomSettings.remember_last_assignees` (default off) | `apps/api/plane/api/serializers/issue.py`, `apps/api/plane/db/models/project_custom_settings.py`, teste em `test_issue_serializer_orca_features.py` |
| D0.3 | Migração `0135`: `routing_state`, `queue_reason`, `queued_at`, `assignment_due_at`, `primary_executor`, `current_assignment_decision` + data migration + CHECKs + índices | `db/models/organizational_unit.py`, `db/migrations/0135_*.py` |
| D0.4 | Migração `0136`: `OrganizationalUnitAssignmentPolicy`; migração `0137`: `AssignmentDecision`, `IssueResponsibilityEvent` | `db/models/organizational_unit.py` (ou novo `organizational_assignment.py` exportado em `db/models/__init__.py`), migrações |
| D0.5 | Serviço `assignment_service.py` com `resolve_policy`, `rank_candidates` (`lb-1`), `allocate`, `claim`, `reassign`, `return_to_queue`, `transfer_unit`, todos com lock (6.5) e decisão (I5) | `apps/api/plane/app/services/orca/assignment_service.py`; `assignment_engine.py` passa a delegar e é marcado como legado |
| D0.6 | Endpoints internos existentes passam a usar o serviço; `organizational-unit-assign` grava decisão; GET de política | `views/organizational_unit.py`, `urls/orca.py` |
| D0.7 | Comando `audit_organizational_routing` (dry-run default) que lista violações de I3/I4 e, com `--write`, devolve à fila o que estiver inconsistente | `apps/api/plane/db/management/commands/audit_organizational_routing.py` |
| D0.8 | Contadores (logger estruturado + métricas quando houver backend): `orca.assignment.outcome{mode,outcome}`, `orca.queue.no_candidate`, `orca.decision.superseded`, `orca.idempotency.conflict` | `assignment_service.py` |
| D0.9 | Testes: matriz da seção 10 para D0 | `apps/api/plane/tests/unit/orca/test_assignment_service.py`, `test_routing_state.py`, `test_assignment_concurrency.py` |
| D0.10 | Docs: atualizar `organizational-units.md` (§Assignment) e este documento | `docs/` |
| D0.11 | Arquivar/desarquivar projeto dispara `dispatch_reconciliation` para aquele projeto; reconciliação com alvo explícito aceita projeto arquivado para desativar o herdado | `views/project/base.py`, `org_unit_reconciler.py` |
| D0.12 | `members_of` do SCIM filtra memberships soft-deleted; teste de contrato do `GET /Groups/{id}` | `views/orca_scim/groups.py`, `test_scim_endpoints.py` |

**Gate D0:** todas as invariantes I1–I7 com teste positivo e negativo; teste
de concorrência (20 alocações simultâneas na mesma área distribuem
corretamente; 10 claims simultâneos produzem 1 vencedor e 9 conflitos);
comando de auditoria sem violações num banco com os dados de `stage`;
`pytest plane/tests/unit/orca/` verde.

### Fase 1 — Contrato público de automação

| Item | Entrega |
| --- | --- |
| 1.1 | Migração `0138`: `ExternalWorkItemBinding`, `AutomationOperation` |
| 1.2 | `OrcaPublicApiFeatureMixin`, flag `ORCA_PUBLIC_API_ENABLED` em `settings/common.py` e `.env.example`; throttle `orca_public` |
| 1.3 | Serviço `automation_operation.py`: parse do header, hash canônico, `get_or_create`, retomada de abandonadas, snapshot |
| 1.4 | `POST work-items/` composto (7.2), `GET by-external`, `GET units/`, `GET queue/` |
| 1.5 | `reassign/` e `transfer/` públicos com `If-Match` |
| 1.6 | Códigos de erro novos nos três lugares |
| 1.7 | `docs/orca-public-api.md` com exemplos `curl` e um cliente Python de referência em `tools/orca-client/` (script, não pacote) usado nos testes de contrato |
| 1.8 | Testes: idempotência, binding, transação, permissões por token, throttle, flag desligada = 404 |

**Gate 1:** o cliente de referência cria 50 itens duas vezes com as mesmas
chaves e o estado final é idêntico (contagem de `Issue`,
`AssignmentDecision`, `IssueAssignee`); duas requisições simultâneas com a
mesma chave criam um item; mesma chave com payload diferente responde 409;
replay após reatribuição humana não altera `primary_executor`; nenhuma rota
`/api/orca/` aceita API key; nenhuma rota `/api/v1/orca/` aceita sessão.
`ORCA_PUBLIC_API_ENABLED` permanece `0` em produção até o Gate 2-mínimo.

### Fase 2 — Fila da área e coordenador

| Item | Entrega |
| --- | --- |
| 2.1 | Migração `0140`: `OrganizationalUnitCoordinator`; reconciliador garante `ProjectMember` (F17) para coordenadores nos projetos cobertos, com proveniência própria (`OrganizationalUnitGrant` com origem `coordinator`) |
| 2.2 | Helper de permissão `allow_unit_coordinator`; endpoints `claim`, `reassign`, `return`, `transfer`, `queue`, `decisions`, `policy PUT`, `coordinators` |
| 2.3 | UI: aba Trabalho em `unit-detail.tsx`; menu de atribuição em `issue-unit-property.tsx`; página "Minha Área"; formulário de política; aba coordenadores |
| 2.4 | Alertas: notificação nativa (`plane.bgtasks.notification_task`) para coordenadores quando `allocation_failed` ou `assignment_due_at` vencido; tarefa Celery `orca_queue_sla_sweep` a cada 15 min, com flag |
| 2.5 | i18n completo; `check:sync` verde |
| 2.6 | Testes de permissão negativos (Guest, Member de outro projeto, coordenador de outra área, lead sem coordenação) |

**Gate 2-mínimo (libera `ORCA_PUBLIC_API_ENABLED` em produção):** fila
visível, alerta de `no_eligible_member`, atribuição manual pelo coordenador,
devolução à fila.
**Gate 2 completo:** um coordenador esvazia uma fila de 30 itens sem tocar em
telas nativas; nenhuma ação da aba altera `ProjectMember` (verificado por
teste que compara a tabela antes e depois); histórico de decisões mostra cada
ação.

### Fase 3 — Disponibilidade e distribuição

| Item | Entrega |
| --- | --- |
| 3.1 | Migração `0139`: `WorkspaceMemberAvailability`, `MembershipAllocationSettings`; flag `ORCA_AVAILABILITY_ENABLED` |
| 3.2 | `rank_candidates` respeita disponibilidade e `accepts_new_work`; `max_open_items` |
| 3.3 | Endpoints `availability/me/` e `members/{pk}/allocation/`; UI: formulário "estou indisponível de/até", toggle por área, indicador na fila |
| 3.4 | Sweep horário `orca_availability_sweep` (6.9), dry-run default, comando manual com `--write` |
| 3.5 | Sugestão de próximo candidato para itens devolvidos (só sugestão; confirmação humana) |
| 3.6 | Testes: férias começam/terminam, saída da área, desativação de `WorkspaceMember`, retorno; sweep idempotente |

**Gate 3:** cenários acima verdes; nenhuma reatribuição sem
`AssignmentDecision(trigger=availability)`; sweep em dry-run num banco
realista sem falsos positivos.

### Fase 4 — Processos automáticos

| Item | Entrega |
| --- | --- |
| 4.1 | Confirmar A5 (Compose). Decidir se Compose gera só schema (estados, labels) ou fica fora |
| 4.2 | Migrações `0141`/`0142`: `IssueServiceLevel`, `ProcessInstanceReference`, `ProcessInstanceItem`; flag `ORCA_PROCESS_PROJECTION_ENABLED` |
| 4.3 | Bloco `process` no `POST work-items/`; endpoint `complete/`; `GET .../process-instances/{source}/{id}/` com progresso |
| 4.4 | Orquestrador sidecar em repositório próprio (`orca-orchestrator`): templates YAML versionados, consumidor de eventos (EspoCRM ou outro), cliente da API pública, reprocessamento seguro por `idempotency_key = f"{source}:{instance}:{step}:{event_id}"` |
| 4.5 | Webhooks nativos do Plane como retorno para o orquestrador (item mudou de estado); `WEBHOOK_ALLOWED_HOSTS` já existe no fork |
| 4.6 | UI: agrupamento visual por instância na fila; projeção opcional em módulo quando a instância está num só projeto (A6) |
| 4.7 | Runbook: desligar o orquestrador não deixa estado inconsistente; retomar instância pela metade |

**Gate 4:** reprocessar o mesmo evento não duplica nada; falha no meio de uma
instância é retomável; desligar o orquestrador e religar converge; cada
instância registra `template_version`.

### Fase 5 — Visão executiva

| Item | Entrega |
| --- | --- |
| 5.1 | Endpoint `executive/` (Workspace Admin) com agregados por área e por processo: backlog, sem executor, atrasados na atribuição, `target_date` vencido, aging (p50/p90 de `queued_at`), throughput semanal, cycle time, concentração (top 3 executores por área) |
| 5.2 | Consultas materializadas por tarefa noturna se o volume exigir; senão, consultas diretas com índices da seção 5 |
| 5.3 | UI: página executiva com drill-down até o item, respeitando acesso nativo (item de projeto sem acesso aparece agregado, não listado) |
| 5.4 | Testes com dataset fixo: cada número tem consulta reproduzível |

**Gate 5:** cada indicador tem teste com valor esperado; nenhuma rota
executiva expõe item de projeto ao qual o leitor não pertence.

---

## 10. Matriz de testes

Local: `apps/api/plane/tests/unit/orca/`. Reaproveitar fixtures de
`conftest.py` (`workspace_with_members`, `project`, `second_project`,
`foreign_project`, `unit`, `second_unit`, usuários por papel). Marcar
`@pytest.mark.unit`. Concorrência usa `TransactionTestCase` ou
`pytest.mark.django_db(transaction=True)` com `threading` e conexões
separadas; documentar em `apps/api/tests/TESTING_GUIDE.md`.

| Área | Casos mínimos |
| --- | --- |
| I2 cobertura | área cobre → 200; área não cobre → 400; área inativa → 400; vínculo área↔projeto removido depois → auditoria aponta |
| Resolução de política | sem política → `manual`/`fallback`, com qualquer modo solicitável; só área → área; área+projeto → projeto; solicitada permitida; solicitada proibida → 400; `explicit` ignora política |
| Ranking `lb-1` | menos carregado vence; empate total → menos na área; empate → nunca alocado antes de já alocado; empate → menor id; colaborador não conta; item concluído não conta; bot excluído; Guest excluído; `max_open_items` exclui |
| Estados | cada transição da tabela 6.2 (positivo) e cada transição ausente (negativo, 409); CHECKs do banco |
| Decisões | toda transição cria decisão; reversão tem `supersedes`; `candidates_snapshot` sem PII além de id; append-only (update falha em teste de modelo) |
| Concorrência | 20 alocações simultâneas na mesma área com 4 membros → distribuição 5/5/5/5; 10 claims → 1 vencedor; alocação e claim simultâneos no mesmo item → um só executor |
| Idempotência | replay idêntico → mesmo snapshot, sem nova decisão; hash diferente → 409; duas simultâneas → um item; abandonada retomada; binding duplicado → 409; replay após reatribuição humana não altera executor |
| Transação | falha na etapa 4 (política proibida) não deixa `Issue` nem binding; `on_commit` não dispara em rollback |
| Permissões | matriz papel × endpoint para: Admin ws, Member do projeto, Member de outro projeto, Guest, coordenador da área, coordenador de outra área, lead sem coordenação, API key de usuário Guest |
| Kill switches | cada flag desligada → 404 nas rotas correspondentes; UI esconde |
| Encaminhamento | mesma área → no-op sem evento; área que não cobre → 400; executor não membro da nova área → volta à fila |
| Disponibilidade | intervalos abertos/fechados; `accepts_new_work=false` exclui só naquela área; sweep devolve e registra; sweep repetido não duplica |
| API pública | header ausente → 400; token de usuário Guest → 403; throttle → 429; flag desligada → 404; `assignees` nativo ignorado no namespace Orca |
| Frontend | testes de store para fila e decisões (vitest, padrão existente em `packages/`); pelo menos um teste de componente para `queue-list.tsx` |

---

## 11. Observabilidade

Desde D0, via logger estruturado (`plane.utils.logging` ou o padrão já usado
pelos reconciliadores) e, quando existir backend de métricas, contadores:

| Nome | Labels | Uso |
| --- | --- | --- |
| `orca.assignment.outcome` | `mode`, `outcome`, `trigger` | taxa de `allocation_failed` |
| `orca.queue.age_seconds` | `unit` | idade da fila (gauge no sweep) |
| `orca.queue.overdue` | `unit` | itens com `assignment_due_at` vencido |
| `orca.decision.superseded` | `unit`, `previous_mode` | quantas escolhas automáticas foram revertidas |
| `orca.idempotency.conflict` | `type` (`payload_mismatch`, `in_progress`, `binding`) | clientes com bug |
| `orca.public_api.latency_ms` | `endpoint` | p50/p95 |
| `orca.availability.returned` | `unit`, `reason` | itens devolvidos por indisponibilidade |

Toda entrada de log carrega `workspace_id`, `unit_id`, `issue_id`,
`decision_id`, `operation_id` quando existirem. Nunca e-mail ou nome.

---

## 12. Riscos e mitigação

| Risco | Mitigação |
| --- | --- |
| Alocação automática criando trabalho que ninguém vê | F24: API pública desligada em produção até o Gate 2-mínimo |
| Migração `0135` em tabela com dados | data migration idempotente; ensaiar contra cópia do banco de `stage` antes da RC |
| Lock por área virando gargalo em lote grande | lote de 500 itens serializado por área leva segundos, não minutos; medir no Gate 1 com 200 criações; se necessário, lock por área+projeto |
| Duas fontes de "quem executa" (nativo × lateral) divergirem | I3 + comando de auditoria + teste que remove `IssueAssignee` nativamente e verifica que a fila reflete (hook `post_delete` em `IssueAssignee` que devolve à fila com `executor_unavailable`, registrado em `services/orca/signals.py`) |
| Coordenador ganhando acesso indevido | F17: acesso só via `ProjectMember` reconciliado; teste compara tabela antes/depois de cada ação |
| Compose se revelar diferente do assumido | F12 isola: nada da Fase 1 depende do Compose |
| Upstream mudar `IssueAssignee` ou API v1 | tudo lateral; serializer público Orca é próprio; sync trimestral com upstream |
| Replay reescrever escolha humana | F11 + teste explícito |

### O que não fazer

- Transformar a área em usuário fictício para virar assignee.
- Colocar todos os integrantes como assignees de tudo.
- Coluna nova em modelo core para executor principal, área ou estado de fila.
- Política chamada `queue` ou `specific_member`.
- Semântica Orca em `assignees` omitido / `[]`.
- Idempotência só por `external_source`/`external_id`.
- "Convergir ao estado desejado" em replay.
- Reatribuição automática na saída de pessoas sem período assistido.
- Compose como motor de instâncias.
- Dashboard executivo antes da fila estar em uso real.
- Habilitar `ORCA_PUBLIC_API_ENABLED` antes do Gate 2-mínimo.

---

## 13. Convenções do repositório para quem pega o handoff

- **Branch:** partir de `stage`; nome `feat/orca-<tema>` ou o gerado pelo
  skill `branch-name`. PR contra `stage` com título Conventional Commit e
  escopo `orca` (`feat(orca):`, `fix(orca):`, `docs(orca):`, `test(orca):`).
  O labeler injeta o template; preencher o checklist.
- **Não rodar** `pnpm check`, `pnpm build`, `check:types` ou migrações
  dentro da sessão de agente (AGENTS.md). Listar os comandos para o
  desenvolvedor. Rodar localmente `ruff check` e `ruff format` em
  `apps/api`, e `pnpm fix` nos pacotes tocados.
- **Migrações:** gerar com `python3 apps/api/manage.py makemigrations`;
  conferir dependência explícita na última Orca (`0134_orca_user_language_preference`);
  nunca editar migrações já mescladas; nunca apagar.
- **Testes backend:** `docker compose -f docker-compose-test.yml run --rm
  api-tests pytest plane/tests/unit/orca/ -q`; ver
  `apps/api/tests/RUNNING_TESTS.md`.
- **Códigos de erro:** três lugares (`utils/orca_error_codes.py`,
  `packages/constants/src/orca/error-codes.ts`, catálogo i18n). O teste
  `test_orca_error_codes.py` verifica a paridade.
- **i18n:** ler o skill `translate` antes de tocar em
  `packages/i18n/src/locales`. Lista de idiomas existe em seis lugares
  (ver `docs/i18n.md`); `test_default_language.py` falha se divergirem.
- **Copyright:** header em todo arquivo novo `.py`/`.ts`/`.tsx` via
  `addlicense` (ver `COPYRIGHT_CHECK.md`); o CI `copyright-check.yml` falha
  sem ele.
- **Docstrings:** formato `@description` / `@param` / `@returns`, como nos
  arquivos Orca existentes. Comentar o porquê de cada override do core.
- **Feature flags:** definir em `apps/api/plane/settings/common.py`, expor
  em `.env.example` e `apps/api/.env.example`, refletir em
  `OrcaConfigEndpoint` para a UI ler.
- **Docs:** cada fase atualiza `docs/organizational-units.md` e este
  documento; Fase 1 cria `docs/orca-public-api.md`; P0 cria
  `docs/release-runbook.md`.
- **Não tocar:** `apps/api/plane/db/models/issue.py`, `project.py`,
  `workspace.py`, `user.py` além de exports; `apps/api/plane/app/views/issue/`
  além do já alterado pelo fork.

---

## Apêndice A — Dúvidas do debate e decisão correspondente

| Dúvida | Decisão |
| --- | --- |
| 1. Uma área por item? | F1 |
| 2. Encaminhamento | F7 |
| 3. Executor vazio | F2; SLA de atribuição em 6.6 |
| 4. Claim | `self_claim` (F3) com lock (6.5) |
| 5. Executor principal | F5 |
| 6. Onde mora a política | F4 |
| 7. Quais políticas | F3 |
| 8. Carga | F6 |
| 9. Habilidades | A1 |
| 10. Reversão registrada | F8 |
| 11. Disponibilidade pessoa × membership | F14 |
| 12. Fonte de férias | F14 (manual; A3) |
| 13. Saída de pessoa | F15 |
| 14. Lead × coordenador | F16 |
| 15. Coordenador sem acesso | F17 |
| 16. CEO | F18 |
| 17. Compose | F12; A5 |
| 18. Template dentro/fora | F19 |
| 19. Agrupamento | F20; A6 |
| 20. Atomicidade | F9, F10, F11 |
| 21. Fechamento automático | F21 |
| 22. Dashboard por último | F23 |
| 23. SLA | F22 |

## Apêndice B — Exemplo de fluxo completo (onboarding)

```text
1. EspoCRM emite "cliente 123 aprovado" (event_id e1).
2. Orquestrador carrega template onboarding-cliente v3 (4 etapas).
3. Para cada etapa, POST /api/v1/orca/.../work-items/ com
   Idempotency-Key = "espo:cliente-123:<step>:e1"
   external = {source: espo-onboarding, id: "cliente-123:<step>"}
   responsibility.unit = área da etapa, assignment.mode = default
   process = {instance_id: cliente-123, template_version: 3, step_key, completion_mode}
4. Compliance tem política least_loaded no projeto Onboarding → item nasce assigned (Maria).
   Jurídico tem política manual → item nasce queued/awaiting_coordinator; coordenador é notificado.
   Backoffice tem self_claim → queued/awaiting_claim; aparece em "Minha Área" dos integrantes.
5. Coordenadora do Jurídico reatribui de Ana para João (decisão B supersedes A).
6. Orquestrador reprocessa e1 por falha de rede → replays retornam os snapshots; João continua.
7. Sistema de contas confirma criação → POST complete/ na etapa Backoffice (automatic) → estado Concluído.
8. Compliance conclui manualmente; Plane dispara webhook; orquestrador marca instância completed.
9. Executivo vê no dashboard: 1 onboarding concluído em 3,2 dias; Jurídico com 2 itens atrasados na atribuição.
```
