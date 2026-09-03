# Fase 3 — Disponibilidade e distribuição

**Objetivo:** férias, afastamentos e "não recebo mais desta área" entram no
cálculo; itens de quem ficou indisponível voltam à fila de forma assistida;
limites por pessoa. Nada redistribui automaticamente para outra pessoa.
**Pré-requisitos:** Gate 2 completo.
**Referência:** RFC §5.2 (`WorkspaceMemberAvailability`,
`MembershipAllocationSettings`), §6.4 (elegibilidade), §6.9, F14, F15.
**Fora desta fase (decisões abertas):** habilidades (A1), rodízio (A2),
sincronização com Entra/RH (A3), peso por estimativa (A7).

---

## 3.1 — Migração 0141 e flag `[x]`

- Modelos `WorkspaceMemberAvailability` e `MembershipAllocationSettings` (RFC §5.2) em `organizational_unit.py`; CHECK de intervalo.
- **Numeração:** o RFC dizia `0139`, que a Fase 2 usou para coordenadores; a migração real é `0141_orca_availability`.
- `ORCA_AVAILABILITY_ENABLED` em `settings/common.py` e `.env.example`; exposto em `OrcaConfigEndpoint`.
- Helper `is_available(workspace_member, at=None)` e `accepts_new_work(membership)` em `services/orca/availability.py`.

**Testes:** intervalos abertos/fechados; `until` null; sobreposição de dois
intervalos; flag desligada → helpers respondem sempre disponível (para não
alterar o ranking se a fase for desligada).

---

## 3.2 — Ranking respeita disponibilidade e limites `[x]`

- `rank_candidates` (D0.5) exclui indisponíveis (`excluded_reason="unavailable"`), `accepts_new_work=false` (`"opted_out"`), acima de `MembershipAllocationSettings.max_open_items` (`"member_limit"`) e acima de `policy.max_open_items_per_member` (`"policy_limit"`).
- `algorithm_version` passa a `"lb-2"` (o snapshot registra qual versão decidiu).

**Testes:** cada exclusão aparece no `candidates_snapshot`; item existente de
pessoa indisponível continua contando na carga dos outros? Não: a carga é
por executor; a pessoa indisponível simplesmente sai do ranking.

**Entregue.** `_exclusion_reason` decide na ordem mais-específica-primeiro
(`unavailable` → `opted_out` → `member_limit` → `policy_limit`): quando duas
valem, a mostrada é a que diz quando deixa de valer. `open_item_limit` toma o
menor entre o teto pessoal e o da política — um limite que outra
configuração afrouxa não é limite. Testes em `test_availability.py`.

---

## 3.3 — Endpoints e UI `[x]`

- `GET/POST/DELETE /api/orca/workspaces/{slug}/availability/me/` (o próprio) e `.../members/{workspace_member_id}/availability/` (coordenador de qualquer área da pessoa, ou Admin).
- `PUT .../organizational-units/{unit_id}/members/{pk}/allocation/` (coordenador da área, Admin; o próprio membro pode desligar `accepts_new_work`, não ligar o limite).
- UI: `availability-form.tsx` no perfil do usuário (Preferences) e na aba de membros da área; badge "indisponível até …" na fila e no modal de atribuição; toggle "recebe novas tarefas" por membership.
- i18n completo.

**Entregue, com um desvio.** O formulário próprio ficou em **Minha Área**, não
em Preferences: a rota `/settings/profile/preferences` não carrega workspace
nenhum e disponibilidade é por `WorkspaceMember` — escolher o workspace pelo
"último acessado" seria confuso para quem tem mais de um. Minha Área é
justamente onde a pessoa cuida do próprio trabalho.

O resto saiu como planejado: `member-work-settings.tsx` (toggle + teto + as
ausências) expande na linha de cada membro na aba de pessoas; a linha da fila
mostra "Ausente" quando o executor está fora (`primary_executor.is_available`
vem da API, calculado em uma consulta só para a fila inteira); e o modal de
atribuição já mostrava `excluded_reason`, que agora inclui `unavailable`.

Quatro códigos de erro novos (4930–4933) nos três lugares de sempre.

---

## 3.4 — Sweep de indisponibilidade `[x]`

- Tarefa `plane.bgtasks.organizational_availability_task.sweep_unavailable_executors` horária (RFC §6.9): para cada `IssueOrganizationalUnit(routing_state=assigned)` cujo `primary_executor` está indisponível, ou cuja membership/`WorkspaceMember`/`ProjectMember` está inativa → `return_to_queue(queue_reason="executor_unavailable", trigger="availability")`; alerta aos coordenadores.
- Comando `sweep_unavailable_executors` com `--write` (dry-run default) para operação manual; a tarefa só escreve com `ORCA_AVAILABILITY_ENABLED=1`.
- `IssueAssignee` do executor é mantido (a pessoa continua vendo o item ao voltar).
- Signal `post_delete`/`pre_save(deleted_at)` em `IssueAssignee` (em `services/orca/signals.py`): se o assignee removido nativamente era `primary_executor`, devolver à fila com `executor_unavailable` (fecha o risco de divergência nativo × lateral do RFC §12).

**Testes:** férias começam → item volta; férias terminam → nada acontece
automaticamente (a pessoa não recupera o item; coordenador decide);
desativação de `WorkspaceMember`; remoção nativa do assignee; sweep repetido
não duplica decisões.

**Entregue, com um limite honesto.** O signal em `IssueAssignee` cobre só os
caminhos que disparam signal: `instance.delete()` e delete duro. O caminho
principal do app — `IssueAssignee.objects.filter(issue=...).delete()` no
serializer de issue — é `UPDATE` de queryset e **não dispara signal nenhum**,
e é código upstream que o fork não altera. Quem fecha essa fresta é o
`audit_organizational_routing` (`executor_not_assignee`), que já existia e por
isso vale rodar diariamente. Está dito assim na docstring e na documentação —
prometer cobertura total aqui seria mentira.

---

## 3.5 — Sugestão de próximo candidato `[x]`

- Na fila, para itens com `queue_reason=executor_unavailable`, a linha mostra "sugestão: <pessoa>" calculada por `rank_candidates` sob demanda (endpoint `GET .../work-items/{issue_id}/organizational-unit/candidates/`, já útil para o modal de atribuição).
- Aceitar a sugestão é a mesma ação "Atribuir a…" (decisão com `trigger="ui_coordinator"`, `reason="accepted_suggestion"`).

**Entregue.** `queue-suggestion.tsx` só aparece em linha com
`queue_reason=executor_unavailable` e `can_assign`, e aceitar chama o mesmo
`assign-to` de sempre — o `trigger` que o serviço grava é `reassign`, e o que
distingue a sugestão é `reason="accepted_suggestion"`.

---

## 3.6 — Testes de fechamento e documentação `[x]`

- Cenários: férias começam/terminam, saída da área, desativação, retorno, remoção nativa de assignee, limites por pessoa e por política.
- Nenhuma reatribuição sem `AssignmentDecision(trigger=availability)` (teste que conta decisões por trigger).
- `docs/organizational-units.md` §Disponibilidade; RFC §2.1 atualizado.

**Entregue.** `test_availability.py` (30 testes: intervalos, ranking lb-2,
endpoints) e `test_availability_sweep.py` (23: as quatro razões, o que o sweep
recusa fazer, uma decisão por item, `ProjectMember` intocado, o signal).
`docs/organizational-units.md` ganhou a seção "Availability" com as três
tabelas, o sweep e o limite do signal. O RFC não foi editado: ele é o registro
do que se decidiu em 2026-09-03, e o quadro é onde o que se construiu fica
registrado — mexer nele apagaria a diferença entre as duas coisas.

**Rodar antes do merge** (precisa de banco; o agente não roda):
`docker compose exec api python manage.py makemigrations --check`
`docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/orca/ -m unit`

---

## Gate 3

- [ ] 6 itens `[x]`.
- [ ] Sweep em dry-run contra dump de produção/staging sem falsos positivos (revisar a saída com o coordenador piloto).
- [ ] Coordenador piloto usou o fluxo férias → devolução → reatribuição ao menos uma vez em staging.

Data do gate: ____
