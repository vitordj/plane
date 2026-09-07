# R1 — Achados da revisão adversarial de 07/09

**O que é este arquivo.** A revisão adversarial da `stage`
([`reviews/2026-09-07-stage-adversarial-review.md`](./reviews/2026-09-07-stage-adversarial-review.md))
produziu 20 achados em 1297 linhas de prosa, com cenário de falha e correção
proposta em cada um. Prosa não é fila de trabalho: sem endereço rastreável, um
achado vira folclore em duas semanas. Este arquivo dá a cada um um item
marcável, e nada mais — **o detalhe, a evidência e a correção proposta ficam na
revisão**, e é lá que se lê antes de mexer.

**Como usar.** Igual às demais fases: escolha o próximo `[ ]`, marque `[~]` ao
começar e `[x]` ao terminar, no mesmo PR. Título do PR com o identificador:
`fix(orca): [R1.A3] the retention job stops rewriting an append-only row`.

**Severidade, como a revisão a definiu.** S1 segura o Gate 2-mínimo; S2 segura
ligar a API pública em produção, ou é perda silenciosa de dado ou acesso
indevido que precisa de decisão; S3 e S4 são dívida com endereço. **Não houve
S1.**

**O que a revisão verificou e estava certo**, e que portanto ninguém precisa
reabrir: nenhuma escrita em `ProjectMember` fora dos reconciliadores; o kill
switch e o rate limit do SCIM; a chave do throttle derivada do id do token e
não do segredo; e zero `.update()` sobre as duas tabelas append-only no código
Orca.

---

## Prioridade — leia esta seção antes da tabela

**Três achados têm prazo, e é o prazo que os ordena, não a severidade.**

**R1.A3 já está em produção-potencial e tem trinta dias de relógio.** A
retenção do P0.20 apaga recibos com hard delete, e a FK `SET_NULL` em
`AssignmentDecision` faz o Django emitir um UPDATE silencioso sobre uma linha
append-only. O relógio começa no primeiro deploy que rode o beat, e o dano é
irreversível: passados trinta dias, "qual chamada tomou esta decisão" fica sem
resposta para tudo que veio pela API pública, e nada no log diz que a
informação existiu. **Este é o único item da lista cujo custo cresce sozinho.**

**R1.A6 e R1.A5 são a condição para ligar a API pública em produção**, conforme
o veredito de gate da revisão (§15). A6 é o mais caro da lista para uma API
cujo público inteiro são programas que retentam.

**R1.A2, R1.A7 e R1.A11 caem sobre o código que a Fase 2 acabou de entregar.**
A revisão argumenta que sairiam mais baratos junto com ela; a Fase 2 foi
mesclada sem eles, então agora são follow-up. O A2 é o mais incômodo dos três,
porque a decisão M3 declara a lógica de drift "intocada" e o defeito está
dentro dela: rebaixar e restaurar um coordenador transforma a concessão da
camada em "escolha manual", e a frase que justifica o M3 deixa de ser verdade.

---

## S2 — seis achados

- [ ] **R1.A1** — Re-POSTar a **mesma** área num item atribuído devolve o item à fila e apaga o executor, respondendo 200. `app/views/organizational_unit.py:583`.
- [ ] **R1.A2** — Rebaixamento a Guest do workspace é capturado como `baseline_role`; sair da área deixa acesso residual ativo. `app/services/orca/org_unit_reconciler.py:436-448`. **Toca o M3.**
- [x] **R1.A3** — A retenção do P0.20 reescreve linha append-only de `AssignmentDecision` pela FK `SET_NULL`. `bgtasks/orca_automation_cleanup_task.py:85`. **Corrigido em 07/09.** A decisão passou a copiar `idempotency_key` e `operation_type` no momento da gravação, em `automation_idempotency_key` e `automation_operation_type` (migração `0140`). A FK continua `SET_NULL` e continua indo a nulo, de propósito, e um teste afirma isso: o ponteiro tem a vida útil do recibo, e perdê-lo passou a ser escolha registrada. O que a purga não alcança mais é a resposta. **A correção que a revisão preferia não foi adotada**, e a razão importa: excluir da limpeza os recibos com decisão viva parte da premissa de que a maioria dos recibos não gera decisão, e é o contrário — toda rota mutante da API pública passa `automation_operation=handle.operation`, então aquilo manteria quase todos para sempre e anularia o P0.20.
- [ ] **R1.A4** — Guest do workspace lê o e-mail de todos os membros de todas as áreas. `app/serializers/organizational_unit.py:92` e `views:269`.
- [ ] **R1.A5** — Guest com API key enumera todas as áreas e todos os projetos que elas cobrem. `api/views/orca/units.py:85`. **Condição para a API em produção.**
- [ ] **R1.A6** — Uma falha transitória queima a `Idempotency-Key` para sempre: toda retentativa replica um 500. `api/views/orca/base.py` e `automation_operation.py:303-311`. **Condição para a API em produção.**

## S3 — dez achados

- [ ] **R1.A7** — A fila mostra título e e-mail de trabalho em projeto cujo acesso o próprio reconciliador retirou. `app/services/orca/queue.py:54`. **Toca o M6.**
- [ ] **R1.A8** — `set_responsibility` lê-e-cria o vínculo fora de lock: duas chamadas simultâneas dão `IntegrityError`. `assignment_service.py:993-1018`.
- [ ] **R1.A9** — Inversão de ordem entre o lock consultivo de área e o lock de linha: deadlock possível. `assignment_service.py:628-631` contra `900-954`.
- [ ] **R1.A10** — Remoção de grupo no Entra desativa o `lead` em silêncio, e re-adicioná-lo dá 500. `app/services/orca/directory_projector.py:248-258`.
- [ ] **R1.A11** — `workload/` conta qualquer assignee, o ranking conta executor principal, e a Fase 2 lê `workload/`. O defeito D4 sobreviveu fora do serviço. `assignment_engine.py:182-191`. **Toca o M5.**
- [ ] **R1.A12** — `Idempotency-Key` é única por workspace e não por token: um token queima o espaço de chaves de outro. `automation_operation.py:216`.
- [ ] **R1.A13** — `ORCA_PUBLIC_API_RATE_LIMIT` malformado vira 500 por requisição em vez de falha de boot. `throttles/orca_public.py:47`.
- [ ] **R1.A14** — O append-only só sobrevive ao cascade de soft-delete por causa de um `print()` e um `continue`. `bgtasks/deletion_task.py:94-96`.
- [ ] **R1.A15** — `effective-access/` e `workload/` abertos a Guest do workspace. `app/views/organizational_unit.py:496,768`.
- [ ] **R1.A16** — `token_last_used_at` é escrito antes do throttle: 600 UPDATEs por minuto numa linha, e o 429 não protege. `orca_scim/base.py:315-316`.

## S4 — quatro achados

- [ ] **R1.A17** — Kill switch do SCIM antes da autenticação: diz a um anônimo se a camada está ligada, e sem throttle. `orca_scim/base.py:248`.
- [ ] **R1.A18** — O mesmo na API pública, e a rota desligada fica sem medição. `api/views/orca/base.py:48`.
- [ ] **R1.A19** — A lista do `compose_env_forwarding` é mantida à mão, que é o passo manual que o P0.19 existiu para eliminar. `.github/workflows/stage.yml:285`.
- [ ] **R1.A20** — `ORG_INVALID_ROUTING_TRANSITION` usado para "filtro de query desconhecido". `api/views/orca/units.py:133`.

---

## Item de desenho que a revisão levantou e não é um defeito

- [ ] **R1.T1** — A trilha append-only é auditável, não é inviolável: `AppendOnlyModel.save()` recusa a gravação, e o próprio docstring reconhece que `QuerySet.update()` passa por cima. Para operação normal isso basta. Se o histórico de decisões vier a ser tratado como trilha regulatória, o degrau seguinte é constraint ou trigger no PostgreSQL, ou log de auditoria externo. **Decisão de negócio antes de virar código.** O R1.A3 é a prova de que o guarda em Python não é suficiente sozinho.

---

## Gate R1

Não existe. Estes itens não formam uma fase e não bloqueiam as Fases 3 a 5 em
conjunto. O que bloqueia está dito em Prioridade, item a item:

- [x] R1.A3 corrigido antes de trinta dias do primeiro deploy com o beat ativo. Fechado em 07/09, antes de qualquer deploy.
- [ ] R1.A5 e R1.A6 corrigidos antes de `ORCA_PUBLIC_API_ENABLED=1` em produção.
