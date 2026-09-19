# Revisão adversarial da `stage` — 07/09/2026

**O que este arquivo é.** A revisão que se faz **antes** de autorizar a Fase 2.
Não é uma conferência dos critérios de aceite: quem escreveu os planos escreveu
o código *e* os critérios, e o que essa circularidade não pega é justamente o
que se procurou aqui. Os RFCs e os arquivos de fase foram lidos só para saber o
que o código **pretende** fazer; a pergunta em cada seção é onde ele não faz.

**Árvore analisada.** `31d35e2b` — ponta do PR #15
(`claude/pendencias-implementacao-auto-7a2rsp`, P0.19 + P0.20), que ainda não
era ancestral de `stage` no início da sessão. Sessão SR do plano
[`MADRUGADA-2026-09-07.md`](../MADRUGADA-2026-09-07.md) §4. **Nenhuma linha de
código foi alterada.**

**Como foi verificado.** Leitura de toda a superfície listada em §4 "SR", mais
**dezesseis testes-sonda** escritos e executados nesta sessão contra um
PostgreSQL 16 local (receita do `HANDOFF-PROMPT.md` §Ambiente local), num banco
separado (`plane_probe`) para não colidir com a suíte. Cada achado abaixo que
diz **confirmado em execução** tem a saída do teste transcrita. Os testes-sonda
foram apagados da árvore antes do commit — eles são evidência, não entrega; o
que fica é a descrição do teste que **faltaria** em cada achado.

**Severidades.**

| | Significado |
| --- | --- |
| **S1** | Segura o Gate 2-mínimo. |
| **S2** | Segura ligar a API pública em produção (§6.8 do plano), ou é perda silenciosa de dado/acesso indevido que precisa de decisão antes da Fase 2. |
| **S3** | Defeito real com janela estreita ou impacto contido; entra como item de fase. |
| **S4** | Cosmético, documental ou inconsistência sem consequência hoje. |

---

## Sumário dos achados

| # | Sev | Onde | O quê |
| --- | --- | --- | --- |
| [A1](#a1) | **S2** | `app/views/organizational_unit.py:583` | Re-POSTar a **mesma** área num item atribuído devolve o item à fila e apaga o executor, com 200 |
| [A2](#a2) | **S2** | `app/services/orca/org_unit_reconciler.py:436-448` | Rebaixamento a Guest do workspace vira `baseline_role`; sair da área deixa acesso residual ativo |
| [A3](#a3) | **S2** | `bgtasks/orca_automation_cleanup_task.py:85` | A retenção do P0.20 **reescreve** linhas append-only de `AssignmentDecision` (FK `SET_NULL`) |
| [A4](#a4) | **S2** | `app/serializers/organizational_unit.py:92` + `views:269` | Guest do workspace lê o e-mail de todos os membros de todas as áreas |
| [A5](#a5) | **S2** | `api/views/orca/units.py:85` | Guest com API key enumera todas as áreas e **todos os projetos** que elas cobrem |
| [A6](#a6) | **S2** | `api/views/orca/base.py` + `automation_operation.py:303-311` | Uma falha transitória queima a `Idempotency-Key` para sempre: toda retentativa replica um 500 |
| [A7](#a7) | **S3** | `app/services/orca/queue.py:54` | A fila mostra título e e-mail de trabalho em projeto cujo acesso o próprio reconciliador retirou |
| [A8](#a8) | **S3** | `app/services/orca/assignment_service.py:993-1018` | `set_responsibility` lê-e-cria fora de lock: duas chamadas simultâneas → `IntegrityError` |
| [A9](#a9) | **S3** | `assignment_service.py:628-631` vs `900-954` | Inversão de ordem de lock (advisory de área × lock de linha): deadlock possível |
| [A10](#a10) | **S3** | `app/services/orca/directory_projector.py:248-258` | Remoção de grupo no Entra desativa o **lead** em silêncio; re-adicioná-lo dá 500 |
| [A11](#a11) | **S3** | `assignment_engine.py:182-191` | `workload/` ainda conta qualquer assignee (D4); o ranking conta executor principal — a Fase 2 lê `workload/` (M5) |
| [A12](#a12) | **S3** | `automation_operation.py:216` | `Idempotency-Key` é única por workspace, não por token: um token queima a chave de outro |
| [A13](#a13) | **S3** | `throttles/orca_public.py:47` | `ORCA_PUBLIC_API_RATE_LIMIT` malformado é 500 por requisição, não falha de boot |
| [A14](#a14) | **S3** | `bgtasks/deletion_task.py:94-96` | O append-only só sobrevive ao cascade de soft-delete por causa de um `print()` e um `continue` |
| [A15](#a15) | **S3** | `app/views/organizational_unit.py:496,768` | `effective-access/` e `workload/` abertos a Guest do workspace |
| [A16](#a16) | **S3** | `orca_scim/base.py:315-316` | O `token_last_used_at` é escrito antes do throttle: 600 UPDATEs/min numa linha, e o 429 não protege |
| [A17](#a17) | **S4** | `orca_scim/base.py:248` | Kill switch antes da autenticação: diz a um anônimo se a camada está ligada, e sem throttle |
| [A18](#a18) | **S4** | `api/views/orca/base.py:48` | Idem na API pública, e a rota desligada fica sem medição |
| [A19](#a19) | **S4** | `.github/workflows/stage.yml:285` | A lista do `compose_env_forwarding` é mantida à mão — é o passo manual que o P0.19 existiu para eliminar |
| [A20](#a20) | **S4** | `api/views/orca/units.py:133` | `ORG_INVALID_ROUTING_TRANSITION` usado para "filtro de query desconhecido" |

Achados em código **novo do PR #15**: A3 (retenção). Todo o resto é anterior.

---

## 1. Escritas em `ProjectMember` fora dos reconciliadores

**Verificado.** `grep` por `ProjectMember` combinado com
`create|update|save|bulk_*|delete|get_or_create|update_or_create` em todo
`apps/api/plane`, excluindo testes e migrações. Escritas fora de teste:

| Arquivo:linha | Natureza |
| --- | --- |
| `app/services/orca/org_unit_reconciler.py:413` | **o reconciliador** (`ACTION_CREATE`) |
| `app/services/orca/org_unit_reconciler.py:429,450,456,462` | idem, via `project_member.save()` |
| `bgtasks/workspace_seed_task.py:116`, `bgtasks/dummy_data_task.py:58,66` | core (seed/dummy) |
| `app/views/user/base.py:308`, `app/views/project/member.py:95,134`, `app/views/project/invite.py:161,257`, `app/views/project/base.py:267,276`, `api/views/project.py:240,251`, `authentication/utils/workspace_project_join.py:76`, `db/management/commands/create_project_member.py:57,62` | core, intocados pelo fork |
| `app/views/workspace/member.py:89` | core: rebaixar alguém a Guest do workspace faz `.update(role=5)` em **todos** os `ProjectMember` da pessoa |

**Sem achado quanto à regra:** nenhum código Orca escreve `ProjectMember` fora
do reconciliador. A regra 8 do plano está cumprida.

**Achado quanto ao que a regra não cobre:** a linha
`app/views/workspace/member.py:89` é uma escrita **do core** que o reconciliador
não reconhece como sua nem como manual. É a origem de [A2](#a2).

---

## 2. `select_for_update` cobre todos os caminhos que mudam `routing_state`?

**Verificado, e sim.** `grep` por atribuições a `routing_state` e a
`primary_executor` fora de testes/migrações/modelos devolve exatamente dois
pontos de escrita — `_apply_assigned` (`assignment_service.py:536`) e
`_apply_queued` (`:555`) — e ambos só são alcançados de dentro de um
`transaction.atomic()` que já passou por `_locked_link()`
(`assignment_service.py:577`, `select_for_update`). Confere caminho a caminho:

| Caminho | Lock |
| --- | --- |
| `allocate` | `:628` atomic → `:631` `_locked_link` ✔ |
| `claim` | `:748` atomic → `:749` `_locked_link` ✔ |
| `reassign` | `:797` → `:798` ✔ |
| `return_to_queue` | `:849` → `:850` ✔ |
| `transfer_unit` | `:900` → `:901`; a `organizational_unit` também muda sob o lock ✔ |
| `routing_audit --write` | `routing_audit.py:143` chama `return_to_queue`, logo herda o lock ✔ |
| `set_responsibility` | delega a `allocate`/`transfer_unit` — **exceto na criação do vínculo**, ver [A8](#a8) |

**Achado sobre ordem de lock:** [A9](#a9).

---

<a id="a9"></a>
### A9 — S3 · Inversão de ordem entre o lock de área e o lock de linha

**Onde.** `apps/api/plane/app/services/orca/assignment_service.py:628-631`
contra `:900-954`.

`allocate` toma o **advisory lock da área** e só depois o lock de linha:

```python
with transaction.atomic():
    if needs_lock:
        unit_allocation_lock(unit.id)   # 1º: advisory da área
    link = _locked_link(issue)          # 2º: linha
```

`transfer_unit` faz o inverso — toma o lock de linha e, quando o item está sem
executor, chama `allocate` **de dentro** dele, que então pede o advisory:

```python
with transaction.atomic():
    link = _locked_link(issue)                      # 1º: linha
    ...
    allocation = allocate(issue, to_unit, ...)      # 2º: advisory (:954 → :630)
```

Dois recursos, duas ordens. É a definição de deadlock.

**Cenário concreto de falha.** Item X pertence à área U, sem executor; a
política efetiva de U em P é `least_loaded`.

- T1: `POST .../organizational-unit-assign/` sobre X → `allocate` pega
  `pg_advisory_xact_lock(hashtext("orca-alloc-U"))` e vai buscar o lock da
  linha de X.
- T2: `POST .../organizational-unit/` com `organizational_unit_id=U` sobre um
  item que hoje pertence a outra área → `transfer_unit` pega o lock da linha de
  X e vai pedir o advisory de U.
- PostgreSQL aborta uma das duas com `deadlock detected`; a view não trata
  `OperationalError`, então a resposta é **500**.

**Confirmado em execução, no nível dos primitivos:**

```
H6 errors: [('transfer', 'OperationalError',
 'deadlock detected\nDETAIL:  Process 2602 waits for ExclusiveLock on advisory
  lock [21014,4294967295,3225728168,1]; blocked by process 2601. ...')]
```

**Não reproduzido pelo caminho real.** Uma segunda sonda que martelou
`allocate` e `transfer_unit` em duas threads, 12 iterações cada, **não**
disparou o deadlock (`H6b errors: []`). A janela entre `unit_allocation_lock` e
`_locked_link` é de microssegundos. Ou seja: a inversão existe e o PostgreSQL
a pune quando as duas transações se intercalam, mas provocá-la exige azar. É
por isso que isto é S3 e não S2 — e é por isso que ela vai continuar existindo
sem ninguém notar até a Fase 2 multiplicar as chamadas concorrentes.

**Teste que faltaria.** Em `test_assignment_concurrency.py`, um teste que
force a intercalação com dois eventos (como a sonda H6 fez) e afirme que
nenhuma das duas transações aborta.

**Correção proposta (não aplicada).** Fixar uma ordem única: tomar o advisory
da área **antes** do lock de linha em todo caminho que possa alcançar
`allocate`. Concretamente, `transfer_unit` passaria a chamar
`unit_allocation_lock(to_unit.id)` como primeira instrução dentro do seu
`atomic()`, antes de `_locked_link`. Alternativa mais barata e menos correta:
envolver a view num retry de `OperationalError`.

---

<a id="a1"></a>
## 3. A1 — S2 · Re-POSTar a mesma área devolve o item à fila e apaga o executor

**Onde.** `apps/api/plane/app/views/organizational_unit.py:583-617`
(`IssueOrganizationalUnitEndpoint.post`) → `assignment_service.py:990-1040`
(`set_responsibility`).

`set_responsibility` só desvia para `transfer_unit` quando a área **muda**:

```python
existing = IssueOrganizationalUnit.objects.filter(issue=issue).first()
if existing is not None and existing.organizational_unit_id != unit.id:
    transfer = transfer_unit(...)
    ...
if existing is None:
    ...cria o vínculo...
return allocate(issue, unit, requested_mode=..., ...)   # <-- cai aqui
```

Quando a área é **a mesma**, o fluxo cai direto em `allocate`, que reexecuta a
política do zero. Numa área `manual`, `allocate` termina em
`_apply_queued(state=QUEUED)` — e `_apply_queued` faz
`link.primary_executor_id = None` (`:556`).

A API **pública** tem exatamente esse guarda e o documenta em nove linhas de
comentário (`api/views/orca/work_items.py:256-278`, "**The early return is the
important half**"): quando a área que pergunta é a que já é dona, ela não
realoca nada. A rota interna, que a aba Trabalho da Fase 2 vai usar, não tem.
Mesmo serviço, dois comportamentos opostos.

**Cenário concreto de falha.** Área Compliance, política `manual`, projeto ONB.
Item I atribuído a Ana (`routing_state=assigned`, `primary_executor=Ana`).
Qualquer Member do projeto ONB faz:

```
POST /api/orca/workspaces/<slug>/projects/<ONB>/issues/<I>/organizational-unit/
{"organizational_unit_id": "<Compliance>"}
```

— um POST que parece idempotente, e que a UI de "marcar a área" faz. Resposta
**200**. Estado depois: `routing_state=queued`, `primary_executor=None`,
`queue_reason=awaiting_coordinator`. A Ana continua como `IssueAssignee`, então
a tela do item não muda; o que muda é a fila do coordenador, que passa a ter um
item que alguém está trabalhando, e a contagem de carga da Ana, que perde um.

**Confirmado em execução:**

```
H1 status: 200
H1 routing_state after re-post: queued
H1 primary_executor after re-post: None
H1 decisions before/after: 2 3
```

**Teste que faltaria.** Em `test_issue_organizational_unit_http.py`: item
atribuído + POST da mesma área → 200, `routing_state` continua `assigned`,
`primary_executor` inalterado, e **nenhuma** `AssignmentDecision` nova.

**Correção proposta (não aplicada).** Portar o early return do `_place`
público para `set_responsibility`: quando `existing.organizational_unit_id ==
unit.id` **e** nenhum `explicit_executor`/`requested_mode` foi pedido, devolver
o estado atual sem realocar. Manter a realocação quando o chamador pede um modo
explicitamente — é o que a rota `organizational-unit-assign/` faz, e ela tem
outro nome de propósito.

---

<a id="a2"></a>
## 4. A2 — S2 · Acesso residual depois de sair da área

**Onde.** `apps/api/plane/app/services/orca/org_unit_reconciler.py:436-448`
(`_apply_change`, ramo `ACTION_ELEVATE`), em interação com
`app/views/workspace/member.py:89` (core).

O docstring do módulo (`:14-31`) promete: "*Downwards*, the layer only lowers or
removes access when the current `ProjectMember.role` still equals the role it
last wrote". A implementação disso é o bloco:

```python
if action == ACTION_ELEVATE and project_member.role != state.last_applied_role:
    # ... "a person chose the role standing here" ...
    state.baseline_role = project_member.role
    state.created_by_org_layer = False
```

O comentário assume que uma divergência só pode ter vindo de uma pessoa. Não é
verdade: o core reescreve `ProjectMember.role` sozinho quando alguém é rebaixado
a Guest do workspace (`.update(role=5)`, `workspace/member.py:89`).

**Sequência exata que quebra.**

1. Bruno é membro da área Compliance, que cobre ONB com `default_role=15`. O
   reconciliador **cria** o `ProjectMember`: `role=15`, `last_applied_role=15`,
   `baseline_role=None`, `created_by_org_layer=True`.
2. Um admin rebaixa Bruno a **Guest do workspace**. O core reescreve o
   `ProjectMember` para `role=5`. Na reconciliação seguinte,
   `cap_role_to_workspace_role(15, 5)` devolve 5, `target == current_role == 5`
   → `ACTION_NONE`, e a guarda de `reconcile_access:553` não chama
   `_apply_change`. **`last_applied_role` fica em 15, mentindo.**
3. O admin devolve Bruno a **Member do workspace**. Agora `inherited=15`,
   `current=5` → `ACTION_ELEVATE`, e como `5 != 15` o bloco acima grava
   `baseline_role=5` e `created_by_org_layer=False`: o rebaixamento **do core**
   foi registrado como se fosse uma escolha manual de um humano.
4. Bruno sai da área. `inherited=None`, `current == last_applied == 15` →
   `ACTION_RESTORE_BASELINE` com `role=5`. O `ProjectMember` fica **ativo em 5**
   em vez de ser desativado.

Bruno nunca teve acesso a ONB por outro motivo que não a área, e continua
dentro do projeto depois de sair dela.

**Confirmado em execução:**

```
H15 after the layer granted:   15 True  last_applied: 15  baseline: None  created_by_layer: True
H15 after downgrade+reconcile:  5       last_applied: 15  baseline: None
H15 after restore+reconcile:   15       last_applied: 15  baseline: 5     created_by_layer: False
H15 after leaving the area:     5 True  last_applied: None baseline: 5
```

**Por que importa agora.** A decisão M3 generaliza exatamente este código: a
`Source` vira tupla, `_sync_grants` passa a chavear por `(kind, source_id,
unit_project_id)`, e o M3 diz explicitamente que "`baseline_role`,
`last_applied_role`, `cap_role_to_workspace_role`, drift ficam intocados". Vão
ficar intocados com este defeito dentro.

**Teste que faltaria.** Em `test_org_unit_reconciler.py`: os quatro passos
acima, afirmando que ao sair da área o `ProjectMember` fica `is_active=False`.
E um teste do passo 2 isolado, afirmando que depois do rebaixamento
`last_applied_role` acompanha o papel efetivo (5) em vez de ficar em 15.

**Correção proposta (não aplicada).** Duas partes, e a primeira sozinha já
fecha o buraco:

1. No ramo `ACTION_NONE` de `_apply_change`, quando o *cap* do papel de
   workspace é o que baixou o alvo, atualizar `last_applied_role` para o papel
   efetivamente escrito em vez de deixar o valor antigo. Alternativa
   equivalente: fazer `_decide` devolver `ACTION_LOWER` quando a diferença vem
   do cap, e não `ACTION_NONE`.
2. Não sobrescrever `created_by_org_layer=False` num `ACTION_ELEVATE` cujo
   `state.created_by_org_layer` já era `True`: uma linha que a camada criou não
   deixa de ser dela porque alguém mexeu no papel.

---

<a id="a3"></a>
## 5. A3 — S2 · A retenção do P0.20 reescreve o registro append-only

**Onde.** `apps/api/plane/bgtasks/orca_automation_cleanup_task.py:82-89` →
`bgtasks/cleanup_task.py:61` → FK em
`db/models/organizational_assignment.py:255-261`.

O item da §4 "SR" pedia para listar **onde** há `.update(` sobre
`AssignmentDecision` e `IssueResponsibilityEvent`. A resposta literal é a
esperada:

```
$ grep -rn "AssignmentDecision\|IssueResponsibilityEvent" --include=*.py plane/ \
    | grep -v "^plane/tests/\|^plane/db/migrations/" | grep "\.update(\|\.delete(\|\.save("
(vazio)
```

**Zero.** Todas as referências fora de teste são `objects.create(`,
`objects.filter(` de leitura, ou serializers. A proveniência disso — que era o
ponto — é boa: o `AppendOnlyModel.save()`
(`organizational_assignment.py:112-115`) levanta `ValueError` em toda gravação
sobre linha existente, e o próprio docstring reconhece que "*a queryset
`update()` bypasses `save()`*".

**O que o grep não pega, e é o achado.** O `UPDATE` existe — só não está
escrito no código Orca. Ele é emitido pelo Django:

```python
automation_operation = models.ForeignKey(
    "db.AutomationOperation",
    on_delete=models.SET_NULL,   # <-- aqui
    ...
    related_name="assignment_decisions",
)
```

O P0.20 apaga receipts com **hard delete** (`cleanup_task.py:61`, comentado no
próprio arquivo: "`all_objects` is a plain manager, so this is a hard delete").
Ao apagar a linha pai, o Django emite
`UPDATE orca_assignment_decisions SET automation_operation_id = NULL WHERE ...`.
Isso passa por cima do guarda append-only e reescreve o registro.

**Cenário concreto de falha.** Uma integração cria um item pela API pública em
01/09. A `AssignmentDecision` gravada aponta para o receipt — é o que o
docstring de `_record` (`assignment_service.py:469-471`) chama de "*Answers
'which call did this?' without joining through timestamps*". Em 01/10 o beat
diário roda `delete_orca_automation_operations`; a coluna vira `NULL`. A partir
daí, para toda decisão tomada pela API pública, "qual chamada fez isto?" é
irrespondível, e nem o log diz que a informação existiu: o `UPDATE` é silencioso.

**Confirmado em execução:**

```
H2 receipt rows left: 0
H2 decision.automation_operation_id after cleanup: None
```

**Por que a análise do P0.20 não pegou.** O docstring do arquivo raciocina em
detalhe sobre *uma* consequência de apagar o receipt — "**Deleting a receipt
un-spends its idempotency key**" — e enumera as três operações. Não pergunta o
que a FK faz. O que era invisível de dentro do arquivo era o efeito colateral
do `on_delete`.

**Teste que faltaria.** Em `test_automation_operation_cleanup.py`: criar uma
decisão apontando para um receipt, envelhecer o receipt além da janela, rodar a
tarefa, e afirmar que `decision.automation_operation_id` **continua** o mesmo —
ou, se a decisão for aceitar a perda, um teste que afirme o oposto
explicitamente, para que a perda seja uma escolha registrada e não um efeito.

**Correção proposta (não aplicada).** Ordem de preferência:

1. **Não apagar o receipt referenciado.** Excluir da query de limpeza os
   receipts com `assignment_decisions` vivas
   (`.filter(assignment_decisions__isnull=True)`); o volume que sobra é o de
   operações que não produziram decisão, que é a maioria.
2. **Congelar a proveniência antes de apagar.** Copiar
   `idempotency_key`/`operation_type` para colunas de texto na decisão no
   momento da gravação, e deixar a FK cair.
3. **`on_delete=PROTECT`** faria a limpeza falhar em voz alta em vez de
   silenciosamente — pior operacionalmente, melhor que a perda silenciosa.

**Item novo de fase que a §4 "SR" pede.** Uma *trigger* PostgreSQL
`BEFORE UPDATE OR DELETE` sobre `orca_assignment_decisions` e
`orca_responsibility_events` que levante exceção, tornando o append-only uma
garantia do banco e não uma convenção do ORM. **Ela pegaria exatamente este
defeito** — a limpeza do P0.20 falharia no primeiro `SET NULL`. Proposta, não
implementada: entra como item da Fase 2.6 ou 3, com a ressalva de que precisa de
uma exceção explícita para o `SET NULL` da FK ou da correção (1) acima antes de
poder ser ligada.

---

<a id="a14"></a>
### A14 — S3 · O append-only só sobrevive ao cascade por causa de um `print()`

**Onde.** `apps/api/plane/bgtasks/deletion_task.py:83-97`.

O cascade de soft-delete do core percorre as relações reversas e faz
`related_obj.save()` em cada uma. Sobre `AssignmentDecision` e
`IssueResponsibilityEvent` isso levanta o `ValueError` do `AppendOnlyModel` —
que é engolido por:

```python
except Exception as e:
    # Log the error or handle as needed
    print(f"Error handling relation {related_name}: {str(e)}")
    continue
```

**Confirmado em execução** (apagando um work item):

```
H13 stdout from the cascade:
 ['Error handling relation orca_assignment_decisions: AssignmentDecision rows are
   append-only; write a new row instead of editing one',
  'Error handling relation orca_responsibility_events: IssueResponsibilityEvent rows
   are append-only; write a new row instead of editing one']
H13 link still live: False
H13 decisions still live: 1
```

O resultado é o **desejado** — o registro sobrevive ao apagamento do item — mas
por acidente: um `print()` para stdout (não o `logger`) e um `continue`. Nada
nos testes fixa isso, e se alguém trocar o guarda de instância por outra coisa,
o cascade passa a apagar o registro em silêncio, sem que teste algum caia.

**Teste que faltaria.** Um teste que apague um work item com decisões e afirme
que as decisões continuam com `deleted_at IS NULL`. Hoje, esse comportamento
não é afirmado em lugar nenhum.

**Correção proposta.** A trigger de A3 torna isso uma garantia. Enquanto ela não
existe: o teste acima, mais trocar o `print` por `logger.exception` para que a
condição seja observável em produção.

---

<a id="a4"></a>
## 6. Escalada e vazamento — Guest

<a id="a4-body"></a>
### A4 — S2 · Guest do workspace lê o e-mail de todos os membros de todas as áreas

**Onde.** `app/views/organizational_unit.py:269` (decorator) +
`app/serializers/organizational_unit.py:87-111`
(`OrganizationalUnitMembershipSerializer`).

```python
@allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST], level="WORKSPACE")
def list(self, request, slug, unit_id): ...
```

e o serializer expõe `email` incondicionalmente.

**Por que isto é uma escalada e não uma escolha.** O Plane trata o e-mail como
um campo que Guest não vê, e o faz de propósito. `WorkSpaceMemberViewSet.list`
(`app/views/workspace/member.py:51-54`) troca de serializer conforme o papel:

```python
if workspace_member.role > 5:
    serializer = WorkspaceMemberAdminSerializer(...)   # UserAdminLiteSerializer: TEM email
else:
    serializer = WorkSpaceMemberSerializer(...)        # UserLiteSerializer: NÃO tem email
```

(`serializers/user.py:141-170` confirma a diferença: `email` e
`last_login_medium` só existem no Admin.) A rota Orca ignora essa distinção.

**Cenário concreto de falha.** Um Guest — um contratado externo, o papel que o
Plane cria justamente para quem não deve ver a organização — faz
`GET /api/orca/workspaces/<slug>/organizational-units/` (também aberto a Guest,
`views:178`), pega os ids, e depois
`GET .../organizational-units/<id>/members/` para cada um. Colhe nome,
**e-mail**, papel no workspace e papel na área de toda a empresa. Uma lista de
e-mails corporativos por departamento é exatamente o insumo de um phishing
direcionado.

**Confirmado em execução:**

```
H3 status: 200
H3 body: [{... 'display_name': 'plain', 'email': 'plain@plane.so',
           'workspace_role': 15, 'role': 'member', ...}]
```

**Teste que faltaria.** Em `test_organizational_unit_api.py`, a matriz de
papéis sobre `members/` afirmando que a resposta a um `guest_client` não contém
a chave `email`. A fixture `guest_client` já existe no `conftest.py` e não é
usada nesta rota.

**Correção proposta (não aplicada).** Espelhar o padrão do core: um
`OrganizationalUnitMembershipLiteSerializer` sem `email`, escolhido quando
`workspace_member.role == 5`. Não tirar o Guest da rota inteira — ver "área é
estrutura, não trabalho" no docstring de `api/views/orca/units.py:16-18`, que é
uma decisão razoável; o que não se sustenta é o e-mail junto.

<a id="a15"></a>
### A15 — S3 · `effective-access/` e `workload/` abertos a Guest

**Onde.** `app/views/organizational_unit.py:496` e `:768`.

Ambos com `@allow_permission([ADMIN, MEMBER, GUEST], level="WORKSPACE")` e sem
nenhuma verificação de pertencimento à área.

**Confirmado em execução:**

```
H4 workload: 200 [{'workspace_member_id': '...', 'display_name': 'plain',
                   'role': 'member', 'open_issues': 0}]
H4 effective-access: 200 {'changes': [{'workspace_member_id': '...',
   'project_id': '...', 'current_role': 15, 'desired_role': 15,
   'action': 'none', 'sources': [{'organizational_unit_name': 'Compliance',
   'membership_id': '...', 'role': 15}]}]}
```

**A hipótese do roteiro (§4 "SR": "`workload/` e `effective-access/` vazando
e-mails para Guest") está parcialmente derrubada:** nenhum dos dois devolve
e-mail. `workload_snapshot` (`assignment_engine.py:193-200`) devolve
`display_name`, papel na área e contagem de trabalho aberto;
`effective-access/` devolve ids, papéis atual e desejado e a proveniência.

**O que resta é real, e é outra coisa.** `effective-access/` é um mapa completo
de RBAC — quem tem acesso a que projeto, com que papel, por causa de qual área —
entregue ao papel mais restrito do produto. `workload/` diz quanto cada pessoa
nomeada tem de trabalho aberto. Nenhum dos dois é informação que um contratado
externo deveria conseguir enumerar.

**Teste que faltaria.** Matriz de papéis para as duas rotas, com Guest
esperando 403.

**Correção proposta.** `effective-access/` para `[ADMIN]` apenas — é uma
prévia de escrita administrativa, não uma leitura de conveniência. `workload/`
para `[ADMIN, MEMBER]` **e** restrito a quem pertence à área (o helper
`may_see_queue` que o A3 do plano cria em
`permissions/organizational_unit.py` serve exatamente para isso). Atenção
especial da Sessão SA: `policy GET` (`views:742`) tem o mesmo decorator, e o
`policy PUT` que o 2.2 acrescenta **não pode** herdá-lo — §5.2 diz Admin ws.

<a id="a5"></a>
### A5 — S2 · Guest com API key enumera todos os projetos do workspace

**Onde.** `api/views/orca/units.py:85-107` (`UnitListEndpoint.get`) +
`:57-71` (`workspace_for`) + `api/serializers/orca/units.py:24-48`
(`unit_payload`).

`workspace_for` aceita **qualquer** `WorkspaceMember` ativo, sem olhar o papel.
`unit_payload` devolve, por área, todo projeto coberto com `project_id`,
`identifier` e a política resolvida.

**Por que isto é uma escalada.** A rota pública nativa de projetos filtra:

```python
Project.objects.filter(workspace__slug=...).filter(
    Q(project_projectmember__member=self.request.user,
      project_projectmember__is_active=True)
    | Q(network=2)          # público no workspace
)
```
(`api/views/project.py:86-95`)

A rota Orca não filtra nada. Projetos privados (`network=0`) dos quais o
chamador não é membro aparecem.

**Cenário concreto de falha.** Um Guest cria um API token (o Plane permite a
qualquer membro) e faz
`GET /api/v1/orca/workspaces/<slug>/units/`. Recebe 200 com a lista completa de
áreas, e para cada uma o id e o `identifier` de cada projeto que ela cobre —
incluindo os privados. Combinado com A4, isso é o organograma da empresa mais o
mapa de projetos, para o papel que não deveria ver nem um nem outro.

**Confirmado em execução:**

```
H9 status: 200
H9 body: {... 'results': [
  {'slug': 'compliance', 'projects': [{'project_id': '...', 'identifier': 'ONB',
     'default_role': 15, 'policy': {...}}]},
  {'slug': 'legal', 'projects': [{'project_id': '...', 'identifier': 'BIL', ...}]}]}
```

(o Guest não é membro de nenhum dos dois projetos)

**Teste que faltaria.** Em `test_public_units.py`, um `token_client(guest_user)`
sobre `units/` afirmando que projetos dos quais o chamador não é `ProjectMember`
ativo e que não são `network=2` **não** aparecem na resposta.

**Correção proposta (não aplicada).** Filtrar `links` em `UnitListEndpoint` pelo
mesmo predicado do core: `Q(project__project_projectmember__member=request.user,
project__project_projectmember__is_active=True) | Q(project__network=2)`, com
Admin do workspace vendo tudo. Uma área cujos projetos todos caem fora do filtro
continua listada (a estrutura é pública) com `projects: []`.

---

<a id="a7"></a>
## 7. A7 — S3 · A fila mostra trabalho de projeto ao qual o leitor perdeu acesso

**Onde.** `app/services/orca/queue.py:54` (`queue_queryset`) +
`api/views/orca/units.py:151-162` (`_may_see_queue`).

`queue_queryset` filtra por área e por estado. Não filtra por cobertura, por
projeto arquivado, nem pelos projetos que o leitor pode abrir. `_may_see_queue`
pergunta só "é membro da área ou Admin do workspace?".

Mas o reconciliador **retira** acesso quando o projeto é arquivado:
`_active_sources` (`org_unit_reconciler.py:145-150`) exige
`project__archived_at__isnull=True`, então a fonte some e o `ProjectMember` é
desativado. E `unit_covers_project` (`coverage.py:46-50`) usa a mesma condição:
uma área ligada só a projetos arquivados não cobre nenhum. As duas metades
concordam entre si e discordam da fila.

**Cenário concreto de falha.**

1. Área Compliance cobre ONB; Ana é membro da área. O reconciliador cria o
   `ProjectMember` de Ana em ONB (role 15, ativo).
2. Um item de ONB é atribuído a Ana pela área.
3. Um admin arquiva ONB. Na reconciliação seguinte, o `ProjectMember` de Ana é
   **desativado** — ela não abre mais ONB na interface.
4. Ana faz
   `GET /api/v1/orca/workspaces/<slug>/units/compliance/queue/?routing_state=all`
   e recebe 200 com o **título** do item e o **e-mail** do executor.

**Confirmado em execução:**

```
H8b granted by the layer: (15, True)
H8b after archiving:      (15, False)      <- o reconciliador retirou o acesso
H8b queue status: 200
H8b rows: [('Secret onboarding item',
            {'id': '...', 'email': 'plain@plane.so', 'display_name': 'plain'})]
```

O docstring de `api/views/orca/units.py:16-20` diz que a fila é restrita "*porque
as linhas carregam títulos de trabalho real*". A restrição escolhida — ser
membro da área — não é a mesma coisa que poder ver o projeto.

**Por que isto é da Fase 2 e não só da API pública.** A decisão M6 manda a fila
interna reaproveitar `queue_queryset` e a M2/F-h mandam a pública passar a
chamar o helper compartilhado `may_see_queue`. Se o helper nascer com a regra de
hoje, a aba Trabalho herda o vazamento — e aí ele passa a ser visível na
interface, não só por API key.

**Teste que faltaria.** Em `test_public_units.py`: membro da área + projeto
arquivado + reconciliação → a fila não devolve linhas daquele projeto.

**Correção proposta (não aplicada).** Em `queue_queryset`, aceitar um
`visible_project_ids` opcional e, nas duas views, passar os projetos em que o
leitor tem `ProjectMember` ativo (Admin do workspace sem restrição). É uma
consulta a mais por página, não por linha. Alternativa mínima: excluir projetos
arquivados do queryset, o que fecha o cenário acima mas não o caso do *drift*
manual (`ACTION_SKIP_MANUAL_DRIFT` também deixa a pessoa sem acesso).

---

<a id="a6"></a>
## 8. Idempotência (RFC §6.7)

### O que está certo

**`all_objects` em todas as leituras de recibo — verificado, sem achado.**

```
plane/app/services/orca/automation_operation.py:216  all_objects.filter(...).first()
plane/app/services/orca/automation_operation.py:226  all_objects.get(...)
plane/bgtasks/orca_automation_cleanup_task.py:75     all_objects.filter(...)
```

As três leituras usam `all_objects`, coerente com a constraint sem condição de
`deleted_at` (`organizational_automation.py:182-190`) e com o raciocínio
registrado ali. `ExternalWorkItemBinding`, ao contrário, tem constraints
parciais (`condition=Q(deleted_at__isnull=True)`, `:98-107`) e é lido com
`objects` — também coerente. Nenhum descasamento.

**`transfer` sem `If-Match` × janela de retenção — verificado, e o PR #15
reconhece.** `transfer_unit` (`assignment_service.py:874-884`) não aceita
`expected_decision_id`, e o docstring de `orca_automation_cleanup_task.py:34-38`
descreve exatamente a consequência: apagado o recibo, uma retentativa de
`transfer` reexecuta e pode escolher outro executor. Com 30 dias a janela é
larga o bastante. **Sem achado novo** — mas ver A12, que é como um terceiro pode
*encurtar* essa janela de propósito.

<a id="a6-body"></a>
### A6 — S2 · Uma falha transitória queima a chave para sempre

**Onde.** `api/views/orca/base.py` + `api/views/orca/work_items.py:120-141`
(`run_operation`) + `app/services/orca/automation_operation.py:300-311`
(`begin_operation`).

`run_operation` trata `OrcaDomainError` e `ValidationError`. Qualquer outra
exceção sobe até `begin_operation`, que marca o recibo **`failed`** com
`ORG_INTERNAL_ERROR` e status 500, e relança. A partir daí a chave está gasta:
`_existing` (`:176-177`) devolve `replayed=True` para status `FAILED`, e toda
retentativa recebe o 500 gravado — **sem nunca tentar o trabalho de novo**.

**Cenário concreto de falha.** Uma integração manda um item novo com
`Idempotency-Key: crm-case-4711`. Nesse instante ocorre qualquer falha
transitória — o `IntegrityError` de [A8](#a8), um deadlock de [A9](#a9), uma
queda momentânea de conexão. A integração recebe erro, e faz o que integrações
fazem: retenta com a mesma chave, que é o contrato. Recebe o mesmo erro. Para
sempre — na prática, por 30 dias, até a retenção do P0.20 apagar o recibo. O
item nunca é criado, e não há resposta possível do lado do cliente: mudar a
chave é exatamente o que a documentação diz para não fazer.

**Confirmado em execução** (com um `IntegrityError` injetado na primeira
chamada):

```
H10b first attempt: 400
H10b receipt: ('failed', 'ORG_INTERNAL_ERROR')
H10b retry status: 500  replay: true  body: {'error_code': 'ORG_INTERNAL_ERROR'}
H10b set_responsibility calls: 1
```

`set_responsibility calls: 1` é o ponto: a retentativa não chegou nem a tentar.

**Um segundo defeito, visível na mesma saída.** A primeira tentativa respondeu
**400** (`BaseAPIView.handle_exception`, `api/views/base.py:73-77`, converte
`IntegrityError` em `{"error": "The payload is not valid"}` com 400), enquanto o
recibo gravou **500**. O docstring de `complete` diz que o status é guardado
"*so a replay reproduces the original status too*" — e não reproduz: o primeiro
chamador viu 400, o segundo vê 500. `begin_operation` grava um status que
inventa, em vez do que a view respondeu.

**Teste que faltaria.** Em `test_automation_operation.py` ou
`test_public_work_items.py`: injetar uma exceção não-domínio na primeira
chamada e afirmar que a retentativa com a mesma chave **executa** a operação, em
vez de replicar o erro. E um teste de paridade de status entre o que o chamador
recebeu e o que ficou no `response_snapshot`.

**Correção proposta (não aplicada).**

1. Distinguir **falha determinística** (domínio, validação — o chamador não
   pode ter sucesso repetindo) de **falha transitória** (qualquer outra). A
   primeira grava `FAILED` e replica, como hoje. A segunda deve **liberar** o
   recibo — apagá-lo, ou marcá-lo com um status novo tipo `abandoned` que
   `_existing` trate como o caminho de `_resume` — para que a retentativa
   execute.
2. Enquanto isso não existe: `begin_operation` deveria gravar o status que a
   view realmente responde, não `500` fixo.

<a id="a12"></a>
### A12 — S3 · A chave de idempotência não é escopada ao token

**Onde.** `db/models/organizational_automation.py:184-190` (constraint) e
`app/services/orca/automation_operation.py:216`.

```python
models.UniqueConstraint(
    fields=["workspace", "idempotency_key"],   # sem api_token
    name="orca_operation_unique_idempotency_key",
)
```

`_existing` (`:168-182`) verifica o hash do payload e o status. Não verifica de
quem é o recibo. `_resume` (`:160-164`) chega a **reatribuir**
`operation.api_token` ao chamador que assumiu.

**Cenário concreto de falha.** Duas integrações no mesmo workspace: A (Zendesk)
e B (um script de migração). B usa chaves previsíveis (`case-1`, `case-2`, …) ou
simplesmente colide com o esquema de A. B chama primeiro com `case-4711` e um
payload diferente. Quando A chama com `case-4711`, recebe **409
`ORG_IDEMPOTENCY_PAYLOAD_MISMATCH`** — e continuará recebendo, porque o recibo
de B é dono da chave. Um detentor de token qualquer do workspace pode, de
propósito, pré-gastar o espaço de chaves de outra integração e desligá-la.

**Confirmado em execução:**

```
H11 outcome for the second token: IdempotencyPayloadMismatch
```

**Teste que faltaria.** Dois tokens, a mesma chave, payloads diferentes: cada um
deve receber a resposta da sua própria operação.

**Correção proposta (não aplicada).** Incluir `api_token` na constraint e no
`filter` de `start_operation`. Um recibo cujo token foi apagado
(`on_delete=SET_NULL`) fica com `api_token IS NULL` e deixa de bloquear —
aceitável, e melhor que o comportamento de hoje. Se a intenção era de fato
"chave por workspace", isso precisa estar dito na
[`docs/orca-public-api.md`](../../orca-public-api.md), porque hoje nenhum
integrador tem como saber.

---

<a id="a8"></a>
## 9. A8 — S3 · `set_responsibility` lê-e-cria o vínculo fora de qualquer lock

**Onde.** `app/services/orca/assignment_service.py:993` e `:1014-1018`.

```python
existing = IssueOrganizationalUnit.objects.filter(issue=issue).first()   # sem lock, fora de atomic
...
if existing is None:
    with transaction.atomic():
        link = IssueOrganizationalUnit.objects.create(...)               # e aqui
```

O modelo tem `UniqueConstraint(fields=["issue"], condition=Q(deleted_at__isnull=True))`
(`db/models/organizational_unit.py:534-538`). Duas chamadas simultâneas para o
mesmo item, ambas vendo `existing is None`, e a segunda estoura.

**Cenário concreto de falha.** Um webhook e um clique na interface chegam juntos
sobre o mesmo item ainda sem área. Uma das duas recebe
`IntegrityError: duplicate key value violates unique constraint
"issue_org_unit_unique_issue_when_deleted_at_null"`. Na rota interna isso é um
400 enganoso ("The payload is not valid"); na rota **pública** é o gatilho de
[A6](#a6) — a chave de idempotência fica queimada, e o cliente nunca consegue
criar o item.

**Confirmado em execução:**

```
H7 errors: [('IntegrityError', 'duplicate key value violates unique constraint
  "issue_org_unit_unique_issue_when_deleted_at_null" ...')]
H7 links: 1  responsibility events: 1
```

Note que o dado fica **certo** (um vínculo, um evento) — o problema é a
resposta, não a consistência.

**Teste que faltaria.** Em `test_assignment_concurrency.py`: duas threads
chamando `set_responsibility` sobre um item sem vínculo, afirmando que as duas
retornam 200/`AllocationResult` e que existe exatamente um vínculo.

**Correção proposta (não aplicada).** Trocar o `filter().first()` + `create()`
por um `get_or_create` dentro de um `atomic()` próprio, tratando o
`IntegrityError` relendo a linha vencedora — o mesmo padrão que
`start_operation` já usa (`automation_operation.py:220-227`) e que o próprio
docstring dele chama de "*settled by the unique constraint rather than by a
lock*". O padrão certo já existe no fork; só não foi aplicado aqui.

---

<a id="a10"></a>
## 10. SCIM

### Kill switch, rate limit — verificado

**Rate limit: existe e está bem construído.** `SCIMBaseView` zera
`throttle_classes` de propósito e aplica os dois throttles à mão, cada um no
ponto do request em que faz sentido (`orca_scim/base.py:225-262`). O comentário
explica por quê: os throttles do DRF rodam dentro de `super().initial()`, antes
da verificação do bearer token, então um chamador sem token nenhum gastaria o
orçamento do workspace. A falha de autenticação é medida por endereço; o
orçamento do workspace só é debitado depois do token conferir. **Nada a
apontar** — este é o pedaço mais bem pensado da superfície revisada.

**Kill switch: depois de `super().initial()`, antes da autenticação** —
`base.py:241-249`, e o comentário assume isso ("*Checked before authentication
so the answer is the same 404*"). Ver [A17](#a17).

<a id="a10-body"></a>
### A10 — S3 · Uma mudança de grupo no Entra desativa o lead em silêncio, e o re-add dá 500

**Onde.** `app/services/orca/directory_projector.py:248-258` (passe subtrativo
de `project_unit`) contra a constraint
`org_unit_membership_single_active_lead_per_unit`
(`db/models/organizational_unit.py:217-221`).

O passe subtrativo desativa toda membership com `sync_source=scim` que não esteja
mais no grupo. Não olha `role`. O passe aditivo reativa com
`membership.is_active = True; membership.save(...)`, mantendo o `role` — e é aí
que a constraint pode explodir.

**Cenário concreto de falha.**

1. O Entra provisiona Ana no grupo da área; a membership nasce
   `sync_source=scim`, `role=member`.
2. Um admin promove Ana a **lead** da área na interface.
3. Alguém tira Ana do grupo no Entra. O `PATCH remove` chega, `project_unit`
   roda o passe subtrativo e desativa a membership. **A área fica sem lead, sem
   aviso, sem entrada no `last_sync_summary`.**
4. O admin nomeia Bruno lead.
5. O Entra recoloca Ana no grupo. O passe aditivo reativa a membership dela —
   que ainda tem `role=lead` — e a constraint parcial `(organizational_unit)
   WHERE role='lead' AND is_active AND deleted_at IS NULL` é violada.
   `project_unit` é `@transaction.atomic`, então a projeção inteira desfaz e o
   endpoint SCIM responde **500**. O Entra reagenda e repete, indefinidamente:
   o provisionamento daquele grupo fica travado até alguém mexer no banco.

**Confirmado em execução:**

```
H16 membership after the first projection: member scim True
H16 after the group removal:               lead   False      <- lead desativado em silêncio
H16 second lead created: 92f28f24-...
H16 re-adding the original lead: IntegrityError: duplicate key value violates
    unique constraint "org_unit_membership_single_active_lead_per_unit"
```

**Teste que faltaria.** Em `test_directory_projector.py`: os cinco passos acima,
afirmando (a) que desativar um lead aparece no `ProjectionResult` como algo
distinto de desativar um membro, e (b) que a reativação de um ex-lead com outro
lead ativo **não** levanta.

**Correção proposta (não aplicada).** No passe aditivo, ao reativar uma
membership com `role='lead'`, verificar se já existe lead ativo e, em caso
afirmativo, reativar como `member` — registrando o rebaixamento no
`ProjectionResult` para que apareça em `last_sync_summary`. No passe subtrativo,
contar separadamente os leads desativados, pela mesma razão. Uma
`IntegrityError` no meio de uma projeção nunca deveria virar 500 para o Entra.

### Nota de escopo (S4)

`SCIMGroupDetailView.get_unit` (`orca_scim/groups.py:140-145`) diz no docstring
"*Fetch a **directory-bound** unit*", mas o queryset é
`OrganizationalUnit.objects.filter(workspace_id=..., pk=unit_id)` — sem checar
`external_id` nem vínculo com o diretório. Um `PATCH` do Entra endereçando o
UUID de uma área criada à mão consegue escrever
`OrganizationalDirectoryGroupMembership` nela e, via `project_unit`, criar
memberships `sync_source=scim`. Não é escalada (o token SCIM já pode criar
grupos), mas o código não faz o que o docstring diz, e um erro de configuração
do lado do Entra alcança áreas manuais.

---

<a id="a11"></a>
## 11. A11 — S3 · `workload/` e o ranking discordam sobre o que é carga (D4 sobreviveu)

**Onde.** `app/services/orca/assignment_engine.py:182-200`
(`workload_snapshot`) contra `assignment_service.py:282-305` (`_load_counts`).

O defeito D4 do RFC §2.2 é "carga conta qualquer assignee". Ele foi fechado **no
ranking**: `_load_counts` conta apenas `IssueOrganizationalUnit` com
`routing_state=ASSIGNED` e `primary_executor_id` na lista, e o docstring explica
por quê — "*a collaborator left on an item from an earlier assignment is not the
person answerable for it*".

`workload_snapshot`, que é o que a rota `workload/` devolve, continua contando
`IssueAssignee`:

```python
load = IssueAssignee.objects.filter(
    assignee_id__in=[...], project_id__in=unit_project_ids
).exclude(issue__state__group__in=CLOSED_STATE_GROUPS).values("assignee_id").annotate(...)
```

E `reassign` mantém o executor anterior como `IssueAssignee` de propósito
(RFC §6.8, `assignment_service.py:805-807`). Logo, toda reatribuição cria uma
discordância permanente entre as duas contas.

**Cenário concreto de falha.** Um item é atribuído a Ana, depois reatribuído a
Bruno. Ana continua `IssueAssignee`.

```
H5 assignees:            [Bruno, Ana]
H5 workload/ snapshot:   {Ana: 1, Bruno: 1}
H5 ranking total_open:   {Ana: 0, Bruno: 1}
```

**Por que isto é da Fase 2 e não uma curiosidade.** A decisão **M5** diz, com
todas as letras: "*Candidatos do 'Atribuir a…' vêm do `workload/` já existente,
não de um endpoint novo de ranking*". Então a tela que o coordenador vai usar
para escolher a quem dar o trabalho vai mostrar a Ana com 1 item aberto que ela
não tem, enquanto o `least_loaded` da mesma área a considera com 0. O
coordenador vai evitar dar trabalho a quem está livre, e vai desconfiar da tela
na primeira vez que os dois números não baterem.

Há ainda uma terceira definição no mesmo arquivo: `_load_counts` conta
`total_open` sobre **todo o workspace** e `unit_open` sobre a área;
`workload_snapshot` conta sobre **os projetos da área**. Três respostas para
"quanto trabalho essa pessoa tem".

**Teste que faltaria.** Um teste que atribua, reatribua, e afirme que
`workload_snapshot` e `rank_candidates` devolvem a mesma contagem por pessoa.

**Correção proposta (não aplicada).** Reescrever `workload_snapshot` sobre
`IssueOrganizationalUnit` com `routing_state=ASSIGNED` e `primary_executor`,
devolvendo os dois números que o ranking usa (`total_open` e `unit_open`) em vez
de um só. Sem isso, M5 deveria ser reaberta e o "Atribuir a…" deveria ler um
endpoint que fale a mesma língua do serviço.

---

<a id="a13"></a>
## 12. Throttle da API pública

**Chave derivada do id, não do segredo — verificado, confirmado.**
`throttles/orca_public.py:49-54`:

```python
token = getattr(view, "api_token", None)
token_id = getattr(token, "id", None)
if token_id is None:
    return None
return f"{self.scope}:{token_id}"
```

O `request.auth` guarda o segredo em claro para essa classe de autenticação, e
a chave de cache usa o `id`. O `None` para chamador não autenticado também está
certo, e pelo motivo certo (não deixar anônimo consumir o balde de um token
real). **Sem achado.**

**O que acontece sem Redis.** `SimpleRateThrottle` usa
`django.core.cache.caches['default']`, que é `django_redis.cache.RedisCache`
(`settings/common.py:258-275`) e não tem `IGNORE_EXCEPTIONS`. Com o Redis fora,
`allow_request` levanta e **toda** requisição da API pública vira 500 — falha
fechada. Para uma API de automação desligada por padrão isso é defensável, mas
não está escrito em lugar nenhum. Vale uma linha no runbook que a Sessão SG
está escrevendo em `docs/orca-public-api.md`: *sem Redis, a API pública não
responde; a fila interna e a interface continuam*.

<a id="a13-body"></a>
### A13 — S3 · Um `ORCA_PUBLIC_API_RATE_LIMIT` malformado é 500 por requisição

**Onde.** `throttles/orca_public.py:47` e `settings/common.py:615`.

```python
ORCA_PUBLIC_API_RATE_LIMIT = os.environ.get("ORCA_PUBLIC_API_RATE_LIMIT", "300/minute")
...
class OrcaPublicThrottle(SimpleRateThrottle):
    rate = settings.ORCA_PUBLIC_API_RATE_LIMIT
```

`SimpleRateThrottle.__init__` chama `parse_rate`, que faz `rate.split('/')`. Um
operador que escreva `ORCA_PUBLIC_API_RATE_LIMIT=300` — a leitura natural de
"limite de taxa" — não recebe erro nenhum no boot: cada requisição da API
pública levanta `ValueError` dentro de `get_throttles()` e vira 500.

**Confirmado em execução:**

```
H12 instantiating the throttle with rate='300': ValueError: not enough values to unpack (expected 2, got 1)
```

**Por que isto contradiz o próprio fork.** Os outros dois interruptores Orca
passam por `env_flag`/`parse_env_flag` (`utils/orca_env.py`), cujo docstring diz
que "*a typo fails the process at boot instead of flipping the switch the wrong
way in production*" — e é essa a lição do P0.14. O terceiro ajuste da mesma
família não tem essa proteção. (O mesmo vale para `SCIM_RATE_LIMIT` e
`SCIM_AUTH_FAILURE_RATE_LIMIT`, `settings/common.py:140,146` — o padrão é
anterior ao Orca, o que reduz a severidade sem tornar o comportamento melhor.)

**Teste que faltaria.** Um teste de settings que instancie `OrcaPublicThrottle`
com cada valor mal formado e espere `ImproperlyConfigured` no import, não
`ValueError` no request.

**Correção proposta (não aplicada).** Um `env_rate("ORCA_PUBLIC_API_RATE_LIMIT",
default="300/minute")` ao lado de `env_flag`, validando `^\d+/(second|minute|
hour|day)$` e levantando `ImproperlyConfigured` — cinco linhas no arquivo que
já existe para isso.

---

<a id="a19"></a>
## 13. Compose e workflows depois do #15

### O que foi verificado, e está certo

**Todas as cinco variáveis `ORCA_*` lidas por `settings/common.py` chegam aos
quatro serviços que compartilham a imagem `api`.** Conferido variável a
variável contra `docker-compose-orca.yml`:

| Variável | api (l.78) | worker (l.133) | beat-worker (l.181) | migrator (l.229) |
| --- | --- | --- | --- | --- |
| `ORCA_ORG_UNITS_ENABLED` | 103 | 157 | 205 | 253 |
| `ORCA_ORG_SYNC_MAX_EDGES` | 104 | 158 | 206 | 254 |
| `ORCA_PUBLIC_API_ENABLED` | 108 | 162 | 210 | 258 |
| `ORCA_PUBLIC_API_RATE_LIMIT` | 109 | 163 | 211 | 259 |
| `ORCA_AUTOMATION_OPERATION_RETENTION_DAYS` | 110 | 164 | 212 | 260 |

O que o P0.19 consertou está consertado, e o P0.20 não deixou variável para trás.

**Hipótese testada e derrubada.** O Compose usa os defaults `:-1` e `:-0`
(`ORCA_ORG_UNITS_ENABLED=${ORCA_ORG_UNITS_ENABLED:-1}`), enquanto `env_flag` é
descrito como "*strict parser*" que "*refuses anything else at startup*". Se
`"1"` e `"0"` não estivessem entre as grafias aceitas, o deploy padrão não
subiria. Leitura de `utils/orca_env.py:23-24`:
`TRUE_VALUES = {"1","true","yes","on"}`, `FALSE_VALUES = {"0","false","no","off"}`.
**Aceitos. Sem achado.**

<a id="a19-body"></a>
### A19 — S4 · A lista do `compose_env_forwarding` é mantida à mão

**Onde.** `.github/workflows/stage.yml:285-288`.

O job faz duas verificações, e as duas são boas:

1. Cada variável de **uma lista literal** de sete nomes tem de aparecer tantas
   vezes quantos são os serviços na imagem `api` (`:285-294`).
2. Cada variável da **tabela de configuração do README** tem de aparecer no
   Compose de alguma forma (`:296-311`).

O que nenhuma das duas faz é a direção que causou o P0.19: **do código para o
Compose**. Uma variável nova lida por `settings/common.py`, esquecida no
Compose *e* na tabela do README, passa pelas duas verificações sem ruído — que é
literalmente o que aconteceu com `ORCA_PUBLIC_API_ENABLED`. A guarda criada para
eliminar um passo manual depende de um passo manual (editar a lista literal).

Hoje o descasamento existente é inofensivo: `ORCA_ORG_SYNC_MAX_EDGES` está no
Compose e na lista literal mas **não** na tabela do README, e `ORCA_SERVICE_NAME`
(lido por `utils/orca_build_info.py:35`) está no Compose e em nenhuma das duas.

**Teste que faltaria.** Um passo do job que derive a lista do código —
`grep -ohE 'ORCA_[A-Z0-9_]+' apps/api/plane/settings/*.py apps/api/plane/utils/*.py | sort -u` —
e falhe para qualquer nome que não apareça no Compose. Custa cinco linhas e
torna o job independente de memória humana.

---

## 14. Achados menores

<a id="a17"></a>
### A17 — S4 · Kill switch do SCIM antes da autenticação

`orca_scim/base.py:241-249`. Com a camada desligada, um chamador **sem token
nenhum** recebe 404 com `"The organizational layer is disabled on this
instance"`; com a camada ligada, recebe 401. A diferença entre as duas respostas
diz a um anônimo o estado do interruptor — e o comentário logo abaixo, em
`authenticate_directory`, explica que o 401 uniforme existe justamente "*so an
unauthorized caller cannot use the response to learn which workspaces exist or
whether provisioning is enabled on them*". As duas intenções se contradizem no
mesmo arquivo.

Consequência operacional maior que a informacional: como o `raise` acontece
**antes** de `enforce_throttle(SCIMAuthFailureRateThrottle(), ...)`, a rota
desligada não é medida por nada. Com `ORCA_ORG_UNITS_ENABLED=0` — o estado em
que se opera durante um incidente — as URLs SCIM aceitam tráfego ilimitado.

*Correção proposta:* mover a verificação do kill switch para depois de
`authenticate_directory`, ou pelo menos cobrar
`SCIMAuthFailureRateThrottle` antes de levantar o 404.

<a id="a18"></a>
### A18 — S4 · O mesmo na API pública

`api/views/orca/base.py:48-57`. `OrcaPublicApiFeatureMixin.initial` levanta
antes de `super().initial()`, logo antes da autenticação **e** antes de
`check_throttles`. Um anônimo aprende que `ORCA_PUBLIC_API_ENABLED` está
desligado (o corpo diz `ORG_PUBLIC_API_DISABLED`) e o faz sem gastar cota. Aqui
o corpo codificado é uma escolha declarada e defensável — o docstring explica
que integrações precisam distinguir "desligado" de "URL errada". O que não é
escolha é a ausência de throttle.

<a id="a20"></a>
### A20 — S4 · Código de erro reaproveitado fora do seu significado

`api/views/orca/units.py:131-133`: um valor desconhecido em `?routing_state=`
devolve `ORG_INVALID_ROUTING_TRANSITION`. Não houve transição nenhuma — é um
parâmetro de consulta inválido. O §5.2 do plano da madrugada repete essa
escolha para a fila interna. Vale um código próprio, ou um 400 de validação
comum, antes que a Fase 2 a consolide em duas APIs.

### Observações sem severidade

- **`_last_automatic_assignment`** (`assignment_service.py:317-325`) varre
  `AssignmentDecision` por `chosen_assignee_id` ordenando por `-created_at`, sem
  índice composto correspondente. Com um ano de decisões e uma área de vinte
  pessoas, isso é uma varredura por alocação `least_loaded`. Não é defeito hoje
  (a tabela é pequena); é o primeiro lugar a olhar se o Gate 1 medir p95 ruim.
- **`IssueOrganizationalUnit.save()`** (`db/models/organizational_unit.py:565`)
  toca `self.issue` e `self.organizational_unit` em toda gravação, e
  `_locked_link` não faz `select_related`. São duas consultas extras por
  transição de estado.
- **`stash_previous_language`** (`services/orca/signals.py:80-95`) emite um
  `SELECT` em **todo** save de `Profile` da instância, inclusive nos que nada
  têm a ver com idioma. É um sidecar correto pagando um custo global.
- **`eligible.sort`** (`assignment_service.py:409-417`) usa
  `candidate.last_auto_at or timezone.now()` como chave; o ramo `None` já foi
  separado pelo booleano anterior, então o `timezone.now()` nunca decide nada.
  Inofensivo, mas confunde quem lê.

---

## 15. Veredito de gate

Um S1 seguraria o merge do PR de bloco (plano §6.1). **Não há S1.** O que há
muda a ordem de duas coisas.

### Gate P0 — **passa**, com uma ressalva

Os dezoito itens fechados continuam fechados; o #15 fecha o P0.19 e o P0.20 e a
verificação do Compose acima confirma que o P0.19 fez o que diz. A ressalva é
[A3](#a3): o P0.20, entregue para dar teto a uma tabela, apaga em silêncio a
proveniência do registro append-only. **Isso deveria ser corrigido antes de o
beat rodar trinta vezes em produção** — o que dá trinta dias de folga a partir
do deploy, mas nenhum depois disso.

### Gate D0 — **passa**

D1 (cobertura), D2 (herança de assignees), D3 (lock no ranking) e D4 (carga)
estão fechados **no serviço**, e cada um está pinado por teste. A ressalva é que
D4 sobreviveu **fora** do serviço, em `workload/` ([A11](#a11)) — o defeito foi
consertado onde o RFC o descreveu e não onde a interface o lê. Não reabre o D0;
vira item da Fase 2.

### Gate 1 (contrato público) — **passa como contrato, não como algo para ligar**

O contrato faz o que promete: idempotência com recibo, `If-Match` no
`reassign`, envelope de erro, throttle por token com chave derivada do id. Três
achados, porém, são sobre o que acontece quando esse contrato encontra o mundo:

- [A6](#a6) — uma falha transitória queima a chave por 30 dias. Para uma API
  cujo público inteiro são programas que retentam, este é o defeito mais caro
  da lista.
- [A5](#a5) — Guest com API key enumera projetos privados.
- [A12](#a12) — um token queima o espaço de chaves de outro.

**Recomendação:** o Gate 1 pode ser declarado fechado como contrato. O passo 8
da §6 do plano — `ORCA_PUBLIC_API_ENABLED=1` **em produção** — não deveria
acontecer antes de A6 e A5. Em staging, com uma integração conhecida, pode.

### Gate 2-mínimo — **liberado para construir; três coisas para a Sessão SA**

Nada aqui impede escrever a fila, o coordenador e o alerta. Mas três achados
caem exatamente sobre o código que o PR de bloco vai tocar, e sai mais barato
tratá-los agora do que depois:

1. **[A2](#a2) e a decisão M3.** M3 generaliza `_active_sources`/`_sync_grants` e
   declara `baseline_role`/`last_applied_role`/drift "intocados". Com o defeito
   dentro. Se A2 não for corrigido antes, ele passa a valer também para o acesso
   que um coordenador ganha — e "remover o coordenador retira só o que ele ganhou
   por isso", que é a frase que justifica M3, deixa de ser verdade no mesmo
   cenário: rebaixar e restaurar o coordenador transforma a concessão da camada
   em "escolha manual".
2. **[A7](#a7) e a decisão M6.** M6 manda a fila interna reaproveitar
   `queue_queryset`, e F-h manda a pública passar a chamar o `may_see_queue`
   compartilhado. O helper novo é a chance de a regra passar a ser "membro da
   área **e** com acesso ao projeto". Se nascer com a regra de hoje, o
   vazamento vira interface.
3. **[A11](#a11) e a decisão M5.** O "Atribuir a…" vai ler `workload/`. Ou
   `workload_snapshot` passa a contar como o ranking, ou M5 escolhe outra fonte.
   Um coordenador que vê dois números diferentes para a mesma pessoa não confia
   em nenhum.

E um alerta de detalhe para a SA: `policy GET`
(`app/views/organizational_unit.py:742`) está com
`@allow_permission([ADMIN, MEMBER, GUEST], level="WORKSPACE")`. O `policy PUT`
do 2.2 é Admin do workspace (§5.2) e **não** pode herdar esse decorator por
copiar-e-colar.

---

## 16. O que não foi verificado, e por quê

| O quê | Por quê |
| --- | --- |
| Suíte Orca completa como baseline desta ponta | Iniciada (`pytest plane/tests/unit/orca -q -m unit`, 767 selecionados de 1022) e ainda rodando ao fim da sessão; o PR #15 reporta 767 passed em 22m32s. As sondas rodaram num banco separado para não colidir. **Este número é entrega da Sessão SG, não desta.** |
| Comportamento sob carga real | Sem staging e sem banco com dados. Os números de p50/p95 do Gate 1 são da Sessão SG. |
| O que acontece de fato com o Redis fora | A conclusão de §12 é por leitura do backend de cache configurado, não por derrubar o Redis. |
| Frontend (`apps/web`) | Fora da superfície de §4 "SR". |
| `directory_projector.match_workspace_member` e o fluxo Entra ponta a ponta | Só o pedaço que o roteiro pede (Groups PATCH, lead) foi coberto. A resolução de identidade por e-mail/`userName` merece uma revisão própria. |
| `api/views/orca/work_items.py` `_create_issue` / `_announce` | Lidos, sem achado que eu conseguisse tornar concreto; não foram sondados. |
| Se A9 é alcançável em produção | A inversão de lock foi provada nos primitivos e **não** reproduzida pelo caminho real em 12 iterações. Reportada como S3 por isso. |

## 17. Sobre a premissa da sessão

O enquadramento pedia que, se ao fim de três horas nada aparecesse, isso fosse
dito em vez de preenchido com achados fracos. Não foi o caso: seis S2 e nove S3,
onze deles confirmados por execução. Mas vale dizer onde **não** achei nada,
porque isso também é resultado:

- O `select_for_update` cobre de fato todos os caminhos que mudam
  `routing_state` (§2), e a verificação foi exaustiva, não por amostragem.
- Não existe um único `.update(` sobre as tabelas append-only no código Orca
  (§5), e o guarda de instância é real.
- Todas as leituras de recibo usam `all_objects` (§8), coerentes com a
  constraint sem `deleted_at`.
- A chave do throttle deriva do id do token, não do segredo (§12).
- O SCIM aplica os throttles à mão, depois da autenticação, e pelo motivo certo
  (§10).
- Nenhum código Orca escreve `ProjectMember` fora do reconciliador (§1).
- As cinco variáveis `ORCA_*` chegam aos quatro serviços (§13), e a hipótese de
  que `env_flag` recusaria os defaults `1`/`0` do Compose está derrubada.

O padrão dos achados que sobraram é razoavelmente uniforme, e vale registrá-lo
porque diz onde procurar da próxima vez: **quase todos vivem na fronteira entre
o código do fork e o do core, ou entre dois arquivos do fork que se assumem
mutuamente.** A6 é `run_operation` assumindo o que `BaseAPIView.handle_exception`
faz. A2 é o reconciliador assumindo que só humanos escrevem `ProjectMember`.
A3 é a limpeza do P0.20 assumindo que apagar um recibo só afeta o recibo. A1 é a
rota interna não sabendo do guarda que a rota pública documenta em nove linhas.
A11 é o serviço tendo consertado o D4 sem a interface saber. A14 é uma regra do
fork sobrevivendo por causa de um `print()` do core.

Nenhum desses é visível de dentro de um único arquivo — que é exatamente a razão
pela qual a revisão de cada PR, feita contra os critérios de aceite do seu item,
não os pegou.
