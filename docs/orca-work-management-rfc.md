# RFC — Gestão de trabalho por área (Orca)

**Status:** rascunho para debate. Nada aqui está implementado além do que a
seção 1 marca como existente.
**Base analisada:** `stage` em `79221cb6` (Plane CE v1.4.1).
**Documentos relacionados:** [organizational-units.md](./organizational-units.md),
[entra-directory-sync.md](./entra-directory-sync.md), [FORK.md](../FORK.md).

---

## 0. O conceito em uma frase

> A **área** é quem responde por uma pendência; a **pessoa** é quem a executa
> num dado momento. A responsabilidade não pode evaporar porque alguém saiu de
> férias, mudou de área ou deixou a empresa.

O fork já separa essas duas coisas: a área responsável fica em
`IssueOrganizationalUnit` (tabela lateral, uma área ativa por work item) e o
executor continua sendo o `IssueAssignee` nativo do Plane. Este RFC discute como
transformar essa separação em um sistema operacional completo: fila da área,
políticas de alocação, disponibilidade, papel de coordenador, automação de
processos (onboarding e rotinas) e visão executiva.

---

## 1. O que existe hoje — verificado no código

| Capacidade | Estado | Onde |
| --- | --- | --- |
| Área responsável persistente por work item, uma por item | **Sim** | `IssueOrganizationalUnit` (`db/models/organizational_unit.py`) |
| Área liga pessoas a projetos e materializa acesso em `ProjectMember` | **Sim** | `services/orca/org_unit_reconciler.py` |
| Acesso manual preservado, papel herdado como piso | **Sim** | idem, documentado em `organizational-units.md` |
| Alocar ao integrante menos carregado | **Parcial** | `services/orca/assignment_engine.py`; disparo manual, modos `fill_empty` e `append` |
| Papel `lead` na área | **Só rótulo** | membership aceita `lead`/`member`; nenhuma permissão decorre disso |
| Endpoint "minhas áreas" e "carga por integrante" | **Sim** | `UserOrganizationalUnitsEndpoint`, `OrganizationalUnitWorkloadEndpoint` |
| Tela da área | **Só membros e projetos** | `components/orca/organizational-units/unit-detail.tsx` |
| Rotas Orca acessíveis por API key / Compose | **Não** | tudo sob `/api/orca/` com autenticação de sessão |
| Disponibilidade, férias, capacidade | **Não** | — |
| Reatribuição quando alguém sai | **Não** | o engine só evita novas atribuições |
| Política automática na criação | **Não** | — |
| Dashboard da área / executivo | **Não** | — |

### 1.1 Defeitos que precisam ser fechados antes de automatizar

Todos confirmados por leitura do código; nenhum tem teste hoje.

1. **Área sem cobertura do projeto.** `IssueOrganizationalUnitEndpoint.post`
   só confere se a área pertence ao workspace. Não exige um
   `OrganizationalUnitProject` ligando área e projeto. A UI filtra só por
   `is_active`. O engine ainda **acrescenta o projeto ao cálculo de carga**
   quando ele não está entre os projetos da área, escondendo a inconsistência.
2. **Herança implícita de assignees na API pública.** O serializer de
   `/api/v1` copia os assignees do último item criado pelo mesmo usuário no
   projeto quando `assignees` vem vazio ou omitido. Para um robô isso torna o
   resultado dependente de histórico e conflita com a ideia de fila da área.
   O upstream usa `default_assignee` do projeto.
3. **Ranking e gravação sem lock.** `assign_from_unit` calcula o ranking e só
   depois cria o `IssueAssignee`. Em criação em lote, N requisições podem ver a
   mesma carga e escolher a mesma pessoa.
4. **Carga é "carga total da pessoa", não da área.** O engine conta todo
   `IssueAssignee` aberto nos projetos da área, inclusive itens de outras áreas
   ou pessoais. Pode ser desejável, mas precisa ser uma decisão declarada.

### 1.2 Correções ao parecer externo que motivou este RFC

- **Plane CE não tem propriedades customizadas.** Work item types e custom
  properties são recurso da edição comercial. A alternativa "criar um dropdown
  'Área responsável' via Compose" não existe neste fork. Isso reforça a decisão
  de manter `IssueOrganizationalUnit` como única fonte da verdade.
- **Os branches antigos ainda existem.** `claude/azure-aad-integration-review-*`,
  `claude/sync-remote-azure-auth-*` e `claude/aad-end-to-end-*` continuam no
  remoto com as abordagens `oidc-free`/`azuread` superadas. Higiene pendente.
- **Pipeline de imagens.** Confirmado: o job `build-push` roda também em
  `pull_request` com `push: true` incondicional para a tag mutável `:stage`, e
  `prod.yml` promove puxando `:stage`. Isso é pré-requisito de qualquer
  automação que dependa de release confiável (ver Fase 0).
- **Plane Compose.** A documentação oficial não pôde ser consultada a partir
  deste ambiente (egresso bloqueado). Tudo o que este RFC assume sobre o
  Compose (campos aceitos, autenticação por token, arquivo de estado,
  comportamento de re-push) é **hipótese a validar**, não fato.

---

## 2. Dúvidas conceituais para debate

Perguntas que precisam de resposta antes de desenhar tabelas. Estão agrupadas
por tema e numeradas para referência.

### Responsabilidade e execução

1. **Uma área por item é suficiente?** Onboarding tem tarefas que dependem de
   duas áreas (Jurídico revisa, Compliance aprova). Isso é uma tarefa com duas
   áreas ou duas tarefas encadeadas? A segunda opção preserva a invariante
   "uma área accountable" e usa relações nativas (`blocked_by`), mas multiplica
   itens. Qual é o custo aceitável?
2. **Área responsável muda de mãos?** Se Compliance encaminha para Jurídico, o
   item troca de área (perde histórico) ou nasce um item filho em Jurídico
   (mantém rastro)? Precisa haver um registro de "encaminhamentos" como
   auditoria própria?
3. **Executor vazio é estado legítimo ou anomalia?** Se é legítimo (fila), qual
   é o prazo máximo tolerado sem executor antes de virar alerta para o
   coordenador? Isso deve ser política da área ou do processo?
4. **O que significa "assumir" (claim)?** Qualquer integrante da área pode se
   atribuir um item da fila, ou só quem o coordenador liberou? Claim precisa
   de lock otimista para evitar duas pessoas assumindo o mesmo item?
5. **Colaboradores secundários.** Plane permite vários assignees. Se a política
   é "um executor principal", como distinguir principal de colaborador sem
   nova coluna no modelo core? Ordem de criação em `IssueAssignee` é frágil.
   Vale uma tabela lateral `IssuePrimaryExecutor`, ou desistimos da distinção?

### Políticas de alocação

6. **Onde a política mora?** Na área (padrão para tudo que ela recebe), no
   vínculo área↔projeto, no template de processo, ou nos três com precedência?
   Cada nível a mais é uma fonte de surpresa para o coordenador.
7. **Quais políticas justificam existir na v1?** `queue`, `claim`, `manual`,
   `least_loaded`, `round_robin`, `specific_member`. Cada uma exige teste de
   concorrência e tela. Dá para começar só com `queue` + `least_loaded` +
   `manual` e adiar rodízio e especialista?
8. **Como medir carga?** Contagem de itens abertos trata revisão de cinco
   minutos igual a análise de três dias. Estimativas existem no Plane mas
   raramente são preenchidas. Aceitamos contagem simples na v1 com a carga
   restrita a itens cuja área responsável é a própria unidade? Ou carga total
   da pessoa (todas as áreas), que é o que existe hoje?
9. **Justiça vs. especialização.** Menos carregado ignora quem sabe fazer.
   Sem custom properties no CE, "habilidade" teria de ser tabela lateral por
   membership. Isso é necessário na v1 ou é otimização prematura?
10. **Alocação automática na criação é reversível?** Se o robô atribui a
    Maria e o coordenador discorda, a reatribuição deve registrar "decisão
    automática revertida" para calibrar a política, ou basta o histórico
    nativo de atividade do Plane?

### Disponibilidade

11. **Disponibilidade é da pessoa ou da membership?** Férias valem para todas
    as áreas em que a pessoa está. Um bloqueio "não recebo mais nada de
    Compliance, mas continuo em Jurídico" é membership. Modelar os dois níveis
    ou só o nível pessoa na v1?
12. **Fonte da verdade de férias.** Entra/Graph expõe out-of-office e o
    calendário; a área de RH tem outro sistema. Sincronizar automaticamente ou
    o coordenador declara manualmente? Sincronização traz privacidade e
    dependência de tenant; manual traz esquecimento.
13. **O que acontece com o trabalho de quem sai?** Redistribuir
    automaticamente na desativação é agressivo (perde contexto, gera
    notificações em massa). Proposta: item volta para a fila da área com
    marcação "executor anterior indisponível", e o coordenador decide. Isso é
    aceitável ou precisa ser automático em algum caso (ex.: item vencido)?

### Papéis e permissões

14. **Lead vs. coordenador operacional.** O `lead` hoje é institucional e não
    tem poder algum. Criamos um segundo papel `coordinator` (vários por área,
    delegável) ou damos poder ao `lead` e permitimos mais de um? O que o
    coordenador pode fazer que um `member` do projeto não pode, dado que o
    Plane já permite a qualquer Member do projeto trocar assignees?
15. **Coordenador sem acesso ao projeto.** Um coordenador de área pode gerir
    a fila de um projeto de que não é membro? Se sim, a fila é uma exceção ao
    modelo de acesso do Plane e precisa de permissão própria e auditada.
16. **Observador executivo.** O CEO precisa ver agregados de áreas e projetos
    dos quais não é membro. Plane só tem Admin/Member/Guest no workspace.
    Criamos uma permissão de leitura executiva fora do modelo nativo, ou o
    dashboard executivo é servido só a Workspace Admin na v1?

### Processos e automação

17. **Compose é definição ou execução?** Cada onboarding é uma instância nova.
    Um arquivo Compose com os mesmos `id`s tende a atualizar, não a
    multiplicar. Confirmar o comportamento real de re-push antes de decidir
    se Compose entra no fluxo ou se fica só como versionamento de estados,
    labels e templates.
18. **Onde vive o template do processo?** Dentro do Plane (tabela
    `ProcessTemplate` no Orca) ou fora (orquestrador sidecar, conforme
    FORK.md)? Dentro dá UI nativa e transações; fora mantém o core intocado e
    permite trocar de motor. Qual peso damos à independência do fork?
19. **Como agrupar uma instância?** Um onboarding gera 5 a 20 itens. Módulo
    por instância, ciclo, label, ou item pai com sub-itens? Módulos têm tela
    de progresso pronta; sub-itens têm hierarquia; labels são baratas mas
    invisíveis. Precisa ser uma escolha única para toda a empresa?
20. **Idempotência de ponta a ponta.** `external_source` + `external_id`
    cobrem o item. Cobrem também "área marcada" e "política aplicada"? Uma
    operação composta (criar + vincular área + alocar) precisa ser atômica ou
    basta ser reexecutável até convergir?
21. **Fechamento automático.** Um robô concluir uma tarefa quando um sistema
    externo confirma (ex.: conta criada) é desejável, mas quem responde se o
    sistema externo mentir? Fechamento automático deve exigir estado
    intermediário "concluído pelo sistema, aguardando confirmação"?

### Métricas e visão executiva

22. **Métricas antes da fila ou depois?** Um dashboard sobre dados sem
    disponibilidade e sem fila mostra números errados com aparência de certos.
    Concordamos que a visão executiva é a última fase?
23. **SLA por área, por processo ou por tipo de tarefa?** Cada escolha exige
    um lugar diferente para o prazo. Sem custom properties, o prazo fica em
    `target_date` nativo, que é editável por qualquer Member. Isso é um
    problema?

---

## 3. Princípios de implementação

Regras que valem para todas as fases. Elas derivam do FORK.md e do que já
funcionou na camada de Areas.

1. **Autoridades nativas intocadas.** `ProjectMember` governa acesso;
   `IssueAssignee` diz quem executa. Orca só escreve nesses modelos por
   reconciliadores explícitos, idempotentes e com proveniência. Nenhuma coluna
   nova em `Issue`, `Project`, `Workspace` ou `User`.
2. **Invariantes no backend.** Área ativa e ligada ao projeto; executor membro
   ativo do projeto; uma área por item; política válida para a área. A UI
   pode filtrar, mas a API rejeita.
3. **Kill switch por capacidade.** `ORCA_ORG_UNITS_ENABLED` já existe.
   Novas capacidades ganham a sua chave (`ORCA_ASSIGNMENT_AUTO_ENABLED`,
   `ORCA_PUBLIC_API_ENABLED`, `ORCA_AVAILABILITY_ENABLED`) com default
   desligado até a fase correspondente ser aprovada.
4. **Toda decisão automática é registrada.** Uma tabela `AssignmentDecision`
   (quem, quando, política, candidatos considerados, escolhido, motivo,
   revertida por quem) é o que permite auditar e calibrar. Sem ela, não há
   alocação automática.
5. **Dry-run antes de write.** Cada comando ou endpoint que redistribui
   trabalho tem modo de pré-visualização, como o `reconcile_organizational_access`.
6. **Idempotência por identificador externo.** Toda operação disparada por
   orquestrador carrega `external_source`/`external_id` e converge ao mesmo
   estado se reexecutada.
7. **Público e interno separados.** Rotas de sessão continuam em
   `/api/orca/`. O que o Compose ou um robô chama vive em `/api/v1/orca/` com
   `APIKeyAuthentication`, serializers próprios e contrato versionado.
8. **Nada automático e irreversível na v1.** Reatribuição por saída ou férias
   é assistida (volta para a fila com marcação); só vira automática com dados
   de uso e decisão explícita.
9. **Testes de concorrência fazem parte da definição de pronto** para tudo que
   toca alocação.

---

## 4. Modelo alvo (proposta para debate)

Todas tabelas laterais, todas em migrações Orca numeradas após `0134`.

```text
OrganizationalUnitAssignmentPolicy
    organizational_unit, unit_project (opcional; precedência sobre a área)
    policy: queue | manual | least_loaded | round_robin | specific_member
    specific_member (opcional), max_open_items_per_member (opcional)
    load_scope: unit_only | person_total
    is_active

OrganizationalUnitMemberAvailability
    membership (ou workspace_member, ver dúvida 11)
    accepts_new_work: bool
    unavailable_from, unavailable_until, reason
    capacity_factor (0..1), max_open_items
    source: manual | directory

OrganizationalUnitCoordinator          (ou papel novo em Membership; ver dúvida 14)
    organizational_unit, workspace_member, is_active

AssignmentDecision
    issue, organizational_unit, policy_applied, trigger (api | ui | orchestrator | reassign)
    candidates_snapshot (JSON), chosen_assignee, reason
    decided_by (user ou "system"), reverted_at, reverted_by

IssueOrganizationalUnit (existente)
    + assigned_via_policy (FK AssignmentDecision, opcional)
```

Filas e dashboards são **consultas**, não tabelas: `IssueOrganizationalUnit`
já desnormaliza `project` e `workspace`, o que torna a fila por área um
`filter` direto.

Templates e instâncias de processo ficam **fora** deste modelo na primeira
proposta (orquestrador sidecar, ver dúvida 18). Se a decisão for "dentro", as
tabelas `ProcessTemplate` / `ProcessInstance` / `ProcessStep` entram na Fase 4.

---

## 5. Fases e gates

Cada fase só começa quando o gate da anterior fecha. A ordem é deliberada:
contrato antes de fila, fila antes de automação, automação antes de métrica.

### Fase 0 — Fundação segura (pré-requisito de tudo)

Entregas:

- Fechar os quatro defeitos da seção 1.1, com teste para cada um.
- `AssignmentDecision` criada e preenchida pelo endpoint manual já existente.
- Pipeline: PR faz build sem push (ou tag `pr-<n>-<sha>`); merge em `stage`
  publica `sha-<commit>` e `:stage`; `prod.yml` promove por digest registrado.
- Senha fixa em `tools/migration/create_users.py` substituída por
  `is_password_autoset=True` sem senha utilizável.
- `TRUSTED_PROXIES` sem default `0.0.0.0/0` em produção.
- Suíte upstream executável no CI (unit, com PostgreSQL e Valkey).

Gate: CI verde com a suíte upstream; teste de concorrência do alocador
passando (N criações simultâneas, distribuição correta); promoção por digest
ensaiada uma vez de ponta a ponta.

### Fase 1 — Contrato público de automação

Entregas:

- `/api/v1/orca/workspaces/{slug}/units/` (leitura, por slug) com API key.
- `POST /api/v1/orca/.../work-items/` que aceita `responsible_unit` (slug),
  `assignment_policy` e `external_source`/`external_id`, e executa
  criar + vincular + alocar em uma transação, reexecutável.
- Semântica explícita: `assignees` omitido aplica a política; `[]` deixa
  vazio; lista atribui. Fallback "último assignee do criador" removido da
  API pública (mantido, se desejado, só na UI, atrás de flag de projeto).
- Contrato documentado em `docs/orca-public-api.md` e validado contra o
  Compose real (dúvida 17) ou contra um script cliente, se o Compose não
  couber.

Gate: cliente externo cria 50 itens idempotentes duas vezes e o estado final
é idêntico; nenhuma rota `/api/orca/` aceita API key; nenhuma rota
`/api/v1/orca/` aceita sessão sem CSRF.

### Fase 2 — Fila da área e coordenador

Entregas:

- Endpoint de fila: itens por área, com filtros (sem executor, vencidos,
  parados, executor indisponível).
- Tela "Minha Área": caixa de entrada, em execução por integrante, atenção,
  ações assumir / atribuir / reatribuir / devolver à fila.
- Papel de coordenador (decisão da dúvida 14) e permissões correspondentes,
  auditadas em `AssignmentDecision`.
- Aba "Trabalho" em `unit-detail.tsx`, ao lado de membros e projetos.

Gate: coordenador consegue esvaziar uma fila sem tocar em telas nativas do
Plane; nenhuma ação da tela altera acesso (`ProjectMember`); testes de
permissão negativos para Guest, Member comum e coordenador de outra área.

### Fase 3 — Disponibilidade e distribuição

Entregas:

- `OrganizationalUnitMemberAvailability` com declaração manual pelo próprio
  integrante e pelo coordenador; engine respeita.
- Políticas `round_robin` e limites por pessoa; `load_scope` configurável.
- Reatribuição **assistida**: desativação ou indisponibilidade devolve itens
  abertos à fila com marcação; comando com dry-run para lote.
- Eventual sincronização com Entra (out-of-office) só se a dúvida 12 for
  respondida a favor e o tenant existir.

Gate: cenários de férias, saída e retorno cobertos por testes; nenhuma
reatribuição automática sem registro em `AssignmentDecision`.

### Fase 4 — Processos automáticos

Entregas:

- Orquestrador sidecar (ou módulo interno, conforme dúvida 18) que instancia
  um template por evento externo, usando exclusivamente `/api/v1/orca/` e
  webhooks do Plane.
- Agrupamento por instância (decisão da dúvida 19).
- Fechamento automático com estado intermediário, se a dúvida 21 assim
  decidir.
- Auditoria da versão do template usada em cada instância.

Gate: reprocessar o mesmo evento não duplica nada; falha no meio de uma
instância é retomável; desligar o orquestrador não deixa o Plane em estado
inconsistente.

### Fase 5 — Visão executiva

Entregas:

- Permissão de leitura executiva (decisão da dúvida 16).
- Agregados por área e por processo: backlog, sem executor, vencidos, aging,
  throughput, cycle time, concentração por pessoa.
- Drill-down até o item, respeitando o acesso do leitor.

Gate: cada número do dashboard tem uma consulta reproduzível e um teste com
dataset fixo.

---

## 6. Decisões que travam a Fase 1

Sem resposta a estas, o contrato público não pode ser escrito:

- Dúvidas 3, 6, 7 (semântica de fila e onde mora a política).
- Dúvida 8 (escopo de carga), porque muda o engine.
- Dúvida 17 (Compose), porque decide se o contrato é YAML-friendly ou só
  REST.
- Dúvida 20 (atomicidade da operação composta).

---

## 7. O que não fazer

- Não transformar a área em usuário fictício para virar assignee.
- Não colocar todos os integrantes como assignees de tudo.
- Não usar Compose como motor de instâncias antes de confirmar o comportamento
  de re-push.
- Não criar coluna nova em modelo core para "executor principal" ou "área".
- Não automatizar reatribuição na saída de pessoas sem registro e sem período
  assistido.
- Não construir o dashboard executivo antes da fila estar em uso real.
