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

### Executado em 06/09 — antes do gate, e o que saiu diferente

**A fase foi implementada com os Gates 2 e 3 abertos**, pelo mesmo motivo das
duas anteriores: foi o que se pediu. Os gates continuam sendo o que libera o
uso real, e nenhum deles é algo que código feche — um processo de verdade
rodando em staging pelo orquestrador precisa do orquestrador, que é serviço
externo a este monorepo.

**O 4.1 continua aberto, e é o único.** Fechar A5 exige ler a documentação
oficial do Plane Compose; a política de rede deste ambiente bloqueia a saída
para `developers.plane.so`. Não dá para "quase" fechar uma pendência cuja
resposta é o que a documentação diz. A fase inteira foi construída sob F12 sem
depender do resultado — schema por Compose, instâncias sempre pela API — então
o que A5 pode mudar é a nota do Compose, não o que foi entregue.

Quatro desvios do enunciado, nenhum reabrindo decisão fechada (registrados no
RFC §4.2, rev. 7):

1. **Uma migração, a `0141`**, não `0141`/`0142`: as quatro tabelas e os dois
   campos de estado na política entram juntos ou não entram. Metade aplicada é
   um estado que ninguém consegue descrever.
2. **A reivindicação recusada é gravada**: `complete/` num passo `manual`
   responde 409 e ainda assim escreve `ProcessCompletionEvent(applied=false)`.
3. **O `status` da instância é derivado dos itens**, não lido da coluna: quem
   fecha o último passo pela interface encerra a execução tão de verdade quanto
   o orquestrador.
4. **A versão do template congela na instância**: o primeiro passo fixa
   `template_version` para a execução inteira; um passo posterior sob outra
   versão é aceito e registrado em log, sem reescrever a instância.

E o **A6** (módulo nativo por instância) ficou de fora deliberadamente: é uma
decisão de produto sobre o que aparece no projeto de quem não trabalha por
área, não uma lacuna de implementação. O agrupamento por instância já existe na
fila, sem criar `Module` nenhum.

---

## 4.1 — Fechar A5 e decidir o papel do Compose `[ ]`

- Ler a documentação oficial do Plane Compose e registrar no RFC §4.2: autenticação, campos de work item, comportamento de re-push com mesmo id, arquivo de estado, ausência/presença de campo de área ou custom property na base CE 1.4.x.
- Decisão esperada (F12): Compose só para schema (estados, labels, estrutura de projetos) versionado em Git; instâncias sempre pela API pública. Se a leitura contradisser F12, reabrir no RFC antes de prosseguir.
- Entregável: `docs/orca-compose-notes.md` curto com a decisão e um exemplo de YAML de schema do projeto piloto (se aplicável).

**Aberto, e o motivo é acesso, não esforço.** Este ambiente não alcança
`developers.plane.so` (a política de rede bloqueia a saída), e a resposta de A5
é literalmente o que aquela documentação diz. Precisa de alguém com acesso a
ela. Nada do que foi entregue nesta fase depende do resultado: F12 já é a
decisão, e `docs/orca-orchestrator-contract.md` §"Schema, not instances"
registra por que instâncias nunca passam por uma ferramenta que reconcilia
estado declarado.

---

## 4.2 — Migrações 0141/0142 e flag `[x]`

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

## 4.4 — Orquestrador sidecar `[x]`

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

**Entregue: o contrato.** O repositório do orquestrador continua fora deste
monorepo por FORK.md §1.B, e o documento é o que permite escrevê-lo sem
adivinhar — oito garantias que a API dá, seis coisas que ele não pode assumir,
os dois corpos de requisição completos e dez testes de contrato contra staging.
Dois desses dez já rodam aqui como teste unitário (`test_process_replay.py`);
os outros exigem HTTP e um orquestrador de verdade.

---

## 4.5 — Webhooks e retorno `[x]`

- Verificar que a criação via `/api/v1/orca/` dispara os webhooks nativos de `issue` (via `issue_activity` em `on_commit`) e que o payload inclui `external_source`/`external_id` (já existe `workspace_slug` no payload pelo fork).
- Se necessário, enriquecer o payload com `orca: {unit_slug, routing_state, primary_executor}` por um `WebhookPayloadExtension` lateral, sem alterar o serializer nativo além de um hook.

---

## 4.6 — Agrupamento visual `[x]`

- Fila e "Minha Área" agrupam por `ProcessInstanceReference` quando existe (colapsável), mostrando progresso `n/m`.
- Opcional (A6): quando todos os itens da instância estão no mesmo projeto, criar um `Module` nativo por instância e vincular os itens (idempotente por `external_id` do módulo). Só com flag de política por área↔projeto (`project_module_per_instance`).

**A6 ficou de fora**, e é decisão de produto, não lacuna: um `Module` por
instância aparece para todo mundo do projeto, inclusive para quem não trabalha
por área. O agrupamento na fila resolve o problema que a fase tinha ("quatro
itens que são quatro passos de um onboarding não podem ler como quatro coisas
soltas") sem escrever nada no projeto. O progresso `n/m` conta a instância
inteira, passos em outras áreas incluídos.

---

## 4.7 — Runbook e testes de fechamento `[x]`

- `docs/orca-processes-runbook.md`: desligar o orquestrador, religar, reprocessar, corrigir uma instância manualmente, desligar `ORCA_PROCESS_PROJECTION_ENABLED` e o que continua funcionando (tudo, exceto o bloco `process` e `complete/`).
- Teste de contrato: reprocessar os mesmos 20 eventos duas vezes → contagens idênticas; falha injetada na etapa 3 de 4 → replay completa a instância.

---

## Gate 4

- [ ] 6 dos 7 itens `[x]`; **4.1 aberto por falta de acesso à documentação oficial do Compose**, não por trabalho pendente. Verificado nesta sessão: 28 testes novos (25 de projeção e conclusão, 3 de replay), suíte Orca em 946 verdes, `makemigrations --check` limpo, `0141` aplicada → revertida → reaplicada, `check:types`/`check:lint`/`check:format`/`check:sync` limpos e os 11 testes do store do web verdes.
- [ ] Um processo real (onboarding piloto) executado de ponta a ponta em staging pelo orquestrador, com `template_version` registrado em cada item. Exige o orquestrador, que é serviço externo a este monorepo; o que ele precisa passar está em `docs/orca-orchestrator-contract.md`.
- [ ] Desligar e religar o orquestrador durante uma instância não deixou item duplicado nem sem área (`audit_organizational_routing` limpo). O equivalente sem orquestrador está coberto por `test_process_replay.py`; o procedimento está em `docs/orca-processes-runbook.md`.

Data do gate: \_\_\_\_
