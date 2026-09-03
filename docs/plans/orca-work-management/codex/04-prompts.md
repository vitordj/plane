# Prompts do Codex — Fase 4 (Processos automáticos)

Plano da fase: [`../04-processes.md`](../04-processes.md).
Contexto obrigatório: [`00-context.md`](./00-context.md).

Pré-requisito: **Gate 3**, e a pendência A5 (comportamento do Plane Compose)
resolvida antes de 4.1. Ordem: 4.1 → 4.2 → 4.3 → 4.4 → 4.5 → 4.6 → 4.7.

O orquestrador (4.4) **vive fora deste monorepo** (FORK.md §1.B). Aqui só
entra o contrato que ele consome.

| Item | Perfil | Risco |
| --- | --- | --- |
| 4.1 | `scout` | baixo (é leitura e decisão) |
| 4.2 | `standard` | médio (migrações) |
| 4.3 | `heavy` | alto (transação composta + fechamento automático) |
| 4.4 | `standard` | baixo aqui (o repositório só ganha o contrato) |
| 4.5, 4.6 | `standard` | médio |
| 4.7 | `standard` | baixo |

---

## 4.1 — Fechar A5 e decidir o papel do Compose

```text
Trabalho de reconhecimento e decisão, item 4.1 do plano Orca. Você vai ler e
escrever documentação; não altere código de produção.

LEIA ANTES
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §3, decisão F12 (papel do Compose), e §4.1
   (pendência A5).
3. docs/plans/orca-work-management/04-processes.md, seção "4.1".
4. A documentação oficial do Plane Compose (se você tiver acesso à rede; se não
   tiver, diga isso e pare: este item depende de fonte externa).

TAREFA
1. Responda, com citação da fonte, para a base CE 1.4.x: como o Compose autentica;
   quais campos de work item ele controla; o que acontece em re-push com o mesmo id;
   como é o arquivo de estado; existe campo de área ou custom property?
2. Registre as respostas em docs/orca-work-management-rfc.md §4.2 (changelog de
   decisões), com data e link da fonte.
3. Confronte com a decisão F12 (Compose só para schema; instâncias sempre pela API
   pública). Se a leitura CONTRADIZ F12, não decida sozinho: escreva o conflito em
   §4.2 e pare, deixando a decisão para o humano.
4. Entregue docs/orca-compose-notes.md: curto, com a decisão vigente e um exemplo de
   YAML de schema do projeto piloto, se aplicável.

DEFINIÇÃO DE PRONTO
- Nenhuma afirmação sem fonte. "Provavelmente" não entra no documento: ou você
  verificou, ou está listado como pendente.

NÃO FAÇA
- Não implemente integração com o Compose.

AO TERMINAR
- Marque 4.1 [x] (ou deixe [~] se a fonte não estava acessível) e atualize a
  contagem no README do plano.
- Commit: docs(orca): [4.1] record what Plane Compose does and does not cover
- Responda no formato da seção 10 do 00-context.md.
```

---

## 4.2 — Migrações 0141/0142 e flag

```text
Você vai implementar o item 4.2 do plano Orca. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md, seção 5.
2. docs/orca-work-management-rfc.md §5.2 (IssueServiceLevel,
   ProcessInstanceReference, ProcessInstanceItem) e §6.6.
3. docs/plans/orca-work-management/04-processes.md, seção "4.2".
4. apps/api/plane/db/models/organizational_assignment.py e a numeração atual
   das migrações Orca.

TAREFA
1. Arquivo novo apps/api/plane/db/models/organizational_process.py (copyright) com
   os três modelos do RFC §5.2. Exporte em db/models/__init__.py.
   IssueServiceLevel guarda original_assignment_due_at e original_completion_due_at
   IMUTÁVEIS: o save() nunca os altera depois de criados (teste prova).
2. Flag ORCA_PROCESS_PROJECTION_ENABLED em settings/common.py, .env.example e
   apps/api/.env.example, default desligado, exposta em OrcaConfigEndpoint.
3. O serviço D0.5 passa a preencher IssueServiceLevel sempre que assignment_due_at
   ou completion_due_at forem definidos, registrando a fonte
   (unit_project | unit | process | manual).
4. Migrações 0141 e 0142 com dependência encadeada.
5. Testes: unicidades; original_* não mudam em update; completion_mode inválido
   rejeitado; o serviço grava o service level com a fonte certa em cada caminho.

DEFINIÇÃO DE PRONTO
- Desligar a flag não afeta nada do que já existe (teste).
- ruff limpo.

NÃO FAÇA
- Não implemente o bloco process da API aqui (é 4.3).
- Não rode makemigrations/migrate; escreva à mão e avise.

AO TERMINAR
- Marque 4.2 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [4.2] add process instance projection and service level models
- Responda no formato da seção 10 do 00-context.md.
```

---

## 4.3 — Bloco `process`, `complete/` e leitura da instância

```text
Você vai implementar o item 4.3 do plano Orca. Só este item. É o item que fecha
etapa de processo automaticamente: um erro aqui conclui trabalho que não terminou.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §7.2 (bloco process e endpoint complete/), §6.6,
   e o Apêndice B (exemplo de fluxo completo de onboarding).
3. docs/plans/orca-work-management/04-processes.md, seção "4.3".
4. apps/api/plane/api/views/orca/work_items.py (1.4) e o serviço de operação (1.3).
5. Como o projeto modela estados: State, group, sequence — e como a UI muda estado.

TAREFA
1. POST work-items/ passa a aceitar o bloco process (RFC §7.2): get_or_create de
   ProcessInstanceReference e ProcessInstanceItem DENTRO da mesma transação da
   criação. template_version é obrigatório — sem ele, 400.
2. POST .../work-items/{issue_id}/complete/, com Idempotency-Key obrigatória:
   - completion_mode=automatic → move o item para o estado do grupo completed do
     projeto: o primeiro por sequence, ou o configurado em
     OrganizationalUnitAssignmentPolicy.completed_state (campo novo opcional nesta
     migração);
   - automatic_with_review → aplica o estado de revisão configurado, ou a label
     aguardando-validacao (criada sob demanda no projeto, idempotente). NÃO conclui;
   - manual → 409, com mensagem dizendo que a conclusão é humana.
   Registra ProcessCompletionEvent (tabela pequena append-only desta fase:
   issue, source, event_id, rule_version, evidence JSON, mode, created_at).
   NÃO grava AssignmentDecision: concluir não é alocar.
3. GET /api/v1/orca/workspaces/{slug}/process-instances/{source}/{instance_id}/:
   itens com estado nativo, routing_state, executor, SLA e completion_mode; status
   derivado (completed quando todos os itens estão em grupo completed ou cancelled).
4. Quando o último item conclui, marcar ProcessInstanceReference.completed_at.
5. Testes: instância com 4 etapas; replay do mesmo evento não duplica etapa nem
   evento; complete em modo manual → 409; automatic_with_review não conclui;
   a leitura reflete estado alterado pela UI (não por cache).

DEFINIÇÃO DE PRONTO
- Nenhum caminho conclui item em modo manual.
- A criação da instância é atômica com a criação do item (teste com rollback).
- ruff limpo.

NÃO FAÇA
- Não crie estado nem label fora do projeto do item.
- Não mexa em apps/api/plane/app/views/issue/.

AO TERMINAR
- Marque 4.3 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [4.3] project process instances and close steps by rule
- Responda no formato da seção 10 do 00-context.md.
```

---

## 4.4 — Contrato do orquestrador sidecar

```text
Você vai implementar o item 4.4 do plano Orca — a parte que cabe a ESTE repositório.
O orquestrador em si mora em repositório próprio (FORK.md §1.B): não o construa aqui.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. FORK.md §1.B (arquitetura sidecar).
3. docs/plans/orca-work-management/04-processes.md, seção "4.4".
4. docs/orca-public-api.md (1.7) e tools/orca-client/orca_client.py.
5. docs/orca-work-management-rfc.md §7 e §6.7.

TAREFA
Escrever docs/orca-orchestrator-contract.md, com:
1. O que o orquestrador PODE assumir da API: idempotência por chave, semântica de
   replay, ordem das operações, o que é atômico, o que dispara webhook nativo.
2. O que ele NÃO pode assumir: ordem de entrega de webhook, que o executor não muda
   entre duas chamadas, que a área ainda cobre o projeto, que o item não foi
   transferido por gente.
3. A fórmula obrigatória da Idempotency-Key: f"{source}:{instance}:{step}:{event_id}",
   e por que ela precisa ser determinística.
4. O formato de template YAML esperado: name, version, steps[] {key, title, unit,
   project, assignment, completion_mode, assignment_sla, completion_sla, depends_on[]}.
5. Como depends_on se materializa: relações nativas blocked_by pela API v1, ou
   criação tardia da etapa quando a anterior conclui — com o trade-off de cada uma.
6. WEBHOOK_ALLOWED_HOSTS do fork precisa incluir o host do orquestrador: diga onde
   se configura.
7. A lista de testes de contrato que o orquestrador deve passar contra staging antes
   de ser considerado pronto.
8. Runbook do lado do orquestrador: parar/religar sem inconsistência, reprocessar
   uma instância pela metade.

DEFINIÇÃO DE PRONTO
- Um time que nunca viu este repositório consegue escrever o orquestrador só com
  este documento e a doc da API pública.
- Nenhuma afirmação sobre a API que não seja verificável no código de 1.4/1.5/4.3.

NÃO FAÇA
- Não crie o serviço orquestrador dentro do monorepo.
- Não altere código de produção neste item.

AO TERMINAR
- Marque 4.4 [x] e atualize a contagem no README do plano.
- Commit: docs(orca): [4.4] specify the orchestrator contract against the public API
- Responda no formato da seção 10 do 00-context.md.
```

---

## 4.5 — Webhooks e retorno

```text
Você vai implementar o item 4.5 do plano Orca. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/plans/orca-work-management/04-processes.md, seção "4.5".
3. apps/api/plane/bgtasks/webhook_task.py e o serializer de payload de webhook.
4. apps/api/plane/api/views/orca/work_items.py (1.4) — a chamada de issue_activity
   em on_commit.

TAREFA
1. Verifique, por leitura e por teste, que a criação via /api/v1/orca/ dispara os
   webhooks nativos de issue, e que o payload inclui external_source e external_id.
   Relate o que encontrou ANTES de mudar qualquer coisa.
2. Se o payload não trouxer o contexto Orca necessário, enriqueça com
   orca: {unit_slug, routing_state, primary_executor} por uma extensão lateral
   (hook/registry), SEM reescrever o serializer nativo além do ponto de extensão.
   Comente o override no padrão do fork.
3. Teste: criação pela API pública gera evento de webhook com o payload esperado
   (mocke o envio); rollback não gera evento.

DEFINIÇÃO DE PRONTO
- O consumidor do webhook consegue identificar a instância de processo e a área
  sem uma segunda chamada à API (ou está documentado por que precisa dela).
- ruff limpo.

NÃO FAÇA
- Não altere o contrato dos webhooks nativos para quem já os consome: só acrescente.

AO TERMINAR
- Marque 4.5 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [4.5] carry unit context in the native issue webhook payload
- Responda no formato da seção 10 do 00-context.md.
```

---

## 4.6 — Agrupamento visual por instância

```text
Você vai implementar o item 4.6 do plano Orca. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/plans/orca-work-management/04-processes.md, seção "4.6" (e a decisão aberta
   A6, que trata do Module nativo).
3. A aba de fila e a página Minha Área (2.3).

TAREFA
1. Fila e "Minha Área" agrupam por ProcessInstanceReference quando ela existe: grupo
   colapsável com progresso n/m. Itens sem instância continuam na lista plana.
2. OPCIONAL (A6), só se a decisão estiver fechada no RFC §4.2: quando todos os itens
   da instância estão no mesmo projeto, criar um Module nativo por instância e
   vincular os itens, idempotente pelo external_id do módulo, atrás de um flag de
   política por área↔projeto (project_module_per_instance).
   Se A6 ainda estiver aberta, NÃO implemente: registre e siga.
3. i18n das strings novas em todas as locales; testes de store e de componente.

DEFINIÇÃO DE PRONTO
- O agrupamento não muda o número de itens exibidos nem a ordenação dentro do grupo.
- check:lint/check:types/check:sync — comandos para o desenvolvedor.

NÃO FAÇA
- Não crie Module nativo sem a decisão A6 fechada.

AO TERMINAR
- Marque 4.6 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [4.6] group queue items by process instance
- Responda no formato da seção 10 do 00-context.md.
```

---

## 4.7 — Runbook e testes de fechamento

```text
Você vai implementar o item 4.7 do plano Orca. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/plans/orca-work-management/04-processes.md, seções "4.7" e "Gate 4".
3. docs/orca-orchestrator-contract.md (4.4) e o código de 4.3.

TAREFA
1. docs/orca-processes-runbook.md: desligar o orquestrador; religar; reprocessar uma
   instância; corrigir uma instância manualmente; desligar
   ORCA_PROCESS_PROJECTION_ENABLED e o que continua funcionando (tudo, menos o bloco
   process e o complete/). Cada passo com comando ou tela, nada de "faça o ajuste".
2. Teste de contrato: reprocessar os MESMOS 20 eventos duas vezes → contagens
   idênticas de Issue, ProcessInstanceItem, ProcessCompletionEvent e AssignmentDecision.
3. Teste de contrato: falha injetada na etapa 3 de 4 → o replay completa a instância,
   sem duplicar as etapas 1 e 2.

DEFINIÇÃO DE PRONTO
- Os dois testes de contrato existem e são determinísticos.
- O runbook foi escrito olhando o código, não o plano.

NÃO FAÇA
- Não feche o Gate 4 (é humano): liste o que falta, incluindo o onboarding piloto
  de ponta a ponta em staging.

AO TERMINAR
- Marque 4.7 [x] e atualize a contagem no README do plano.
- Commit: test(orca): [4.7] prove process replay is idempotent, and add the runbook
- Responda no formato da seção 10 do 00-context.md.
```
