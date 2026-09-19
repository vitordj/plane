# Fase 4 — Processos automáticos

**Objetivo:** instanciar processos recorrentes (onboarding, rotinas) a
partir de eventos externos, com templates versionados fora do Plane, projeção
mínima da instância dentro do Orca, SLA lateral e fechamento automático por
etapa.
**Pré-requisitos:** Gate 3. Pendência A5 (Compose) resolvida antes de 4.1.
**Referência:** RFC §5.2 (`IssueServiceLevel`, `ProcessInstanceReference`,
`ProcessInstanceItem`), §6.6, §7.2 (bloco `process`, `complete/`), F12, F19,
F20, F21, F22, Apêndice B.

> Os itens **4.2 e 4.3** foram entregues com o Gate 3 ainda aberto (só
> falta staging + piloto). Nada neles depende do gate para estar correto —
> são modelos, uma migração, uma flag e a superfície pública —, mas o
> pré-requisito continua valendo para o **Gate 4**.

---

## 4.1 — Fechar A5 e decidir o papel do Compose `[x]`

- Lida a documentação oficial do Plane Compose (`https://developers.plane.so/dev-tools/plane-compose`, PyPI `0.5.2`) e registrada em [`docs/orca-compose-notes.md`](../../orca-compose-notes.md).
- Autenticação: connection `(server, pat\|workspace, token)` em `~/.config/plane-compose/`, nunca em `plane.yaml`.
- Re-push com o mesmo `id` local atualiza o mesmo work item remoto; sem `id`, a chave derivada do conteúdo pode duplicar.
- Não há campo de área nem custom property na CE 1.4.x. Compose não consegue carregar a área.
- Decisão (F12, confirmada): Compose só para schema (estados, labels, tipos) versionado em Git; instâncias sempre pela API pública. RFC §4.2 rev. 14.

---

## 4.2 — Migrações 0142/0143 e flag `[x]`

- `IssueServiceLevel`, `ProcessInstanceReference`, `ProcessInstanceItem` (RFC §5.2) em `organizational_process.py`; exportar.
- `ORCA_PROCESS_PROJECTION_ENABLED` em settings e `.env.example`.
- `IssueServiceLevel` passa a ser preenchido pelo serviço D0.5 sempre que `assignment_due_at`/`completion_due_at` chegam (fonte `unit_project`/`unit`/`process`/`manual`), com `original_*` imutáveis.

**Testes:** unicidades; `original_*` não mudam em update; `completion_mode`
inválido rejeitado.

---

## 4.3 — Bloco `process`, `complete/` e leitura da instância `[x]`

- `POST work-items/` aceita `process` (RFC §7.2): `get_or_create` de `ProcessInstanceReference` e `ProcessInstanceItem` dentro da mesma transação; `template_version` obrigatório.
- `POST .../work-items/{issue_id}/complete/`: RFC §7.2 — `automatic` move para o estado do grupo `completed` do projeto (o primeiro por `sequence`, ou o configurado em `OrganizationalUnitAssignmentPolicy.completed_state` — campo novo opcional nesta migração); `automatic_with_review` aplica o estado de revisão configurado ou a label `aguardando-validacao` (criada sob demanda no projeto); `manual` → 409. Registra `AssignmentDecision`? Não: registra `ProcessCompletionEvent` (tabela pequena append-only nesta fase: `issue, source, event_id, rule_version, evidence JSON, mode, created_at`). `Idempotency-Key` obrigatório.
- `GET /api/v1/orca/workspaces/{slug}/process-instances/{source}/{instance_id}/`: itens com estado nativo, `routing_state`, executor, SLA, `completion_mode`; `status` derivado (`completed` quando todos os itens estão em grupo `completed`/`cancelled`).
- Quando o último item conclui, marcar `ProcessInstanceReference.completed_at`.

**Testes:** instância com 4 etapas; replay do evento não duplica; `complete`
em `manual` → 409; `automatic_with_review` não muda para concluído; leitura
reflete estado nativo alterado pela UI.

---

## 4.4 — Orquestrador sidecar `[ ]`

**Fora deste monorepo.** Esta sessão não abre o item: o orquestrador é um
repositório próprio, e o contrato (`docs/orca-orchestrator-contract.md`) só
faz sentido quando esse repo existir. O runbook de 4.7 e o sidecar de
webhook de 4.5 são o que esse repo poderá assumir.

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

---

## 4.5 — Webhooks e retorno `[x]`

- A criação via `/api/v1/orca/` já disparava `model_activity` em `on_commit` (os testes de 1.4 pinam isso). O payload nativo inclui `workspace_slug`, `external_source` e `external_id`.
- Sidecar `data.orca = {unit_slug, routing_state, primary_executor}` (ou `null`) em `get_model_data`, sem alterar `IssueExpandSerializer`. Ver `services/orca/webhook_payload.py`.
- `WEBHOOK_ALLOWED_HOSTS` / `WEBHOOK_ALLOWED_IPS` já existem no fork; o runbook diz ao operador para incluir o host do orquestrador.

**Testes:** item com área nomeia a área; item sem área tem `orca: null`; `external_source`/`external_id` viajam no serializer nativo.

---

## 4.6 — Agrupamento visual `[x]`

- Fila interna e pública, e portanto a aba Trabalho / Minha Área, carregam `process: {source, instance_id, template_name, step_key, done, total}` (ou `null`). `n/m` é o progresso da instância, não o da página.
- A lista agrupa por `ProcessInstanceReference` (colapsável, aberto por omissão).
- A6: módulo nativo por instância **não** entra na v1 (F20). Reabrir se um piloto de um só projeto precisar da vista de Module.

---

## 4.7 — Runbook e testes de fechamento `[x]`

- [`docs/orca-processes-runbook.md`](../../orca-processes-runbook.md): desligar o orquestrador, religar, reprocessar, corrigir uma instância à mão, desligar `ORCA_PROCESS_PROJECTION_ENABLED`.
- Teste: 20 eventos de criação, reprocessados, deixam as contagens iguais; falha injetada na etapa 3 de 4 (projeto sem estado completed) e replay com chave nova completa a instância.

---

## Gate 4

- [ ] 7 itens `[x]`.
- [ ] Um processo real (onboarding piloto) executado de ponta a ponta em staging pelo orquestrador, com `template_version` registrado em cada item.
- [ ] Desligar e religar o orquestrador durante uma instância não deixou item duplicado nem sem área (`audit_organizational_routing` limpo).

Data do gate: \_\_\_\_
