# Prompts do Codex — Fase 1 (Contrato público de automação)

Plano da fase: [`../01-public-contract.md`](../01-public-contract.md).
Contexto obrigatório: [`00-context.md`](./00-context.md).

**Não despache nada desta fase antes dos Gates P0 e D0 fechados.** A fase
inteira entrega uma API que fica **desligada em produção**
(`ORCA_PUBLIC_API_ENABLED=0`) até o Gate 2-mínimo. Ordem obrigatória:
1.1 → 1.2 → 1.3 → 1.4 → 1.5 → 1.6 → 1.7 → 1.8.

| Item | Perfil | Risco |
| --- | --- | --- |
| 1.1, 1.2 | `standard` | baixo |
| 1.3 | `heavy` | alto (idempotência é o contrato inteiro) |
| 1.4 | `heavy` | alto (transação + on_commit + permissão) |
| 1.5, 1.6 | `standard` | médio |
| 1.7 | `standard` | baixo |
| 1.8 | `heavy` | médio (testes de contrato com live_server) |

---

## 1.1 — Migração 0138: binding externo e operação de automação

```text
Você vai implementar o item 1.1 do plano Orca. Só este item. Pressupõe D0 fechado
(migrações até 0137 em stage) — confirme antes de começar.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md, com atenção à seção 5.
2. docs/orca-work-management-rfc.md §5.2 — ExternalWorkItemBinding e
   AutomationOperation, campo a campo; §6.1 (I8, I9); §6.7 (idempotência).
3. docs/plans/orca-work-management/01-public-contract.md, seção "1.1".
4. apps/api/plane/db/models/organizational_assignment.py (D0.4), como padrão.
5. apps/api/plane/db/models/api.py — o modelo APIToken, para a FK.

TAREFA
1. Arquivo novo apps/api/plane/db/models/organizational_automation.py (copyright),
   com ExternalWorkItemBinding e AutomationOperation exatamente como o RFC §5.2:
   - binding: unicidade PARCIAL (workspace, external_source, external_id) e
     unicidade parcial (issue) — as duas ignorando linhas deletadas, no padrão já
     usado nos modelos Orca;
   - operação: unicidade NÃO condicional (workspace, idempotency_key) — é o que
     garante I9 sob corrida; status como TextChoices; request_hash CharField(64);
     api_token FK db.APIToken null=True on_delete=SET_NULL.
2. Exporte os dois em db/models/__init__.py, seção Orca.
3. Migração 0138_orca_automation_binding.py, dependencies em 0137.
4. Testes apps/api/plane/tests/unit/orca/test_automation_models.py: as três
   unicidades (incluindo o caso de linha deletada não bloquear), request_hash de
   64 chars, status inválido rejeitado.

DEFINIÇÃO DE PRONTO
- A unicidade de idempotency_key é incondicional (explique na resposta por que ela
  não pode ser parcial como as outras).
- ruff limpo.

NÃO FAÇA
- Não crie view, serializer nem serviço aqui.
- Não rode makemigrations/migrate; escreva à mão e avise.

AO TERMINAR
- Marque 1.1 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [1.1] add external binding and automation operation models
- Responda no formato da seção 10 do 00-context.md.
```

---

## 1.2 — Flag, mixin e throttle da API pública

```text
Você vai implementar o item 1.2 do plano Orca. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §7.1 (regras gerais da API pública).
3. docs/plans/orca-work-management/01-public-contract.md, seção "1.2".
4. apps/api/plane/settings/common.py — como ORCA_ORG_UNITS_ENABLED é definido e
   comentado; DEFAULT_THROTTLE_RATES.
5. apps/api/plane/throttles/scim.py — padrão de throttle do fork.
6. apps/api/plane/api/views/base.py — BaseAPIView e APIKeyAuthentication.
7. O mixin OrganizationalUnitFeatureMixin (grep para achar o arquivo).
8. O OrcaConfigEndpoint interno.

TAREFA
1. settings/common.py, na seção Orca, com o mesmo estilo de comentário das flags
   existentes:
   ORCA_PUBLIC_API_ENABLED = os.environ.get("ORCA_PUBLIC_API_ENABLED", "0") == "1"
   ORCA_PUBLIC_API_RATE_LIMIT = os.environ.get("ORCA_PUBLIC_API_RATE_LIMIT", "300/minute")
   e a entrada "orca_public": ORCA_PUBLIC_API_RATE_LIMIT em DEFAULT_THROTTLE_RATES.
2. As duas variáveis em .env.example e apps/api/.env.example, com comentário de
   uma linha cada, deixando claro que o default é DESLIGADO.
3. apps/api/plane/api/views/orca/base.py (pacote novo, com __init__.py e copyright):
   - OrcaPublicApiFeatureMixin: 404 quando ORCA_ORG_UNITS_ENABLED ou
     ORCA_PUBLIC_API_ENABLED estiverem desligados (404, não 403 — a rota não existe
     para quem não deve saber dela);
   - OrcaPublicBaseAPIView(OrcaPublicApiFeatureMixin, BaseAPIView), reutilizando a
     autenticação por API key da API pública nativa.
4. apps/api/plane/throttles/orca_public.py: OrcaPublicThrottle(SimpleRateThrottle),
   scope="orca_public", chave por api_token.id (siga scim.py).
5. OrcaConfigEndpoint passa a expor public_api_enabled, para a UI decidir se mostra
   as instruções de integração.
6. Testes: flag desligada → 404 numa rota qualquer da fase (crie uma rota mínima de
   health se ainda não houver rota, ou teste o mixin diretamente); throttle estoura
   em 429 com DEFAULT_THROTTLE_RATES sobrescrito para 2/minute no teste (a fixture
   clear_throttle_history já existe no conftest).

DEFINIÇÃO DE PRONTO
- Com as duas flags desligadas, nenhuma rota da fase responde diferente de 404.
- ruff limpo.

NÃO FAÇA
- Não ligue a flag por default em lugar nenhum, nem em compose de staging.
- Não implemente endpoint de negócio aqui.

AO TERMINAR
- Marque 1.2 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [1.2] gate the public automation API behind a flag and throttle
- Responda no formato da seção 10 do 00-context.md.
```

---

## 1.3 — Serviço de operação idempotente

```text
Você vai implementar o item 1.3 do plano Orca: idempotência. Este item É o contrato
público — se ele estiver errado, um robô cria itens duplicados em produção. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §6.7 INTEIRO (cada ramo do fluxo) e §6.1 (I9).
3. docs/plans/orca-work-management/01-public-contract.md, seção "1.3".
4. apps/api/plane/db/models/organizational_automation.py (1.1).
5. apps/api/plane/app/services/orca/assignment_service.py (D0.5), para o estilo.

TAREFA
1. Arquivo novo apps/api/plane/app/services/orca/automation_operation.py com:
   canonical_hash(payload: dict) -> str
       sha256 de json.dumps(payload, sort_keys=True, separators=(",", ":"),
       ensure_ascii=False). Determinístico a reordenação de chaves.
   begin_operation(workspace, api_token, key, operation_type, payload) -> OperationHandle
       get_or_create sobre (workspace, idempotency_key). Todos os ramos do RFC §6.7:
       - chave nova → in_progress, segue;
       - chave conhecida com hash DIFERENTE → IdempotencyPayloadMismatch (409);
       - succeeded/failed → ReplayResult com o snapshot da resposta original;
       - in_progress recente (< 60 s) → OperationInProgress (409, cliente tenta depois);
       - in_progress abandonada (> 60 s) → retoma a operação.
   complete_operation(handle, *, issue, response: dict, status="succeeded")
   fail_operation(handle, *, error_code, response: dict)
2. OperationHandle é usável como context manager e garante fail_operation em exceção
   não tratada, com error_code="ORG_INTERNAL_ERROR". Atenção: esse registro precisa
   acontecer FORA da transação principal, senão o rollback apaga o registro da falha.
   Explique na resposta como você garantiu isso.
3. Erros de domínio no errors.py existente (D0.5), herdando de OrcaDomainError.
4. Testes apps/api/plane/tests/unit/orca/test_automation_operation.py:
   - um teste por ramo do §6.7, nomeado pelo ramo;
   - hash estável a reordenação de chaves; hash muda com valor diferente;
   - operação abandonada é retomada;
   - falha dentro do context manager deixa a operação como failed no banco depois de
     um rollback da transação de negócio (este é o teste que prova o item 2).

DEFINIÇÃO DE PRONTO
- Cada ramo do RFC §6.7 tem teste com nome reconhecível.
- Nenhuma chamada a get_or_create sem tratamento de IntegrityError sob corrida
  (dois processos com a mesma chave ao mesmo tempo).
- ruff limpo.

NÃO FAÇA
- Não implemente endpoint aqui (é 1.4).
- Não invente política de expiração de operação além dos 60 s do RFC.

AO TERMINAR
- Marque 1.3 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [1.3] add the idempotent automation operation service
- Responda no formato da seção 10 do 00-context.md, com a tabela ramo §6.7 → teste.
```

---

## 1.4 — Endpoints `work-items/`, `by-external/`, `units/`, `queue/`

```text
Você vai implementar o item 1.4 do plano Orca: os endpoints públicos. Só este item.
Depende de 1.1, 1.2, 1.3 e de todo o D0.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §7 INTEIRO — especialmente §7.2, que traz a
   ORDEM FIXA das operações. Siga essa ordem literalmente.
3. docs/plans/orca-work-management/01-public-contract.md, seção "1.4".
4. apps/api/plane/api/views/issue.py — como a API pública nativa cria Issue, com
   qual serializer, e como chama issue_activity.delay em on_commit. Copie o padrão.
5. apps/api/plane/api/urls/__init__.py e a estrutura de urls da API pública.
6. apps/api/plane/app/services/orca/{assignment_service,automation_operation}.py.

TAREFA
1. Arquivos: apps/api/plane/api/views/orca/{units,work_items}.py,
   apps/api/plane/api/serializers/orca/{units,work_items}.py,
   apps/api/plane/api/urls/orca.py (incluído em api/urls/__init__.py).
   Todos herdando de OrcaPublicBaseAPIView (1.2), com copyright e docstrings.
2. POST /api/v1/orca/workspaces/{slug}/projects/{project_id}/work-items/ na ordem
   exata do RFC §7.2:
   a. begin_operation FORA da transação principal;
   b. transaction.atomic():
      - ExternalWorkItemBinding.get_or_create; binding existente apontando para item
        do MESMO projeto → reutiliza o Issue; apontando para OUTRO projeto →
        ExternalBindingConflict (409);
      - se novo: cria Issue pelo serializer nativo da API v1
        (plane.api.serializers.IssueSerializer) com assignees=[] EXPLÍCITO e
        external_source/external_id preenchidos;
      - set_responsibility(...) do D0.5, com requested_mode, explicit_executor,
        collaborators, assignment_due_at, trigger="public_api",
        operation=handle.operation;
      - monta a resposta do RFC §7.2 e complete_operation;
   c. transaction.on_commit: issue_activity.delay(...) igual à API nativa, para que
      webhooks e atividade nativos funcionem. Nunca dentro da transação.
3. Serializer de entrada WorkItemAutomationSerializer:
   - assignment.mode=explicit sem primary_executor → 400;
   - assignees dentro do bloco work_item → 400 ORG_ASSIGNEES_NOT_ALLOWED_HERE
     (código novo nos três lugares);
   - bloco process presente → 400 ORG_PROCESS_PROJECTION_DISABLED (a Fase 4 libera).
4. Autorização: APIToken.user precisa de ProjectMember ativo com role >= 15 no
   projeto. Reutilize ProjectMemberPermission/allow_permission como em
   plane/api/views/issue.py. Guest → 403.
5. GET .../work-items/by-external/{source}/{id}/ — mesmo envelope de resposta, estado
   atual do item.
6. GET .../units/ e GET .../units/{unit_slug}/queue/ conforme RFC §7.2, paginação com
   o BasePaginator da API pública. A fila só para token de usuário membro da área,
   coordenador (Fase 2) ou Admin do workspace; os demais → 403.
7. Testes apps/api/plane/tests/unit/orca/test_public_work_items.py:
   - caminho feliz para cada modo (manual, self_claim, least_loaded, explicit);
   - replay idêntico → mesma resposta, nenhum item novo;
   - mismatch de payload → 409; binding duplicado em outro projeto → 409;
   - explicit com executor não elegível → 400;
   - token de Guest → 403;
   - assignees no bloco work_item → 400;
   - transação: política proibida não deixa Issue NEM binding no banco;
   - issue_activity.delay NÃO é chamado quando a transação faz rollback (mocke-o).

DEFINIÇÃO DE PRONTO
- A ordem do §7.2 está no código na mesma sequência, com comentário numerado.
- Nenhuma escrita em ProjectMember (I10).
- Com ORCA_PUBLIC_API_ENABLED=0, tudo responde 404.
- ruff limpo.

NÃO FAÇA
- Não crie Issue por .objects.create() driblando o serializer nativo: os hooks
  nativos (sequência, atividade, webhook) dependem dele.
- Não implemente reassign/transfer aqui (é 1.5) nem o bloco process (Fase 4).

AO TERMINAR
- Marque 1.4 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [1.4] add the public work-item automation endpoints
- Responda no formato da seção 10 do 00-context.md, com o mapa passo §7.2 → linha
  do código.
```

---

## 1.5 — `reassign/` e `transfer/` públicos

```text
Você vai implementar o item 1.5 do plano Orca. Só este item. Depende de 1.4.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §7.2 (os dois endpoints), §6.8 (encaminhamento).
3. docs/plans/orca-work-management/01-public-contract.md, seção "1.5".
4. apps/api/plane/api/views/orca/work_items.py (1.4) e o serviço D0.5.

TAREFA
1. POST .../work-items/{issue_id}/reassign/
   - Header If-Match: <decision_id> OBRIGATÓRIO. Ausente → 428 ORG_IF_MATCH_REQUIRED
     (código novo). Divergente do current_assignment_decision → 412 ORG_DECISION_STALE.
   - Corpo: {"primary_executor": <id>} ou {"return_to_queue": true}, mais "reason".
   - Header Idempotency-Key obrigatório, mesma disciplina de 1.3.
   - Delega a reassign(...) ou return_to_queue(...) com trigger="public_api".
2. POST .../work-items/{issue_id}/transfer/
   - Corpo {"unit": <slug>, "reason": ...}; Idempotency-Key obrigatório;
   - Delega a transfer_unit(...) com trigger="public_api";
   - Área destino que não cobre o projeto → 400 (I2).
3. Testes em test_public_work_items.py (ou arquivo irmão):
   - stale → 412; sem If-Match → 428;
   - replay idêntico NÃO gera segunda AssignmentDecision (conte as decisões);
   - transfer para área que não cobre → 400;
   - transfer gera IssueResponsibilityEvent com from e to corretos.

DEFINIÇÃO DE PRONTO
- Os dois endpoints são idempotentes por chave e concorrentes por decisão.
- ruff limpo.

NÃO FAÇA
- Não reimplemente regra de negócio que o serviço D0.5 já tem: a view só traduz
  HTTP ↔ serviço.

AO TERMINAR
- Marque 1.5 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [1.5] add public reassign and transfer endpoints
- Responda no formato da seção 10 do 00-context.md.
```

---

## 1.6 — Códigos de erro e respostas

```text
Você vai implementar o item 1.6 do plano Orca. Só este item. É pequeno e chato:
faça com cuidado, porque é o que o cliente da API vê.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md, seção 4.
2. docs/orca-work-management-rfc.md §7.3 (tabela de códigos).
3. docs/plans/orca-work-management/01-public-contract.md, seção "1.6".
4. apps/api/plane/utils/orca_error_codes.py, packages/constants/src/orca/error-codes.ts,
   o catálogo i18n e apps/api/plane/tests/unit/orca/test_orca_error_codes.py.

TAREFA
1. Todos os códigos do RFC §7.3 registrados nos três lugares, mais
   ORG_ASSIGNEES_NOT_ALLOWED_HERE, ORG_IF_MATCH_REQUIRED e ORG_INTERNAL_ERROR.
   Mensagem i18n em TODAS as locales (skill translate; plurais CLDR; placeholders
   preservados).
2. Respostas de replay (1.3/1.4/1.5) passam a trazer o header
   Idempotent-Replay: true. Teste isso.
3. Confira que cada código realmente é usado por algum caminho de código (grep) e
   que nenhum caminho devolve código não registrado. Liste as duas verificações.

DEFINIÇÃO DE PRONTO
- test_orca_error_codes.py verde (paridade dos três lugares).
- Nenhum código órfão nem código usado sem registro.

NÃO FAÇA
- Não invente código novo que o RFC não pede sem registrar em §4.2.

AO TERMINAR
- Marque 1.6 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [1.6] register the public API error codes and replay header
- Responda no formato da seção 10 do 00-context.md.
```

---

## 1.7 — Documentação e cliente de referência

```text
Você vai implementar o item 1.7 do plano Orca. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §7 inteiro e §6.7.
3. docs/plans/orca-work-management/01-public-contract.md, seção "1.7".
4. O código entregue em 1.4 e 1.5 — a doc descreve o que EXISTE, não o que o RFC
   planejou. Onde divergirem, a doc segue o código e você reporta a divergência.

TAREFA
1. docs/orca-public-api.md (novo): autenticação por API key, headers obrigatórios
   (Idempotency-Key, If-Match), um bloco curl completo por endpoint, tabela de
   erros (código → HTTP → o que o cliente deve fazer), semântica de replay em
   linguagem de cliente, exemplos dos quatro modos de atribuição e do explicit.
   Cada curl precisa ser copiável e funcionar contra staging com a flag ligada.
2. tools/orca-client/orca_client.py (novo, copyright): cliente Python com requests,
   funções create_work_item, get_by_external, reassign, transfer, list_queue.
   A Idempotency-Key é DETERMINÍSTICA, derivada de (source, id, operation, event_id)
   — documente a fórmula no docstring, porque o orquestrador da Fase 4 vai depender
   dela. Timeouts em todas as chamadas. Sem segredo no código.
3. tools/orca-client/README.md curto: instalar, configurar token, um exemplo.
4. README.md do fork (tabela de features) e RFC §2.1 atualizados.

DEFINIÇÃO DE PRONTO
- Alguém que nunca viu o projeto consegue criar um item pela API só com a doc.
- O cliente é o que os testes de contrato de 1.8 vão usar: pense na interface dele
  como pública.
- ruff limpo (o cliente também é Python do repositório).

NÃO FAÇA
- Não documente endpoint que não existe, nem comportamento que você não leu no código.

AO TERMINAR
- Marque 1.7 [x] e atualize a contagem no README do plano.
- Commit: docs(orca): [1.7] document the public API and add a reference client
- Responda no formato da seção 10 do 00-context.md.
```

---

## 1.8 — Testes de contrato e gate

```text
Você vai implementar o item 1.8 do plano Orca: os testes de contrato. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §6.7, §7 e §10.
3. docs/plans/orca-work-management/01-public-contract.md, seções "1.8" e "Gate 1".
4. apps/api/plane/tests/contract/ — o que já existe lá e como está configurado.
5. tools/orca-client/orca_client.py (1.7) — é o cliente que os testes usam.
6. .github/workflows/stage.yml, job de testes (P0.8).

TAREFA
1. apps/api/plane/tests/contract/test_orca_public_contract.py, usando o cliente de
   referência contra o live_server do pytest-django:
   a. 50 criações com chaves determinísticas, executadas DUAS vezes → contagens
      idênticas de Issue, AssignmentDecision, IssueAssignee e AutomationOperation;
      todas as respostas da segunda rodada com Idempotent-Replay: true;
   b. 2 threads com a MESMA chave simultaneamente → exatamente 1 Issue;
   c. reatribuir pela UI (serviço interno) e depois fazer replay da criação → o
      executor NÃO muda (o replay devolve o snapshot, não realoca);
   d. rota /api/orca/... com API key → 401/403; rota /api/v1/orca/... com sessão e
      sem token → 401.
2. Acrescente esse arquivo ao job de testes do CI (o job de PR, não só o manual).
3. Se algum teste falhar, PARE e reporte o defeito com o cenário mínimo. Não
   ajuste o teste para passar.

DEFINIÇÃO DE PRONTO
- Os quatro cenários existem e são determinísticos.
- O CI roda o arquivo.

NÃO FAÇA
- Não relaxe asserção ("assert >= 1" em vez de "== 1") para o teste passar.
- Não ligue ORCA_PUBLIC_API_ENABLED em nenhum ambiente: nos testes, use override
  de settings.

AO TERMINAR
- Marque 1.8 [x] e atualize a contagem no README do plano. Não feche o Gate 1
  (é humano); liste o que falta para fechá-lo, incluindo a medição de latência de
  200 criações least_loaded em staging (RFC §12).
- Commit: test(orca): [1.8] add the public API contract suite
- Responda no formato da seção 10 do 00-context.md.
```
