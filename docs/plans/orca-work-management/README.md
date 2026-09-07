# Plano de execução — Gestão de trabalho por área (Orca)

Este diretório materializa a especificação
[`docs/orca-work-management-rfc.md`](../../orca-work-management-rfc.md) em
itens de trabalho rastreáveis. A especificação diz **o que** e **por quê**;
estes arquivos dizem **em que ordem, em quais arquivos, com que critério de
aceite** e **como provar**.

## Como usar

1. Leia o RFC seções 1 a 3 (conceito, estado atual, decisões fechadas) antes
   de qualquer item. Não reabra uma decisão F1–F24 sem registrar no RFC §4.
2. Abra o quadro abaixo, escolha o próximo item `[ ]` da fase ativa e siga o
   arquivo da fase. Marque `[~]` ao começar e `[x]` ao terminar, no mesmo PR
   que entrega o item.
3. Cada item vira **um PR pequeno** contra `stage`, com o identificador do
   item no título: `feat(orca): [D0.5] assignment service with policy resolution`.
4. Um gate só fecha quando todos os seus critérios estão marcados e o CI de
   `stage` está verde. Registre a data do gate no quadro.
5. Se descobrir algo que muda o desenho, escreva no RFC §4.2 (changelog) e
   ajuste o item aqui. O RFC é a fonte da verdade do desenho; este diretório
   é a fonte da verdade do progresso.

Convenções do repositório (branch, commits, o que a sessão pode e não pode
rodar, migrações, i18n, códigos de erro, copyright): RFC §13. Prompt para
iniciar uma sessão de agente: [`HANDOFF-PROMPT.md`](./HANDOFF-PROMPT.md).

## Ordem das fases

```text
P0 Segurança da plataforma ──┐
                             ├──> 1 Contrato público ──> 2 Fila e coordenador ──> 3 Disponibilidade ──> 4 Processos ──> 5 Visão executiva
D0 Fundação do domínio ──────┘                                  │
                                                                └── Gate 2-mínimo libera ORCA_PUBLIC_API_ENABLED em produção
```

P0 e D0 correm em paralelo e não dependem um do outro. A Fase 1 exige os
dois gates fechados. As demais são sequenciais.

## Quadro de estado

Legenda: `[ ]` não iniciado · `[~]` em andamento · `[x]` concluído · `[-]` descartado (registrar motivo).

| Fase                       | Arquivo                                                      | Itens             | Estado                                                                                                | Gate fechado em |
| -------------------------- | ------------------------------------------------------------ | ----------------- | ----------------------------------------------------------------------------------------------------- | --------------- |
| P0 Segurança da plataforma | [P0-platform-hardening.md](./P0-platform-hardening.md)       | 21                | `[~]` 18/21 (P0.0–P0.11, P0.14–P0.16, P0.18–P0.20) · P0.12, P0.13 e P0.17 parciais                    | —               |
| D0 Fundação do domínio     | [D0-domain-foundation.md](./D0-domain-foundation.md)         | 12                | `[~]` 12/12 · migrações e `check:types` fechados em 07/09 — falta só a auditoria num dump de `stage`  | —               |
| 1 Contrato público         | [01-public-contract.md](./01-public-contract.md)             | 8                 | `[x]` 8/8 · **iniciada e concluída com os gates P0 e D0 abertos**; o Gate 1 continua exigindo os dois | —               |
| 2 Fila e coordenador       | [02-queue-and-coordinator.md](./02-queue-and-coordinator.md) | 6 (+ gate mínimo) | `[~]` 2/6 + 2.3 parcial (07/09) · 2.1 e 2.2 `[x]`, 2.3 mínima entregue, **2.4 não entregue**          | —               |
| 3 Disponibilidade          | [03-availability.md](./03-availability.md)                   | 6                 | `[ ]` 0/6                                                                                             | —               |
| 4 Processos                | [04-processes.md](./04-processes.md)                         | 7                 | `[ ]` 0/7                                                                                             | —               |
| 5 Visão executiva          | [05-executive-view.md](./05-executive-view.md)               | 4                 | `[ ]` 0/4                                                                                             | —               |

## Próximo item recomendado

**Estado em 07/09/2026 — leia isto primeiro, e confie nesta seção mais do que
na sua memória do que este arquivo dizia ontem.** `stage` está em `f490a2d7`.

**Os PRs #12, #13 e #14 estão mesclados** — os três tips são ancestrais de
`f490a2d7`, verificado por `git merge-base --is-ancestor`. Foram: #12 (1.1,
1.2, 1.3 e o 1.6 parcial — a fundação da API de automação, sem superfície
HTTP), #13 (1.4 → 1.8, as seis rotas) e #14 (P0.18, credenciais do Compose sem
default). Com isso a **Fase 1 está com os 8 itens entregues e em `stage`**, não
numa branch.

Duas armadilhas ao conferir isso no `git log`. O #12 **não tem commit de merge
próprio**: a branch do #13 foi cortada da ponta do #12, então mesclar o #13
trouxe os dois e o GitHub marcou o #12 como mesclado sem produzir a linha
`Merge pull request #12`. E procurar essa linha encontra `3a4c1715`, que é o
**PR #12 do repositório-pai** (release-please), não este — a numeração colide.
O teste que vale é `merge-base --is-ancestor`, não o `grep` na mensagem.

**O PR #15 está aberto** (`claude/pendencias-implementacao-auto-7a2rsp`,
`31d35e2b`): P0.19 e P0.20. Ele importa mais do que o tamanho sugere. O P0.19
descobriu que `ORCA_PUBLIC_API_ENABLED` e `ORCA_PUBLIC_API_RATE_LIMIT` eram
documentadas no README, lidas pelo `settings/common.py` e **nunca
encaminhadas** pelo `docker-compose-orca.yml`: ligar a API pública na
plataforma não tinha efeito nenhum, e portanto o **Gate 2-mínimo era
impossível de cumprir** — o critério "deploy com a flag ligada" não podia
passar nem em princípio. Só depois do #15 o Gate 2-mínimo é alcançável. O
P0.20 dá teto ao `AutomationOperation`, que crescia sem expurgo.

A flag continua desligada — `ORCA_PUBLIC_API_ENABLED` tem `default=False` em
`apps/api/plane/settings/common.py:609` e o Compose passa `:-0` — e **ninguém
a liga fora do Gate 2-mínimo**. O que a Fase 1 entregou atrás dela (as seis
rotas, o serviço de idempotência do §6.7, `docs/orca-public-api.md`, o cliente
de referência em `tools/orca-client/` e os testes de contrato sobre HTTP real)
está no §Histórico, na linha de 06/09.

**O próximo bloco é a Fase 2** (fila da área e coordenador). O arquivo da
fase é [`02-queue-and-coordinator.md`](./02-queue-and-coordinator.md); o plano
de execução da madrugada de 07/09 — decisões M1–M12, contratos entre sessões,
ordem de merge e linhas de corte — está em
`docs/plans/orca-work-management/MADRUGADA-2026-09-07.md`, que **vive na
branch `claude/fork-architecture-review-pmszir`** e não nesta árvore:

```bash
git fetch origin claude/fork-architecture-review-pmszir
git show origin/claude/fork-architecture-review-pmszir:docs/plans/orca-work-management/MADRUGADA-2026-09-07.md
```

Toda branch nova nasce da **ponta do #15**, não de `stage`, enquanto ele não
mesclar; é o mesmo arranjo com que o bloco 1.4 → 1.8 nasceu da ponta do #12. O
item 2.2 já tem a consulta da fila pronta em `app/services/orca/queue.py`.

### O que a sessão de agente pode rodar — a versão correta

Isto estava errado neste arquivo, no `HANDOFF-PROMPT.md` e no `AGENTS.md`, e
o erro tinha consequência: itens ficaram esperando um humano rodar o que a
sessão sempre pôde rodar. Medido em 07/09 na ponta do #15:

- **Backend:** `pytest` (a suíte Orca inteira, ou um arquivo),
  `makemigrations --check` e `migrate` de ida e volta. Receita em
  [`HANDOFF-PROMPT.md`](./HANDOFF-PROMPT.md) §Ambiente local — Backend
  (venv, PostgreSQL 16, Redis; ≈ 5 min).
- **Frontend:** `pnpm install --frozen-lockfile` (19 s com store quente),
  `pnpm check:types --filter=web` (**exit 0 em 61 s** — pelo turbo; a forma
  `pnpm --filter web check:types` falha por não construir os pacotes do
  workspace antes, e a falha é o build ausente, não o código),
  `pnpm --filter web check:lint`, `check:format` e
  `pnpm --filter @plane/i18n check:sync`, todos em segundos.
- **O que continua de fato fora:** Docker (não há daemon), deploy,
  `git push --delete`, e qualquer verificação que precise de um banco **com
  dados** — que é exatamente o que os Gates P0 e D0 ainda pedem. Um banco
  montado aqui nasce vazio.

A regra que sobrevive do `AGENTS.md` é sobre **saída**, não execução:
redirecionar para arquivo e ler o `tail`, nunca despejar a saída no contexto.

### Os defeitos D1–D4 estão fechados

Fechados no PR #9 (05/09/2026), cada um pinado por teste; a lista está em
[RFC §2.2](../../orca-work-management-rfc.md) e a tabela por invariante em
[`D0-domain-foundation.md`](./D0-domain-foundation.md) §Testes por invariante.
**Não são trabalho a fazer.** Uma sessão que os leia como pendência vai
"corrigir" o que já está corrigido — foi por isso que esta seção foi reescrita.

### O que falta, e de quem é

Nada do que falta em P0, D0 e Fase 1 é código:

- **Gate D0:** o arquivo da fase tem **um** critério aberto —
  `audit_organizational_routing` sem violações num dump do banco de `stage`
  ([`D0-domain-foundation.md`](./D0-domain-foundation.md) §Gate D0); os outros
  cinco estão marcados. O texto que estava aqui e a coluna do quadro listavam
  outras duas coisas que **não são critérios do gate**: a ida e volta das
  migrações num banco com dados (útil, mas não escrita ali) e
  `pnpm --filter web check:types` — que, além de não ser critério, **roda na
  sessão com exit 0**. Só o dump continua fora de alcance.
- **Gate 1:** revisão do `docs/orca-public-api.md` por quem não escreveu o
  código, executando os `curl` contra staging com a flag em `1` **só em
  staging**; o registro de quem verificou que ela segue `0` em produção; e a
  medição de p50/p95 em 200 criações sequenciais na mesma área com
  `least_loaded`. As quatro caixas estão em
  [`01-public-contract.md`](./01-public-contract.md) §Gate 1 — onde as duas
  primeiras ("8 itens `[x]`" e "critérios do 1.8 verdes") continuam abertas
  por escrituração, apesar de o quadro acima contar 8/8; quem fechar o gate
  reconcilia isso.
- **P0.12:** verificação completa e refeita; falta o `git push --delete`,
  barrado para a sessão de agente.
- **P0.13:** o ensaio do runbook num ambiente real e a decisão do prerelease
  no Release Please, que só o primeiro PR de release revela.
- **P0.17:** o texto de implantação foi neutralizado; falta a decisão de
  negócio sobre qual é o alvo real da 4UM.
- **Operação, e uma delas já vencida:** o `TRUSTED_PROXIES` obrigatório está
  em `stage` desde o PR #8, então é **antes do próximo deploy**, não "antes de
  mesclar" — sem a variável o Compose Orca recusa subir, de propósito. Junto:
  rotacionar as credenciais que o P0.18 deixou de aceitar com default;
  invalidar as contas criadas pela versão antiga do `create_users.py`
  (procedimento em `tools/migration/README.md`); e empurrar o mirror
  `origin/upstream`, que continua em `1.4.1` — verificado nesta sessão com
  `git show origin/upstream:package.json` — enquanto o fork já está em
  `1.5.0-plane.1.4.2`.

A cadeia de proveniência do release está fechada no código (P0.0–P0.3, P0.14,
P0.15): PR não publica `:stage`, todo commit de `stage` ganha `:sha-<commit>`
nos seis serviços, e a promoção para produção copia digests daquele commit em
vez de seguir uma tag mutável. O que falta ali é o ensaio em ambiente real.

O relato item por item de como cada coisa fechou está no §Histórico, que é o
lugar dele; esta seção diz só onde o trabalho está e para onde vai.

## O que a madrugada de 07/09 entregou, e o que não entregou

Sete sessões de agente em paralelo, plano em `MADRUGADA-2026-09-07.md` (branch
`claude/fork-architecture-review-pmszir`). Cinco entregaram, duas não.

**Entregue e verificado na árvore integrada** (`feat/orca-phase2-minimum`, PR #18):

- **2.1 e 2.2 completos** — coordenador, proveniência própria no grant, helpers
  de permissão e os oito endpoints internos, com 94 testes novos.
- **2.3 na parte mínima** — a aba Trabalho, a caixa de entrada, as três ações e
  as 19 locales.
- **Saneamento da documentação** (PR #16) — o RFC, este README, o
  `HANDOFF-PROMPT.md` e o `AGENTS.md` deixaram de afirmar coisas falsas.
- **Evidência de gate** — o Gate D0 fechou tudo menos a auditoria num dump, e o
  runbook de desligar a API pública entrou em `docs/orca-public-api.md`.
- **Revisão adversarial da `stage`** (PR #17) — 20 achados em
  `reviews/2026-09-07-stage-adversarial-review.md`, **nenhum S1**, seis S2. Os
  dois que mudam a ordem das coisas: `A3`, em que a retenção do P0.20 reescreve
  linha append-only de `AssignmentDecision`, e `A6`, em que uma falha
  transitória queima a chave de idempotência por 30 dias.

**Não entregue:**

- **2.4 (alertas)** — a sessão foi rejeitada pelo limite de uso de 5 horas ao
  ser disparada e não executou nada. É o próximo item da fase, e é o que
  **bloqueia o Gate 2-mínimo**, cujo critério de alerta não tem outro caminho.
- **A integração automática** — a sessão que a faria nasceu sem repositório
  anexado, um erro de despacho; foi refeita à mão, com a verificação completa
  registrada no corpo do PR #18.

## Pendências externas (não bloqueiam P0/D0)

| Ref. | Pendência                                                                                          | Quem                            | Necessária em                                                 |
| ---- | -------------------------------------------------------------------------------------------------- | ------------------------------- | ------------------------------------------------------------- |
| A5   | Confirmar comportamento do Plane Compose em re-push com mesmo id e ausência de campo de área       | pessoa com acesso à doc oficial | Fase 4                                                        |
| —    | Faixa de rede do proxy/ingress da 4UM para `TRUSTED_PROXIES`                                       | operação                        | P0.7                                                          |
| —    | Alvo real de implantação (o repositório documenta Coolify, que é o ambiente da Orca, não o da 4UM) | negócio/operação                | P0.17                                                         |
| —    | Tenant Azure de testes para validar P0.10 de ponta a ponta                                         | operação                        | fim de P0 (o item é implementável com testes unitários antes) |
| —    | Definir quem é coordenador de cada área piloto                                                     | negócio                         | Fase 2                                                        |
| —    | Área piloto e projeto piloto para o primeiro uso real da fila                                      | negócio                         | Gate 2-mínimo                                                 |

## Histórico

| Data       | Evento                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 2026-09-07 | **Madrugada de sete sessões paralelas.** Entregues 2.1, 2.2 e a parte mínima de 2.3, mais o saneamento da documentação, a evidência de gate que não precisa de staging e uma revisão adversarial da `stage` com 20 achados e nenhum S1. O item **2.1 era maior do que o arquivo da fase dizia**: `OrganizationalUnitGrant.membership` era FK obrigatória e um coordenador pode não ser membro da área, então a proveniência exigiu FK própria, `grant_source` e CHECK de exclusividade (RFC §5.1, rev. 7); a migração saiu `0139` e a Fase 3 passou a `0140`. **Duas sessões falharam, por motivos diferentes:** a do item 2.4 foi rejeitada pelo limite de uso de 5 horas ao ser disparada às 06:45 UTC e não executou nada, e a de integração nasceu sem repositório anexado. A integração foi refeita à mão: quatro branches mescladas sem conflito, e na árvore integrada **861 testes Orca passando** (baseline 767, nenhuma queda) em 10m49s, **9 de contrato** em 2m01s, `makemigrations --check` limpo, `0139` aplicada/revertida/reaplicada, `ruff check` limpo, e `check:types`, `check:lint`, `check:format` e `check:sync` do i18n todos exit 0. Também fechado por execução o critério `check:types` do Gate D0, que estava aberto por premissa errada: `pnpm --filter web check:types` isolado falha porque a tarefa depende de `^build`; `pnpm check:types --filter=web`, pelo turbo, passa. |
| 2026-09-07 | **Saneamento do registro de progresso (S0 do plano da madrugada).** O registro afirmava coisas falsas que faziam uma sessão nova refazer trabalho pronto: o cabeçalho do RFC dizia que nenhuma seção marcada como proposta estava implementada (com a Fase 1 inteira em `stage`) e fixava a base em v1.4.1; o §2.2 dizia que **nenhum** dos defeitos D1–D4 tinha teste, quando os quatro fecharam no PR #9 e cada um está pinado — os testes foram executados nesta sessão para confirmar antes de o texto mudar; este README dizia que o PR #12 estava aberto, quando #12, #13 e #14 estão mesclados e o **#15** é o aberto; e o RFC §13, o `AGENTS.md` e o `HANDOFF-PROMPT.md` diziam que a sessão não roda `pnpm`/`check:types`/migrações, o que era premissa e não fato — `pnpm check:types --filter=web` dá exit 0 em 61 s pelo turbo. Plano da noite em `MADRUGADA-2026-09-07.md`, na branch `claude/fork-architecture-review-pmszir`. |
| 2026-09-07 | **P0.20**: `AutomationOperation` não tinha expurgo — uma linha por mutação aceita, com o `response_snapshot` inteiro, crescendo sem teto, enquanto os logs de API/webhook/e-mail do upstream têm janela e job diário. Tarefa nova reaproveitando `process_cleanup_task`, janela de 30 dias em `ORCA_AUTOMATION_OPERATION_RETENTION_DAYS`, entrada no `beat_schedule` **e** no `CELERY_IMPORTS` (sem a segunda, o beat manda um nome que o worker nunca registrou e a tarefa falha em silêncio uma vez por dia — o `test_celery_task_registration.py` já dizia isso das outras duas). A decisão de desenho está no RFC §4.2 rev. 6: uma chave deixa de ser lembrada para sempre, e a janela é muito maior que as do upstream justamente porque apagar um recibo desgasta a chave. **Rodado**: 22 passed (11 novos + os 11 do registro), `makemigrations --check` limpo, e ruff `check`/`format` limpos na versão pinada do CI. O teste pegou uma afirmação errada minha, repetida no docstring e na doc do cliente: um replay de criação responde **201**, a resposta gravada, não 200 — o que distingue um replay não é o status. |
| 2026-09-07 | **P0.19**: auditoria das variáveis de implantação. As duas flags da API pública eram documentadas, lidas por `settings` e **nunca encaminhadas** pelo `docker-compose-orca.yml` (lista `environment:` explícita, sem `env_file`), nos quatro serviços da imagem da api — ligar `ORCA_PUBLIC_API_ENABLED=1` não fazia efeito nenhum e o Gate 2-mínimo era inalcançável. `DOMAIN_NAME` e `WEB_URL` eram fixados nas variáveis mágicas do Coolify, então um valor explícito era ignorado e fora do Coolify a stack subia em `localhost` — que `common.py:326` usa para as URLs de anexo do MinIO e o `work_item_url()` da Fase 1 para o `web_url` da resposta. Corrigidas com cadeia de fallback, verificadas nos três caminhos com `docker compose config`, e o job novo `compose_env_forwarding` gateia `build-push`: provado nas duas direções (falha na árvore anterior nomeando os três achados; falha numa flag removida de um só serviço). Auditado e **limpo**: paridade i18n (28 namespaces × 19 locales), os 32 códigos de erro entre `orca_error_codes.py`, `error-codes.ts` e as 19 locales, e nenhum `TODO`/stub no código Orca. |
| 2026-09-06 | **Bloco 1.4 → 1.8 executado inteiro.** Seis rotas em `/api/v1/orca/`, o serviço D0.5 estendido em vez de copiado (`trigger`, `collaborators`, `automation_operation`, `expected_decision_id`, todos com o default que a função já escrevia), a fila como serviço que o 2.2 reaproveita, `docs/orca-public-api.md`, `tools/orca-client/` e testes de contrato sobre HTTP real no merge gate. Local: **99 testes novos** (41 criação, 16 áreas/fila, 22 reatribuição/transferência, 11 no serviço, 9 de contrato em 2m01s) e a suíte inteira verde — `pytest plane/tests/unit/orca plane/tests/contract/test_orca_public_contract.py` → **1017 passed, 0 failed** em 34m31s. **Três defeitos que só a execução pegou**, dois deles de código: (1) `WorkItemNotFound` e `IfMatchRequired` são levantados antes de o recibo existir e escapavam do `try` do bloco idempotente — dois caminhos documentados como 404 e 428 respondiam **500**; corrigido com `handle_exception` na base pública, que uma rota futura herda. (2) **O pior achado**: `transaction.on_commit` dispara a atividade nativa *depois* do commit, e um broker fora do ar propagava a exceção — o item ficava criado, o recibo virava `failed` e **todo retry daquela chave replicava o 500 para sempre**, deixando trabalho real que o sistema chamador acredita não existir. A publicação passou a registrar em log em vez de estourar; é também o que permite o arquivo de contrato rodar no job sem RabbitMQ. (3) A constraint I3 recusou uma fixture que criava `assigned` sem executor — o teste estava errado, a constraint certa. Registrados no RFC §4.2 mais dois esclarecimentos: 412 público × 409 interno para `ORG_DECISION_STALE`, e que a autorização de projeto roda antes do recibo (uma chamada não autorizada não gasta a chave de quem a enviou). |
| 2026-09-05 | **Bloco 1.4 → 1.8 planejado** em `01-public-contract.md`: ordem em oito passos com commit e prova por passo, quinze decisões fechadas (B1–B15: 412 público × 409 interno para `ORG_DECISION_STALE`; serviço D0.5 ganha `trigger`/`collaborators`/`automation_operation`/`expected_decision_id` com defaults que preservam o comportamento; recibo antes da validação; `default_assignee_id=None` para desligar o D2 no caminho público; `completion_due_at` recusado até a Fase 4; fila como serviço reutilizável pelo 2.2; contrato entra no job `api_tests`). Branch do bloco cortada da ponta do PR #12. Ambiente local refeito e confirmado (receita no `HANDOFF-PROMPT.md`): baseline `pytest plane/tests/unit/orca -q -m unit` sobre a ponta do PR #12 → **663 passed, 0 failed** em 8m08s (255 deselecionados são os testes do diretório sem o marker `unit`); o `HANDOFF-PROMPT.md` deixou de afirmar que a sessão não roda pytest.                                                                                                                                                                                                                                                                                                                  |
| 2026-09-03 | Plano criado a partir do RFC rev. 2. Nenhum item iniciado.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 2026-09-04 | PRs #5 e #6 mesclados em `stage` (`3a4c769`): hardening complementar da camada de Áreas (kill switch nas tarefas/comandos/SCIM, baseline ao elevar papel, rate limit SCIM pós-autenticação, rejeição de convidados Entra). Não fecha item P0/D0; registrado no cabeçalho de P0.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| 2026-09-04 | Revisão externa do commit `3a4c769` verificada contra o código. Achado novo: Compose apontava para o namespace do repositório-pai. Itens criados: P0.0, P0.14, P0.15, P0.16, D0.11, D0.12; critério novo em P0.10. P0.0 e P0.14 entregues no mesmo PR.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| 2026-09-05 | CI do PR #8 verde em tudo que terminou: `API Lint (ruff)`, `API Tests (pytest)`, `Code Quality Checks` (formato, lint, `check:sync`, tipos), proveniência do Compose, copyright e os **seis** builds. O log do build de PR confirma o P0.1: tag `pr-8-<sha>` e nenhuma publicação.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| 2026-09-05 | P0.12 verificado (falta executar): os três branches do enunciado confirmados como superados contra o código — inclusive o único commit que parecia valer um port, que corrige um bloco de settings inexistente em `stage` — e mais nove branches `claude/*` identificados como totalmente contidos em `stage`. A sessão não tem permissão para apagar branch remoto; comandos e SHAs registrados no item.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 2026-09-05 | P0.13 parcial: `docs/release-runbook.md` escrito (fluxo em duas etapas, verificação pós-deploy, rollback por digest), template de RC e FORK.md §Phase 4 alinhados ao que os workflows fazem. O bump de versão fica para o PR do sync (P0.11); achado registrado: o sufixo `-plane.<upstream>` é um prerelease semver e o Release Please pode descartá-lo.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 2026-09-05 | P0.10 entregue: `id_token` do Entra verificado por completo contra o JWKS do tenant (assinatura, `aud`, `iss`, janela de validade, claims obrigatórias), nonce de uso único no fluxo, timeouts em todas as chamadas OAuth, dois códigos de erro novos nos cinco lugares que os espelham, e testes com par de chaves RSA. Item novo P0.17 (documentação de implantação presume Coolify, que não é o ambiente da 4UM).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 2026-09-05 | P0.15 entregue: as imagens gravam o commit (`ORCA_BUILD_SHA`/`ORCA_IMAGE_TAG`), `GET /api/orca/build-info/` responde a admin de instância e `manage.py orca_build_info` responde no worker e no beat, que não têm HTTP. Fecha no runtime a cadeia que P0.0–P0.3 fecharam no registry.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| 2026-09-05 | P0.16 entregue: MinIO fixado em tag imutável no Compose Orca e no de teste; CI passa a rodar PostgreSQL 15.7, igual ao ambiente implantado, com a decisão registrada em `RUNNING_TESTS.md`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 2026-09-05 | P0.9 entregue: job `api_lint` roda `ruff check` e `ruff format --check` em `apps/api` com a versão fixada em `requirements/local.txt`; 30 achados de lint corrigidos, 23 arquivos do fork formatados e 38 arquivos upstream em exclusão temporária de formatação até o sync do P0.11.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| 2026-09-05 | P0.7 entregue: `trusted_proxies` sem default aberto nos dois Caddyfiles, variável obrigatória no Compose Orca e encaminhada (faixas privadas) no Compose padrão. Achado: a variável não era encaminhada a nenhum dos dois, então o `0.0.0.0/0` valia sempre.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| 2026-09-05 | P0.6 entregue: contas migradas passam a nascer sem senha utilizável (`set_unusable_password` + `is_password_autoset`), com testes e com o procedimento de invalidação das contas já criadas no README da ferramenta.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 2026-09-05 | P0.5 parcial: permissões mínimas por job em `stage.yml` e `prod.yml`. A metade do lockfile ficou bloqueada por um achado — `pnpm-lock.yaml` está defasado em relação ao catálogo do workspace desde o commit upstream `31853ab2` (46 dependências com `catalog:` no `package.json` e especificador resolvido no lockfile), então `--frozen-lockfile` falharia em todo run. Precisa de `pnpm install --lockfile-only` no mesmo commit da troca da flag.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| 2026-09-05 | P0.4 entregue na mesma branch: `promote-rc` passa a usar `gh`, verifica a existência de `prod`, e falha quando a RC não existe nem pôde ser criada.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| 2026-09-05 | P0.1, P0.2 e P0.3 entregues em `claude/loving-carson-n9x6eq`: PR constrói sem publicar; push em `stage` publica `:stage` e `:sha-<commit>` e retagueia por digest os serviços não reconstruídos, para que todo commit tenha os seis serviços; artifact `image-digests`; `prod.yml` resolve o commit de `stage` promovido e copia os digests daquele commit, falhando antes de qualquer retag se faltar imagem.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| 2026-09-05 | D0 completa (D0.1–D0.12) na branch `feat/orca-unit-project-coverage`: cobertura área↔projeto, herança de assignee removida da API pública, estado de fila, política e log de decisões, serviço único de alocação, endpoints internos falando com ele, comando de auditoria, métricas, matriz de testes, documentação, reconciliação no arquivamento e roster SCIM. Falta só a execução do Gate D0 (suíte, migrações, auditoria num dump).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 2026-09-05 | **P0.5 fechado, corrigindo o achado do próprio dia.** `pnpm install --frozen-lockfile` foi executado inteiro sobre `0e4ab05c` com o pnpm 11.3.0 do `packageManager`: exit 0, 1459 pacotes instalados, `pnpm-lock.yaml` idêntico depois; `--lockfile-only` antes disso também não gerou diff. Das 11 entradas de catálogo ausentes do lockfile, 2 não são referenciadas por workspace nenhum e as 9 restantes estão pinadas em versão exata igual ao especificador gravado — o pnpm resolve `catalog:` antes de comparar. A checagem foi verificada no sentido oposto com uma divergência plantada (`chroma-js` → `^3.0.0`): `ERR_PNPM_OUTDATED_LOCKFILE`, exit 1. Flag trocada no `stage.yml`.                                                                                                                                                                                                                                                                                                                                                                           |
| 2026-09-05 | **Execução local provou o que a Fase 1 tinha entregue no escuro.** O CI do PR #12 abriu vermelho; em vez de adivinhar, a sessão montou PostgreSQL 16 e Redis no próprio contêiner e rodou a suíte. Resultado: `makemigrations --check --dry-run` limpo, `0138` aplicada/revertida/reaplicada, 1001 testes verdes — e três erros que só a execução pegaria. (1) **Bug de código**: `start_operation` lia pelo manager padrão, que esconde linhas soft-deletadas, enquanto a constraint de idempotência é incondicional — um recibo soft-deletado ficava invisível, o INSERT batia na constraint e a releitura estourava `DoesNotExist`; replay virava crash. Corrigido para `all_objects`. (2) **Teste que testava nada**: a classe do teste do kill switch definia `initial` e sobrescrevia o do mixin em vez de encadear, então todas as asserções do gate passavam sem exercitar o gate. (3) **Duas suposições erradas seguidas** sobre o soft delete de `APIToken`; o comportamento real é que o cascade anula a FK e preserva o recibo.                              |
| 2026-09-05 | **Fase 1 iniciada com os gates abertos**, a pedido, e o desvio registrado no cabeçalho do arquivo da fase. 1.1: modelos `ExternalWorkItemBinding` e `AutomationOperation`, migração `0138` à mão, e a FK `AssignmentDecision.automation_operation` que o D0.4 adiou por não poder apontar para tabela inexistente. 1.2: `ORCA_PUBLIC_API_ENABLED` com o parser estrito do P0.14 — **não** o `== "1"` que o item pedia, que é o defeito que aquele item fechou —, throttle por id de token lido do view (nunca de `request.auth`, que é o segredo em texto puro), e `public_api_enabled` no `OrcaConfigEndpoint`. 1.3: serviço de idempotência com os ramos do §6.7, incluindo a retomada que reinicia o relógio e a corrida resolvida pela constraint. 1.6 parcial: dez códigos novos (4922–4931) nos três lugares e nas 19 locales, `sync:check` em 100%; falta o header `Idempotent-Replay`, que depende do 1.4. Achado para o 1.5: `DecisionStale` responde 409 no D0.5 e o RFC §7.3 exige 412 na rota pública, então o status terá de ser mapeado em vez de herdado. |
| 2026-09-05 | **P0.8 fechado pelo run.** PR #10 verde nos 16 checks (run 33974385564). `API Tests (pytest)` **success** em 9m45s com `plane/tests/unit -q -m unit` — a suíte upstream inteira, **zero exclusões**, confirmando a previsão. A previsão vinha de ter lido antes os candidatos a falhar sem MinIO/RabbitMQ: `test_storage.py` e `test_copy_s3_objects.py` mockam `boto3`, e `test_ssrf_advisories.py` faz `patch` em `socket.getaddrinfo` e em `requests.Session` — nada na suíte unit toca a rede. Os `ERROR` de constraint no log do PostgreSQL do run são as asserções negativas do D0, não defeito. `Code Quality Checks` verde no mesmo run fecha também o último critério do P0.5.                                                                                                                                                                                                                                                                                                                                                                                  |
| 2026-09-05 | PR #11 mesclado em `stage` (`af571341`): **P0.11 fechado** e a metade de versão do P0.13 junto. Fica em aberto no item o mirror `origin/upstream`, ainda em 1.4.1.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| 2026-09-05 | P0.11 entregue no PR #11: upstream `v1.4.2` (`5f7d9278`) são 3 commits e uma mudança real (auto-reload em falha de chunk após deploy). Conflito único na versão do `package.json` raiz, resolvido para `1.5.0-plane.1.4.2`, com o manifest do Release Please junto — fecha a metade de versão do P0.13. Merge com os dois pais confirmado; `--frozen-lockfile` continua exit 0 depois dele. O mirror `origin/upstream` ficou uma release atrás: a sessão não tem autorização para empurrar naquela branch, e o comando está no corpo do PR.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 2026-09-05 | P0.8 ligado (falta o run): `api_tests` roda `pytest plane/tests/unit -q -m unit`, sem exclusões; job manual `api_integration_tests` roda `contract/` e `smoke/` pelo `docker-compose-test.yml`; `RUNNING_TESTS.md` documenta os dois e a política de exclusões. Achado: o workflow **não declarava `workflow_dispatch`**, embora `changes`, `ci` e `api_tests` já testassem `github.event_name == 'workflow_dispatch'` — condições mortas, e nenhuma forma de rodar o workflow à mão. Gatilho declarado.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| 2026-09-05 | P0.12 reverificado por `merge-base --is-ancestor` contra `0e4ab05c`, não pela lista anterior: os tips dos três branches superados agora estão todos anotados (faltavam dois) e mais dois branches entraram na lista de apagar, porque seus PRs foram mesclados desde o levantamento (`claude/loving-carson-n9x6eq`, `feat/orca-unit-project-coverage`). `git push --delete` barrado de novo; sem ferramenta de deleção de branch no MCP do GitHub.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| 2026-09-05 | P0.17 na metade: README §Self-Hosting deixa de tratar o Compose como coisa de Coolify (Quick Start neutro em três condições, passos do Coolify num `<details>` de exemplo, `SERVICE_FQDN_PROXY` identificado como variável do Coolify) e `FORK.md` §Phase 3 passa a dizer que o deploy de staging é opt-in por `COOLIFY_DEPLOY_ENABLED`. Falta a decisão de negócio sobre o alvo real da 4UM.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| 2026-09-05 | PR #8 mesclado em `stage` (`1fb7a074`); `stage` mesclado na branch da D0, com a leitura de `default_assignee_id` recolocada — o git tinha aceitado a remoção do lado do #8 em silêncio e o resultado seria `NameError`. Primeira execução real da suíte D0: 19 falhas, das quais duas eram bug de produto — `routing_state` em `varchar(16)` recusando `allocation_failed` (17 caracteres) e `rank_candidates` sem checagem de cobertura. Corrigidas; PR #9 verde nas 16 checks.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
