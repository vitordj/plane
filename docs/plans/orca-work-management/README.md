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
| P0 Segurança da plataforma | [P0-platform-hardening.md](./P0-platform-hardening.md) | 17 | `[~]` 9/17 (P0.0–P0.7, P0.14) | — |
| D0 Fundação do domínio | [D0-domain-foundation.md](./D0-domain-foundation.md) | 12 | `[ ]` 0/12 | — |
| 1 Contrato público | [01-public-contract.md](./01-public-contract.md) | 8 | `[ ]` 0/8 | — |
| 2 Fila e coordenador | [02-queue-and-coordinator.md](./02-queue-and-coordinator.md) | 6 (+ gate mínimo) | `[ ]` 0/6 | — |
| 3 Disponibilidade | [03-availability.md](./03-availability.md) | 6 | `[ ]` 0/6 | — |
| 4 Processos | [04-processes.md](./04-processes.md) | 7 | `[ ]` 0/7 | — |
| 5 Visão executiva | [05-executive-view.md](./05-executive-view.md) | 4 | `[ ]` 0/4 | — |

## Próximo item recomendado

A cadeia de release está fechada no código (P0.1–P0.5) e os dois riscos de
comprometimento direto também (P0.6 senha fixa, P0.7 `TRUSTED_PROXIES`), em
`claude/continue-implementations-bquse8`. O que resta de P0 se divide em três
grupos:

1. **Provar em ambiente o que já está no código.** Vários aceites de P0.0–P0.7
   só fecham com um deploy real (digest em staging, `curl` com
   `X-Forwarded-For` forjado, ensaio de promoção). São de operação, não de
   código — ver o Gate P0.
2. **Qualidade do próprio CI:** **P0.8** (suíte upstream) e **P0.9** (ruff
   obrigatório, com 31 findings a corrigir). Melhor par seguinte de itens de
   código, e P0.9 é pré-requisito honesto para confiar no lint dos próximos.
3. **Restantes:** P0.10 (validação do `id_token` do Entra — o maior dos que
   sobraram), P0.11 (sync 1.4.2), P0.12 (branches), P0.13 (versão e runbook —
   o arquivo já existe, faltam duas seções), P0.15 (build-info no runtime),
   P0.16 (tags de MinIO/PostgreSQL).

**D0.1** continua o melhor primeiro item do domínio e não depende de nada
acima.

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
| 2026-09-04 | Revisão externa do commit `3a4c769` verificada contra o código. Achado novo: Compose apontava para o namespace do repositório-pai. Itens criados: P0.0, P0.14, P0.15, P0.16, D0.11, D0.12; critério novo em P0.10. P0.0 e P0.14 entregues no mesmo PR. |
| 2026-09-04 | P0.1–P0.7 entregues em `claude/continue-implementations-bquse8`: cadeia de release (PR não publica `:stage`, tag `sha-<commit>` + artifact de digests, promoção por digest do commit sob release, RC que falha em vez de ficar verde sem PR, lockfile congelado e permissões mínimas) e os dois riscos de conta/IP (senha fixa da migração, `TRUSTED_PROXIES`). `docs/release-runbook.md` criado. P0 em 9/17. |
