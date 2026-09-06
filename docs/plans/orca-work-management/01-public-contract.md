# Fase 1 — Contrato público de automação

**Objetivo:** expor a responsabilidade por área a clientes autenticados por
API key, em `/api/v1/orca/`, com operação composta idempotente. Ao final
desta fase a API existe, é testada e fica **desligada em produção** até o
Gate 2-mínimo.
**Pré-requisitos:** Gate P0 e Gate D0 fechados.

> [!WARNING]
> **Os itens 1.1, 1.2, 1.3 e 1.6 foram entregues com os dois gates ainda
> abertos**, a pedido explícito. Nada neles depende dos gates para estar
> correto — são modelos, uma migração, duas flags, um serviço e uma tabela de
> códigos —, mas o pré-requisito continua valendo para o **Gate 1**: ele não
> fecha antes dos outros dois. O que os gates ainda esperam está no README do
> plano.
> **Referência:** RFC §5.2 (`ExternalWorkItemBinding`, `AutomationOperation`),
> §6.7, §7 inteiro, §9 (Fase 1), §10.
> **Ordem:** 1.1 → 1.2 → 1.3 → 1.4 → 1.5 → 1.6 → 1.7 → 1.8.

---

## Bloco 1.4 → 1.8 — plano de execução (escrito em 05/09, para a sessão seguinte)

Este bloco é o resto da fase: os endpoints (1.4, 1.5), o fecho do 1.6, a
documentação e o cliente (1.7) e os testes de contrato (1.8). Está escrito
para que uma sessão de agente o execute de ponta a ponta com o mínimo de
redescoberta — cada passo diz o que muda, em que arquivo, qual teste prova, e
o que já está decidido para não ser rediscutido no meio.

### Onde estamos

- `stage` está em `89becdc7` (PR #10). O **PR #12** (`claude/project-next-steps-kyd7u5`, ponta `bb2265df`) entrega 1.1, 1.2, 1.3 e o 1.6 parcial; está **aberto, verde nos 16 checks e sem conflito**, ainda não mesclado.
- Este bloco vive na branch **`claude/plano-blocos-1-4-1-8-reo0t9`**, cortada da **ponta do PR #12**, não de `stage`: o 1.4 importa o serviço do 1.3, o mixin do 1.2 e os modelos do 1.1. O repositório mescla PRs com merge commit (todos os PRs #5–#11), então, mesclado o #12, a PR deste bloco passa a mostrar só o delta. Se o #12 ganhar commits antes disso: `git merge origin/claude/project-next-steps-kyd7u5` nesta branch, sem rebase.
- Os **Gates P0 e D0 continuam abertos** e nada deste bloco os fecha nem depende deles. O que falta em cada um está no README do plano; o Gate 1 continua exigindo os dois.
- **A sessão consegue rodar a suíte.** A receita está em `HANDOFF-PROMPT.md` (§Ambiente local); o baseline sobre a ponta do PR #12 foi executado ao escrever este plano: `pytest plane/tests/unit/orca -q -m unit` → 663 passed em 8m08s. Isso muda o ritmo do bloco: cada passo termina com o teste **executado**, não só escrito, e um item só é marcado `[x]` com a sua suíte verde localmente.

### Ordem, tamanho e o que prova cada passo

| Passo | Entrega                                                                                                                                                                  | Arquivos principais                                                                                                   | Prova                                                                | Commit                                                                    |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| 0     | Ambiente local de pé e baseline da suíte Orca verde                                                                                                                      | —                                                                                                                     | `pytest plane/tests/unit/orca -q -m unit`                            | —                                                                         |
| 1.4a  | O serviço D0.5 sabe falar pela API pública: `trigger`, `collaborators` e `automation_operation` atravessam `set_responsibility` → `allocate`/`transfer_unit` → `_record` | `app/services/orca/assignment_service.py`, `tests/unit/orca/test_assignment_service.py`                               | 3 testes novos + suíte D0 inalterada                                 | `feat(orca): [1.4] let the assignment service speak for the public API`   |
| 1.4b  | Serializers de entrada e envelope de resposta                                                                                                                            | `api/serializers/orca/{__init__,work_items,units}.py`                                                                 | testes de serializer (rejeições e envelope)                          | `feat(orca): [1.4] the request and response shapes of the automation API` |
| 1.4c  | As quatro rotas, os helpers de header e a fila como serviço                                                                                                              | `api/views/orca/{base,work_items,units}.py`, `app/services/orca/queue.py`, `api/urls/orca.py`, `api/urls/__init__.py` | `test_public_work_items.py`, `test_public_units.py`                  | `feat(orca): [1.4] work-items, by-external, units and queue over API key` |
| 1.5   | `reassign/` e `transfer/` com `If-Match`                                                                                                                                 | `api/views/orca/work_items.py`, `api/urls/orca.py`                                                                    | `test_public_reassign_transfer.py`                                   | `feat(orca): [1.5] reassign and transfer with If-Match`                   |
| 1.6   | Header `Idempotent-Replay` (já sai no 1.4c) e o item vira `[x]`; nota no RFC §4.2 sobre 412 × 409                                                                        | este arquivo, `docs/orca-work-management-rfc.md`                                                                      | `test_orca_error_codes.py` continua verde                            | `docs(orca): [1.6] close the error-code item`                             |
| 1.7   | `docs/orca-public-api.md`, `tools/orca-client/`, README do fork, RFC §2.1                                                                                                | idem                                                                                                                  | os `curl` do doc batem com os testes; o cliente é importado pelo 1.8 | `docs(orca): [1.7] the automation API guide and its reference client`     |
| 1.8   | Testes de contrato com `live_server` e o passo no CI                                                                                                                     | `tests/contract/test_orca_public_contract.py`, `.github/workflows/stage.yml`, `apps/api/tests/RUNNING_TESTS.md`       | contrato verde local; depois no CI da PR                             | `test(orca): [1.8] the public contract, end to end`                       |
| fim   | README do plano, este arquivo, `HANDOFF-PROMPT.md` atualizados; push; PR proposta descrita (não abrir sem pedido)                                                        | —                                                                                                                     | —                                                                    | `docs(orca): record the block`                                            |

Cada passo é um commit; um item pode ter mais de um. Rodar `ruff check` e
`ruff format --check` em `apps/api` antes de cada commit. O `check:sync` do
i18n só é necessário se algum código de erro **novo** aparecer — o plano
abaixo foi desenhado para não precisar de nenhum.

### Decisões fechadas para o bloco

Nenhuma reabre F1–F24. As que esclarecem o RFC vão para o §4.2 no commit do 1.6.

| #   | Decisão                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | Motivo                                                                                                                                                                                                           |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| B1  | **`ORG_DECISION_STALE` responde 412 na API pública e continua 409 na interna.** A view pública mapeia o status por código (`PUBLIC_STATUS_OVERRIDES = {"ORG_DECISION_STALE": 412}`) em vez de herdar `exc.http_status`.                                                                                                                                                                                                                                                                                | Achado do 1.6: mudar a classe mudaria a API interna já em uso pela UI.                                                                                                                                           |
| B2  | **O serviço ganha parâmetros, não uma cópia.** `set_responsibility`, `allocate`, `transfer_unit`, `reassign` e `return_to_queue` recebem `trigger=` e `automation_operation=`; `set_responsibility` recebe `collaborators=`; `_record` grava a FK. **Defaults preservam o comportamento atual**: nenhum teste D0 muda. `transfer_unit` passa a repassar o `trigger` ao `allocate`/`return_to_queue` internos, que hoje estão fixos em `INTERNAL_API`.                                                  | Achado 2 do PR #12. Duas implementações da regra de alocação é o que o D0.5 existe para impedir.                                                                                                                 |
| B3  | **Recibo primeiro, validação depois** (RFC §6.7 ao pé da letra): só o `Idempotency-Key` é conferido antes de `begin_operation`; tudo o mais — serializer, permissão de projeto, cobertura, política — acontece dentro da operação e, quando falha, fecha o recibo como `failed` com o código e o status originais. Um replay de um 400 devolve o mesmo 400.                                                                                                                                            | Um cliente que corrige o payload muda o hash e **precisa de chave nova**; o documento do 1.7 diz isso em destaque. É o preço de a semântica ser uma só.                                                          |
| B4  | **Chave vazia ou com mais de 255 caracteres = ausente** → `400 ORG_IDEMPOTENCY_KEY_REQUIRED`. Sem código novo.                                                                                                                                                                                                                                                                                                                                                                                         | 255 é a coluna. Um código "chave inválida" custaria três lugares e 19 locales por um caso que o doc evita.                                                                                                       |
| B5  | **O hash é do corpo JSON parseado**, sem headers nem parâmetros de rota, como o RFC descreve. A chave é única por workspace (constraint do 1.1).                                                                                                                                                                                                                                                                                                                                                       | Mesma chave em outro projeto do mesmo workspace é replay da primeira — e é o que a constraint já diz.                                                                                                            |
| B6  | **`Issue` criado pelo `IssueSerializer` nativo com `context["default_assignee_id"] = None`** e `assignees=[]`.                                                                                                                                                                                                                                                                                                                                                                                         | O serializer nativo cai para o `default_assignee_id` do projeto quando `assignees` vem vazio — é exatamente o fallback D2 que o §7.2 manda desligar neste caminho. Passar `None` desliga sem tocar o serializer. |
| B7  | `external_source`/`external_id` são gravados **também** nas colunas nativas do `Issue`, além do binding. O binding é a fonte da verdade (RFC §5.2); as colunas nativas mantêm o `GET /api/v1/.../work-items/` upstream coerente.                                                                                                                                                                                                                                                                       | O `POST` nativo responde 409 para o mesmo par — comportamento desejado, o par passa a ter um dono.                                                                                                               |
| B8  | **Serializer de entrada estrito**: chave desconhecida em qualquer bloco → 400 com o erro do DRF (não é código Orca). `assignees` em `work_item` → `ORG_ASSIGNEES_NOT_ALLOWED_HERE`; bloco `process` → `ORG_PROCESS_PROJECTION_DISABLED`.                                                                                                                                                                                                                                                               | Um cliente que envia `asignees` (typo) não pode acreditar que foi atendido.                                                                                                                                      |
| B9  | **`completion_due_at` é recusado até a Fase 4** (400 do DRF, mensagem dizendo a fase), em vez de aceito e ignorado.                                                                                                                                                                                                                                                                                                                                                                                    | Aceitar sem guardar mentiria; a `IssueServiceLevel` que o guarda é da Fase 4. Fica registrado no RFC §4.2.                                                                                                       |
| B10 | Permissões: `POST work-items/`, `reassign/`, `transfer/` → `ProjectEntityPermission` (membro ativo com role ≥ 15, o mesmo do `POST` nativo; Guest → 403). `GET by-external/` é rota de workspace: resolve o binding, e o usuário do token precisa ser membro ativo do projeto do item (qualquer papel) → senão 403; sem binding → `404 ORG_WORK_ITEM_NOT_FOUND`. `GET units/` → membro ativo do workspace. `GET queue/` → membro ativo da área **ou** Admin do workspace; coordenador entra na Fase 2. | RFC §7.1: a permissão é a do usuário do token. Nada é concedido pela API.                                                                                                                                        |
| B11 | **A fila é um serviço** (`app/services/orca/queue.py::queue_queryset(unit, *, routing_state=None, overdue=None, project_id=None)`), ordenação: atrasados primeiro, depois `queued_at` asc. Default: `routing_state in (queued, allocation_failed)`; `routing_state=assigned` ou `all` alarga.                                                                                                                                                                                                          | O 2.2 precisa da mesma fila com mais filtros; escrever a consulta uma vez.                                                                                                                                       |
| B12 | `work_item.url` = `f"{settings.WEB_URL}/{slug}/browse/{identifier}-{sequence_id}/"` — a rota real da web (`apps/web/app/routes/core.ts`, `:workspaceSlug/browse/:workItem`).                                                                                                                                                                                                                                                                                                                           | É o link que uma pessoa abre.                                                                                                                                                                                    |
| B13 | `transaction.on_commit`: `issue_activity.delay(type="issue.activity.created", ...)` **e** `model_activity.delay(...)`, copiados de `plane/api/views/issue.py` (`:499–520`), com `requested_data` = o bloco `work_item`. Só na criação; reassign/transfer registram decisão e evento Orca, e o `IssueAssignee` novo gera atividade pelo caminho nativo.                                                                                                                                                 | Webhooks e atividade nativos continuam a funcionar para o item criado por robô.                                                                                                                                  |
| B14 | **Chave determinística do cliente de referência** = `"orca-" + sha256(f"{source}\|{id}\|{operation}\|{event_id}").hexdigest()` (69 chars, cabe na coluna, sem caracteres de escape).                                                                                                                                                                                                                                                                                                                   | Reprodutível de qualquer linguagem; o 1.8 conta com isso.                                                                                                                                                        |
| B15 | **Testes de contrato ficam em `plane/tests/contract/test_orca_public_contract.py`**, marcados `contract`, com `plane_server` (o `live_server` renomeado do conftest raiz) e `transaction=True`. No CI entram como um **segundo passo do job `api_tests`** (`pytest plane/tests/contract/test_orca_public_contract.py -q`), não no job manual: não precisam de MinIO nem RabbitMQ.                                                                                                                      | O item 1.8 manda adicionar ao job; o job manual não é merge gate.                                                                                                                                                |

### 1.4 — passos

**1.4a — o serviço.** Em `assignment_service.py`:

- `_record(..., automation_operation=None)` grava `AssignmentDecision.automation_operation`.
- `allocate(..., automation_operation=None)` repassa a `_record` em todos os ramos (assigned, queued, allocation_failed).
- `set_responsibility(..., trigger=DecisionTrigger.INTERNAL_API, collaborators=(), automation_operation=None)` repassa a `allocate` e, no ramo de troca de área, a `transfer_unit`.
- `transfer_unit(..., trigger=DecisionTrigger.INTERNAL_API, automation_operation=None)` repassa ao `allocate` e ao `return_to_queue` que chama.
- `reassign(..., trigger=DecisionTrigger.REASSIGN, automation_operation=None)` e `return_to_queue(..., expected_decision_id=None, automation_operation=None)` — o `expected_decision_id` é a mesma checagem otimista que `reassign` já faz, sob o mesmo lock de linha; o 1.5 precisa dela para `{"return_to_queue": true}`.
- Docstrings: `@param` novo em cada função; comentário explicando por que o default é o valor antigo.

Testes em `test_assignment_service.py`: (1) `set_responsibility(trigger=PUBLIC_API)` grava `trigger="public_api"` na decisão e `source` no evento; (2) `automation_operation` aparece na decisão em alocação, em transferência e em reatribuição; (3) `collaborators` via `set_responsibility` viram `IssueAssignee` sem virar executor, e um colaborador não elegível é recusado antes de qualquer escrita. Rodar a suíte D0 inteira depois: nada pode mudar.

**1.4b — formas.** `api/serializers/orca/work_items.py`:

- `ExternalReferenceSerializer` (`source`, `id`, ambos 1–255).
- `WorkItemBodySerializer`: `name` (obrigatório), `description_html`, `state`, `priority`, `labels`, `start_date`, `target_date`, `parent`, `estimate_point`. Qualquer outra chave → 400; `assignees` → `ORG_ASSIGNEES_NOT_ALLOWED_HERE` (checado antes do resto, para o código sair mesmo com outros erros).
- `AssignmentSerializer`: `mode` ∈ `RequestedAssignmentMode`; `primary_executor` obrigatório se `explicit`, proibido caso contrário; `collaborators` só com `explicit`.
- `ResponsibilitySerializer`: `unit` (slug), `assignment`, `assignment_due_at`; `completion_due_at` → 400 (B9).
- `WorkItemAutomationSerializer`: os três blocos; `process` → `ORG_PROCESS_PROJECTION_DISABLED`.
- `work_item_envelope(issue, link, decision, *, binding, binding_created, operation, replay)` → o JSON do RFC §7.2, usado por criação, `by-external`, `reassign` e `transfer`.

`api/serializers/orca/units.py`: `PublicUnitSerializer` (`id`, `slug`, `name`, `projects[]` com `project_id`, `identifier`, `default_role`, `policy{default_mode, allowed_modes}` via `resolve_policy`) e `PublicQueueRowSerializer` (`issue_id`, `sequence_id`, `name`, `routing_state`, `queue_reason`, `queued_at`, `assignment_due_at`, `primary_executor{id, email}`).

**1.4c — rotas.** `api/views/orca/base.py` ganha três helpers, todos com teste: `read_idempotency_key(request)` (B4), `public_error(exc_or_name, status=None)` (B1, mesmo envelope de `orca_error`), `replay(handle)` → `Response(body, status)` com `Idempotent-Replay: true` e `operation.replay = true` no corpo.

`api/views/orca/work_items.py::WorkItemAutomationEndpoint.post`, na ordem fixa do §7.2:

1. `read_idempotency_key`; workspace pelo slug (404 se não existe); `begin_operation(workspace, self.api_token, key, CREATE_WORK_ITEM, request.data)`; se `handle.replayed` → `replay(handle)`.
2. Dentro do `with`: permissão de projeto (`ProjectEntityPermission` já corre no `initial`; aqui só resolve o `Project`), serializer; falha → `handle.fail(error_code=..., response=..., http_status=400)` e o mesmo `Response`.
3. `transaction.atomic()`: (a) `ExternalWorkItemBinding.objects.select_for_update()...get_or_create` num `atomic()` aninhado para a corrida de dois primeiros — perdedor relê; binding para item de **outro projeto** → `ExternalBindingConflict(issue_id=...)`; do mesmo projeto → reutiliza a `Issue`; (b) novo: `IssueSerializer(data=work_item | {"assignees": [], "external_source": ..., "external_id": ...}, context={project_id, workspace_id, default_assignee_id: None})`, `created_by` = usuário do token como a API nativa faz; (c) `set_responsibility(issue, unit, actor=request.user, source=PUBLIC_API, trigger=PUBLIC_API, requested_mode=..., explicit_executor=..., collaborators=..., assignment_due_at=..., automation_operation=handle.operation)`; (d) montar envelope; `transaction.on_commit` com as duas tarefas (B13).
4. Fora do `atomic()`: `handle.complete(issue=issue, response=body, http_status=201)`; `Response(body, 201)`.
5. `except OrcaDomainError as exc`: `handle.fail(error_code=exc.error_code, response=<envelope de erro>, http_status=<B1>)`; responder o mesmo. A transação já reverteu: nem `Issue` nem binding ficam.

`WorkItemByExternalEndpoint.get` (rota de workspace): binding → 404; membro do projeto → envelope com estado atual (`decision` = `link.current_assignment_decision`, `operation` = `null`, `binding.created = false`).

`api/views/orca/units.py::UnitListEndpoint.get` e `UnitQueueEndpoint.get`, ambos `self.paginate(...)`; a fila via `queue_queryset` (B11), com `unit_slug` na rota e 404 `ORG_UNIT_NOT_FOUND` para slug inexistente ou área inativa.

`api/urls/orca.py`:

```text
workspaces/<str:slug>/units/                                              GET
workspaces/<str:slug>/units/<str:unit_slug>/queue/                        GET
workspaces/<str:slug>/work-items/by-external/<str:source>/<str:external_id>/  GET
workspaces/<str:slug>/projects/<uuid:project_id>/work-items/              POST
workspaces/<str:slug>/projects/<uuid:project_id>/work-items/<uuid:issue_id>/reassign/   POST  (1.5)
workspaces/<str:slug>/projects/<uuid:project_id>/work-items/<uuid:issue_id>/transfer/   POST  (1.5)
```

Tudo sob `orca/` no `api/urls/__init__.py`, estilo `path()` com barra final e `http_method_names` fixados, como `urls/work_item.py`. `source` e `external_id` como `str` — o cliente URL-encoda `:` e `/`.

**1.4d — testes.** `tests/unit/orca/conftest.py` ganha os construtores de URL `/api/v1/orca/...` e uma fixture `public_client(user)` (API key daquele usuário, `X-Api-Key`, as duas flags ligadas via `settings`). `test_public_work_items.py`: caminho feliz nos quatro modos (`default`, `manual`, `self_claim`, `least_loaded`) e `explicit` com colaborador; replay idêntico (201 → 200, `Idempotent-Replay: true`, mesma contagem de `Issue`/`AssignmentDecision`/`IssueAssignee`/`AutomationOperation`); mismatch 409; binding duplicado em outro projeto 409; `explicit` com executor inelegível 400 **e** o recibo `failed`; Guest 403; `assignees` no bloco 400; `process` 400; `completion_due_at` 400; política proibida → sem `Issue`, sem binding, recibo `failed` com `ORG_ASSIGNMENT_MODE_NOT_ALLOWED`; `on_commit` (`django_capture_on_commit_callbacks(execute=True)`) dispara as duas tarefas na criação e **nenhuma** no rollback (mockar `issue_activity.delay` e `model_activity.delay`); throttle 429 de ponta a ponta com `DEFAULT_THROTTLE_RATES["orca_public"] = "2/minute"` (o que o 1.2 deixou para aqui); flag desligada → 404 codificado numa rota real. `test_public_units.py`: lista paginada; fila default/`assigned`/`all`/`overdue`/`project`; membro da área vê, Member de fora não, Admin vê; área inativa 404.

Cuidado conhecido: a fixture autouse `run_celery_inline` executa as tarefas Celery no lugar — mockar `.delay` nos testes em que a atividade não é o assunto.

### 1.5 — passos

- `WorkItemReassignEndpoint.post`: `Idempotency-Key` (B4); `If-Match` ausente → `428 ORG_IF_MATCH_REQUIRED` **antes** de abrir o recibo (um 428 é falta de header, como o 400 da chave); `begin_operation(..., REASSIGN, request.data)`; corpo: exatamente um de `primary_executor` | `return_to_queue: true`, `reason` opcional (serializer próprio); `reassign(issue, executor, actor=request.user, reason=..., expected_decision_id=<If-Match>, trigger=PUBLIC_API, automation_operation=handle.operation)` ou `return_to_queue(issue, actor=..., reason=..., expected_decision_id=<If-Match>, trigger=PUBLIC_API, automation_operation=...)` — `return_to_queue` ganha `expected_decision_id` no 1.4a (B2) para que a comparação aconteça sob o mesmo `select_for_update` que o `reassign` já usa, e não na view; `DecisionStale` → 412 (B1). Resposta: envelope com estado atual, 200.
- `WorkItemTransferEndpoint.post`: `Idempotency-Key`; corpo `{unit: slug, reason}`; `transfer_unit(issue, to_unit, actor=request.user, source=PUBLIC_API, trigger=PUBLIC_API, reason=..., automation_operation=handle.operation)`; `UnitNotCoveringProject` → 400; mesma área → 400 `ORG_INVALID_ROUTING_TRANSITION` (o serviço já levanta).
- Item que existe mas não tem `IssueOrganizationalUnit` → `400 ORG_WORK_ITEM_HAS_NO_UNIT` (é estado do item, não ausência de rota); item inexistente no projeto → `404 ORG_WORK_ITEM_NOT_FOUND`.
- Testes (`test_public_reassign_transfer.py`): stale 412 com `current_decision_id` no corpo; sem `If-Match` 428; replay não gera segunda decisão; `return_to_queue` mantém o `IssueAssignee`; transfer para área que não cobre 400; transfer cujo executor não é membro da nova área devolve à fila; Guest 403; a decisão gravada tem `trigger=public_api` e a FK do recibo.

### 1.7 — passos

- `docs/orca-public-api.md` (inglês, como as demais docs técnicas): autenticação (`X-Api-Key`), as duas flags e o 404 codificado, headers (`Idempotency-Key`, `If-Match`, `Idempotent-Replay`), **"uma chave por payload: um 400 gasta a chave"** (B3), cada rota com `curl` cujo corpo é o mesmo dos testes, tabela dos códigos 4922–4931 mais os do serviço que a API expõe (4916–4921) com status **da API pública** (412 para stale), semântica de replay em linguagem de cliente, exemplos dos quatro modos e do `explicit`, e a seção "o que não existe ainda" (`process`, `completion_due_at`, `complete/`).
- `tools/orca-client/orca_client.py` (`requests`): `OrcaClient(base_url, api_key, workspace_slug)` com `create_work_item`, `get_by_external`, `reassign`, `transfer`, `list_units`, `list_queue`, `idempotency_key(source, id, operation, event_id)` (B14); erros como `OrcaApiError(status, error_code, error_message, body)`; `Idempotent-Replay` exposto no resultado. Sem dependência além de `requests`. `tools/orca-client/README.md` curto no estilo de `tools/migration/README.md`.
- `README.md` do fork: linha nova na tabela de features ("Automation API" sob Automations) e a variável `ORCA_PUBLIC_API_ENABLED` na tabela de variáveis, default `0`.
- RFC §2.1: "Rotas Orca por API key" passa a "Sim — `/api/v1/orca/`, desligada por `ORCA_PUBLIC_API_ENABLED` até o Gate 2-mínimo"; "Política automática na criação" passa a "Sim"; "Rate limit dedicado" passa a "SCIM e API pública".

### 1.8 — passos

- `plane/tests/contract/test_orca_public_contract.py` importa o cliente por caminho (`sys.path` para `tools/orca-client` no topo do arquivo, com comentário) e fala com `plane_server.url`; `settings.ORCA_PUBLIC_API_ENABLED = True` (a thread do servidor lê o mesmo `settings`).
- Cenários do item: 50 criações determinísticas × 2 → contagens iguais e todas as segundas com `Idempotent-Replay: true`; 2 threads, mesma chave → 1 `Issue` (a corrida é resolvida pela constraint do 1.1, testar de verdade com `ThreadPoolExecutor` e `requests`); reatribuição pelo serviço interno e replay da criação → executor não muda; `/api/orca/...` com API key → 401/403; `/api/v1/orca/...` com sessão → 401.
- `stage.yml`, job `api_tests`: passo novo "Run Orca Contract Tests" com `pytest plane/tests/contract/test_orca_public_contract.py -q`, comentário dizendo por que este arquivo de contrato entra no merge gate e os demais não (B15). `RUNNING_TESTS.md` ganha a linha correspondente.
- Medição do Gate 1 (200 criações `least_loaded`, p50/p95) **não** é deste bloco: é staging.

### Riscos e onde já se sabe a resposta

- **`IssueSerializer` e o assignee padrão** — B6. Sem o `None`, o item nasce com o `default_assignee` do projeto e o D2 volta pela porta dos fundos.
- **`on_commit` em teste** — `pytest.mark.django_db` envolve o teste numa transação, e callbacks de commit nunca disparam; usar `django_capture_on_commit_callbacks(execute=True)` nos testes de atividade e `transaction=True` nos de concorrência.
- **Recibo × transação** — `begin_operation` e `complete`/`fail` abrem transações próprias (1.3). O `atomic()` da view fica **dentro** do `with`, e `complete` é chamado **depois** de o `atomic()` fechar; senão o recibo `succeeded` pode sobreviver a um rollback do item.
- **Corrida no binding** — `get_or_create` do `ExternalWorkItemBinding` em `atomic()` aninhado (savepoint), como o 1.3 faz com o recibo; o perdedor relê e cai no caminho "já existe".
- **`--nomigrations --reuse-db`** — a base de teste nasce dos modelos, então a `0138` está lá; mas um `--reuse-db` de uma sessão anterior à `0138` não tem a tabela: `--create-db` uma vez.
- **PostgreSQL 16 local × 15.7 no CI** — nada aqui depende de versão; se divergirem, é o primeiro lugar a olhar.
- **Throttle no teste** — a fixture autouse `clear_throttle_history` já limpa o cache; sobrescrever `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` inteiro via `settings`, não só a chave (o DRF lê o dicionário no `__init__` do throttle).

### Pronto quando — e o que aconteceu

- [x] 1.4, 1.5, 1.6, 1.7, 1.8 `[x]` neste arquivo, com "Entregue" e "Aceite" preenchidos.
- [x] `pytest plane/tests/unit/orca plane/tests/contract/test_orca_public_contract.py` → **1017 passed, 0 failed** (34m31s); `ruff check`/`ruff format --check` limpos; `test_orca_error_codes.py` verde **sem código novo**, então o `check:sync` do i18n não foi necessário.
- [x] README do plano com a Fase 1 em 8/8, o que falta para o Gate 1, e o histórico do bloco.
- [x] Push na branch; PR proposta descrita, não aberta.

**Quinze decisões, catorze cumpridas como escritas.** A que mudou foi a B10, e
para melhor: o plano dizia que a autorização de projeto correria dentro da
operação, e `permission_classes` do DRF corre no `initial()`, antes do `post()`.
O efeito é que uma chamada não autorizada responde 403 **sem abrir recibo** e
portanto não gasta a chave de idempotência de quem a enviou — melhor do que o
planejado, e registrado no RFC §4.2 para não ser lido como descuido.

**O que a execução acrescentou ao plano:** o guard da publicação pós-commit
(nenhuma das quinze decisões previa que um broker fora do ar pudesse envenenar
uma chave), o `handle_exception` na base pública, e `expected_decision_id` em
`return_to_queue` — os três nasceram de rodar o código, não de lê-lo.

---

## 1.1 — Migração 0138: binding externo e operação de automação `[x]`

**Modelos** em `apps/api/plane/db/models/organizational_automation.py`
(exportar em `__init__.py`): `ExternalWorkItemBinding` e
`AutomationOperation` exatamente como RFC §5.2, incluindo:

- binding: unicidade parcial `(workspace, external_source, external_id)` e `(issue)`;
- operação: unicidade **não condicional** `(workspace, idempotency_key)`; `status` choices; `request_hash` `CharField(64)`; `api_token` FK `db.APIToken` null.

**Testes** (`test_automation_models.py`): unicidades; `request_hash` de 64
chars; `status` inválido rejeitado.

**Entregue.** `organizational_automation.py` com os dois modelos e os enums
(`AutomationOperationType`, `AutomationOperationStatus`), exportados no
`__init__.py`; migração `0138_orca_automation_binding` escrita à mão no padrão
da `0137`.

**Um campo a mais do que o item pedia.** A `0138` também adiciona
`AssignmentDecision.automation_operation`. Não é escopo novo: é o campo que o
**D0.4** registrou como divergência consciente do RFC §5.2 e adiou justamente
para cá, porque uma FK não pode apontar para uma tabela que ainda não existe.
Deixá-lo de fora agora significaria que nenhuma decisão tomada pela API
pública saberia dizer qual chamada a causou — e a `0139` teria de mexer numa
tabela que a `0138` já estava tocando.

**Decisão de modelagem que vale registrar.** A unicidade de
`AutomationOperation` é a **única** regra Orca sem condição de `deleted_at`, e
isso é deliberado: se apagar o recibo liberasse a chave, um replay que
chegasse depois de uma limpeza executaria a operação de novo — exatamente o
que a tabela existe para impedir. O binding, ao contrário, é condicional nas
duas constraints, porque uma limpeza de workspace pode legitimamente aposentar
um vínculo e liberar a chave externa. Ambos os comportamentos têm teste.

**Aceite.**

- [x] `makemigrations --check --dry-run` → **"No changes detected"**. A migração foi escrita à mão, mas desta vez foi possível prová-la: a sessão subiu um PostgreSQL 16 local (o `initdb` recusa root, então sob um usuário sem privilégio) e instalou as dependências, o que também permitiu aplicar as 182 migrações num banco limpo, **reverter** a `0138` e **reaplicá-la** — as três com exit 0.
- [x] Migração declara `("db", "0137_orca_assignment_decision")` como dependência e o `swappable_dependency` do usuário.
- [x] Testes das duas unicidades, do `request_hash` de 64 chars, do `status` e do `operation_type` inválidos, do soft-delete que **não** libera a chave, e da FK nova nos dois sentidos. **19 testes, executados e verdes.**

**Comportamento medido, não suposto.** Revogar um `APIToken` (`delete()`, que
é soft) faz o cascade do Plane **anular** `AutomationOperation.api_token` e
**não** apagar o recibo: a credencial é retirada, o registro do que ela fez
permanece. Duas versões erradas deste teste passaram por aqui antes da
medição — uma supunha que `delete()` apagava a linha, outra que o cascade
soft-deletava o recibo. Só a execução resolveu. **19 testes, executados e verdes.**

**Comportamento medido, não suposto.** Revogar um `APIToken` (`delete()`, que
é soft) faz o cascade do Plane **anular** `AutomationOperation.api_token` e
**não** apagar o recibo: a credencial é retirada, o registro do que ela fez
permanece. Duas versões erradas deste teste passaram por aqui antes da medição
— uma supunha que `delete()` apagava a linha, outra que o cascade soft-deletava
o recibo. Só o teste executado resolveu a questão.

---

## 1.2 — Flag, mixin e throttle da API pública `[x]`

- `apps/api/plane/settings/common.py`: `ORCA_PUBLIC_API_ENABLED = os.environ.get("ORCA_PUBLIC_API_ENABLED", "0") == "1"` e `ORCA_PUBLIC_API_RATE_LIMIT = os.environ.get(..., "300/minute")`; entrada `"orca_public"` em `DEFAULT_THROTTLE_RATES`; comentários no padrão dos existentes.
- `.env.example` e `apps/api/.env.example`: as duas variáveis com comentário.
- `apps/api/plane/api/views/orca/base.py`: `OrcaPublicApiFeatureMixin` (404 quando `ORCA_ORG_UNITS_ENABLED` ou `ORCA_PUBLIC_API_ENABLED` desligados) e `OrcaPublicBaseAPIView(OrcaPublicApiFeatureMixin, BaseAPIView)` reutilizando `APIKeyAuthentication` da API pública.
- `apps/api/plane/throttles/orca_public.py`: `OrcaPublicThrottle(SimpleRateThrottle)`, `scope="orca_public"`, chave por `api_token.id` (ler o padrão em `throttles/scim.py`).
- `OrcaConfigEndpoint` (interno) passa a expor `public_api_enabled` para a UI mostrar/ocultar instruções.

**Testes:** flag desligada → 404 em uma rota qualquer da fase; throttle
estoura em 429 com `DEFAULT_THROTTLE_RATES` sobrescrito para `2/minute` no
teste (fixture `clear_throttle_history` já existe no conftest).

**Divergência deliberada do enunciado: o parser da flag.** O item manda
escrever `os.environ.get("ORCA_PUBLIC_API_ENABLED", "0") == "1"`. Isso é
exatamente o defeito que o **P0.14** fechou: com o `== "1"`,
`ORCA_PUBLIC_API_ENABLED=true` lê como **desligado**. Num kill switch de API
pública o erro cai para o lado seguro, mas o inverso não é verdade para quem
tenta _ligar_ e não consegue entender por quê. A flag usa `env_flag`, o mesmo
parser estrito do `ORCA_ORG_UNITS_ENABLED`, que recusa grafia desconhecida no
boot. Nenhuma outra parte do item mudou.

**Onde a flag mora.** Em `app/services/orca/feature_flags.py`, ao lado de
`organizational_units_enabled` e pelo mesmo motivo declarado lá: um comando ou
uma tarefa Celery precisa poder perguntar sem importar a camada de API.

**Chave do throttle.** Lida do _view_ (`view.api_token`), como o
`SCIMRateThrottle` lê a sua conexão, e nunca de `request.auth`: nesta classe
de autenticação `request.auth` é o **segredo em texto puro**, e chave de cache
aparece em monitoramento do Redis, slow log e dump de crash. O
`OrcaPublicBaseAPIView` resolve o `APIToken` uma vez por request e o expõe —
o throttle precisa de um id estável e a `AutomationOperation` precisa da
linha.

**Testes** (`test_public_api_gate.py`): as quatro combinações das duas flags,
o default desligado, a leitura em tempo de chamada, o 404 com corpo codificado,
e o throttle (chave por id, ausência de token = sem balde, escopo registrado
em `DEFAULT_THROTTLE_RATES`). O 429 de ponta a ponta fica para o 1.4, que é
quando existe rota para estourar.

---

## 1.3 — Serviço de operação idempotente `[x]`

**Arquivo.** `apps/api/plane/app/services/orca/automation_operation.py`.

```python
canonical_hash(payload: dict) -> str          # sha256(json.dumps(payload, sort_keys=True, separators=(",",":"), ensure_ascii=False))
begin_operation(workspace, api_token, key, operation_type, payload) -> OperationHandle
    # get_or_create; RFC §6.7 com todos os ramos (mismatch → IdempotencyPayloadMismatch,
    # succeeded/failed → ReplayResult com snapshot, in_progress recente → OperationInProgress,
    # in_progress abandonada (>60 s) → retoma).
complete_operation(handle, *, issue, response: dict, status="succeeded")
fail_operation(handle, *, error_code, response: dict)
```

Usado como context manager que garante `fail_operation` em exceção não
tratada (com `error_code="ORG_INTERNAL_ERROR"`) **fora** da transação
principal, para que a falha fique registrada mesmo após rollback.

**Entregue**, com dois nomes diferentes do enunciado e um motivo para cada:
`start_operation` é a função (o enunciado a chamava `begin_operation`) e
`begin_operation` é o **context manager**, porque é ele que o item descreve
logo abaixo e é ele que as views usam. `complete_operation` e `fail_operation`
existem como o item pede, e o handle expõe `.complete()` / `.fail()`.

**Status HTTP no snapshot.** O replay precisa reproduzir o status original —
uma falha replayed não pode virar 200 com corpo de erro. Guardado dentro do
`response_snapshot` sob `_http_status` (uma escrita JSON em vez de uma coluna
nova) e **removido na leitura**, para nunca vazar como campo do corpo.

**Testes** (`test_automation_operation.py`): cada ramo do RFC §6.7;
abandonada retomada; hash estável a reordenação de chaves; hash muda com
valor diferente. Mais: ordem de lista **importa** (ao contrário da ordem de
chaves), não-ASCII hasheia como si mesmo, a retomada reinicia o relógio dos 60
s (senão uma operação retomada no segundo 59 seria retomada em laço), o
replay responde o original e não o presente, o `error_code` fica em coluna
própria, e a corrida de duas primeiras chamadas simultâneas abre **um** recibo
— resolvida pela constraint, não por lock, com o perdedor relendo a linha
vencedora.

---

## 1.4 — Endpoints: `work-items/` composto, `by-external/`, `units/`, `queue/` `[x]`

**Arquivos.** `apps/api/plane/api/views/orca/{units,work_items}.py`,
`apps/api/plane/api/serializers/orca/{units,work_items}.py`,
`apps/api/plane/api/urls/orca.py` (incluído em `api/urls/__init__.py`).

**`POST /api/v1/orca/workspaces/{slug}/projects/{project_id}/work-items/`**
Implementar a ordem fixa do RFC §7.2:

1. `begin_operation` (fora da transação principal);
2. `transaction.atomic()`:
   a. `ExternalWorkItemBinding.get_or_create` → se existe e aponta para item do mesmo projeto, reutiliza `Issue`; se aponta para item de outro projeto, `ExternalBindingConflict`;
   b. se novo: criar `Issue` pelo serializer nativo da API v1 (`plane.api.serializers.IssueSerializer`) com `assignees=[]` explícito, `external_source`/`external_id` preenchidos;
   c. `set_responsibility(...)` do D0.5 com `requested_mode`, `explicit_executor`, `collaborators`, `assignment_due_at`, `trigger="public_api"`, `operation=handle.operation`;
   d. montar resposta (RFC §7.2) e `complete_operation`;
3. `transaction.on_commit`: `issue_activity.delay(...)` como a API nativa faz na criação (copiar a chamada de `plane/api/views/issue.py`), para webhooks e atividade nativos.

Validação de entrada com serializer dedicado (`WorkItemAutomationSerializer`)
que rejeita `assignment.mode=explicit` sem `primary_executor`, e
`assignees` no bloco `work_item` (→ 400 `ORG_ASSIGNEES_NOT_ALLOWED_HERE`,
código novo). Bloco `process` → 400 `ORG_PROCESS_PROJECTION_DISABLED` até a
Fase 4.

Autorização: `APIToken.user` precisa de `ProjectMember` ativo com role ≥ 15
no projeto (reutilizar `ProjectMemberPermission`/`allow_permission` da API
pública, conforme padrão de `plane/api/views/issue.py`).

**`GET .../work-items/by-external/{source}/{id}/`** — mesmo envelope, estado
atual.
**`GET .../units/`** e **`GET .../units/{unit_slug}/queue/`** — RFC §7.2;
paginação com `BasePaginator` da API pública; a fila só para tokens de
usuários membros da área, coordenadores (Fase 2) ou Admin.

**Testes** (`test_public_work_items.py`): caminho feliz para cada modo;
replay idêntico; mismatch 409; binding duplicado 409; `explicit` com
executor não elegível 400; permissão por token de Guest 403; `assignees` no
bloco → 400; transação (política proibida não deixa `Issue` nem binding);
`on_commit` não dispara em rollback (mockar `issue_activity.delay`).

**Entregue em quatro passos, na ordem do bloco.**

**1.4a — o serviço, não uma cópia dele.** `assignment_service.py` ganhou
`trigger`, `collaborators`, `automation_operation` e `expected_decision_id`,
cada um com o default que a função já escrevia — nenhum dos 54 testes do D0.5
mudou de comportamento, e há teste explícito afirmando que um chamador que não
passa `trigger` continua gravando `internal_api`. `transfer_unit` deixou de
fixar `INTERNAL_API` no `allocate` e no `return_to_queue` que dispara, e passou
a anexar os colaboradores dentro da sua própria transação: dos três ramos da
transferência, dois nunca chegam ao `allocate`, então passá-los adiante teria
perdido silenciosamente os colaboradores de um item cujo executor permanece.

`return_to_queue` ganhou `expected_decision_id` — a mesma checagem otimista do
`reassign`, sob o mesmo lock de linha. O 1.5 precisa dela porque
`{"return_to_queue": true}` chega pela mesma rota que uma reatribuição, e as
duas metades têm de ser igualmente seguras de repetir. Fazer a comparação na
view, com uma leitura própria, seria uma corrida.

**1.4b — as formas.** `api/serializers/orca/` com um `StrictSerializer` como
base: **chave desconhecida é erro**, com as aceitas listadas na resposta. O
default do DRF é ignorar o que não conhece, o que está certo para um formulário
e errado para um contrato de máquina — um cliente que manda `asignees` recebe
201 e acredita que atribuiu alguém, e a falha aparece semanas depois como "o
SLA nunca é preenchido". `work_item_envelope()` monta o envelope do §7.2 uma
vez e as quatro rotas o reusam, de forma que um replay é idêntico byte a byte
ao que replica.

**1.4c — as rotas.** `api/views/orca/{work_items,units}.py`, `api/urls/orca.py`
incluído no agregador da v1. A ordem fixa do §7.2 está no `_create`, e o
`run_operation` da base é o que garante o invariante que interessa: **toda
recusa fecha o recibo com o código e o status que o chamador vê**, para que um
retry de um pedido que não pode dar certo seja respondido em vez de tentado de
novo.

**1.4d — os testes.** `test_public_work_items.py` (38) e `test_public_units.py`
(16), com construtores de URL e uma fixture `token_client` novos no
`conftest.py` — autenticando por `X-Api-Key` de verdade, não por
`force_authenticate`, porque o throttle e o recibo dois se apoiam no token e
uma sessão forçada não tem nenhum.

**Três coisas que a execução mudou, e uma que ela confirmou.**

1. **Defeito de código, encontrado pelos testes: dois caminhos documentados
   respondiam 500.** `WorkItemNotFound` e `IfMatchRequired` são levantados
   **antes** de o recibo existir — resolver o item, ler o `If-Match` — e a
   primeira versão só capturava `OrcaDomainError` dentro do bloco idempotente.
   As duas escapavam para o handler genérico do `BaseAPIView`. Corrigido com um
   `handle_exception` no `OrcaPublicBaseAPIView`, que converte qualquer recusa
   da camada em qualquer ponto do request — e que uma rota futura herda em vez
   de reabrir o buraco.
2. **A autorização de projeto roda antes do recibo**, porque `permission_classes`
   do DRF corre no `initial()`. O plano supunha o contrário. O resultado é
   melhor do que o planejado: uma chamada não autorizada responde 403 sem abrir
   operação, e portanto **não gasta a chave de idempotência de quem a enviou**.
   Registrado no RFC §4.2.
3. **A constraint I3 recusou um teste, com razão.** A fixture da fila criava uma
   linha `assigned` sem executor; o CHECK do banco a rejeitou. O teste estava
   errado, a constraint estava certa, e a fixture passou a respeitá-la nos dois
   sentidos.
4. **O default do assignee (defeito D2) está desligado neste caminho**, e há
   teste que o prova: com `default_assignee` configurado no projeto, o item
   criado pela API não recebe assignee nenhum. `default_assignee_id=None` no
   contexto do serializer nativo é o que desliga, sem tocar no serializer que
   todo o resto usa.

**Aceite.**

- [x] `pytest plane/tests/unit/orca/test_public_work_items.py` → **38 passed**; `test_public_units.py` → **16 passed**. Executados nesta sessão.
- [x] Sem migração nova: a `0138` do 1.1 já tinha as duas tabelas e a FK.
- [x] `ruff check` e `ruff format --check` limpos em tudo que foi tocado.
- [x] `python manage.py check` limpo — as seis rotas carregam.

---

## 1.5 — `reassign/` e `transfer/` públicos `[x]`

- `POST .../work-items/{issue_id}/reassign/` com header `If-Match: <decision_id>`; corpo `{"primary_executor": ...}` ou `{"return_to_queue": true}`, `reason`. Sem `If-Match` → 428 `ORG_IF_MATCH_REQUIRED` (código novo); divergente → 412 `ORG_DECISION_STALE`. Também exige `Idempotency-Key`.
- `POST .../work-items/{issue_id}/transfer/` corpo `{"unit": slug, "reason"}`; `Idempotency-Key`.
- Ambos delegam ao serviço D0.5 com `trigger="public_api"`.

**Testes:** stale 412; sem If-Match 428; replay idêntico não gera segunda
decisão; transfer para área que não cobre 400.

**Entregue** nas mesmas views, com o mapeamento de status do achado do 1.6:
`PUBLIC_STATUS_OVERRIDES = {"ORG_DECISION_STALE": 412}` na base pública. A
exceção continua carregando 409 e a rota interna continua respondendo 409 à
UI; quem traduz é a borda que fala HTTP com máquinas.

**Por que `transfer` não pede `If-Match` e `reassign` pede.** Uma reatribuição
é uma edição disputada de **uma** decisão: dois coordenadores agindo ao mesmo
tempo, e o segundo não pode sobrescrever o primeiro em silêncio. Uma
transferência é a afirmação de que o trabalho pertence a outro lugar; os dois
lados de uma corrida deixam o item numa área só, com um histórico só.

**O que o recibo salva aqui, e que não é óbvio.** Sem ele, um retry de uma
reatribuição encontraria o próprio `If-Match` vencido — a primeira chamada
moveu a decisão — e responderia **412 a um chamador que teve sucesso**. O
mesmo vale para `transfer`: o retry bateria em "já pertence a essa área" e
responderia 400. Os dois casos têm teste.

**Aceite.**

- [x] `pytest plane/tests/unit/orca/test_public_reassign_transfer.py` → **22 passed**. Executado nesta sessão.
- [x] 412 com `current_decision_id` no corpo, 428 sem `If-Match` (e **sem** abrir operação — o teste afirma que nenhuma `AutomationOperation` é criada), replay sem segunda decisão, transfer para área que não cobre 400, executor fora da nova área volta à fila mantendo o `IssueAssignee`.

---

## 1.6 — Códigos de erro e respostas `[x]`

Registrar nos três lugares (RFC §7.3 + `ORG_ASSIGNEES_NOT_ALLOWED_HERE`,
`ORG_IF_MATCH_REQUIRED`, `ORG_INTERNAL_ERROR`). Header `Idempotent-Replay:
true` nas respostas de replay. `test_orca_error_codes.py` verde.

**Entregue: os dez códigos, nos três lugares.** 4922–4931 em
`orca_error_codes.py`, em `packages/constants/src/orca/error-codes.ts` e no
catálogo i18n das **19 locales** (via skill `translate`; `sync:check` em 100%).
As checagens de paridade do `test_orca_error_codes.py` foram reproduzidas fora
do pytest e passam: numeração única, na faixa 4900–4999, sem colisão com
upstream, tabela Python ≡ tabela TS, e toda chave TS existente no catálogo.

**Falta o header `Idempotent-Replay: true`**, que só existe quando existe
resposta HTTP — vai com o 1.4.

**Achado que o 1.5 resolveu.** O RFC §7.3 e o item 1.5 especificam **412**
para `ORG_DECISION_STALE`, mas a exceção `DecisionStale` entregue no D0.5
carrega **409**, e a rota interna já responde 409 para a UI. Mudar a classe
mudaria a API interna; a rota pública mapeia o status explicitamente
(`PUBLIC_STATUS_OVERRIDES`), em vez de herdar o `http_status` da exceção.

**Fechado.** O header `Idempotent-Replay: true` sai em toda resposta de replay,
por `replay_response()` na base pública, que também vira `operation.replay`
para `true` no corpo — os dois, porque um cliente que lê só o corpo e um que lê
só o header têm ambos de saber. Seis códigos ganharam exceção de domínio
própria (`AssigneesNotAllowedHere`, `ProcessProjectionDisabled`,
`IfMatchRequired`, `UnitNotInWorkspace`, `WorkItemNotFound`,
`WorkItemHasNoUnit`), de modo que serializer, serviço e view recusam pelo mesmo
caminho e o recibo grava o mesmo código que o chamador lê.

**Nenhum código novo foi preciso** — os dez de 4922–4931 cobriram a fase
inteira, e por isso o `check:sync` do i18n não precisou rodar. Um corpo
malformado que não é nenhum deles responde 400 com
`"error_message": "VALIDATION_ERROR"` e o `detail` do DRF: a informação útil é
a lista de campos, que código nenhum carregaria, e inventar um poria um número
em três arquivos e dezenove locales para dizer "olhe o detail".

**Aceite.**

- [x] `test_orca_error_codes.py` verde, sem código novo (executado com a suíte Orca).
- [x] `Idempotent-Replay: true` afirmado em replay de criação, de reatribuição, de transferência **e de falha** — um 400 replicado continua 400.

---

## 1.7 — Documentação e cliente de referência `[x]`

- `docs/orca-public-api.md`: autenticação, headers obrigatórios, cada endpoint com `curl`, tabela de erros, semântica de replay (RFC §6.7 em linguagem de cliente), exemplos dos três modos e do `explicit`.
- `tools/orca-client/orca_client.py`: script Python (requests) com funções `create_work_item`, `get_by_external`, `reassign`, `transfer`, `list_queue`, gerando `Idempotency-Key` determinística a partir de `(source, id, operation, event_id)`. README curto. Usado pelos testes de contrato (1.8) contra o servidor de teste.
- Atualizar `README.md` do fork (tabela de features) e RFC §2.1.

**Entregue.** `docs/orca-public-api.md` em inglês, como as demais docs
técnicas, aberta pela pergunta que a API responde e a API nativa não: quem faz
o trabalho. A seção mais longa é a das chaves de idempotência, e é de
propósito — é o único lugar onde um integrador pode errar de um jeito que só
aparece semanas depois, como dois itens para um evento.

Duas advertências entraram em destaque porque a implementação as tornou
concretas: **`uuid4()` é a resposta errada** para a chave (um webhook
reentregue depois de o worker morrer ganharia chave nova, e chave nova é
operação nova), e **um 4xx gasta a chave** — corrigir o payload muda o pedido,
e pedido mudado precisa de chave nova. A tabela de erros lista os 19 códigos
que a API pode devolver com o status **da API pública**, incluindo a diferença
do 412.

`tools/orca-client/` é pequeno por decisão: sem retry, sem pool, sem variante
assíncrona. Existe para ser **lido** por quem vai escrever o mesmo em PHP ou
n8n, e para ser executado pelo 1.8 — é isso que mantém os exemplos do guia
honestos. `idempotency_key()` é o método que importa.

Também atualizados: `README.md` do fork (linha nova na tabela de features e as
duas variáveis), RFC §2.1 (três linhas que diziam "Não"/"Parcial" e agora
dizem o que existe) e `docs/organizational-units.md`, com a seção que separa
os dois namespaces.

**Aceite.**

- [x] Os `curl` do guia usam os mesmos corpos dos testes; o fluxo completo (criar → ler → reatribuir → 412 com chave velha → fila) é um teste de contrato.
- [ ] Revisão por alguém que não escreveu o código, executando os `curl` contra staging — critério do Gate 1, precisa de ambiente.

---

## 1.8 — Testes de contrato e gate `[x]`

`apps/api/plane/tests/contract/test_orca_public_contract.py` (o diretório
`contract` já existe): usando o cliente de referência contra o `live_server`
do pytest-django:

- 50 criações com chaves determinísticas, executadas duas vezes → mesmas contagens de `Issue`, `AssignmentDecision`, `IssueAssignee`, `AutomationOperation`; todas as segundas respostas com `Idempotent-Replay: true`.
- 2 threads com a mesma chave → 1 `Issue`.
- Reatribuir por UI (serviço interno) e depois replay da criação → executor não muda.
- Rota `/api/orca/...` com API key → 401/403; rota `/api/v1/orca/...` com sessão sem token → 401.

**Entregue.** `test_orca_public_contract.py` fala HTTP de verdade com
`live_server`, através do cliente de referência importado por caminho de
`tools/orca-client/`. O que ele prova não é observável de um teste que divide
transação com o servidor:

- **50 criações determinísticas, executadas duas vezes**, e a asserção não é "os corpos bateram" e sim que a contagem de `Issue`, `AssignmentDecision`, `IssueAssignee`, `ExternalWorkItemBinding` e `AutomationOperation` **não mudou**. Um replay que reexecutasse a alocação apareceria aqui como 100 decisões muito antes de alguém notar na interface.
- **Duas threads na mesma chave → um item.** Resolvido pela constraint única, não por lock; o perdedor recebe 409 `ORG_OPERATION_IN_PROGRESS`, que é resultado legítimo — o que não é legítimo é um segundo item.
- **Replay depois de reatribuição humana** devolve o executor original, e o `GET by-external` devolve o atual: a diferença entre "o que sua chamada fez" e "como está agora", que é a única forma de as duas coisas serem verdade ao mesmo tempo.
- **Os dois namespaces não aceitam a credencial um do outro** — API key em `/api/orca/` e sessão em `/api/v1/orca/`.
- **A chave gasta num 4xx continua gasta**, e o caminho de saída (variar `attempt`) funciona. É o comportamento que o guia documenta, verificado em vez de afirmado.

**No CI.** Um passo novo no job `api_tests` (`pytest
plane/tests/contract/test_orca_public_contract.py -q`), **não** no job manual: o
arquivo precisa de servidor HTTP e PostgreSQL, que o job já tem, e não precisa
de MinIO nem de RabbitMQ. O porquê de ser merge gate está no comentário do
workflow e em `RUNNING_TESTS.md`: as duas promessas acima são aquelas em que
uma integração se apoia.

**Aceite.**

- [x] Testes de contrato verdes localmente (comando e resultado no README do plano).
- [x] Passo adicionado ao job `api_tests` do `stage.yml` e documentado em `apps/api/tests/RUNNING_TESTS.md`.
- [ ] Verdes no CI — precisa do run do PR.

---

## Gate 1

- [ ] 8 itens `[x]`.
- [ ] Critérios do 1.8 verdes.
- [ ] `docs/orca-public-api.md` revisada por alguém que não escreveu o código, executando os `curl` contra staging com `ORCA_PUBLIC_API_ENABLED=1` **em staging apenas**.
- [ ] `ORCA_PUBLIC_API_ENABLED` permanece `0` em produção (registrar aqui quem verificou).
- [ ] Medição: 200 criações sequenciais na mesma área com `least_loaded` em staging; anotar p50/p95 de latência (RFC §12 risco de lock).

Data do gate: \_\_\_\_
