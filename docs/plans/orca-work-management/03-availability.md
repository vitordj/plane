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

### Executado em 06/09 — antes do gate, e o que saiu diferente

**A fase foi implementada com o Gate 2 aberto**, pelo mesmo motivo que a Fase 1
começou antes dos seus gates: foi o que se pediu. O gate continua sendo o que
libera o uso real — uma semana de fila em produção pela área piloto não é algo
que código feche.

Quatro desvios do enunciado, nenhum reabrindo decisão fechada:

1. **A migração é a `0140`**, não a `0139`: a Fase 2 entrou antes e levou o
   número anterior.
2. **`ORCA_AVAILABILITY_ENABLED` nasce desligada**, e desligada o
   comportamento é exatamente o da Fase 2 — todo mundo disponível, todo mundo
   aceitando, sem teto, rotas em 404, varredura sem escrita. Uma chave cuja
   posição "off" muda respostas é uma chave que ninguém aciona no meio de um
   incidente.
3. **O teto por pessoa e o teto da área viraram motivos distintos** no
   `candidates_snapshot` (`member_limit` × `policy_limit`), no lugar do
   `at_max_open_items` único: são respostas diferentes para "por que ela foi
   pulada?". Registrado no RFC §4.2 (rev. 6).
4. **A varredura ganhou um quinto motivo** — executor retirado do item pelo
   campo nativo de assignees. O sinal do 3.4 cobre a remoção de uma linha; o
   caminho da própria aplicação apaga assignees como queryset e não dispara
   sinal nenhum, então sem isso a divergência do RFC §12 continuaria aberta
   entre uma execução do comando de auditoria e outra.

E um ajuste de lugar: o formulário de disponibilidade do 3.3 ficou na página
**Minhas áreas** e na aba de membros da área, não em _Preferences_ do perfil.
Uma ausência pertence a uma `WorkspaceMember`, e a tela de perfil não tem
workspace no escopo — colocá-la lá exigiria escolher um workspace por conta
própria.

---

## 3.1 — Migração 0140 e flag `[x]`

- Modelos `WorkspaceMemberAvailability` e `MembershipAllocationSettings` (RFC §5.2) em `organizational_unit.py`; CHECK de intervalo.
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

---

## 3.3 — Endpoints e UI `[x]`

- `GET/POST/DELETE /api/orca/workspaces/{slug}/availability/me/` (o próprio) e `.../members/{workspace_member_id}/availability/` (coordenador de qualquer área da pessoa, ou Admin).
- `PUT .../organizational-units/{unit_id}/members/{pk}/allocation/` (coordenador da área, Admin; o próprio membro pode desligar `accepts_new_work`, não ligar o limite).
- UI: `availability-form.tsx` no perfil do usuário (Preferences) e na aba de membros da área; badge "indisponível até …" na fila e no modal de atribuição; toggle "recebe novas tarefas" por membership.
- i18n completo.

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

---

## 3.5 — Sugestão de próximo candidato `[x]`

- Na fila, para itens com `queue_reason=executor_unavailable`, a linha mostra "sugestão: <pessoa>" calculada por `rank_candidates` sob demanda (endpoint `GET .../work-items/{issue_id}/organizational-unit/candidates/`, já útil para o modal de atribuição).
- Aceitar a sugestão é a mesma ação "Atribuir a…" (decisão com `trigger="ui_coordinator"`, `reason="accepted_suggestion"`).

---

## 3.6 — Testes de fechamento e documentação `[x]`

- Cenários: férias começam/terminam, saída da área, desativação, retorno, remoção nativa de assignee, limites por pessoa e por política.
- Nenhuma reatribuição sem `AssignmentDecision(trigger=availability)` (teste que conta decisões por trigger).
- `docs/organizational-units.md` §Disponibilidade; RFC §2.1 atualizado.

---

## Gate 3

- [x] 6 itens `[x]`. Verificado nesta sessão: 58 testes novos (22 de janela e ranking, 31 de rota e varredura, 5 de fechamento), suíte Orca em 918 verdes, `makemigrations --check` limpo, `0140` aplicada → revertida → reaplicada.
- [ ] Sweep em dry-run contra dump de produção/staging sem falsos positivos (revisar a saída com o coordenador piloto). O comando existe e é dry-run por padrão: `python manage.py sweep_unavailable_executors --workspace <slug>`.
- [ ] Coordenador piloto usou o fluxo férias → devolução → reatribuição ao menos uma vez em staging.

Data do gate: \_\_\_\_
