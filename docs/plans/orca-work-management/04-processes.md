# Fase 4 — Processos automáticos

**Objetivo:** instanciar processos recorrentes (onboarding, rotinas) a
partir de eventos externos, com templates versionados fora do Plane, projeção
mínima da instância dentro do Orca, SLA lateral e fechamento automático por
etapa.
**Pré-requisitos:** Gate 3. Pendência A5 (Compose) resolvida antes de 4.1.
**Referência:** RFC §5.2 (`IssueServiceLevel`, `ProcessInstanceReference`,
`ProcessInstanceItem`), §6.6, §7.2 (bloco `process`, `complete/`), F12, F19,
F20, F21, F22, Apêndice B.

---

## 4.1 — Fechar A5 e decidir o papel do Compose `[x]`

- Ler a documentação oficial do Plane Compose e registrar no RFC §4.2: autenticação, campos de work item, comportamento de re-push com mesmo id, arquivo de estado, ausência/presença de campo de área ou custom property na base CE 1.4.x.
- Decisão esperada (F12): Compose só para schema (estados, labels, estrutura de projetos) versionado em Git; instâncias sempre pela API pública. Se a leitura contradisser F12, reabrir no RFC antes de prosseguir.
- Entregável: `docs/orca-compose-notes.md` curto com a decisão e um exemplo de YAML de schema do projeto piloto (se aplicável).

**Entregue, e F12 continua de pé — com uma ressalva sobre como.** A
documentação oficial não pôde ser lida: `developers.plane.so` e `plane.so`
estão bloqueados pelo proxy de egress deste ambiente. O que sustenta a decisão
é o que dá para verificar aqui, e é mais forte do que o RFC supunha: o CE
1.4.x não tem modelo de propriedade customizada nenhum, então **área não pode
ser campo de Compose** — ela mora em `IssueOrganizationalUnit`, tabela lateral
que só `/api/orca/` escreve; e a direção do Compose (arquivo local é a
verdade) é incompatível com instâncias vindas de um fluxo de eventos.

As cinco perguntas que exigem a documentação — autenticação, re-push do mesmo
id, arquivo de estado, CE vs Pro, e o que um `plane pull` faz num projeto de
uma área — ficam listadas no arquivo, para quem rodar o primeiro `plane push`
responder. Até lá: Compose na mão, em projeto de rascunho, nunca em CI e nunca
contra projeto coberto por área.

---

## 4.2 — Migração 0142 e flag `[x]`

- `IssueServiceLevel`, `ProcessInstanceReference`, `ProcessInstanceItem` (RFC §5.2) em `organizational_process.py`; exportar.
- `ORCA_PROCESS_PROJECTION_ENABLED` em settings e `.env.example`.
- `IssueServiceLevel` passa a ser preenchido pelo serviço D0.5 sempre que `assignment_due_at`/`completion_due_at` chegam (fonte `unit_project`/`unit`/`process`/`manual`), com `original_*` imutáveis.

**Testes:** unicidades; `original_*` não mudam em update; `completion_mode`
inválido rejeitado.

**Entregue.** As três tabelas + `ProcessCompletionEvent` (append-only) em
`organizational_process.py`, migração **0142** (0141 foi disponibilidade).
`IssueServiceLevel.save` recusa mexer nos `original_*` — a diferença entre o
original e o atual é a única coisa que faz relatório de atraso significar
alguma coisa, então não é responsabilidade de quem chama. O serviço D0.5
grava por `record_from_resolution`, chamado de dentro do `_record`, que é o
único ponto por onde passa toda mudança de estado.

`OrganizationalUnitAssignmentPolicy` ganhou `completed_state` e `review_state`
(ambos opcionais) na mesma migração.

---

## 4.3 — Bloco `process`, `complete/` e leitura da instância `[x]`

- `POST work-items/` aceita `process` (RFC §7.2): `get_or_create` de `ProcessInstanceReference` e `ProcessInstanceItem` dentro da mesma transação; `template_version` obrigatório.
- `POST .../work-items/{issue_id}/complete/`: RFC §7.2 — `automatic` move para o estado do grupo `completed` do projeto (o primeiro por `sequence`, ou o configurado em `OrganizationalUnitAssignmentPolicy.completed_state` — campo novo opcional nesta migração); `automatic_with_review` aplica o estado de revisão configurado ou a label `aguardando-validacao` (criada sob demanda no projeto); `manual` → 409. Registra `AssignmentDecision`? Não: registra `ProcessCompletionEvent` (tabela pequena append-only nesta fase: `issue, source, event_id, rule_version, evidence JSON, mode, created_at`). `Idempotency-Key` obrigatório.
- `GET /api/v1/orca/workspaces/{slug}/process-instances/{source}/{instance_id}/`: itens com estado nativo, `routing_state`, executor, SLA, `completion_mode`; `status` derivado (`completed` quando todos os itens estão em grupo `completed`/`cancelled`).
- Quando o último item conclui, marcar `ProcessInstanceReference.completed_at`.

**Testes:** instância com 4 etapas; replay do evento não duplica; `complete`
em `manual` → 409; `automatic_with_review` não muda para concluído; leitura
reflete estado nativo alterado pela UI.

**Entregue.** Os três, em `test_process_projection.py` (23 testes). O status
da instância é **derivado** dos itens em cada leitura, não contado à medida
que etapas fecham: quem arrasta o cartão na UI não chama `complete/`, e a
corrida tem que ler como terminada mesmo assim.

---

## 4.4 — Orquestrador sidecar `[~]`

Repositório próprio (sugestão: `orca-orchestrator`), fora deste monorepo,
conforme FORK.md §1.B. Escopo mínimo:
- Templates YAML versionados: `name`, `version`, `steps[] {key, title, unit, project, assignment, completion_mode, assignment_sla, completion_sla, depends_on[]}`.
- Consumidor de eventos (webhook do EspoCRM ou fila) com armazenamento de `event_id` processados.
- Cliente da API pública (pode partir de `tools/orca-client/orca_client.py`), `Idempotency-Key = f"{source}:{instance}:{step}:{event_id}"`.
- Criação das etapas respeitando `depends_on` via relações nativas `blocked_by` (API v1 de relações), ou criação tardia quando a etapa anterior conclui (decidir por template).
- Consumo dos webhooks nativos do Plane para reagir a mudança de estado (marcar instância, liberar próxima etapa). `WEBHOOK_ALLOWED_HOSTS` do fork precisa incluir o host do orquestrador.
- Runbook: parar/religar sem inconsistência; reprocessar uma instância pela metade.

Neste monorepo, o entregável é `docs/orca-orchestrator-contract.md`: o que o
orquestrador pode assumir da API (RFC §7), o que não pode, e os testes de
contrato que ele deve passar contra staging.

**O entregável deste repositório está pronto** —
`docs/orca-orchestrator-contract.md`, com seis garantias, cinco não-garantias
e oito testes de contrato. O item fica `[~]` porque o orquestrador em si é
outro repositório: escrevê-lo aqui violaria FORK.md §1.B.

---

## 4.5 — Webhooks e retorno `[x]`

- Verificar que a criação via `/api/v1/orca/` dispara os webhooks nativos de `issue` (via `issue_activity` em `on_commit`) e que o payload inclui `external_source`/`external_id` (já existe `workspace_slug` no payload pelo fork).
- Se necessário, enriquecer o payload com `orca: {unit_slug, routing_state, primary_executor}` por um `WebhookPayloadExtension` lateral, sem alterar o serializer nativo além de um hook.

**A verificação achou um buraco.** A criação por `/api/v1/orca/` chamava
`issue_activity` mas **não** `model_activity` — ou seja, os webhooks nativos
de `issue.created` não disparavam para item criado por automação, que é a
maioria deles. Corrigido em `_track`, espelhando o que
`plane/api/views/issue.py` faz.

O bloco `orca` entra por `orca_webhook_extension` em
`services/orca/webhook_payload.py`, chamado de **uma linha** em
`webhook_task.py` — o serializer nativo não foi tocado, e um sync com o
upstream tem uma linha para reconciliar em vez de um serializer para mesclar.
A função nunca levanta: webhook perdido por causa de bloco opcional seria pior
que bloco ausente.

---

## 4.6 — Agrupamento visual `[~]`

- Fila e "Minha Área" agrupam por `ProcessInstanceReference` quando existe (colapsável), mostrando progresso `n/m`.
- Opcional (A6): quando todos os itens da instância estão no mesmo projeto, criar um `Module` nativo por instância e vincular os itens (idempotente por `external_id` do módulo). Só com flag de política por área↔projeto (`project_module_per_instance`).

**Entregue o obrigatório.** A fila carrega `process` em cada linha (uma
consulta para a fila inteira, não uma por linha) e `queue-process-group.tsx`
agrupa a caixa de entrada por instância, colapsável, com progresso `n/m`.
Quatro etapas de um onboarding mostradas como quatro linhas soltas é como se
atribui a etapa 3 antes da 1.

**A6 continua aberto e por isso o item é `[~]`:** criar `Module` nativo por
instância é decisão de produto (o módulo aparece no menu do projeto, tem
sprint e gráfico próprios — vira ruído se cada instância virar um), e o RFC
marca A6 como "decidir na Fase 4". Sem alguém decidir, construir seria chutar.

---

## 4.7 — Runbook e testes de fechamento `[x]`

- `docs/orca-processes-runbook.md`: desligar o orquestrador, religar, reprocessar, corrigir uma instância manualmente, desligar `ORCA_PROCESS_PROJECTION_ENABLED` e o que continua funcionando (tudo, exceto o bloco `process` e `complete/`).
- Teste de contrato: reprocessar os mesmos 20 eventos duas vezes → contagens idênticas; falha injetada na etapa 3 de 4 → replay completa a instância.

**Entregue.** `docs/orca-processes-runbook.md` (o que cada flag desliga e o
que continua funcionando, religar, reprocessar, corrigir à mão, e o que **não**
editar à mão) e `TestReplayingAWholeRun` com os dois cenários pedidos mais o
que importa mais que os dois: replay não desfaz reatribuição feita por pessoa.

**Rodar antes do merge** (precisa de banco; o agente não roda):
`docker compose exec api python manage.py makemigrations --check`
`docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/orca/ -m unit`

---

## Gate 4

- [ ] 7 itens `[x]`.
- [ ] Um processo real (onboarding piloto) executado de ponta a ponta em staging pelo orquestrador, com `template_version` registrado em cada item.
- [ ] Desligar e religar o orquestrador durante uma instância não deixou item duplicado nem sem área (`audit_organizational_routing` limpo).

Data do gate: ____
