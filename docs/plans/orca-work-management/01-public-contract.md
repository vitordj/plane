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

### Pronto quando

- 1.4, 1.5, 1.6, 1.7, 1.8 `[x]` neste arquivo, com "Entregue" e "Aceite" preenchidos como os itens anteriores.
- `pytest plane/tests/unit/orca -q -m unit` e `pytest plane/tests/contract/test_orca_public_contract.py -q` verdes localmente; `ruff check` e `ruff format --check` limpos; `test_orca_error_codes.py` verde sem código novo.
- README do plano: linha da Fase 1 em `8/8` (gate ainda aberto), "Próximo item recomendado" apontando para o fecho dos Gates P0/D0/1, Histórico com a entrada do bloco.
- Push na branch. PR **proposta** (título, base e corpo) no relatório final; abrir só se pedido.

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

## 1.4 — Endpoints: `work-items/` composto, `by-external/`, `units/`, `queue/` `[ ]`

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

---

## 1.5 — `reassign/` e `transfer/` públicos `[ ]`

- `POST .../work-items/{issue_id}/reassign/` com header `If-Match: <decision_id>`; corpo `{"primary_executor": ...}` ou `{"return_to_queue": true}`, `reason`. Sem `If-Match` → 428 `ORG_IF_MATCH_REQUIRED` (código novo); divergente → 412 `ORG_DECISION_STALE`. Também exige `Idempotency-Key`.
- `POST .../work-items/{issue_id}/transfer/` corpo `{"unit": slug, "reason"}`; `Idempotency-Key`.
- Ambos delegam ao serviço D0.5 com `trigger="public_api"`.

**Testes:** stale 412; sem If-Match 428; replay idêntico não gera segunda
decisão; transfer para área que não cobre 400.

---

## 1.6 — Códigos de erro e respostas `[~]`

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

**Achado que o 1.5 tem de resolver.** O RFC §7.3 e o item 1.5 especificam
**412** para `ORG_DECISION_STALE`, mas a exceção `DecisionStale` entregue no
D0.5 carrega **409**, e a rota interna já responde 409 para a UI. Mudar a
classe mudaria a API interna; a rota pública terá de mapear o status
explicitamente, em vez de herdar o `http_status` da exceção. Registrado aqui
para não ser descoberto durante o 1.5.

---

## 1.7 — Documentação e cliente de referência `[ ]`

- `docs/orca-public-api.md`: autenticação, headers obrigatórios, cada endpoint com `curl`, tabela de erros, semântica de replay (RFC §6.7 em linguagem de cliente), exemplos dos três modos e do `explicit`.
- `tools/orca-client/orca_client.py`: script Python (requests) com funções `create_work_item`, `get_by_external`, `reassign`, `transfer`, `list_queue`, gerando `Idempotency-Key` determinística a partir de `(source, id, operation, event_id)`. README curto. Usado pelos testes de contrato (1.8) contra o servidor de teste.
- Atualizar `README.md` do fork (tabela de features) e RFC §2.1.

---

## 1.8 — Testes de contrato e gate `[ ]`

`apps/api/plane/tests/contract/test_orca_public_contract.py` (o diretório
`contract` já existe): usando o cliente de referência contra o `live_server`
do pytest-django:

- 50 criações com chaves determinísticas, executadas duas vezes → mesmas contagens de `Issue`, `AssignmentDecision`, `IssueAssignee`, `AutomationOperation`; todas as segundas respostas com `Idempotent-Replay: true`.
- 2 threads com a mesma chave → 1 `Issue`.
- Reatribuir por UI (serviço interno) e depois replay da criação → executor não muda.
- Rota `/api/orca/...` com API key → 401/403; rota `/api/v1/orca/...` com sessão sem token → 401.

**Aceite.**

- [ ] Todos os testes da fase verdes no runner Docker e no CI (P0.8 já inclui `plane/tests/unit`; adicionar `plane/tests/contract/test_orca_public_contract.py` ao job).

---

## Gate 1

- [ ] 8 itens `[x]`.
- [ ] Critérios do 1.8 verdes.
- [ ] `docs/orca-public-api.md` revisada por alguém que não escreveu o código, executando os `curl` contra staging com `ORCA_PUBLIC_API_ENABLED=1` **em staging apenas**.
- [ ] `ORCA_PUBLIC_API_ENABLED` permanece `0` em produção (registrar aqui quem verificou).
- [ ] Medição: 200 criações sequenciais na mesma área com `least_loaded` em staging; anotar p50/p95 de latência (RFC §12 risco de lock).

Data do gate: \_\_\_\_
