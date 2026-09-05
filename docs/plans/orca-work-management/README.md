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

Convenções do repositório (branch, commits, o que não rodar, migrações,
i18n, códigos de erro, copyright): RFC §13. Prompt para iniciar uma sessão
de agente: [`HANDOFF-PROMPT.md`](./HANDOFF-PROMPT.md).

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

| Fase                       | Arquivo                                                      | Itens             | Estado                                                                                                         | Gate fechado em |
| -------------------------- | ------------------------------------------------------------ | ----------------- | -------------------------------------------------------------------------------------------------------------- | --------------- |
| P0 Segurança da plataforma | [P0-platform-hardening.md](./P0-platform-hardening.md)       | 18                | `[~]` 13/18 (P0.0–P0.7, P0.9, P0.10, P0.14, P0.15, P0.16) · P0.8, P0.12, P0.13 e P0.17 parciais · P0.11 aberto | —               |
| D0 Fundação do domínio     | [D0-domain-foundation.md](./D0-domain-foundation.md)         | 12                | `[~]` 12/12 · suíte verde no CI — faltam migrações, `check:types` e a auditoria num dump                       | —               |
| 1 Contrato público         | [01-public-contract.md](./01-public-contract.md)             | 8                 | `[ ]` 0/8                                                                                                      | —               |
| 2 Fila e coordenador       | [02-queue-and-coordinator.md](./02-queue-and-coordinator.md) | 6 (+ gate mínimo) | `[ ]` 0/6                                                                                                      | —               |
| 3 Disponibilidade          | [03-availability.md](./03-availability.md)                   | 6                 | `[ ]` 0/6                                                                                                      | —               |
| 4 Processos                | [04-processes.md](./04-processes.md)                         | 7                 | `[ ]` 0/7                                                                                                      | —               |
| 5 Visão executiva          | [05-executive-view.md](./05-executive-view.md)               | 4                 | `[ ]` 0/4                                                                                                      | —               |

## Próximo item recomendado

A cadeia de proveniência do release está fechada no código: P0.0 e P0.14
(branch `claude/wayfinder-areas-review-yt98v5`) e P0.1 → P0.2 → P0.3 (branch
`claude/loving-carson-n9x6eq`) — PR não publica `:stage`, todo commit de
`stage` ganha `:sha-<commit>` nos seis serviços, e a promoção para produção
copia digests daquele commit em vez de seguir uma tag mutável. Falta o
ensaio em CI/ambiente real dos critérios que só operação pode marcar.

P0.4 (o job `promote-rc` que ficava verde sem criar o RC), a metade de
permissões do P0.5 e o P0.6 (senha fixa na migração de usuários) foram na
mesma branch, junto com **P0.7** (`TRUSTED_PROXIES` sem fallback aberto).
**P0.9** (ruff obrigatório no CI) e **P0.10** (validação completa do `id_token`
do Entra, nonce e timeouts) também entraram.

**P0.5 fechou**: o "bloqueio do lockfile" registrado em 05/09 era um
diagnóstico errado. `pnpm install --frozen-lockfile` **não falha** — foi
executado na íntegra sobre `0e4ab05c` (exit 0, lockfile intacto), e a
checagem foi verificada também no sentido oposto, com uma divergência
plantada de propósito. As 11 entradas de catálogo "faltando" no lockfile são
2 catálogo morto e 9 pinadas em versão exata, que o pnpm resolve antes de
comparar. A flag está trocada no `stage.yml`.

Restam em P0: **P0.11** (sync com o upstream 1.4.2, o único item ainda sem
nada feito e o mais pesado), **P0.13** (bump de versão, que depende do
P0.11, e o ensaio do runbook), **P0.8** (a suíte upstream já está ligada no
CI; falta o run verde dizer se alguma exclusão é necessária), **P0.12**
(a verificação está completa e refeita; falta só o `git push --delete`, que
a sessão de agente não tem permissão de executar) e **P0.17** (o texto de
implantação foi neutralizado; falta a decisão de negócio sobre qual é o
alvo real da 4UM).

No domínio, a **D0 está com os 12 itens entregues** e a suíte Orca verde no CI
do PR #9. O que falta para fechar o gate não é código, são as três coisas que
a sessão de agente não executa (AGENTS.md): aplicar e reverter as migrações
`0135`–`0137` num banco com dados (com `makemigrations --check` limpo),
`pnpm --filter web check:types`, e passar o `audit_organizational_routing`
num dump de `stage`. Fechado o gate, a **Fase 1** (contrato público) é o
próximo bloco.

Pendências de operação, **uma delas bloqueante e já vencida**: o
`TRUSTED_PROXIES` obrigatório entrou em `stage` com o merge do PR #8, então
não é mais "antes de mesclar" — é **antes do próximo deploy**, porque sem a
variável o Compose Orca recusa subir, de propósito. Também aberto: invalidar
as contas criadas pela versão antiga do `create_users.py` (procedimento em
`tools/migration/README.md`).

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

| Data       | Evento                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-09-03 | Plano criado a partir do RFC rev. 2. Nenhum item iniciado.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 2026-09-04 | PRs #5 e #6 mesclados em `stage` (`3a4c769`): hardening complementar da camada de Áreas (kill switch nas tarefas/comandos/SCIM, baseline ao elevar papel, rate limit SCIM pós-autenticação, rejeição de convidados Entra). Não fecha item P0/D0; registrado no cabeçalho de P0.                                                                                                                                                                                                                                                                                                                                                                                                                |
| 2026-09-04 | Revisão externa do commit `3a4c769` verificada contra o código. Achado novo: Compose apontava para o namespace do repositório-pai. Itens criados: P0.0, P0.14, P0.15, P0.16, D0.11, D0.12; critério novo em P0.10. P0.0 e P0.14 entregues no mesmo PR.                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| 2026-09-05 | CI do PR #8 verde em tudo que terminou: `API Lint (ruff)`, `API Tests (pytest)`, `Code Quality Checks` (formato, lint, `check:sync`, tipos), proveniência do Compose, copyright e os **seis** builds. O log do build de PR confirma o P0.1: tag `pr-8-<sha>` e nenhuma publicação.                                                                                                                                                                                                                                                                                                                                                                                                             |
| 2026-09-05 | P0.12 verificado (falta executar): os três branches do enunciado confirmados como superados contra o código — inclusive o único commit que parecia valer um port, que corrige um bloco de settings inexistente em `stage` — e mais nove branches `claude/*` identificados como totalmente contidos em `stage`. A sessão não tem permissão para apagar branch remoto; comandos e SHAs registrados no item.                                                                                                                                                                                                                                                                                      |
| 2026-09-05 | P0.13 parcial: `docs/release-runbook.md` escrito (fluxo em duas etapas, verificação pós-deploy, rollback por digest), template de RC e FORK.md §Phase 4 alinhados ao que os workflows fazem. O bump de versão fica para o PR do sync (P0.11); achado registrado: o sufixo `-plane.<upstream>` é um prerelease semver e o Release Please pode descartá-lo.                                                                                                                                                                                                                                                                                                                                      |
| 2026-09-05 | P0.10 entregue: `id_token` do Entra verificado por completo contra o JWKS do tenant (assinatura, `aud`, `iss`, janela de validade, claims obrigatórias), nonce de uso único no fluxo, timeouts em todas as chamadas OAuth, dois códigos de erro novos nos cinco lugares que os espelham, e testes com par de chaves RSA. Item novo P0.17 (documentação de implantação presume Coolify, que não é o ambiente da 4UM).                                                                                                                                                                                                                                                                           |
| 2026-09-05 | P0.15 entregue: as imagens gravam o commit (`ORCA_BUILD_SHA`/`ORCA_IMAGE_TAG`), `GET /api/orca/build-info/` responde a admin de instância e `manage.py orca_build_info` responde no worker e no beat, que não têm HTTP. Fecha no runtime a cadeia que P0.0–P0.3 fecharam no registry.                                                                                                                                                                                                                                                                                                                                                                                                          |
| 2026-09-05 | P0.16 entregue: MinIO fixado em tag imutável no Compose Orca e no de teste; CI passa a rodar PostgreSQL 15.7, igual ao ambiente implantado, com a decisão registrada em `RUNNING_TESTS.md`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| 2026-09-05 | P0.9 entregue: job `api_lint` roda `ruff check` e `ruff format --check` em `apps/api` com a versão fixada em `requirements/local.txt`; 30 achados de lint corrigidos, 23 arquivos do fork formatados e 38 arquivos upstream em exclusão temporária de formatação até o sync do P0.11.                                                                                                                                                                                                                                                                                                                                                                                                          |
| 2026-09-05 | P0.7 entregue: `trusted_proxies` sem default aberto nos dois Caddyfiles, variável obrigatória no Compose Orca e encaminhada (faixas privadas) no Compose padrão. Achado: a variável não era encaminhada a nenhum dos dois, então o `0.0.0.0/0` valia sempre.                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| 2026-09-05 | P0.6 entregue: contas migradas passam a nascer sem senha utilizável (`set_unusable_password` + `is_password_autoset`), com testes e com o procedimento de invalidação das contas já criadas no README da ferramenta.                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| 2026-09-05 | P0.5 parcial: permissões mínimas por job em `stage.yml` e `prod.yml`. A metade do lockfile ficou bloqueada por um achado — `pnpm-lock.yaml` está defasado em relação ao catálogo do workspace desde o commit upstream `31853ab2` (46 dependências com `catalog:` no `package.json` e especificador resolvido no lockfile), então `--frozen-lockfile` falharia em todo run. Precisa de `pnpm install --lockfile-only` no mesmo commit da troca da flag.                                                                                                                                                                                                                                         |
| 2026-09-05 | P0.4 entregue na mesma branch: `promote-rc` passa a usar `gh`, verifica a existência de `prod`, e falha quando a RC não existe nem pôde ser criada.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| 2026-09-05 | P0.1, P0.2 e P0.3 entregues em `claude/loving-carson-n9x6eq`: PR constrói sem publicar; push em `stage` publica `:stage` e `:sha-<commit>` e retagueia por digest os serviços não reconstruídos, para que todo commit tenha os seis serviços; artifact `image-digests`; `prod.yml` resolve o commit de `stage` promovido e copia os digests daquele commit, falhando antes de qualquer retag se faltar imagem.                                                                                                                                                                                                                                                                                 |
| 2026-09-05 | D0 completa (D0.1–D0.12) na branch `feat/orca-unit-project-coverage`: cobertura área↔projeto, herança de assignee removida da API pública, estado de fila, política e log de decisões, serviço único de alocação, endpoints internos falando com ele, comando de auditoria, métricas, matriz de testes, documentação, reconciliação no arquivamento e roster SCIM. Falta só a execução do Gate D0 (suíte, migrações, auditoria num dump).                                                                                                                                                                                                                                                      |
| 2026-09-05 | **P0.5 fechado, corrigindo o achado do próprio dia.** `pnpm install --frozen-lockfile` foi executado inteiro sobre `0e4ab05c` com o pnpm 11.3.0 do `packageManager`: exit 0, 1459 pacotes instalados, `pnpm-lock.yaml` idêntico depois; `--lockfile-only` antes disso também não gerou diff. Das 11 entradas de catálogo ausentes do lockfile, 2 não são referenciadas por workspace nenhum e as 9 restantes estão pinadas em versão exata igual ao especificador gravado — o pnpm resolve `catalog:` antes de comparar. A checagem foi verificada no sentido oposto com uma divergência plantada (`chroma-js` → `^3.0.0`): `ERR_PNPM_OUTDATED_LOCKFILE`, exit 1. Flag trocada no `stage.yml`. |
| 2026-09-05 | P0.8 ligado (falta o run): `api_tests` roda `pytest plane/tests/unit -q -m unit`, sem exclusões; job manual `api_integration_tests` roda `contract/` e `smoke/` pelo `docker-compose-test.yml`; `RUNNING_TESTS.md` documenta os dois e a política de exclusões. Achado: o workflow **não declarava `workflow_dispatch`**, embora `changes`, `ci` e `api_tests` já testassem `github.event_name == 'workflow_dispatch'` — condições mortas, e nenhuma forma de rodar o workflow à mão. Gatilho declarado.                                                                                                                                                                                       |
| 2026-09-05 | P0.12 reverificado por `merge-base --is-ancestor` contra `0e4ab05c`, não pela lista anterior: os tips dos três branches superados agora estão todos anotados (faltavam dois) e mais dois branches entraram na lista de apagar, porque seus PRs foram mesclados desde o levantamento (`claude/loving-carson-n9x6eq`, `feat/orca-unit-project-coverage`). `git push --delete` barrado de novo; sem ferramenta de deleção de branch no MCP do GitHub.                                                                                                                                                                                                                                             |
| 2026-09-05 | P0.17 na metade: README §Self-Hosting deixa de tratar o Compose como coisa de Coolify (Quick Start neutro em três condições, passos do Coolify num `<details>` de exemplo, `SERVICE_FQDN_PROXY` identificado como variável do Coolify) e `FORK.md` §Phase 3 passa a dizer que o deploy de staging é opt-in por `COOLIFY_DEPLOY_ENABLED`. Falta a decisão de negócio sobre o alvo real da 4UM.                                                                                                                                                                                                                                                                                                  |
| 2026-09-05 | PR #8 mesclado em `stage` (`1fb7a074`); `stage` mesclado na branch da D0, com a leitura de `default_assignee_id` recolocada — o git tinha aceitado a remoção do lado do #8 em silêncio e o resultado seria `NameError`. Primeira execução real da suíte D0: 19 falhas, das quais duas eram bug de produto — `routing_state` em `varchar(16)` recusando `allocation_failed` (17 caracteres) e `rank_candidates` sem checagem de cobertura. Corrigidas; PR #9 verde nas 16 checks.                                                                                                                                                                                                               |
