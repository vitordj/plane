# Fase D0 — Fundação do domínio

**Objetivo:** fechar os quatro defeitos conhecidos do alocador (RFC §2.2,
D1–D4), introduzir o estado de fila, o executor principal, a política de
alocação e a auditoria de decisões, e reescrever o engine com lock e
determinismo. Tudo atrás do kill switch existente; nenhuma capacidade nova
exposta a robôs ainda.
**Pré-requisitos:** nenhum. Corre em paralelo com P0.
**Referência:** RFC §5.1, §5.2 (três primeiras tabelas), §6 inteiro, §9
(Fase D0), §10.
**Ordem recomendada:** D0.1 → D0.2 → D0.3 → D0.4 → D0.5 → D0.6 → D0.7 →
D0.8 → D0.9 (os testes acompanham cada item; D0.9 é o fechamento da matriz)
→ D0.10.

Todo item que toca alocação termina com teste de concorrência. Comandos são
para o desenvolvedor rodar; a sessão de agente não executa migrações nem
suíte completa (AGENTS.md).

---

## D0.1 — Área precisa cobrir o projeto (defeito D1) `[x]`

**Onde estava o defeito.**
- `apps/api/plane/app/views/organizational_unit.py`, `IssueOrganizationalUnitEndpoint.post`: validava só `workspace_id`.
- `apps/web/core/components/orca/organizational-units/issue-unit-property.tsx` l.67: filtrava só `unit.is_active` — com um comentário logo acima afirmando que só áreas que cobrem o projeto apareciam. O comentário descrevia a intenção; o código não a implementava.
- `apps/api/plane/app/services/orca/assignment_engine.py` ~l.105: acrescentava `project_id` a `unit_project_ids` quando não estava lá, o que transformava "não coberto" em "coberto" **e** fazia trabalho de um projeto que a área não possui contar na carga dos membros dela.

**Mudança** (entregue em `feat/orca-unit-project-coverage`).
- `services/orca/coverage.py` (novo): `unit_covers_project(unit, project_id)` — área ativa, link vivo (o manager padrão já exclui soft-deleted) e projeto não arquivado. Projeto arquivado não concede nada, então uma área ligada só a projetos arquivados não cobre nenhum.
- `IssueOrganizationalUnitEndpoint.post` e `IssueOrganizationalUnitAssignEndpoint.post` chamam o helper; falha → `ORG_UNIT_NOT_COVERING_PROJECT` (4916). A checagem é feita **de novo** na rota de atribuição, não só na criação do vínculo: o projeto pode ser desvinculado ou arquivado depois que o item já estava marcado como da área.
- Engine: o `append` saiu. Coberto passou a ser pré-condição, verificada logo no começo de `candidates_for`, que devolve `[]` quando não é o caso.
- Código novo nos três lugares (`orca_error_codes.py`, `error-codes.ts`, catálogo i18n nas 19 locales).
- API: `OrganizationalUnitSerializer` ganha `project_ids` (read-only, sem projetos arquivados). O endpoint de lista alimenta o campo com um `Prefetch(..., to_attr=...)`, então listar áreas continua sendo uma consulta e não uma por área; a serialização de uma única área cai numa consulta filtrada.
- UI: o dropdown filtra `unit.is_active && unit.project_ids.includes(projectId)` — o comentário passou a ser verdade.
- `docs/organizational-units.md` §Assignment descreve a regra, o código de erro e o campo novo.

**Testes.** `test_issue_unit_coverage.py` (novo): a regra em si (ligado, não
ligado, ligado a outro projeto, área inativa, projeto arquivado, link
removido, argumentos nulos); a recusa nas duas rotas com o código certo,
inclusive o caso em que a cobertura some depois de o vínculo existir; o engine
sem candidatos e `no_eligible_member`; e o serializer, incluindo que trabalho
num projeto não coberto **deixou** de inflar a carga. Em
`test_issue_organizational_unit_http.py`, os testes que marcavam uma área
responsável passaram a ligar o projeto antes (fixture `covering_unit`) — o
comportamento mudou, e é isso que eles agora afirmam.

**Aceite.**
- [x] Ruff limpo nos arquivos tocados; paridade dos quatro lugares do código de erro conferida fora do pytest (17 códigos, mapa TS e catálogo en batem). Testes escritos; a sessão não roda pytest (AGENTS.md) — confirmar no CI de `stage`.
- [ ] `check:sync` do i18n verde (a sessão não tem `node_modules`; a paridade de chaves das 19 locales foi conferida com uma comparação equivalente de conjuntos de chaves).

**Migração.** Nenhuma — o defeito era de validação, não de esquema.

**Efeito em dados existentes.** Vínculos já gravados em projetos não cobertos
continuam no banco e não são apagados por este item: a partir de agora eles
recusam atribuição (com o código novo) em vez de atribuir errado. Se aparecer
algum em produção, o caminho é vincular o projeto à área ou trocar a área do
item — vale um levantamento antes de ligar a camada num workspace que já usa
áreas.

---

## D0.2 — Remover herança implícita de assignees na API pública (defeito D2) `[x]`

**Onde estava.** `apps/api/plane/api/serializers/issue.py` (~l.188) e o mesmo
bloco em `apps/api/plane/app/serializers/issue.py`: sem `assignees`, o item
copiava os assignees do último item criado pela mesma pessoa naquele projeto.

**Mudança** (entregue em `feat/orca-unit-project-coverage`).
- O bloco upstream foi restaurado **literalmente** (copiado de `5662b7610`) nos dois serializers: sem `assignees`, usa o `default_assignee` do projeto se ele ainda for um membro ativo com papel ≥ 15; nada além disso.
- **Decisão tomada nesta sessão:** removido também do serializer interno, não só do público. O plano deixava a alternativa de manter na UI atrás de `ProjectCustomSettings.remember_last_assignees`; ninguém reivindicou o comportamento, e o próprio enunciado manda remover nesse caso. Um toggle que ninguém pediu é código morto com migração junto. Se aparecer demanda, a lógica está no histórico e vira item próprio.

**Por que era defeito e não feature.** Para um robô que posta um item sem
assignees, a escolha dependia de histórico invisível: o mesmo request produzia
resultados diferentes conforme o que aquela conta tivesse criado antes. O
contrato do `/api/v1` também divergia em silêncio do upstream, o que é
exatamente o que um fork não deve fazer numa rota pública.

**Testes.** `test_issue_serializer_orca_features.py`: a classe de herança deu
lugar a `TestDefaultAssignee` (endpoint interno — sem default, lista
explícita, default aplicado, default que saiu do projeto, default rebaixado a
guest) e a `TestPublicApiDefaultAssignee`, que exercita
`plane.api.serializers.IssueSerializer.create` direto (a rota pública
autentica por API key; o que importa aqui é quais linhas o `create` grava).
Os dois lados têm um teste que **fixa a ausência** da herança — é o que pega
o comportamento voltando.

**Aceite.**
- [x] Criar item via `/api/v1` sem `assignees` em projeto sem `default_assignee` → zero assignees (teste direto do serializer).
- [x] Criar item via `/api/v1` sem `assignees` em projeto com `default_assignee` → esse assignee.
- [ ] Registrar a mudança de comportamento nas notas do próximo release. O `CHANGELOG` é gerado pelo Release Please a partir das mensagens de commit, e o corpo do commit deste item descreve a mudança; **não** foi marcado `BREAKING CHANGE:` de propósito — com a versão em 1.x isso dispararia um bump major.
- [ ] README: não há linha na tabela de features anunciando a herança (nunca houve), então não há o que corrigir lá. Fica registrado para o caso de alguém procurar.

**Arquivos:** `apps/api/plane/api/serializers/issue.py`, `apps/api/plane/app/serializers/issue.py`, `apps/api/plane/tests/unit/orca/test_issue_serializer_orca_features.py`.

---

## D0.3 — Migração 0135: estado de fila e executor principal `[x]`

**Mudança** (entregue em `feat/orca-unit-project-coverage`).
- `IssueOrganizationalUnit` ganhou `routing_state`, `queue_reason`, `queued_at`, `assignment_due_at` e `primary_executor` (FK `User`, `SET_NULL`, `related_name="orca_primary_executions"`). `RoutingState` e `QueueReason` são `TextChoices` no mesmo arquivo.
- Dois CHECKs: `assigned` exige executor, e executor só existe em `assigned`. O primeiro impede o estado que a fila leria como "alguém está com isso" sem ninguém estar; o segundo impede o executor esquecido, que continuaria sendo cobrado na contagem de carga e mudaria quem o alocador escolhe.
- Dois índices: `(workspace, organizational_unit, routing_state)` para a fila do coordenador e `(primary_executor, routing_state)` para a carga.
- `current_assignment_decision` ficou para a `0137`, depois que a tabela de decisões existe — assim o import continua de mão única (o log conhece os modelos organizacionais, não o contrário).

**Migração `0135_orca_issue_routing_state`.** Campos → `RunPython` →
constraints → índices, nessa ordem: até o backfill rodar, toda linha existente
está em `assigned` por default de campo e sem executor, então o CHECK
adicionado antes rejeitaria o banco inteiro. O backfill é idempotente: item
com `IssueAssignee` vivo vira `assigned` com o assignee mais antigo; o resto
entra na fila com `new_item`, e `queued_at` só é preenchido se estiver vazio,
para que uma re-execução não zere há quanto tempo o item espera. Reverso do
`RunPython` é no-op — os campos somem com o reverso dos `AddField`.

**Escrita à mão.** A sessão de agente não tem banco, então a migração foi
escrita seguindo o padrão das existentes em vez de gerada. **O desenvolvedor
precisa confirmar** com:

```bash
python3 apps/api/manage.py makemigrations --check --dry-run
python3 apps/api/manage.py migrate db 0137 && python3 apps/api/manage.py migrate db 0134
```

**Testes.** `test_routing_state.py`: defaults, os dois CHECKs contra o
PostgreSQL de verdade (um CHECK que só existe no modelo não é constraint),
`allocation_failed` também sem executor, o caminho feliz, e o backfill
chamado direto com o registro de apps (o `django_test_migrations` não é
dependência aqui) — inclusive a idempotência e o fato de links já limpos não
serem reescritos.

**Aceite.**
- [ ] `makemigrations --check` limpo após a migração (comando acima; a sessão não roda Django).
- [ ] Migração aplicada e revertida com sucesso num banco local com dados.
- [x] Testes escritos; ruff limpo. Verdes a confirmar no CI.

---

## D0.4 — Migrações 0136 e 0137: política, decisão e evento de responsabilidade `[x]`

**Mudança** (entregue em `feat/orca-unit-project-coverage`).
Novo `apps/api/plane/db/models/organizational_assignment.py`, exportado em
`db/models/__init__.py`:
- `OrganizationalUnitAssignmentPolicy` — modo padrão, modos permitidos, SLA, teto de carga, `is_active` e `version`. `save()` incrementa a versão, preenche `allowed_modes` com o próprio `default_mode` quando vem vazio e desnormaliza `workspace`. `clean()` recusa `allowed_modes` que não seja lista, que traga modo desconhecido, ou que não contenha o `default_mode` — esta última é a que faria toda alocação sob a política rejeitar justamente o modo para o qual ela cai.
- `AssignmentDecision` e `IssueResponsibilityEvent`, ambos herdando de `AppendOnlyModel` (novo, abstrato): `save()` numa linha existente levanta `ValueError`, e o soft delete também, porque soft delete é uma escrita. Um `update()` de queryset passa por fora, como em qualquer modelo — a guarda torna a regra óbvia, não é um sistema de permissão. Está documentado no docstring.
- Duas constraints parciais de unicidade na política, uma para `unit_project IS NULL` e outra para `IS NOT NULL`: com uma só, o Postgres trataria os NULLs como distintos e uma área juntaria quantas políticas "padrão" quisesse, deixando o resolvedor escolhendo arbitrariamente entre elas.

**Divergência consciente do RFC.** `AssignmentDecision.automation_operation`
(FK para `AutomationOperation`) **não** entrou: aquele modelo nasce na `0138`,
na Fase 1. Uma FK não pode apontar para uma tabela que ainda não existe;
o campo entra junto com ela, pelo mesmo motivo que `current_assignment_decision`
ficou para a `0137`.

**Migrações.** `0136_orca_assignment_policy` (tabela + as duas constraints) e
`0137_orca_assignment_decision` (as duas tabelas append-only, o
`current_assignment_decision` no link e os três índices). Escritas à mão, pelo
mesmo motivo do D0.3 — **confirmar com `makemigrations --check --dry-run`**.

**Testes.** `test_assignment_models.py`: unicidade nos quatro arranjos
(duas padrão, duas por projeto, uma de cada, duas áreas) e o fato de o soft
delete liberar a vaga; validação dos `allowed_modes` e incremento de
`version`; e os logs recusando edição e soft delete, com `supersedes` como a
forma correta de mudar uma decisão.

**Aceite.**
- [ ] `makemigrations --check` limpo (comando no D0.3).
- [x] Testes escritos; ruff limpo. Verdes a confirmar no CI.

---

## D0.5 — `assignment_service.py`: resolução, ranking `lb-1`, alocação, claim, reatribuição, devolução, transferência `[x]`

**Entregue** (`feat/orca-unit-project-coverage`).
`apps/api/plane/app/services/orca/assignment_service.py`, com
`resolve_policy`, `rank_candidates`, `allocate`, `claim`, `reassign`,
`return_to_queue`, `transfer_unit` e `set_responsibility`, mais
`unit_allocation_lock`. Erros de domínio em `services/orca/errors.py`, todos
com `error_code` e `http_status`, para a view converter a família inteira num
`except OrcaDomainError`.

**Decisões que o enunciado deixava em aberto.**
- `rank_candidates` ganhou `exclude_user_ids`. O modo `append` do caminho legado quer alguém que **não** esteja no item; sem isso o ranking devolveria a própria pessoa já atribuída e o "acrescentar" não acrescentaria ninguém.
- SLA e teto de carga caem de projeto para área **independentemente**: uma política de projeto que não diz nada sobre SLA herda o da área em vez de zerar.
- `claim` exige que a política efetiva permita `self_claim`. O bypass "ou coordenador/admin" do RFC §6.3 não entrou: o papel de coordenador é da Fase 2 e não existe no modelo ainda. Fica registrado como pendência do D0.6/Fase 2.
- Cinco códigos novos (4917–4921) nos três lugares, com mensagem nas 19 locales.

**`assignment_engine.py` virou casca.** O docstring diz isso e aponta para o
serviço. `candidates_for` delega para `rank_candidates`; `assign_from_unit`
delega para `allocate` **quando o item tem vínculo com aquela área** —
caminho completo, com lock, estado e decisão. Um item **sem** vínculo continua
sendo atribuído como antes, gravando só o `IssueAssignee`: é a lacuna que o
D0.6 fecha quando os endpoints passarem a falar com o serviço, e foi deixada
funcionando de propósito para que este item não mude o que o endpoint atual
faz. `workload_snapshot` continua no engine: é a consulta do painel, não
ranking.

**Concorrência.** `allocate` abre `transaction.atomic()`, tira advisory lock
por área **só** em `least_loaded` (é onde a carga lida precisa incluir a
alocação anterior) e faz `select_for_update` na linha do vínculo em todos os
caminhos. O claim usa só o lock de linha: o segundo espera o primeiro comitar
e então vê um item já atribuído, o que é a resposta certa — `AlreadyClaimed`
carrega o vencedor para o perdedor não precisar recarregar.

**Testes.** `test_assignment_service.py` (resolução com e sem política, herança
projeto→área, recusa de modo fora do permitido; ranking por carga, trabalho
concluído fora da conta, só executor principal contando, exclusões com motivo,
teto de carga, determinismo; os quatro caminhos de `allocate`; claim,
reatribuição com If-Match, devolução, transferência; e um teste de que o
serviço não escreve `ProjectMember`) e `test_assignment_concurrency.py`
(20 alocações simultâneas → 5/5/5/5; 10 claims → 1 vencedor e 9
`AlreadyClaimed`, com `transaction=True`, `ThreadPoolExecutor` e
`connection.close()` por thread).

**Aceite.**
- [ ] Testes verdes, incluindo concorrência no runner Docker (`docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/orca/test_assignment_concurrency.py -q`). A sessão não roda pytest.
- [x] `assignment_engine.py` não contém mais lógica própria de ranking.
- [x] Nenhuma escrita em `ProjectMember` no módulo (grep, e um teste que compara o conjunto de ids antes e depois).

---

## D0.6 — Endpoints internos passam a usar o serviço; GET de política `[x]`

**Mudança** em `apps/api/plane/app/views/organizational_unit.py` e
`apps/api/plane/app/urls/orca.py`:
- `IssueOrganizationalUnitEndpoint.post` → `set_responsibility(...)`; a resposta virou `{organizational_unit, routing}`, com `routing_state`, `queue_reason`, `primary_executor`, `assignment_due_at` e a decisão corrente.
- `IssueOrganizationalUnitEndpoint.get` → mesmo par; item sem área devolve `{"organizational_unit": null, "routing": null}`.
- `IssueOrganizationalUnitEndpoint.delete` → grava `IssueResponsibilityEvent(to_unit=None)` na mesma transação, antes de apagar o vínculo (I6). Os assignees ficam: o item volta a ser um item comum.
- `IssueOrganizationalUnitAssignEndpoint.post` → `set_responsibility(...)`, que **cria o vínculo** quando o chamador nomeia a área. É o fechamento da lacuna aberta no D0.5: não existe mais atribuição por área que não deixe estado de fila nem decisão.
- Cobertura (D1) continua checada na rota antes do serviço: a resposta do serviço seria "ninguém elegível", verdadeira e inútil; a rota sabe dizer que o projeto foi desvinculado ou arquivado.

**A rota de atribuição pede `least_loaded`.** É gente apertando um botão que
diz "atribuir a quem tem menos trabalho aberto", não a política da área
agindo sozinha: usar o `default_mode` faria o botão responder enfileirando o
item de novo em toda área `manual`. Área que declarou `allowed_modes` sem
`least_loaded` recusa com `ORG_ASSIGNMENT_MODE_NOT_ALLOWED` (4917) — I7 vale.
`assignment_mode` no corpo pede outro modo.

**Fallback de política alargado** (desvio consciente do RFC §6.3, registrado
lá). Sem nenhuma política, `allowed_modes` passou a ser a lista inteira em vez
de `["manual"]`. O default continua `manual` — área não configurada não
distribui nada sozinha —, mas com a lista antiga o botão recusaria em **toda**
área existente, já que a UI de política é da Fase 2. Uma área que declarou
`allowed_modes` decide de verdade.

- `mode=fill_empty`/`append` continuam sendo a forma antiga da rota: dizem o que fazer com quem já está no item, não como escolher. `append` vira `exclude_user_ids` na alocação. Deprecados em favor de `assignment_mode`.
- Novo `OrganizationalUnitPolicyEndpoint` (`GET .../organizational-units/{unit_id}/policy/` e `.../{unit_id}/projects/{project_id}/policy/`) devolvendo a política efetiva resolvida — Admin/Member/Guest do workspace, como os demais GETs.
- Serializers: `AssignmentPolicySerializer`, `AssignmentDecisionSerializer` (sem `candidates_snapshot`: é auditoria, não payload de tela), `IssueRoutingSerializer`.
- Frontend: tipos em `packages/types`, serviço e store devolvendo `routing`, e `issue-unit-property.tsx` escondendo o botão quando o item já está atribuído e mostrando o aviso de fila (`queued`) — duas chaves novas nas 19 locales. A UI completa da fila é Fase 2.

**Testes.** `test_issue_organizational_unit_http.py`: o payload de roteamento,
a rota de política, o `delete` gerando evento, a transferência gerando os
dois, e três casos novos na atribuição — o vínculo e a decisão criados pela
própria rota, a recusa da área que proíbe o ranking, e `self_claim`
respondendo `queued` em vez de erro. Dois testes de carga passaram a montar o
vínculo: a carga é contada pelo executor principal desde o D0.5, então um
`IssueAssignee` solto não pesa mais. O de determinismo virou outro teste: pela
rota não dá para ranquear duas vezes o mesmo estado, porque o log de decisões
é append-only e o `last_auto_at` que ele grava é justamente uma entrada do
desempate seguinte. O que a rota prova é o rodízio — dois itens empatados vão
para pessoas diferentes; o determinismo em si fica em
`test_assignment_service.py::TestRanking::test_the_order_is_deterministic_on_a_tie`,
que ranqueia duas vezes sem escrever nada.
`test_assignment_service.py`: o fallback agora aceita qualquer modo
solicitado, e um modo inexistente continua recusado.

**Aceite.**
- [x] Ruff e `ruff format` limpos nos arquivos tocados; paridade das 19 locales conferida por comparação de conjuntos de chaves.
- [ ] Todos os testes Orca verdes (a sessão não roda pytest — AGENTS.md; conferir no CI de `stage`).
- [ ] `pnpm --filter web check:types` limpo (rodar localmente).

---

## D0.7 — Comando `audit_organizational_routing` `[x]`

**Arquivos.** A lógica ficou em
`apps/api/plane/app/services/orca/routing_audit.py` (`audit_routing(workspace_id,
write=False)` devolvendo uma lista de `Finding`) e o comando
`apps/api/plane/db/management/commands/audit_organizational_routing.py` só
imprime — mesmo esqueleto do `reconcile_organizational_access.py` (`--workspace`,
`--write`, saída tabular, kill switch recusando o comando inteiro). Separar as
duas coisas é o que deixa a auditoria testável sem `call_command` e reutilizável
por uma tarefa periódica na Fase 2.

**Verificações** (RFC §6.1 I3, I4): `assigned` sem `IssueAssignee` do executor;
`assigned` com executor que não é mais membro ativo da área ou do projeto
(reusa `_assert_eligible`, então a auditoria e a alocação não podem divergir);
`queued`/`allocation_failed` com `IssueAssignee` (mostrar; pode ser
colaborador); política com `default_mode` fora de `allowed_modes` ou com modo
desconhecido.

`--write` devolve à fila os dois primeiros casos com
`return_to_queue(..., queue_reason="executor_unavailable",
trigger="command")` — `return_to_queue` ganhou o parâmetro `trigger` para
isso, senão o reparo apareceria no histórico como alguém clicando na
interface. A linha do `IssueAssignee` não é tocada: tirar alguém de um item é
decisão de gente.

**Testes.** `test_audit_routing_command.py`: um caso de cada violação, o
workspace limpo, o isolamento por workspace, dry-run não escrevendo, `--write`
devolvendo à fila com o motivo certo, o reparo virando decisão com
`trigger=command` e `previous_primary_executor`, o item enfileirado com
assignee ficando intacto, idempotência (rodar duas vezes não acha nada na
segunda) e o kill switch fechando o comando.

**Aceite.**
- [x] Teste `test_audit_routing_command.py` com um caso de cada violação, em dry-run e write.
- [x] Documentado em `docs/organizational-units.md` ao lado do reconcile.

---

## D0.8 — Observabilidade mínima `[x]`

**Mudança.** `services/orca/metrics.py` com `record_assignment_outcome`,
`record_no_candidate` e `record_decision_superseded`; implementação = log
estruturado INFO no logger `plane.orca.metrics`, com o campo `metric` levando
o nome do RFC §11 e os rótulos que a tabela lista. Prometheus/StatsD depois
trocam só este módulo.

Chamadas em `assignment_service`: o contador de desfecho sai de `_record`, ou
seja, de **toda** decisão — inclusive as que enfileiram, senão "nada está
sendo atribuído" e "nada está sendo pedido" ficam iguais no painel.
`no_candidate` sai do ramo de `allocation_failed` com quantas pessoas o
ranking chegou a olhar (`considered`), que é o que separa área vazia de área
lotada. `superseded` só sai quando a decisão nova **tira** o trabalho de quem
a anterior escolheu: alocar um item que estava na fila, ou confirmar a mesma
pessoa, não passou por cima de ninguém.

**Aceite.**
- [x] `test_assignment_metrics.py`: nomes e rótulos de cada contador, o desfecho enfileirado contando, `considered` distinguindo área vazia de teto atingido, alocação bem-sucedida sem `no_candidate`, reatribuição e devolução contando como superseded, alocação de item enfileirado não contando — e um teste de que nenhuma entrada carrega e-mail, nome de exibição ou título de item.

---

## D0.9 — Fechar a matriz de testes da fase `[x]`

Percorridas as linhas do RFC §10 que pertencem à D0. Os nomes dos testes
**não** ganharam prefixo `test_i2_...`: o repositório nomeia teste pela
afirmação, não pelo identificador, e um prefixo de spec envelhece mal (a
numeração da RFC muda, o nome fica). O rastro fica na tabela ao final deste
arquivo, que é onde alguém procura "onde isto está coberto".

**Lacunas encontradas e fechadas.**
- **Estados**: a máquina de estados de §6.2 só era exercida de lado, por cada
  operação no estado em que ela costuma rodar. `test_routing_transitions.py`
  percorre a tabela: cada transição permitida acontece e cada transição
  ausente é recusada — inclusive `allocation_failed → assigned` (por claim e
  por rodar a alocação de novo), `suspended → queued`, e as recusas com o
  status certo (409 quando alguém chegou primeiro, 400 quando o movimento não
  faz sentido daquele estado).
- **Concorrência**: faltava a terceira corrida do §10, alocação e claim
  simultâneos no mesmo item — `test_an_allocation_and_a_claim_leave_one_executor`.
- **Permissões e kill switch** da rota nova de política: Guest lê, gente de
  outro workspace não, camada desligada devolve 404.
- **`apps/api/tests/RUNNING_TESTS.md`** (o guia existente; não há
  `TESTING_GUIDE.md` neste fork) ganhou a seção "Concurrency tests" com o
  padrão: `transaction=True`, fixtures próprias, `connection.close()` por
  thread, e asserção sobre o agregado e nunca sobre quem venceu.

**Aceite.**
- [ ] `pytest plane/tests/unit/orca/ -q` verde no runner Docker (a sessão não roda pytest — AGENTS.md).
- [x] Cobertura das linhas listadas registrada na tabela ao final deste arquivo.

---

## D0.10 — Documentação `[x]`

- `docs/organizational-units.md` §Assignment reescrita: a tabela dos campos de
  roteamento, as três políticas e como resolvem, o que o `least_loaded`
  ordena e exclui, o registro (decisão append-only e evento de área), como se
  dispara, e uma seção "What changed from v1" dizendo o que o engine fazia de
  errado. §Auditoria nova ao lado do reconcile (D0.7). §Tests atualizada com
  os arquivos novos e o ponteiro para o padrão de concorrência. Tabela de
  rotas com as duas de política.
- RFC §2.1 atualizada: "Alocar ao menos carregado", "Estado de fila" e
  "Executor principal" → Sim; "Reatribuição quando alguém sai" e "Política
  automática na criação" → Parcial, com o que existe e o que falta. §6.3
  registra o alargamento do fallback decidido no D0.6.
- Este arquivo com a tabela de invariantes preenchida (D0.9) e o `README.md`
  do plano com o estado.

---

## D0.11 — Arquivar projeto dispara reconciliação `[x]`

**Problema** (pendência do PR #6). `_active_sources` já ignora projetos
arquivados e `projects_in_workspace` só devolve não arquivados, então o
acesso herdado a um projeto arquivado deixa de ter fonte — mas nenhuma
reconciliação é disparada no arquivamento, e a passagem de workspace não
inclui o projeto. O `ProjectMember` herdado fica ativo até alguém reconciliar
aquele projeto explicitamente.

**Mudança.**
- `ProjectArchiveUnarchiveEndpoint` (`views/project/base.py`): depois de gravar `archived_at`, chama `dispatch_reconciliation(workspace_id, project_ids=[project.id])`; ao desarquivar, idem (o acesso volta). Um projeto é uma aresta na estimativa de fan-out, então roda inline e a resposta já reflete a retirada.
- `reconcile_access` com `project_ids` explícitos **já** aceitava projeto arquivado como alvo: só `_active_sources` o ignora; o `state_filter` de `_collect_context` traz os pares que a camada já escreveu, arquivado ou não. Nada a mudar aqui — verificado antes de escrever código.

**Testes** (`test_org_unit_reconciler.py`, classe `TestArchivingAProject`, pela
rota HTTP e não pelo serviço, porque o que estava faltando era o gatilho):
arquivar → `ProjectMember` herdado desativado; desarquivar → restaurado;
acesso manual sobrevive ao arquivamento com o papel de baseline; kill switch
desligado → arquivar funciona e a camada não age.

**Aceite.**
- [x] Testes acima escritos; `ORCA_ORG_UNITS_ENABLED=0` faz o arquivamento não reconciliar (o guard de `reconcile_access` cobre, e há teste).

---

## D0.12 — Roster SCIM de grupo exclui memberships removidos logicamente `[x]`

**Problema** (pendência do PR #6). `members_of(unit)` em
`views/orca_scim/groups.py` atravessa `group_memberships__organizational_unit_id`
sem filtrar `deleted_at`; um membership soft-deleted pode reaparecer na
resposta de `GET /Groups/{id}`, e o Entra passa a considerar a pessoa membro.

**Mudança.** `members_of` passou a fazer duas consultas em vez de uma junção:
seleciona os memberships vivos pelo manager padrão e depois as identidades por
id. Filtrar `group_memberships__deleted_at__isnull=True` na junção resolveria o
caso, mas deixaria a próxima pessoa que escrever `identity__...` com a mesma
armadilha; a junção reversa monta pelo manager base e simplesmente não conhece
o soft delete. `remove_members` já apaga pelo manager padrão (soft), e
`add_members` usa `get_or_create` — a constraint única é parcial (só linhas
vivas), então readicionar quem saiu grava uma linha nova em vez de estourar.

**Testes** (`test_scim_endpoints.py`): membership removido some do
`GET /Groups/{id}` e a linha continua existindo em `all_objects` (a retirada é
proveniência, não apagamento); sair e voltar ao grupo lista a pessoa uma vez
só. O roster interno da área já tinha teste equivalente.

**Aceite.**
- [x] Testes acima escritos (CI de `stage` confirma).

---

## Gate D0

- [ ] Todos os 12 itens `[x]`.
- [ ] Invariantes I1–I7 com teste positivo e negativo (listar nomes dos testes abaixo).
- [ ] Concorrência: 20 alocações → 5/5/5/5; 10 claims → 1 vencedor.
- [ ] `audit_organizational_routing` sem violações num dump do banco de `stage`.
- [ ] `pytest plane/tests/unit/orca/` verde no CI.
- [ ] `ORCA_ORG_UNITS_ENABLED=0` continua respondendo 404 em todas as rotas novas e antigas.

Data do gate: ____

### Testes por invariante

Arquivos em `apps/api/plane/tests/unit/orca/`.

| Invariante | Testes |
| --- | --- |
| I1 — uma área ativa por item | `test_issue_organizational_unit_http.py::test_replacing_the_responsible_unit_keeps_a_single_link`; a constraint parcial existente é do PR anterior |
| I2 — área ativa cobrindo o projeto | `test_issue_unit_coverage.py` inteiro (a regra, as duas rotas, o engine, o serializer); `test_assignment_service.py::test_an_area_that_does_not_cover_the_project_is_refused`, `::test_marking_an_area_that_does_not_cover_the_project_is_refused`, `::test_a_transfer_to_an_area_that_does_not_cover_the_project_is_refused` |
| I3 — `assigned` ⇔ executor ⇔ `IssueAssignee` | positivo: `test_routing_state.py::test_assigned_with_an_executor_is_accepted`, `::test_assigned_without_an_executor_is_rejected`, `::test_an_executor_in_any_other_state_is_rejected` (CHECKs). O terceiro elo não é constraint: `test_audit_routing_command.py::test_an_executor_who_is_no_longer_an_assignee` e `::test_write_returns_the_item_to_the_queue` |
| I4 — executor elegível na hora da decisão | `test_assignment_service.py::test_an_explicit_executor_outside_the_area_is_refused`, `::test_an_explicit_executor_outside_the_project_is_refused`, `::test_someone_outside_the_area_cannot_claim`; auditoria: `test_audit_routing_command.py::test_an_executor_who_left_the_area`, `::test_an_executor_who_lost_project_access` |
| I5 — toda mudança gera decisão | `test_assignment_service.py::test_every_allocation_leaves_a_decision`, `::test_the_second_decision_supersedes_the_first`; append-only em `test_assignment_models.py::test_a_decision_cannot_be_edited`, `::test_a_decision_cannot_be_soft_deleted`, `::test_superseding_is_how_a_decision_changes`; pela rota em `test_issue_organizational_unit_http.py::test_assigning_makes_the_area_responsible_and_records_the_decision` |
| I6 — toda troca de área gera evento | `test_assignment_service.py::test_marking_an_area_creates_the_link_and_the_event`, `::test_a_transfer_records_both_areas`; pela rota em `test_issue_organizational_unit_http.py::test_clearing_the_area_leaves_the_event_behind`, `::test_moving_an_item_between_areas_records_both`; append-only em `test_assignment_models.py::test_a_responsibility_event_cannot_be_edited` |
| I7 — modo fora de `allowed_modes` recusado, nunca degradado | `test_assignment_service.py::test_a_requested_mode_outside_the_allowed_list_is_refused`, `::test_an_unknown_mode_is_refused_even_with_no_policy`, `::test_claiming_is_refused_when_the_policy_does_not_allow_it`; pela rota em `test_issue_organizational_unit_http.py::test_an_area_that_forbids_the_ranking_refuses_the_button`. O fallback sem política aceita qualquer modo por decisão registrada no D0.6: `::test_with_no_policy_any_mode_may_be_requested` |
| I8 — binding externo | Fase 1 (API pública). Sem modelo nesta fase. |
| I9 — idempotência | Fase 1 (API pública). Sem modelo nesta fase. |
| I10 — nada escreve `ProjectMember` fora dos reconciliadores | `test_assignment_service.py::test_the_service_never_writes_project_member` |

### Linhas do RFC §10 cobertas nesta fase

| Linha | Onde |
| --- | --- |
| I2 cobertura | `test_issue_unit_coverage.py` |
| Resolução de política | `test_assignment_service.py::TestPolicyResolution`; rota em `test_issue_organizational_unit_http.py::TestThePolicyRoute` |
| Ranking `lb-1` | `test_assignment_service.py::TestRanking` (carga, trabalho concluído, colaborador, Guest, bot, teto, determinismo, motivo da exclusão) |
| Estados | `test_routing_transitions.py` (tabela §6.2, positivo e negativo) e `test_routing_state.py` (CHECKs e backfill) |
| Decisões | `test_assignment_models.py` e `test_assignment_service.py::TestAllocate` (toda alocação deixa decisão, e a segunda supersede a primeira) |
| Concorrência | `test_assignment_concurrency.py` (20 alocações → 5/5/5/5; 10 claims → 1 vencedor; alocação × claim → um executor) |
| Encaminhamento | `test_assignment_service.py::TestResponsibilityAndTransfer` |
| Kill switches | `test_issue_organizational_unit_http.py::TestThePolicyRoute::test_the_kill_switch_closes_it`, `test_audit_routing_command.py::test_the_kill_switch_closes_the_command`, `test_org_unit_reconciler.py::TestArchivingAProject::test_the_kill_switch_stops_the_reconciliation`, e a cobertura anterior das rotas da camada |
| Permissões | `test_issue_organizational_unit_http.py` (Admin/Member/Guest/fora do workspace nas rotas de item e na rota de política) |
| Observabilidade | `test_assignment_metrics.py` |
| Disponibilidade, API pública, Frontend | Fase 1 e Fase 2 |
