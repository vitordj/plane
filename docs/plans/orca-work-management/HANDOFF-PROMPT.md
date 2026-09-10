# Prompt de handoff para a próxima sessão

Copie o bloco abaixo como primeira mensagem de uma nova sessão de agente no
repositório `vitordj/plane`. Ajuste apenas a linha **Item desta sessão**.

---

```text
Você vai continuar a implementação do plano "Gestão de trabalho por área (Orca)"
neste fork do Plane CE. Trabalhe em Português nas respostas; código, commits e
docs técnicas seguem as convenções do repositório (inglês nos identificadores e
mensagens de commit, escopo `orca`).

Leia, nesta ordem, antes de qualquer alteração:
1. AGENTS.md e FORK.md (regras do fork: sidecar, sem coluna em modelo core,
   commits `feat(orca):`, e a regra de saída: rode as verificações, mas nunca
   com a saída indo para o contexto)
2. docs/orca-work-management-rfc.md — seções 1, 2, 3 (conceito, estado atual,
   24 decisões fechadas F1–F24), depois as seções citadas pelo item que você
   vai executar. Não reabra decisão fechada sem registrar em §4.2.
3. docs/plans/orca-work-management/README.md — quadro de estado e ordem das
   fases. Confira qual é o próximo item `[ ]` da fase ativa.
4. O arquivo da fase do item (ex.: docs/plans/orca-work-management/D0-domain-foundation.md).

5. Se o item for um achado R1, leia a seção correspondente de
   docs/plans/orca-work-management/reviews/2026-09-07-stage-adversarial-review.md
   antes de tocar no código: o cenário de falha, a evidência de execução e a
   correção proposta estão lá, e o R1 só carrega o endereço.

Item desta sessão: <FASE.ITEM — ex.: R1.A3 — a retenção reescreve linha append-only>

Regras de execução:
- Um item = um PR pequeno contra `stage`. Branch a partir de `origin/stage`
  (nome: skill `branch-name` ou `feat/orca-<tema>`). Título do PR com o
  identificador do item: `fix(orca): [D0.1] require unit coverage of the project`.
- Antes de codar, leia os arquivos que o item aponta e os testes Orca vizinhos
  em apps/api/plane/tests/unit/orca/ (fixtures em conftest.py). Siga o padrão
  dos arquivos Orca existentes: docstrings @description/@param/@returns,
  comentário explicando cada override do core, header de copyright em arquivo
  novo (COPYRIGHT_CHECK.md).
- Códigos de erro Orca vivem em três lugares e o teste test_orca_error_codes.py
  exige paridade: apps/api/plane/utils/orca_error_codes.py,
  packages/constants/src/orca/error-codes.ts e o catálogo i18n. Strings novas
  de UI entram em todas as locales via skill `translate` (o CI roda check:sync).
- Migrações: gerar com makemigrations (comando para o desenvolvedor rodar),
  dependência explícita na última Orca, nunca editar migração mesclada.
  Você pode escrever o arquivo de migração à mão seguindo o padrão das
  existentes se não puder rodar o comando; diga isso explicitamente.
- Testes acompanham o item. Todo item que toca alocação termina com teste de
  concorrência (RFC §10; padrão em D0.5). Marque @pytest.mark.unit.
- O que você pode rodar na sessão: ruff check/format em apps/api, grep, leitura,
  git; com a seção "Ambiente local — backend" deste arquivo, pytest (suíte Orca,
  um arquivo, ou a suíte inteira quando o item pede), `makemigrations --check` e
  `migrate` num banco vazio; e com a seção "Ambiente local — frontend",
  `pnpm install --frozen-lockfile`, `pnpm check:types --filter=web`,
  `check:lint`, `check:format` e `check:sync`. Rode com a saída redirecionada
  para arquivo e leia só o `tail`: o que o AGENTS.md protege é o volume de
  saída no contexto, não a execução. O que continua **de fato** fora: docker,
  deploy, `git push --delete`, `pnpm build` inteiro (tempo) e qualquer
  verificação que precise de um banco **com dados**. Para essas, liste os
  comandos exatos para o desenvolvedor.
- Um item só é marcado `[x]` com os seus testes executados e verdes na sessão,
  não só escritos. Diga no relatório o comando e o resultado.
- Ao terminar: marque o item `[x]` no arquivo da fase e atualize a contagem no
  README.md do plano no mesmo PR; se descobriu algo que muda o desenho, escreva
  em docs/orca-work-management-rfc.md §4.2. Commit e push na sua branch.
  Não abra PR nem faça merge sem ser pedido; descreva o PR proposto.
- Reporte no final: o que foi feito, o que foi verificado e como, o que não foi
  verificado, e o próximo item recomendado.

Contexto que você não precisa redescobrir:
- Base: Plane CE v1.4.2 (commit upstream 5f7d92784); fork com Areas
  (OrganizationalUnit), reconciliador de acesso, SCIM 2.0, Entra ID, i18n de
  idioma padrão, ciclos paralelos, labels/estados de workspace, bulk ops.
- Rotas Orca internas: apps/api/plane/app/urls/orca.py sob /api/orca/, sessão.
  API pública nativa: apps/api/plane/api/ sob /api/v1/, APIKeyAuthentication.
- Kill switch: ORCA_ORG_UNITS_ENABLED via OrganizationalUnitFeatureMixin (404).
- Os defeitos D1–D4 (RFC §2.2) estão **fechados** desde o PR #9 (05/09/2026) e
  cada um tem teste que o pina — não são trabalho a fazer, e "corrigi-los" de
  novo é o erro mais fácil de cometer aqui. Eram: cobertura área↔projeto não
  validada (D1, hoje pinado por test_issue_unit_coverage.py); API pública
  herdando assignees do último item do criador (D2, test_issue_serializer_orca_features.py);
  ranking sem lock (D3, test_assignment_concurrency.py); carga contando
  qualquer assignee (D4, test_assignment_service.py::TestRanking).
- Plane CE não tem custom properties; a única fonte da verdade da área é
  IssueOrganizationalUnit.
- A Fase 2 está entregue em stage nos itens 2.1, 2.2, 2.4 e na parte mínima do
  2.3: existe OrganizationalUnitCoordinator (migração 0139), o ledger de grants
  distingue origem por membership ou por coordenação, há oito rotas em
  app/views/organizational_queue.py com helpers em
  app/permissions/organizational_unit.py, a aba Trabalho existe na interface, e
  os alertas de allocation_failed e de SLA vencido existem em
  services/orca/alerts.py e bgtasks/organizational_queue_task.py. A parte
  completa do 2.3 (Atenção, decisões, política, coordenadores, Minha Área,
  transferir), o 2.5 (i18n e docs) e o 2.6 (coordenador esvazia 30 itens)
  estão entregues. Não reimplemente nada disso.
- Os achados de código da revisão adversarial de 07/09 (R1.A1–A20) estão
  corrigidos. Resta só R1.T1, que é decisão de negócio (trilha regulatória),
  não código. Se o seu item toca reconciliador, fila, idempotência ou
  permissões de Guest, olhe o R1 antes: o comportamento que você encontrar já
  pode ser o conserto, não o defeito original.
- O job `Ensure Release Candidate PR` falha em todo push para stage por uma
  configuração do repositório, não por código (GitHub Actions sem permissão para
  criar PR). Um stage vermelho nesse job só, com os outros 17 verdes, não é
  regressão sua.
- A migração mais recente é 0141_orca_availability. A Fase 3 está fechada no
  código (3.1–3.6). A 0142 é da Faixa B (4.2), não desta. O ranking já é
  `lb-2`. Depois que A (este PR) e B (4.2/4.3) mesclarem em stage — A primeiro
  se brigarem em urls/orca.py — o próximo código é 4.5+4.6+4.7 num PR e
  5.1+5.3+5.4 noutro. Não abrir 4.1, 4.4, 5.1 inteiro agora, 5.2, nem
  consertar o job vermelho do Release Please.
```

---

## Ambiente local

### Backend — a suíte e as migrações

Confirmado duas vezes (PR #12 e a sessão de planejamento do bloco 1.4 → 1.8).
O contêiner da sessão não tem Docker, mas tem os binários do PostgreSQL 16,
o `redis-server` e Python 3.11. Três coisas que não são óbvias e custaram
tempo: `initdb` recusa rodar como root (usar o usuário `postgres`, com o
`PGDATA` num diretório que ele consiga atravessar — o scratchpad da sessão
não serve); o `pip` do sistema não substitui o PyJWT do Debian (usar um
venv); `psycopg-c` precisa de headers do libpq que não existem
(`psycopg-binary` já está no requirements, então basta filtrar a linha).

```bash
# 1. venv + dependências (≈3 min)
python3 -m venv /tmp/orca-venv
/tmp/orca-venv/bin/pip install -q --upgrade pip 'setuptools>=70' wheel
grep -v '^psycopg-c' apps/api/requirements/base.txt > /tmp/req-base.txt
grep -v '^psycopg-c' apps/api/requirements/test.txt | grep -v '^-r' > /tmp/req-test.txt
/tmp/orca-venv/bin/pip install -q -r /tmp/req-base.txt -r /tmp/req-test.txt

# 2. PostgreSQL 16 sob o usuário postgres
PGDATA=/var/lib/postgresql/pgdata
mkdir -p $PGDATA && chown postgres:postgres $PGDATA && chmod 700 $PGDATA
su postgres -c "/usr/lib/postgresql/16/bin/initdb -D $PGDATA -U postgres --auth=trust"
su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D $PGDATA -l /var/lib/postgresql/pg.log \
  -o '-p 5432 -c listen_addresses=127.0.0.1' start"
su postgres -c "psql -h 127.0.0.1 -U postgres -c \"CREATE ROLE plane WITH LOGIN SUPERUSER PASSWORD 'plane';\""
su postgres -c "psql -h 127.0.0.1 -U postgres -c \"CREATE DATABASE plane OWNER plane;\""

# 3. Redis
redis-server --daemonize yes --port 6379 --bind 127.0.0.1

# 4. Variáveis (as mesmas do job api_tests em .github/workflows/stage.yml)
export DATABASE_URL=postgres://plane:plane@127.0.0.1:5432/plane
export REDIS_URL=redis://127.0.0.1:6379/
export SECRET_KEY=local-session-secret-key-not-used-outside-tests
export DJANGO_SETTINGS_MODULE=plane.settings.test
export APP_BASE_URL=http://localhost:3000 WEB_URL=http://localhost:3000
export PATH=/tmp/orca-venv/bin:$PATH

# 5. Rodar (sempre a partir de apps/api)
cd apps/api
pytest plane/tests/unit/orca -q -m unit -p no:cacheprovider          # suíte Orca
pytest plane/tests/unit/orca/test_public_work_items.py -q            # um arquivo
pytest plane/tests/unit -q -m unit                                    # o que o CI roda (≈10 min)
pytest plane/tests/contract/test_orca_public_contract.py -q           # contrato (1.8)
python manage.py makemigrations --check --dry-run                     # migração bate com os modelos
python manage.py migrate && python manage.py migrate db 0137 && python manage.py migrate   # ida e volta
```

O que o AGENTS.md protege ao vetar "testes completos" na sessão é o volume de
saída no contexto, não a execução em si: rodar sempre com `-q` e `| tail -20`
(ou redirecionar para um arquivo e ler só o resumo), nunca com `-vs` solto.
`--reuse-db` está no `pytest.ini`: depois de uma migração nova, rodar uma vez
com `--create-db`. PostgreSQL aqui é 16 e no CI é 15.7; nada até agora
dependeu disso, mas é o primeiro lugar a olhar se divergirem. O ambiente
morre com o contêiner — a receita é o que fica.

### Frontend — tipos, lint, formato e i18n

Medido nesta sessão (07/09/2026) na ponta do PR #15, `31d35e2b`, num contêiner
com o store do pnpm quente em `/root/.local/share/pnpm/store/v11`. Os tempos
são de parede; um store frio troca os 19 s do primeiro comando por minutos.

O detalhe que custa tempo a quem não sabe: **`check:types` precisa passar pelo
turbo.** A tarefa declara `dependsOn: ["^build"]` em `turbo.json`, então a
forma `pnpm --filter web check:types` roda o `tsc` sem antes construir os
pacotes do workspace e falha com 5 126 erros, 4 363 deles `TS2307: Cannot find
module '@plane/…'`. Não é o código: é a ausência do build. A forma que passa é
`pnpm check:types --filter=web`, que deixa o turbo construir os 11 alvos
primeiro.

```bash
pnpm install --frozen-lockfile          # exit 0 · 19 s com store quente
pnpm check:types --filter=web           # exit 0 · 61 s (turbo: 11 tarefas)
pnpm --filter web check:lint            # exit 0 · 1 s · 733 warnings, 0 errors
pnpm --filter web check:format          # exit 0 · 3 s
pnpm --filter @plane/i18n check:sync    # exit 0 · 2 s · en 4.176 chaves + 18 locales em 100 %
```

Como sempre: `> /tmp/x.log 2>&1` e `tail`. O `check:types` é o único que passa
de um minuto, e é o que mais vale rodar: o README do plano listou por dias o
`check:types` entre o que faltava para o Gate D0, porque se acreditava que a
sessão não o executava.

## Variantes

**Sessão de correção de um achado da revisão:**

```text
Corrija o achado <R1.An> de docs/plans/orca-work-management/R1-review-findings.md.
Leia primeiro a seção daquele achado em
docs/plans/orca-work-management/reviews/2026-09-07-stage-adversarial-review.md:
ela traz o cenário de falha, a evidência de execução e uma correção proposta.
A correção proposta é uma sugestão, não uma ordem — se você discordar dela,
diga por quê antes de escrever outra coisa.
Regra de aceite deste tipo de item: o PR precisa de um teste que falhe sem a
correção e passe com ela, e o relatório precisa dizer que você viu esse teste
falhar. Um achado sem teste de regressão volta.
Marque o item [x] em R1-review-findings.md no mesmo PR.
```

**Sessão de revisão (sem implementar):**

```text
Revise o PR <n> contra o item <FASE.ITEM> de docs/plans/orca-work-management/.
Verifique: (1) cada critério de aceite do item; (2) invariantes I1–I10 do RFC
§6.1 tocadas pelo diff; (3) que nenhuma escrita em ProjectMember ocorre fora
dos reconciliadores; (4) paridade dos códigos de erro; (5) testes de
concorrência quando o diff toca alocação. Responda com findings ordenados por
severidade e o veredito de gate.
```

**Sessão de fechamento de gate:**

```text
Feche o Gate <P0|D0|1|2|3|4|5> do plano docs/plans/orca-work-management/.
Para cada critério do gate, diga se está atendido e a evidência (teste, run
de CI, comando). Preencha a data do gate no arquivo da fase e a coluna
"Gate fechado em" no README.md. Se algum critério não está atendido, liste o
que falta como itens `[ ]` novos no arquivo da fase, sem mudar os existentes.
```
