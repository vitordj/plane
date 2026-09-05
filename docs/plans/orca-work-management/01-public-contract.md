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

- [ ] `makemigrations --check` limpo; migração depende de `0137` (a sessão de agente não roda o comando; a migração foi escrita à mão a partir do modelo, como a `0135`–`0137`).
- [x] Migração declara `("db", "0137_orca_assignment_decision")` como dependência e o `swappable_dependency` do usuário.
- [x] Testes das duas unicidades, do `request_hash` de 64 chars, do `status` e do `operation_type` inválidos, do soft-delete que **não** libera a chave, e da FK nova nos dois sentidos.

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
