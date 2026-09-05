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

| Fase | Arquivo | Itens | Estado | Gate fechado em |
| --- | --- | --- | --- | --- |
| P0 Segurança da plataforma | [P0-platform-hardening.md](./P0-platform-hardening.md) | 17 | `[~]` 11/17 (P0.0–P0.4, P0.6, P0.7, P0.9, P0.14, P0.15, P0.16) · P0.5 parcial | — |
| D0 Fundação do domínio | [D0-domain-foundation.md](./D0-domain-foundation.md) | 12 | `[ ]` 0/12 | — |
| 1 Contrato público | [01-public-contract.md](./01-public-contract.md) | 8 | `[ ]` 0/8 | — |
| 2 Fila e coordenador | [02-queue-and-coordinator.md](./02-queue-and-coordinator.md) | 6 (+ gate mínimo) | `[ ]` 0/6 | — |
| 3 Disponibilidade | [03-availability.md](./03-availability.md) | 6 | `[ ]` 0/6 | — |
| 4 Processos | [04-processes.md](./04-processes.md) | 7 | `[ ]` 0/7 | — |
| 5 Visão executiva | [05-executive-view.md](./05-executive-view.md) | 4 | `[ ]` 0/4 | — |

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
**P0.9** (ruff obrigatório no CI) também entrou. Siga com **P0.8** (suíte
upstream no CI), que é o que falta da qualidade do pipeline e precisa de uma
execução real da suíte para saber o que excluir. **D0.1** continua o melhor
primeiro item do domínio.

Pendências de operação abertas por esta branch, **uma delas bloqueante**:
definir `TRUSTED_PROXIES` no ambiente do Coolify **antes** de mesclar em
`stage` (sem ela o Compose Orca recusa subir, de propósito); invalidar as
contas criadas pela versão antiga do `create_users.py` (procedimento em
`tools/migration/README.md`); regenerar `pnpm-lock.yaml` para destravar o
`--frozen-lockfile` (P0.5).

## Pendências externas (não bloqueiam P0/D0)

| Ref. | Pendência | Quem | Necessária em |
| --- | --- | --- | --- |
| A5 | Confirmar comportamento do Plane Compose em re-push com mesmo id e ausência de campo de área | pessoa com acesso à doc oficial | Fase 4 |
| — | Faixa de rede do proxy Coolify para `TRUSTED_PROXIES` | operação | P0.7 |
| — | Tenant Azure de testes para validar P0.10 de ponta a ponta | operação | fim de P0 (o item é implementável com testes unitários antes) |
| — | Definir quem é coordenador de cada área piloto | negócio | Fase 2 |
| — | Área piloto e projeto piloto para o primeiro uso real da fila | negócio | Gate 2-mínimo |

## Histórico

| Data | Evento |
| --- | --- |
| 2026-09-03 | Plano criado a partir do RFC rev. 2. Nenhum item iniciado. |
| 2026-09-04 | PRs #5 e #6 mesclados em `stage` (`3a4c769`): hardening complementar da camada de Áreas (kill switch nas tarefas/comandos/SCIM, baseline ao elevar papel, rate limit SCIM pós-autenticação, rejeição de convidados Entra). Não fecha item P0/D0; registrado no cabeçalho de P0. |
| 2026-09-05 | P0.15 entregue: as imagens gravam o commit (`ORCA_BUILD_SHA`/`ORCA_IMAGE_TAG`), `GET /api/orca/build-info/` responde a admin de instância e `manage.py orca_build_info` responde no worker e no beat, que não têm HTTP. Fecha no runtime a cadeia que P0.0–P0.3 fecharam no registry. |
| 2026-09-05 | P0.16 entregue: MinIO fixado em tag imutável no Compose Orca e no de teste; CI passa a rodar PostgreSQL 15.7, igual ao ambiente implantado, com a decisão registrada em `RUNNING_TESTS.md`. |
| 2026-09-05 | P0.9 entregue: job `api_lint` roda `ruff check` e `ruff format --check` em `apps/api` com a versão fixada em `requirements/local.txt`; 30 achados de lint corrigidos, 23 arquivos do fork formatados e 38 arquivos upstream em exclusão temporária de formatação até o sync do P0.11. |
| 2026-09-05 | P0.7 entregue: `trusted_proxies` sem default aberto nos dois Caddyfiles, variável obrigatória no Compose Orca e encaminhada (faixas privadas) no Compose padrão. Achado: a variável não era encaminhada a nenhum dos dois, então o `0.0.0.0/0` valia sempre. |
| 2026-09-05 | P0.6 entregue: contas migradas passam a nascer sem senha utilizável (`set_unusable_password` + `is_password_autoset`), com testes e com o procedimento de invalidação das contas já criadas no README da ferramenta. |
| 2026-09-05 | P0.5 parcial: permissões mínimas por job em `stage.yml` e `prod.yml`. A metade do lockfile ficou bloqueada por um achado — `pnpm-lock.yaml` está defasado em relação ao catálogo do workspace desde o commit upstream `31853ab2` (46 dependências com `catalog:` no `package.json` e especificador resolvido no lockfile), então `--frozen-lockfile` falharia em todo run. Precisa de `pnpm install --lockfile-only` no mesmo commit da troca da flag. |
| 2026-09-05 | P0.4 entregue na mesma branch: `promote-rc` passa a usar `gh`, verifica a existência de `prod`, e falha quando a RC não existe nem pôde ser criada. |
| 2026-09-05 | P0.1, P0.2 e P0.3 entregues em `claude/loving-carson-n9x6eq`: PR constrói sem publicar; push em `stage` publica `:stage` e `:sha-<commit>` e retagueia por digest os serviços não reconstruídos, para que todo commit tenha os seis serviços; artifact `image-digests`; `prod.yml` resolve o commit de `stage` promovido e copia os digests daquele commit, falhando antes de qualquer retag se faltar imagem. |
| 2026-09-04 | Revisão externa do commit `3a4c769` verificada contra o código. Achado novo: Compose apontava para o namespace do repositório-pai. Itens criados: P0.0, P0.14, P0.15, P0.16, D0.11, D0.12; critério novo em P0.10. P0.0 e P0.14 entregues no mesmo PR. |
