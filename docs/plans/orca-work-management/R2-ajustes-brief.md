# R2 — avaliação das branches F0–F5 e ajustes feitos (brief de retorno)

Data: 2026-11-21. Para o agente que escreveu as branches `fix/orca-r2-*`.

## Onde está o resultado

A branch `claude/laughing-pasteur-uhje1u` contém as seis branches mescladas, na
ordem F0 → F1 → F3 → F4 → F5 → F2. Em cima delas há 14 commits de ajuste. Não
refaça nada disso: parta dessa branch. Ela avança sem conflito a partir da sua
F0.

Dois conflitos foram resolvidos no merge, ambos em
`apps/api/plane/app/views/organizational_unit.py`:

1. **`OrganizationalUnitViewSet.get_queryset`.** Ficaram as duas anotações:
   - `member_count`, que filtra membership ativa e workspace member ativo (F4);
   - `project_count`, que filtra pelos projetos visíveis (F1).
2. **`IssueOrganizationalUnitAssignEndpoint.post`.** A F3 tinha criado a exceção
   `is_waiting_for_executor`, e ela quebrava
   `test_assigning_is_a_no_op_when_the_item_already_has_an_assignee`. Foi
   restaurado `if current_assignees and mode == MODE_FILL_EMPTY:`. O caso
   "item voltou para a fila com responsável residual" deixou de existir, porque
   agora `return_to_queue` remove o executor nativo (veja a seção de decisões).

Também falhava `test_a_revoked_token_does_not_take_the_decisions_with_it`. O
teste foi atualizado: o hunk da F4 em `deletion_task.py` faz a decisão
sobreviver e continuar apontando para a operação, e esse comportamento é o
correto.

## Avaliação por branch (antes dos ajustes)

| Branch | Veredito | Principais problemas encontrados |
|---|---|---|
| F1 access-and-scim | Ok, com ressalvas | R2.03: o progresso contava etapas ocultas, e 403 vs 404 revelava se a execução existia. R2.04: um `replace` com valor que não é lista (`""`, `{}`, `5`) ainda esvaziava o grupo. |
| F2 coordinator-gate | **Bloqueante** | O `POST /api/v1/orca/.../work-items/` público (`_place`) ignorava o gate: um token de Member movia o item de área e derrubava o executor (reproduzido, 201). A rota `assign/` devolvia 403 a Members em itens sem área, contrariando D2. O retorno pelo executor na API pública foi perdido. O gate estava duplicado em 4 lugares. A UI mostrava os controles a todos. |
| F3 queue-native-sync | Parcial | O PATCH da API pública não reconciliava. Uma troca nativa por alguém elegível deixava o link `queued` sem executor enquanto o Plane mostrava a pessoa nova. Notificações com ator `None` e os alertas não tinham `data.issue_activity`, o que **derrubava a página `/notifications`** (reproduzido no navegador). As métricas executivas contavam itens fechados. Reabrir um item mantinha o relógio antigo. O sweep de drift não alertava ninguém. |
| F4 member-and-unit-lifecycle | Ok, com ressalvas | A desativação de conta por `bulk_update` contorna o signal: o slot de líder ficava preso. Os itens de quem saiu continuavam atribuídos a essa pessoa. O `pre_save` fazia uma query extra em todo save. Houve erros de tradução (pt-BR, fr). O delete de área não tinha lock. |
| F5 web-quick-wins | Ok, com ressalvas | A fila piscava o skeleton depois de cada ação. `can_claim` ficava desatualizado. A timeline ficava em branco num erro 5xx. `formatLocalDate` estava duplicado. O locale `ua` é BCP-47 inválido. Dois testes de R2.12 não provavam nada. |

## O que os ajustes fizeram (commits em `claude/laughing-pasteur-uhje1u`)

### Acesso (lote A)

- Um único helper, `may_change_item_responsibility` em
  `app/permissions/organizational_unit.py`, usado pelo POST/DELETE de área,
  pela rota `assign/` e pelas rotas públicas create-or-find, reassign e
  transfer.
  - O create-or-find público agora checa o gate antes de abrir o recibo de
    idempotência.
  - O executor voltou a poder devolver o próprio item pela API pública.
- A rota `assign/` segue D2. Um Member pode rodar a política num item sem área.
  Também pode rodá-la num item da própria área que está esperando sem ninguém
  (queued ou allocation_failed). Isso alinha a rota com o POST irmão.
- Web: o serializer expõe `routing.can_change_responsibility`, e
  `issue-unit-property.tsx` usa esse booleano em vez de deduzir papel no
  navegador.
- R2.03: o progresso conta só as etapas visíveis. Uma execução sem nenhuma etapa
  visível responde 404. Por isso um teste da F1 passou de 403 para 404.
- R2.04: em `replace`, o campo `members` exige uma lista. Uma lista não vazia
  sem nenhum id utilizável dá 400. `[]` explícito continua limpando o grupo.

### Fila e sincronização nativa (lote B)

- Um builder único, `alerts.build_notification_data`, monta o payload no
  formato do `notification_task` upstream, com `issue_activity`.
  - A alocação pelo sistema renderiza "added assignee X".
  - Os alertas renderizam "updated queue alert to …".
  - Conferido no navegador: a caixa de entrada abre.
- Adoção do responsável nativo. Ela só acontece quando existe exatamente um
  responsável elegível e a política permite `manual`, ou `self_claim` se a
  pessoa adicionou a si mesma. A adoção passa por `reassign`, com
  `expected_decision_id`. Uma troca por alguém inelegível devolve o item à fila.
- O PATCH da API pública (`api/serializers/issue.py`) recebeu a mesma seam do
  serializer do app.
- `return_to_queue` remove o `IssueAssignee` do executor devolvido, avisa essa
  pessoa e registra histórico. A exceção é `transfer_unit`, que mantém o
  executor anterior como colaborador (RFC §6.8).
- Quem sai do workspace tem os itens devolvidos à fila, por soft delete ou hard
  delete. O `pre_save` não faz query com a camada desligada nem quando o
  `update_fields` não interessa.
- As métricas executivas e os drill-downs usam `open_work_links`.
- Reabrir um item na fila reinicia `queued_at` e `assignment_due_at`. Isso é
  feito por `pre_save`/`post_save` em `Issue`, no `signals.py` do Orca, sem
  editar upstream.
- O sweep de drift alerta os coordenadores uma vez por item devolvido.

### Ciclo de vida e tela (lote C)

- `retire_stale_unit_ties(unit)` roda antes das checagens de líder no
  create/update de membro. Ele cobre o caso de conta desativada por
  `bulk_update`.
- O delete de área roda sob `select_for_update`, usa `open_work_links` e ignora
  projeto soft-deleted.
- Web:
  - a fila atualiza em silêncio, sem skeleton;
  - os itens otimistas são mantidos se a ação falhar;
  - `can_claim` vem de `viewer.is_member`;
  - a timeline mostra o erro com um botão de tentar de novo;
  - `isForbiddenError` aceita o código 4932;
  - `format-local-date.ts` é compartilhado;
  - `ua` virou `uk`;
  - textos pt-BR e fr corrigidos.
- `GET decisions/` responde 403 com `ORG_NOT_UNIT_COORDINATOR`. Antes era um
  403 genérico.

## Verificação na árvore final

- `pytest plane/tests/unit/orca`: veja o resultado no fim deste arquivo.
- `makemigrations --check`: limpo. `pnpm check:types --filter=web`: 11/11.
  `check:lint`: 0 erros.
- Os novos testes falham sem o fix e passam com ele, nos casos positivos:
  - `test_r2_review_access.py`: 14 de 30;
  - `test_r2_review_queue.py`: 14 de 22;
  - lifecycle: 5 de 6.
  Os demais são guardas de regressão.
- Reteste ao vivo contra a API em execução, com base demo:
  - R2.01, R2.02, R2.06, R2.08, R2.09, R2.10, R2.11 e R2.12 ok;
  - R2.05 agora adota Rafael (elegível) e devolve à fila quando o escolhido é
    Paulo (inelegível);
  - R2.09 devolve à fila o item de quem saiu.
- `/notifications` renderiza os alertas do Orca sem erro.

## Decisões que precisam de confirmação (são de produto, não de código)

1. **Férias tiram a pessoa do item.** O sweep de disponibilidade usa
   `return_to_queue`, que agora remove o responsável nativo. Antes, a pessoa
   ficava no item "para ver quando voltar". Se for preciso manter, passe
   `detach_executor=False` em `return_unusable`
   (`bgtasks/organizational_availability_task.py`). Nesse caso a adoção
   precisa ignorar pessoas indisponíveis.
2. **Regra da adoção.** Ela exige `manual` em `allowed_modes`, ou `self_claim`
   quando a pessoa adicionou a si mesma.
3. **Mudança incompatível na API pública (F2 e ajuste).** Um token de Member
   não move nem reatribui item que já tem área. Também não reatribui o
   executor de outra pessoa. Documente isso no changelog e na doc da API
   pública.
4. **R2.03.** Um Guest ou alguém de fora vê 404, e não 403, numa execução de
   processo sem etapas visíveis.

## Ainda em aberto (próximo trabalho)

- **F6**: o resto de R2.15. R2.16, itens 3 (confirmação ao remover) e 4 (SLA em
  segundos).
- **F7**: D1 e D4. Essas decisões continuam pendentes com o dono do produto.
- Chaves i18n órfãs: `work.attention_no_date` e `form.toast.name_taken`.
- O texto do alerta na caixa de entrada sai em inglês ("updated queue alert
  to …"). Isso vem do card upstream (`content.tsx`, campo `queue_alert` sem
  ramo próprio). Para localizar, abra uma seam mínima e comentada no card, ou
  use `field="assignees"`.
- `routing.can_change_responsibility` vem `null` nas respostas das rotas de
  queue, claim, reassign e transfer, porque o serializer não recebe `request`.
  A UI mantém o último valor conhecido. Passe o `request` no contexto dessas
  views.
- Corrida pequena entre o gate e `_place` no create-or-find público: não há
  lock.
- Reabertura por `bulk_update` (bulk operations) não dispara signal e mantém o
  relógio antigo.
- Bordas de R2.07: com janelas sobrepostas, falta definir qual vale. A
  proteção de "atribuído durante a janela" nunca expira.
- O sweep de drift não tem limite de itens por execução. A reconciliação custa
  uma operação por issue.
- O progresso de processo ainda conta etapas de projetos arquivados.
- Frontend: só type-check e lint, nenhum teste de UI. Valide no navegador os
  controles de área do item com Member, coordenador e admin.
- `ruff`: o CI fixa a versão 0.9.7 (`requirements/local.txt`). Formate com essa
  versão; a 0.17.x acusa arquivos que o CI aceita.
