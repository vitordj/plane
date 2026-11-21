# R2 — Achados da revisão de 21/11 (Áreas no uso diário)

**O que é este arquivo.** Revisão da `stage` em `3d894760` (depois dos PRs #45
e #46), feita com a aplicação rodando no contêiner da sessão: API, worker,
PostgreSQL, Redis e web, com um workspace de demonstração de três áreas, nove
pessoas e dezoito itens criados pelas rotas reais. A pergunta era outra que a
do R1: não "o código está correto?", mas "isto aguenta o dia a dia de um time
que adiciona área como responsável, troca pessoas e nomeia líderes?".

**Como usar.** Igual ao R1: escolha o próximo `[ ]`, marque `[~]` ao começar e
`[x]` ao terminar, no mesmo PR. Título com o identificador:
`fix(orca): [R2.05] a native assignee change reaches the area's routing`.
Regra de aceite do R1 vale aqui: um teste que falha sem a correção e passa com
ela, visto falhar.

**Como ler a coluna Evidência.** `executado` = o cenário foi rodado contra a
aplicação viva e o resultado conferido no banco. `tela` = visto na interface.
`lido` = confirmado só por leitura; tratar como provável até existir o teste.

**O que a revisão verificou e estava certo**, e ninguém precisa reabrir: toda
mutação de área, pessoa, coordenador, vínculo de projeto, política e diretório
é Admin do workspace; todo lookup é escopado pelo slug e os `save()` recusam
linha de outro workspace; piso de papel, baseline e dupla proveniência do
reconciliador; os CHECKs `assigned ⇔ executor`; trava de linha em assumir,
reatribuir e devolver; `expected_decision_id` comparado sob a trava; a visão
executiva (Admin, drill-down limitado aos projetos do leitor); paridade de
i18n e de códigos de erro; os 20 achados do R1 continuam fechados. **Um achado
levantado na leitura não se confirmou na execução** e ficou fora: desvincular
um projeto retira corretamente o acesso do coordenador que não é membro.

**Verificações de base na mesma sessão:** `pytest plane/tests/unit/orca -q` →
**1345 passed** em 9m59s; `makemigrations --check --dry-run` limpo; `pnpm check:types --filter=web` (11 tarefas), `check:lint` (0 erros,
733 avisos), `check:format` e `check:sync` com exit 0. Nenhum achado abaixo é
pego por elas: todos moram em caminho que a suíte não exercita.

---

## Prioridade

1. **R2.01 e R2.02** — acesso. Os únicos dois que são furo de permissão.
2. **R2.05, R2.06 e R2.08** — o mesmo problema visto de três lados: a área não
   ouve o Plane nativo e não escreve no histórico dele. Sem isto a aba
   Trabalho fica errada toda semana, e é o que segura colocar um time na fila.
3. **R2.12 e R2.13** — pequenos, e mudam a primeira impressão.
4. **D1 antes de mexer em papéis** (ver "Decisões de desenho").

## Acesso

- [ ] **R2.01 — S2 · executado.** Papel herdado sobrevive em linha inativa e
      volta sem área. Área ligada a projeto público com `default_role=20`; a pessoa
      (Member do workspace) sai do projeto nativamente e depois da área;
      `ACTION_RELINQUISH_CLAIM` larga a linha `(20, inativa)` como está; ao entrar
      de novo por `users/me/workspaces/<slug>/projects/invitations/`, que só faz
      `is_active=True`, ela volta Admin. Irmão do `0c777e18`, que cobriu só
      `ACTION_DEACTIVATE`. `services/orca/org_unit_reconciler.py:323-330, 589-594`.
      Correção: ao abrir mão de linha inativa cujo papel ainda é o
      `last_applied_role`, gravar `baseline_role` (ou o papel de auto-entrada).
- [ ] **R2.02 — S2 · executado.** O portão de coordenador é contornável por
      qualquer Member do projeto. `transfer/` e `return/` respondem 403
      (`ORG_NOT_UNIT_COORDINATOR`, `ORG_NOT_THE_EXECUTOR`), mas
      `POST organizational-unit/` com outra área transfere e tira o executor; com a
      mesma área e `mode=manual` devolve à fila; `DELETE` remove a área;
      (Esses três executados. Lidos, não executados: `organizational-unit-assign/`
      com `mode=append` reordena e troca o executor, e as rotas públicas
      `reassign/` e `transfer/` exigem só `ProjectEntityPermission`.) `app/views/organizational_unit.py:621-677,
700-755`; `api/views/orca/work_items.py:122`. Depende de D2.
- [ ] **R2.03 — S2 latente · lido.** `ProcessInstanceEndpoint` confere só
      participação no workspace: um Guest com API key lê título, projeto, e-mail do
      executor e prazos de itens de projetos que não abre. Exige
      `ORCA_PUBLIC_API_ENABLED` e `ORCA_PROCESS_PROJECTION_ENABLED`. Mesma classe
      do R1.A5/A7, numa rota posterior. `api/views/orca/work_items.py:730-771`.
      **Fechar antes de ligar a API pública.**
- [ ] **R2.04 — S2 · lido, não executado.** SCIM `PUT` sem `members`, ou
      `PATCH replace` de `members` sem valor/nulo, chega em
      `replace_members(unit, [])` e desativa toda participação SCIM do grupo. O
      `d4f42733` tornou inofensivo o formato equivalente de `remove`; `replace` e
      `PUT` ficaram. `app/views/orca_scim/groups.py:386, 476-477, 559-563`.
      Validar junto com o tenant Azure.

## A fila contra o uso normal do Plane

- [ ] **R2.05 — S2 · executado.** Troca nativa de responsável não chega à
      área. `PATCH issues/<id>/ {"assignee_ids": [...]}` tirando a executora e
      pondo outra pessoa deixa o vínculo `assigned` com a executora antiga. O
      `QuerySet.delete()` do serializer não dispara signal (conhecido), mas a rede
      de segurança não existe em produção: `audit_organizational_routing` não tem
      entrada no `beat_schedule`, e a varredura horária não confere "executor ainda
      é assignee" e está atrás de `ORCA_AVAILABILITY_ENABLED`. O docstring em
      `signals.py:253-259` promete "within the hour". `app/serializers/issue.py:307`,
      `app/views/issue/bulk_operations.py:317`, `celery.py`.
- [ ] **R2.06 — S2 · executado.** Item fechado continua na fila; item
      concluído volta para a fila. Fechar um item `queued` não muda o vínculo e ele
      segue no payload da fila e elegível ao alerta de SLA. Com o executor ausente,
      `sweep_unavailable_executors --write` devolveu à fila um item que ele já
      tinha concluído. Nenhuma das quatro consultas filtra estado do item,
      `archived_at` ou projeto arquivado.
      `bgtasks/organizational_availability_task.py:74-78`,
      `services/orca/routing_audit.py:80-84`,
      `bgtasks/organizational_queue_task.py:51-56`, `services/orca/queue.py:79`.
- [ ] **R2.07 — S2 · executado.** A varredura de ausência desfaz a atribuição
      nominal do coordenador. `reassign/` para alguém dentro de uma janela de
      ausência responde 200 (como `organizational-units.md` promete), e a varredura
      seguinte devolve o item com `executor_unavailable`, de hora em hora.
      `services/orca/assignment_service.py:460-480`. Correção: pular vínculo cuja
      decisão vigente é humana e posterior ao início da janela.
- [ ] **R2.08 — S2 · executado.** O executor escolhido não é avisado e o
      histórico do item fica mudo. `_ensure_assignee` grava `IssueAssignee` direto:
      zero `Notification` para a pessoa e `IssueActivity` só com `created`.
      `services/orca/assignment_service.py:483-489`. Correção: emitir a atividade
      nativa de `assignee_ids` no commit.
- [ ] **R2.09 — S2 · executado.** Quem sai do workspace continua líder e
      coordenador. `DELETE workspaces/<slug>/members/<id>/` deixa a participação
      `(lead, ativa)` e a coordenação ativa; o roster segue listando a pessoa e
      nomear outro líder responde 400 "already has an active lead". Reconvite
      devolve a coordenação na hora. O caminho do diretório trata; o manual não.
      `app/views/workspace/member.py:144-148`,
      `app/views/organizational_unit.py:293-297`.
- [ ] **R2.10 — S2 · executado.** Apagar área com itens não tem guarda nem
      rastro. `DELETE organizational-units/<id>/` responde 204, o vínculo do item é
      soft-deletado em cascata e nenhum `IssueResponsibilityEvent` é escrito (I6).
      `app/views/organizational_unit.py:258-273`. Correção: 409 enquanto houver
      vínculo vivo, ou limpar pelo serviço.
- [ ] **R2.11 — S3 · executado.** `return_to_queue` reinicia `queued_at` e
      mantém `assignment_due_at`: item devolvido depois do prazo nasce vencido. O
      assignee nativo também fica, e `organizational-unit-assign/` depois responde
      "já atribuído". `services/orca/assignment_service.py:573-589, 923-925`.

## Interface

- [ ] **R2.12 — S2 · executado.** Coordenador que não é membro não tem tela.
      `organizational-units/me/` lista só participações; Configurações é Admin. A
      aba Coordenadores diz que ele "não precisa pertencer à área".
      `app/views/organizational_unit.py:569-575`.
- [ ] **R2.13 — S2 · tela.** Botões da linha da fila ficam errados depois de
      cada ação: após "Assumir" a linha segue com "Assumir" e sem "Devolver à
      fila" até recarregar. A store copia `permissions` antigo.
      `apps/web/core/store/orca/organizational-unit.store.ts:653-685`.
- [ ] **R2.14 — S2 · lido.** Doze métodos do serviço fazem
      `throw error?.response` em vez de `error?.response?.data`, e
      `resolveOrcaErrorKey` não acha `error_code`: pessoas, projetos e apagar área
      sempre dizem "tente novamente".
      `apps/web/core/services/orca/organizational-unit.service.ts`.
- [ ] **R2.15 — S2 · tela.** O campo Área só existe na página cheia do item:
      não há na criação, no peek, em colunas nem em filtros; não há "nenhuma"; a
      escolha não mostra o estado (`na fila` / `com Fulano`) nem atualiza os
      responsáveis. `components/orca/organizational-units/issue-unit-property.tsx:127-139`.
- [ ] **R2.16 — S3 · tela.** Miúdos: idade "Agora mesmo" em todo item
      atribuído (`age_seconds` nulo); contador da aba Trabalho em 0 até abrir;
      remover pessoa, coordenador e vínculo com um clique sem confirmação; SLA em
      segundos; datas de ausência em inglês; `work.reason.new_item` em pt-BR como
      "Recém-assumido"; falha de carga da fila igual a fila vazia; 403 do log de
      decisões exibido como "ainda não há"; "Atenção: sem data de entrega" marca
      todo item atribuído sem prazo.

## Menores, lidos e não executados

- [ ] **R2.17** `allocate()` não reconfere a área depois da trava
      (`assignment_service.py:677-681`).
- [ ] **R2.18** Impressão digital da idempotência ignora método e rota
      (`automation_operation.py:208-222`).
- [ ] **R2.19** Varredura de SLA marca `alerted` depois do laço, sem lote, e
      não filtra coordenador desativado (`organizational_queue_task.py:65-73`,
      `alerts.py:54-70`).
- [ ] **R2.20** Rotas de ausência e o receiver de `IssueAssignee` agem com
      `ORCA_ORG_UNITS_ENABLED` desligado (`organizational_availability.py:56-69`,
      `signals.py:184-229`).
- [ ] **R2.21** `IssueCandidatesEndpoint` aberto a qualquer Member; lista de
      projetos da área visível a Guest, projeto secreto incluído
      (`organizational_queue.py:541`, `organizational_unit.py:176-200, 433-440`).
- [ ] **R2.22** Autoridade de coordenador sobrevive a rebaixamento a Guest e a
      área desativada (`permissions/organizational_unit.py:83-100`).
- [ ] **R2.23** Ausência de um dia impossível pela tela
      (`availability-form.tsx:81-82`).

## Decisões de desenho (não são código até serem tomadas)

- **D1 — Líder é rótulo; quem manda é o admin do workspace.** Hoje há Líder
  (um por área, sem permissão), Coordenador (opera a fila, nomeado por admin) e
  Admin (único que mexe em pessoas, projetos e política). A tela diz "liderar
  uma área dá controle sobre a própria área". Proposta: fundir em "líder da
  área", que opera a fila e gerencia pessoas e ausências da própria área, com
  mais de um permitido; vínculo de projeto e papel herdado ficam com o admin.
  Reabre o `lead` de RFC §2.1 e a unicidade do índice
  `org_unit_membership_single_active_lead_per_unit`.
- **D2 — Quem muda a área de um item.** Proposta: qualquer Member do projeto
  dá área a item sem área; trocar ou remover exige coordenador da área atual ou
  admin. Fecha R2.02; RFC §8.1 hoje diz Admin/Member para a rota.
- **D3 — O roteamento termina quando o item fecha.** Concluído ou cancelado
  sai da fila, para de alertar e fica fora das varreduras. Fecha R2.06.
- **D4 — Convidado em área.** Pode ser membro e líder, não pode assumir nem
  coordenar. Ou a área recusa Guest, ou a tela esconde o que ele não pode.
