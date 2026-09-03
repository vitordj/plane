# Prompts do Codex — Fase 3 (Disponibilidade e distribuição)

Plano da fase: [`../03-availability.md`](../03-availability.md).
Contexto obrigatório: [`00-context.md`](./00-context.md).

Pré-requisito: **Gate 2 completo**. Ordem: 3.1 → 3.2 → 3.3 → 3.4 → 3.5 → 3.6.
Regra que atravessa a fase inteira: **nada é redistribuído automaticamente
para outra pessoa**. Indisponibilidade devolve à fila; quem decide o próximo
executor é gente.

Fora desta fase (decisões abertas do RFC §4.1): habilidades (A1), rodízio
(A2), sincronização com Entra/RH (A3), peso por estimativa (A7). Se um
prompt parecer pedir isso, não é: pare e reporte.

| Item | Perfil | Risco |
| --- | --- | --- |
| 3.1 | `standard` | baixo |
| 3.2 | `heavy` | médio (muda o ranking já em produção) |
| 3.3 | `standard` | médio (API + UI + i18n) |
| 3.4 | `heavy` | alto (mexe em item já atribuído, e por signal) |
| 3.5, 3.6 | `standard` | baixo |

---

## 3.1 — Migração 0139 e flag

```text
Você vai implementar o item 3.1 do plano Orca. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md, seção 5.
2. docs/orca-work-management-rfc.md §5.2 (WorkspaceMemberAvailability e
   MembershipAllocationSettings), §6.9, decisões F14 e F15 em §3.
3. docs/plans/orca-work-management/03-availability.md, seção "3.1".
4. apps/api/plane/db/models/organizational_unit.py e a numeração atual das migrações
   Orca (use o próximo número livre; o plano diz 0139, confirme).

TAREFA
1. Modelos WorkspaceMemberAvailability e MembershipAllocationSettings (RFC §5.2) em
   organizational_unit.py, com o CHECK de intervalo (until nulo = indeterminado;
   until < from é inválido).
2. Flag ORCA_AVAILABILITY_ENABLED em settings/common.py, .env.example e
   apps/api/.env.example, default DESLIGADO, exposta em OrcaConfigEndpoint.
3. apps/api/plane/app/services/orca/availability.py (novo): is_available(
   workspace_member, at=None) -> bool e accepts_new_work(membership) -> bool.
   Com a flag desligada, os dois respondem sempre "disponível"/"aceita" — assim
   desligar a fase não altera o ranking.
4. Migração com os dois modelos, dependência explícita na última Orca.
5. Testes: intervalo aberto e fechado; until null; dois intervalos sobrepostos;
   flag desligada neutraliza os helpers.

DEFINIÇÃO DE PRONTO
- Desligar a flag reproduz exatamente o comportamento anterior à fase (teste prova).
- ruff limpo.

NÃO FAÇA
- Não altere rank_candidates aqui (é 3.2).
- Não rode makemigrations/migrate; escreva à mão e avise.

AO TERMINAR
- Marque 3.1 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [3.1] add member availability and allocation settings
- Responda no formato da seção 10 do 00-context.md.
```

---

## 3.2 — Ranking respeita disponibilidade e limites

```text
Você vai implementar o item 3.2 do plano Orca. Só este item. Ele muda o algoritmo
de alocação que já está em produção: cuidado com regressão silenciosa.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §6.4 (ranking e elegibilidade) e §6.9.
3. docs/plans/orca-work-management/03-availability.md, seção "3.2".
4. apps/api/plane/app/services/orca/assignment_service.py — rank_candidates (D0.5).
5. apps/api/plane/app/services/orca/availability.py (3.1).

TAREFA
1. rank_candidates passa a excluir, cada um com seu excluded_reason no snapshot:
   - indisponível → "unavailable";
   - accepts_new_work=False → "opted_out";
   - acima de MembershipAllocationSettings.max_open_items → "member_limit";
   - acima de policy.max_open_items_per_member → "policy_limit".
2. algorithm_version passa de "lb-1" para "lb-2". O snapshot registra a versão que
   decidiu — decisões antigas continuam legíveis como lb-1. Não reescreva histórico.
3. A carga continua sendo contada por executor principal: a pessoa indisponível
   simplesmente sai do ranking, e os itens dela NÃO viram carga de outra pessoa.
4. Testes: um por motivo de exclusão, verificando que o motivo aparece no
   candidates_snapshot; um teste de que ninguém elegível → allocation_failed com
   no_eligible_member; um de que a flag desligada devolve exatamente o ranking lb-1
   (compare com o teste de D0.5).

DEFINIÇÃO DE PRONTO
- Determinismo mantido (desempate final por user_id).
- Nenhuma consulta N+1 nova no ranking (é caminho quente; diga como verificou).
- ruff limpo.

NÃO FAÇA
- Não redistribua nada aqui: exclusão do ranking não move item existente (isso é 3.4).

AO TERMINAR
- Marque 3.2 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [3.2] exclude unavailable and over-limit members from ranking
- Responda no formato da seção 10 do 00-context.md.
```

---

## 3.3 — Endpoints e UI de disponibilidade

```text
Você vai implementar o item 3.3 do plano Orca. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §6.9 e §8.
3. docs/plans/orca-work-management/03-availability.md, seção "3.3".
4. apps/api/plane/app/views/organizational_unit.py, urls/orca.py e
   permissions/organizational_unit.py (2.2).
5. A tela de Preferences do usuário em apps/web e a aba de membros da área.

TAREFA
1. Endpoints:
   - GET/POST/DELETE /api/orca/workspaces/{slug}/availability/me/ — o próprio usuário;
   - GET/POST/DELETE .../members/{workspace_member_id}/availability/ — coordenador de
     qualquer área da pessoa, ou Admin do workspace;
   - PUT .../organizational-units/{unit_id}/members/{pk}/allocation/ — coordenador da
     área ou Admin. O próprio membro pode DESLIGAR accepts_new_work, mas não pode
     mexer no limite nem religar por cima da decisão do coordenador. Implemente essa
     assimetria explicitamente e teste os dois lados.
2. UI:
   - availability-form.tsx no perfil do usuário (Preferences) e na aba de membros da área;
   - badge "indisponível até …" na fila e no modal de atribuição;
   - toggle "recebe novas tarefas" por membership.
   Componentes de @plane/ui e @plane/propel; nada de CSS novo.
3. i18n completo em todas as locales, incluindo o formato de data por locale.
4. Testes: permissões dos três endpoints (matriz), a assimetria do accepts_new_work,
   store e um teste de componente do formulário.

DEFINIÇÃO DE PRONTO
- Ninguém consegue editar a disponibilidade de outra pessoa sem ser coordenador da
  área dela ou Admin (teste por papel).
- check:lint/check:types/check:sync — comandos para o desenvolvedor.

NÃO FAÇA
- Não sincronize com Entra/RH (decisão A3, aberta).

AO TERMINAR
- Marque 3.3 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [3.3] let people declare availability and opt out of new work
- Responda no formato da seção 10 do 00-context.md.
```

---

## 3.4 — Sweep de indisponibilidade

```text
Você vai implementar o item 3.4 do plano Orca. Só este item. É o item de maior risco
da fase: ele mexe em itens JÁ atribuídos, inclusive por signal. Um erro aqui devolve
à fila trabalho que estava em andamento.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/orca-work-management-rfc.md §6.9 INTEIRO, §6.1 (I3) e §12 (o risco de
   divergência entre o assignee nativo e o executor lateral).
3. docs/plans/orca-work-management/03-availability.md, seção "3.4".
4. apps/api/plane/app/services/orca/signals.py — os signals Orca que já existem.
5. apps/api/plane/bgtasks/organizational_queue_task.py (2.4) e o padrão de comando
   em db/management/commands/audit_organizational_routing.py (D0.7).

TAREFA
1. Tarefa plane.bgtasks.organizational_availability_task.sweep_unavailable_executors,
   horária: para cada IssueOrganizationalUnit com routing_state=assigned cujo
   primary_executor esteja indisponível, ou cuja membership na área / WorkspaceMember
   / ProjectMember esteja inativa → return_to_queue(queue_reason="executor_unavailable",
   trigger="availability"), e alerta aos coordenadores.
   A tarefa só ESCREVE com ORCA_AVAILABILITY_ENABLED=1; com a flag desligada ela
   roda em dry-run e loga.
2. Comando de gestão sweep_unavailable_executors com --write (dry-run é o default),
   para operação manual.
3. IssueAssignee do executor é MANTIDO: a pessoa continua vendo o item quando voltar.
   Só o primary_executor sai.
4. Signal em services/orca/signals.py: quando um IssueAssignee é removido nativamente
   (post_delete ou pre_save com deleted_at) e aquela pessoa era o primary_executor do
   item, devolver à fila com queue_reason="executor_unavailable". Isso fecha a
   divergência nativo × lateral do RFC §12. Cuidado para o signal não disparar em
   cascata a partir das próprias escritas do serviço — trate explicitamente e explique.
5. Testes:
   - férias começam → item volta à fila;
   - férias TERMINAM → nada acontece automaticamente (a pessoa não recupera o item;
     quem decide é o coordenador). Esse teste é uma decisão de produto: mantenha;
   - WorkspaceMember desativado → item volta;
   - remoção nativa do assignee que era executor → item volta;
   - sweep repetido não duplica decisão;
   - flag desligada → dry-run não escreve nada.

DEFINIÇÃO DE PRONTO
- Nenhum caminho remove IssueAssignee.
- Nenhum laço de signal (prove com o teste de sweep repetido e com um de reatribuição).
- ruff limpo.

NÃO FAÇA
- Não reatribua para outra pessoa automaticamente. Nunca, em nenhum caminho.

AO TERMINAR
- Marque 3.4 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [3.4] return work to the queue when the executor becomes unavailable
- Responda no formato da seção 10 do 00-context.md, com a lista dos gatilhos que
  levam um item de volta à fila.
```

---

## 3.5 — Sugestão de próximo candidato

```text
Você vai implementar o item 3.5 do plano Orca. Só este item. É pequeno.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/plans/orca-work-management/03-availability.md, seção "3.5".
3. rank_candidates em assignment_service.py e o modal de atribuição de 2.3a.

TAREFA
1. Endpoint GET /api/orca/.../work-items/{issue_id}/organizational-unit/candidates/
   devolvendo o ranking sob demanda (eleitos com carga, excluídos com motivo).
   Ele serve tanto à sugestão quanto ao modal "Atribuir a…" — use um só.
2. Na fila, itens com queue_reason=executor_unavailable mostram
   "sugestão: <pessoa>" com o primeiro do ranking.
3. Aceitar a sugestão é a MESMA ação "Atribuir a…", com a decisão registrando
   trigger="ui_coordinator" e reason="accepted_suggestion".
4. i18n das strings novas em todas as locales. Testes de endpoint (permissão +
   conteúdo) e de store.

DEFINIÇÃO DE PRONTO
- A sugestão nunca atribui sozinha: é só exibição até alguém clicar.
- ruff limpo.

NÃO FAÇA
- Não crie um segundo caminho de ranking: reutilize o do serviço.

AO TERMINAR
- Marque 3.5 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [3.5] suggest the next candidate for unavailable executors
- Responda no formato da seção 10 do 00-context.md.
```

---

## 3.6 — Testes de fechamento e documentação

```text
Você vai implementar o item 3.6 do plano Orca. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/plans/orca-work-management/03-availability.md, seções "3.6" e "Gate 3".
3. docs/organizational-units.md e docs/orca-work-management-rfc.md §2.1.

TAREFA
1. Cenários de ponta a ponta, um teste cada: férias começam; férias terminam; a
   pessoa sai da área; WorkspaceMember desativado; a pessoa volta; assignee removido
   nativamente; limite por pessoa atingido; limite por política atingido.
2. Teste que conta decisões por trigger: nenhuma reatribuição acontece sem
   AssignmentDecision(trigger="availability") quando a origem é o sweep.
3. docs/organizational-units.md: seção "Disponibilidade" — o que a pessoa declara, o
   que acontece com os itens dela, o que NÃO acontece (ninguém recebe o trabalho
   automaticamente).
4. RFC §2.1 atualizado.

DEFINIÇÃO DE PRONTO
- Os oito cenários existem, nomeados de forma reconhecível.
- A doc diz explicitamente o que o sistema não faz.

NÃO FAÇA
- Não feche o Gate 3 (é humano): liste o que falta, incluindo o sweep em dry-run
  contra dump de staging revisado com o coordenador piloto.

AO TERMINAR
- Marque 3.6 [x] e atualize a contagem no README do plano.
- Commit: test(orca): [3.6] cover the availability scenarios end to end
- Responda no formato da seção 10 do 00-context.md.
```
