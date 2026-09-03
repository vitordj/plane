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
| P0 Segurança da plataforma | [P0-platform-hardening.md](./P0-platform-hardening.md) | 13 | `[~]` 11/13 | — |
| D0 Fundação do domínio | [D0-domain-foundation.md](./D0-domain-foundation.md) | 10 | `[x]` 10/10 | — |
| 1 Contrato público | [01-public-contract.md](./01-public-contract.md) | 8 | `[x]` 8/8 | — |
| 2 Fila e coordenador | [02-queue-and-coordinator.md](./02-queue-and-coordinator.md) | 6 (+ gate mínimo) | `[x]` 6/6 | — |
| 3 Disponibilidade | [03-availability.md](./03-availability.md) | 6 | `[ ]` 0/6 | — |
| 4 Processos | [04-processes.md](./04-processes.md) | 7 | `[ ]` 0/7 | — |
| 5 Visão executiva | [05-executive-view.md](./05-executive-view.md) | 4 | `[ ]` 0/4 | — |

## Próximo item recomendado

**Fase 3** (disponibilidade). Os gates das fases 1 e 2 continuam abertos:
dependem de deploy em staging e de verificação humana, não de código.

Pendentes de P0 que não são de código: **P0.11** (sync com Plane CE 1.4.2) e
**P0.12** (apagar branches remotos obsoletos) — os dois exigem acesso ao
remoto upstream e decisão humana sobre o que descartar.

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
| 2026-09-03 | P0.1–P0.10 e P0.13 entregues na branch `feat/orca-work-management`. |
| 2026-09-03 | D0.1–D0.8 e D0.10 entregues na mesma branch. |
