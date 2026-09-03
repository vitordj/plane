# Prompts do Codex — Fase P0 (Segurança da plataforma)

Plano da fase: [`../P0-platform-hardening.md`](../P0-platform-hardening.md).
Contexto obrigatório: [`00-context.md`](./00-context.md).

P0 corre em paralelo com D0. Ordem interna recomendada: **P0.6 → P0.7 →
P0.1 → P0.2 → P0.3 → P0.4 → P0.5 → P0.9 → P0.8 → P0.10 → P0.13 → P0.11 →
P0.12**. P0.11 (sync upstream) e P0.12 (limpeza de branches) são humanos —
os prompts abaixo cobrem só a parte que uma máquina faz bem.

| Item | Perfil | Risco | Dá para paralelizar com |
| --- | --- | --- | --- |
| P0.1, P0.2, P0.4, P0.5 | `standard` | baixo | entre si não (mesmo arquivo) |
| P0.3 | `heavy` | médio | P0.6, P0.7 |
| P0.6, P0.7 | `standard` | baixo | qualquer outro |
| P0.8, P0.9 | `standard` | baixo (P0.9 é volumoso) | P0.6, P0.7 |
| P0.10 | `heavy` | alto (autenticação) | qualquer outro |
| P0.13 | `standard` | baixo | qualquer outro |

> Os itens P0.1–P0.5 e P0.8–P0.9 tocam o mesmo `.github/workflows/stage.yml`.
> Despache **um de cada vez** e faça o rebase antes do próximo, senão o
> conflito consome mais tempo que o ganho de paralelismo.

---

## P0.6 — Remover a senha fixa da migração de usuários

```text
Você vai implementar o item P0.6 do plano "Gestão de trabalho por área (Orca)"
neste fork do Plane CE. Faça apenas este item.

LEIA ANTES DE EDITAR QUALQUER ARQUIVO
1. docs/plans/orca-work-management/codex/00-context.md — inteiro, é o contrato desta base.
2. docs/plans/orca-work-management/P0-platform-hardening.md, seção "P0.6".
3. tools/migration/create_users.py e tools/migration/README.md.
4. apps/api/plane/authentication/provider/oauth/entra.py — veja como o provider
   cria usuário sem senha utilizável. É o padrão a seguir.

PROBLEMA
tools/migration/create_users.py define DEFAULT_PASSWORD = "TemporaryOrca123!" (~l.23)
e aplica essa senha a todo usuário criado (~l.84). Qualquer pessoa que leia o
repositório tem a senha de todas as contas importadas. Não há troca forçada.

TAREFA
1. Remova a constante DEFAULT_PASSWORD e todo uso dela.
2. Refatore a criação de usuário para uma função pública
   create_user_from_payload(payload: dict) -> tuple[User, bool], para que o teste
   possa importá-la sem executar o script. Mantenha o comportamento de linha de
   comando idêntico no resto.
3. Todo usuário criado recebe user.set_unusable_password() e
   user.is_password_autoset = True, exatamente como o provider Entra faz.
4. tools/migration/README.md: nova seção "Primeiro acesso" explicando que o
   acesso se dá por Entra ID ou magic link; remova qualquer menção à senha antiga.
5. tools/migration/README.md: bloco "Se este script já foi executado antes desta
   versão" com o comando Django (shell) que invalida as credenciais das contas
   criadas pelo script — filtrando por lista de e-mails ou por created_at da
   execução — usando set_unusable_password(), e a orientação de revisar os logs
   de autenticação do período.
6. Novo teste apps/api/plane/tests/unit/orca/test_migration_tools.py: importa
   create_user_from_payload, cria um usuário e verifica
   has_usable_password() is False e is_password_autoset is True. Marque
   @pytest.mark.unit e siga o estilo dos testes vizinhos (conftest.py tem as fixtures).
   Header de copyright no arquivo novo.

DEFINIÇÃO DE PRONTO
- grep -rn "TemporaryOrca" tools/ docs/ README.md não retorna nada.
- Nenhum caminho do script define senha utilizável.
- O teste novo existe e é coerente com as fixtures do conftest.
- ruff check . e ruff format --check . limpos em apps/api.

NÃO FAÇA
- Não mude o formato do CSV/entrada nem a semântica das outras flags do script.
- Não toque em apps/api/plane/db/models/user.py.
- Não tente rodar a suíte de testes nem docker (ver seção 6 do 00-context).

AO TERMINAR
- Marque P0.6 como [x] em docs/plans/orca-work-management/P0-platform-hardening.md
  e atualize a contagem da fase P0 no README.md do plano, no mesmo commit.
- Deixe o terceiro critério de aceite ("contas já criadas ... invalidadas") como
  [ ] — é ação de operação, não sua.
- Commit: fix(orca): [P0.6] stop seeding a fixed password in the user migration
- Responda no formato da seção 10 do 00-context.md.
```

---

## P0.7 — `TRUSTED_PROXIES` sem fallback aberto

```text
Você vai implementar o item P0.7 do plano Orca neste fork do Plane CE. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md — inteiro.
2. docs/plans/orca-work-management/P0-platform-hardening.md, seção "P0.7".
3. apps/proxy/Caddyfile.ce, .env.example, docker-compose-orca.yml,
   docker-compose-local.yml e a tabela de variáveis do README.md.

PROBLEMA
apps/proxy/Caddyfile.ce l.8 traz trusted_proxies static {$TRUSTED_PROXIES:0.0.0.0/0}
e .env.example l.44 repete 0.0.0.0/0. Com isso qualquer cliente pode forjar
X-Forwarded-For e o Django acredita: rate limiting, logs de autenticação e
qualquer decisão por IP passam a ser controlados pelo atacante.

TAREFA
1. Caddyfile.ce: trusted_proxies static {$TRUSTED_PROXIES}, sem default. Falhar no
   boot com a variável vazia é o comportamento desejado — não invente fallback.
2. .env.example: TRUSTED_PROXIES= (vazio) com comentário de uma linha:
   obrigatório, CIDR da rede do proxy externo/Coolify, ex.: 10.0.0.0/8.
   Mesma variável em apps/api/.env.example se ela existir lá.
3. docker-compose-orca.yml, serviço proxy:
   TRUSTED_PROXIES=${TRUSTED_PROXIES:?TRUSTED_PROXIES is required}
4. docker-compose-local.yml: TRUSTED_PROXIES=127.0.0.1/32,172.16.0.0/12,
   para que o desenvolvimento local continue funcionando sem configuração.
5. README.md: linha na tabela de variáveis de ambiente com "Required: Yes" e a
   explicação curta.
6. Confirme por leitura (e diga na resposta) que SECURE_PROXY_SSL_HEADER e o
   middleware de IP em apps/api/plane/settings/ continuam coerentes; não altere
   nada lá se já estiverem corretos.

DEFINIÇÃO DE PRONTO
- grep -rn "0.0.0.0/0" apps/proxy .env.example docker-compose*.yml não retorna
  mais nenhuma ocorrência ligada a trusted_proxies.
- Um docker compose -f docker-compose-orca.yml config sem a variável falharia
  (você não roda; explique por que falha).

NÃO FAÇA
- Não mexa em outros serviços do compose nem em portas/volumes.
- Não rode docker.

AO TERMINAR
- Marque P0.7 [x] no arquivo da fase e atualize a contagem no README do plano.
  Os dois critérios de aceite que exigem rodar compose/curl ficam [ ] — são do
  desenvolvedor; liste os dois comandos exatos.
- Commit: fix(orca): [P0.7] require an explicit trusted proxy range
- Responda no formato da seção 10 do 00-context.md.
```

---

## P0.1 — Build em pull request não publica imagem

```text
Você vai implementar o item P0.1 do plano Orca. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/plans/orca-work-management/P0-platform-hardening.md, seção "P0.1".
3. .github/workflows/stage.yml inteiro — entenda os jobs changes, ci, build-push,
   api_tests, deploy e as dependências entre eles antes de tocar em qualquer linha.

PROBLEMA
No job build-push, o passo docker/build-push-action tem push: true incondicional,
e o workflow também roda em pull_request. Resultado: um PR ainda não revisado
reescreve a tag mutável :stage, que é a que o ambiente de staging consome.

TAREFA
1. No passo de build: push: ${{ github.event_name != 'pull_request' }}.
2. Em pull_request, tags: passa a
   ghcr.io/<owner>/<repo>/<service>:pr-${{ github.event.pull_request.number }}-${{ github.sha }}
   (mantenha o mesmo esquema de nome de serviço já usado no workflow) e load: false.
   O build continua rodando: ele serve de prova de que compila.
3. O passo "Log in to GHCR" recebe if: github.event_name != 'pull_request'.
4. Não altere o comportamento em push para stage: continua publicando :stage.
5. Se o mesmo padrão existir em outro job do arquivo, corrija lá também e diga
   na resposta.

DEFINIÇÃO DE PRONTO
- Nenhum passo do workflow publica em GHCR quando github.event_name == 'pull_request'.
- O YAML é válido (confira indentação com cuidado; se houver actionlint disponível
  sem instalar nada, rode).
- O caminho de push para stage está textualmente inalterado quanto ao que publica.

NÃO FAÇA
- Não mexa em prod.yml (é o item P0.3).
- Não mexa em permissões nem no lockfile (é o item P0.5).
- Não altere a matriz de serviços nem os Dockerfiles.

AO TERMINAR
- Marque P0.1 [x] no arquivo da fase e atualize a contagem no README do plano.
  Os aceites que exigem um run real ficam [ ]; descreva como verificá-los.
- Commit: fix(orca): [P0.1] stop publishing mutable image tags from pull requests
- Responda no formato da seção 10 do 00-context.md.
```

---

## P0.2 — Tag imutável por SHA e registro de digests

```text
Você vai implementar o item P0.2 do plano Orca. Só este item. Ele pressupõe P0.1
já mesclado — confirme lendo .github/workflows/stage.yml antes de começar; se
push: true ainda estiver incondicional, pare e reporte o bloqueio.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/plans/orca-work-management/P0-platform-hardening.md, seções "P0.2" e "P0.3"
   (o P0.3 consome o que você produz aqui; o formato precisa servir a ele).
3. .github/workflows/stage.yml, jobs build-push e deploy.

TAREFA
1. Em push para stage, tags: passa a duas linhas por serviço: :stage e
   :sha-${{ github.sha }}. Mesma imagem, mesmo digest, dois ponteiros.
2. Dê id ao passo de build (id: build) e acrescente um passo que capture
   steps.build.outputs.digest e componha image-digests.json no formato
   {"<service>": "sha256:..."}. Como o build roda em matriz, cada job escreve o
   seu fragmento e um passo/job de agregação junta os seis num único
   image-digests.json — escolha entre agregar por artifact (upload por serviço +
   job que baixa todos) ou por output de matriz, e explique a escolha na resposta.
3. Publique image-digests.json como artifact do run (actions/upload-artifact) com
   o nome image-digests, e exponha os digests como output do job (outputs.digests).
4. Novo passo no job deploy que loga os digests que o Coolify vai puxar.

DEFINIÇÃO DE PRONTO
- Após merge em stage, existiriam api:stage e api:sha-<commit> apontando para o
  mesmo digest (explique por que, já que é o mesmo build).
- O artifact image-digests tem uma entrada por serviço construído (seis quando
  todos mudam; quando a matriz é parcial, o arquivo traz só os construídos e o
  agregador não falha por isso — trate esse caso explicitamente).
- YAML válido.

NÃO FAÇA
- Não altere prod.yml.
- Não introduza action de terceiros que não esteja já em uso no repositório,
  além de actions/upload-artifact e actions/download-artifact.

AO TERMINAR
- Marque P0.2 [x] e atualize a contagem no README do plano.
- Commit: feat(orca): [P0.2] publish immutable image tags and record digests
- Responda no formato da seção 10 do 00-context.md.
```

---

## P0.3 — Promoção para produção por SHA

```text
Você vai implementar o item P0.3 do plano Orca. Só este item. Depende de P0.2
(tags :sha-<commit> existirem) — confirme em .github/workflows/stage.yml antes
de começar; se não existirem, pare e reporte o bloqueio.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/plans/orca-work-management/P0-platform-hardening.md, seções "P0.3" e "P0.13".
3. .github/workflows/prod.yml inteiro, com atenção ao job "Promote Images" (~l.81)
   e à condição do commit chore(prod): release.
4. FORK.md §3, fases 4 e 5, para não contradizer o fluxo documentado.

PROBLEMA
prod.yml promove fazendo docker pull <img>:stage e retagueando para :latest.
A tag :stage é mutável: o que vai a produção não está preso ao commit que foi
revisado e aprovado na RC. Uma corrida entre um merge em stage e a promoção
publica código não revisado em produção.

TAREFA
1. O job de promoção passa a resolver o SHA do commit de stage que está sendo
   promovido — pelo segundo pai do merge commit em prod
   (git rev-parse HEAD^2) ou por git log -1 --format=%H origin/stage no momento
   do release. Escolha um, deixe comentado no YAML por que esse, e trate o caso
   de o merge ser fast-forward/squash (sem segundo pai) com falha explícita.
2. docker pull <img>:sha-<sha> para cada serviço, em vez de :stage.
3. Se a tag sha-<sha> não existir para algum serviço, o job falha com mensagem
   clara ("image <svc>:sha-<sha> not found; it did not pass stage CI") ANTES de
   retaguear qualquer imagem. Nada de promoção parcial.
4. Retag para :latest e :v<versão do package.json>, como hoje.
5. Escreva os digests promovidos no corpo do GitHub Release.
6. :stage continua existindo como ponteiro de conveniência do ambiente de staging.
7. Crie docs/release-runbook.md se ele ainda não existir, com a seção "Promoção
   por digest" descrevendo o fluxo novo; o resto do runbook é do item P0.13 —
   deixe os títulos das outras seções com "(P0.13)" e sem conteúdo.

DEFINIÇÃO DE PRONTO
- Nenhum docker pull de tag mutável no caminho de promoção.
- A falha por tag ausente ocorre antes do primeiro retag (ordem dos passos).
- YAML válido; runbook criado com a seção de promoção.

NÃO FAÇA
- Não mude o gatilho do workflow nem a condição do commit chore(prod): release.
- Não mexa em stage.yml.

AO TERMINAR
- Marque P0.3 [x] e atualize a contagem no README do plano. Os dois ensaios de
  aceite ficam [ ] (são do desenvolvedor); descreva exatamente como fazer os dois.
- Commit: fix(orca): [P0.3] promote images by commit digest instead of the stage tag
- Responda no formato da seção 10 do 00-context.md.
```

---

## P0.4 — Job `promote-rc` não pode ficar verde sem PR

```text
Você vai implementar o item P0.4 do plano Orca. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/plans/orca-work-management/P0-platform-hardening.md, seção "P0.4".
3. .github/workflows/stage.yml, job "Ensure Release Candidate PR", linha a linha.
4. .github/PULL_REQUEST_TEMPLATE/release_candidate.md.

PROBLEMA
O job usa curl -sS sem -f, encadeia python3 ... || true e tem exit 0 em caminhos
que não confirmam a criação da PR. Ou seja: fica verde sem ter criado a Release
Candidate. Um sinal de CI que mente é pior que nenhum sinal.

TAREFA
1. Reescreva o job preferencialmente com o gh CLI, que já existe no runner:
   gh pr list --base prod --head stage --state open para checar, gh pr create
   --base prod --head stage --title "<título>" --body-file <template> para criar.
   Mantenha o corpo vindo de .github/PULL_REQUEST_TEMPLATE/release_candidate.md.
2. Se optar por manter curl: curl -fsS e verificação explícita do código HTTP em
   toda chamada; remova || true de tudo que busca ou cria a PR (pode manter só no
   git fetch origin prod).
3. No final do job, sempre:
   test -n "$PR_NUMBER" || { echo "::error::RC PR not found nor created"; exit 1; }
4. Preserve o comportamento de não criar PR quando stage não está à frente de prod
   (isso é sucesso, não erro) — e deixe isso explícito no log.
5. Em docs/release-runbook.md, seção "Pré-requisitos", registre que
   Settings → Actions → General → "Allow GitHub Actions to create and approve pull
   requests" precisa estar ligado, e o sintoma quando não está.

DEFINIÇÃO DE PRONTO
- Não existe caminho de saída 0 no job em que a PR não exista e stage esteja à
  frente de prod. Enumere os caminhos de saída na resposta.
- Nenhum || true nos passos de busca/criação.
- YAML e shell válidos (set -euo pipefail onde couber).

NÃO FAÇA
- Não mude o título nem o template da RC (isso é P0.13).
- Não mexa nos outros jobs.

AO TERMINAR
- Marque P0.4 [x] e atualize a contagem no README do plano.
- Commit: fix(orca): [P0.4] fail the release-candidate job when no PR exists
- Responda no formato da seção 10 do 00-context.md.
```

---

## P0.5 — Lockfile congelado e permissões mínimas

```text
Você vai implementar o item P0.5 do plano Orca. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/plans/orca-work-management/P0-platform-hardening.md, seção "P0.5".
3. .github/workflows/stage.yml, prod.yml, release-please.yml, copyright-check.yml.

TAREFA
1. Troque toda ocorrência de pnpm install --no-frozen-lockfile por
   pnpm install --frozen-lockfile, em todos os jobs de todos os workflows.
2. permissions: no nível do workflow passa a contents: read em stage.yml e
   prod.yml. Cada job que precisa de mais declara o seu, mínimo necessário:
   - labeler: pull-requests: write, issues: write
   - build-push: packages: write, contents: read
   - promote-rc: contents: write, pull-requests: write
   - release/promote (prod.yml): contents: write, packages: write
   Confira job a job o que é realmente usado; não copie a lista às cegas — se um
   job não precisa de escrita, não dê escrita, e diga na resposta o que auditou.
3. release-please.yml: revise as permissões e deixe o mínimo que a action exige
   (ela documenta contents: write e pull-requests: write).

DEFINIÇÃO DE PRONTO
- grep -rn "no-frozen-lockfile" .github/ vazio.
- Todo workflow tem permissions: explícito no topo; nenhum job usa write que ele
  não exerce. Liste na resposta a tabela job → permissões → por quê.

NÃO FAÇA
- Não altere versões de dependência nem o pnpm-lock.yaml.
- Não mude a lógica de nenhum job (isso é P0.1–P0.4).

AO TERMINAR
- Marque P0.5 [x] e atualize a contagem no README do plano. O aceite do "PR
  descartável que altera package.json sem lockfile" fica [ ]; descreva o teste.
- Commit: chore(orca): [P0.5] freeze the lockfile in CI and drop excess permissions
- Responda no formato da seção 10 do 00-context.md.
```

---

## P0.9 — Ruff obrigatório

```text
Você vai implementar o item P0.9 do plano Orca. Só este item. Ele é volumoso mas
mecânico: 31 findings de lint e a decisão sobre formatação.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/plans/orca-work-management/P0-platform-hardening.md, seção "P0.9".
3. apps/api/pyproject.toml (seção [tool.ruff]) e apps/api/requirements/*.txt
   (para descobrir a versão de ruff que o projeto usa; se não houver, escolha a
   estável atual e fixe-a).
4. .github/workflows/stage.yml, jobs changes e ci.

PONTO DE PARTIDA (verifique você mesmo, não confie no número)
cd apps/api && ruff check . ; ruff format --check .
O levantamento de 03/09 achou 31 findings de check e 66 arquivos de format.

TAREFA
1. Corrija todos os findings de ruff check em apps/api. Regras E e F: import não
   usado, variável não usada, comparação, linha longa. Correção real, nunca
   # noqa — se um caso exigir noqa, justifique na resposta arquivo por arquivo.
2. Formatação: rode ruff format apenas nos arquivos que o fork tocou. Descubra
   quais com: git diff --name-only 5662b7610 -- apps/api | grep '\.py$'
   (5662b7610 é o commit upstream base). Os demais arquivos, upstream puro,
   entram numa exclusão temporária explícita em [tool.ruff.format] exclude, com
   comentário dizendo que ela sai na próxima sync (item P0.11).
3. Acrescente a verificação ao CI: passo no job ci ou um job api_lint
   condicionado a needs.changes.outputs.api, que instala a versão fixada de ruff
   e roda, em apps/api: ruff check . && ruff format --check .
4. Não altere a lista de regras selecionadas ([E, F]) nem o line-length.

DEFINIÇÃO DE PRONTO
- ruff check . limpo em apps/api (rode e cole a saída).
- ruff format --check . limpo com a exclusão documentada.
- Nenhuma mudança de comportamento: o diff é só lint/format. Se alguma correção
  mudar semântica (ex.: variável não usada que era efeito colateral), pare nesse
  arquivo e reporte em vez de adivinhar.

NÃO FAÇA
- Não formate arquivos upstream fora da lista do fork: isso destrói a próxima sync.
- Não toque em migrações (já excluídas no pyproject).

AO TERMINAR
- Marque P0.9 [x] e atualize a contagem no README do plano.
- Commit: separe em dois: chore(orca): [P0.9] fix ruff findings across apps/api
  e ci(orca): [P0.9] enforce ruff in stage CI — nessa ordem.
- Responda no formato da seção 10 do 00-context.md, com a lista dos arquivos
  formatados e a lista dos excluídos.
```

---

## P0.8 — Suíte upstream no CI

```text
Você vai implementar o item P0.8 do plano Orca. Só este item.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/plans/orca-work-management/P0-platform-hardening.md, seção "P0.8".
3. .github/workflows/stage.yml, job api_tests (hoje roda só
   pytest plane/tests/unit/orca/ -q).
4. apps/api/tests/RUNNING_TESTS.md, apps/api/tests/TESTING_GUIDE.md,
   docker-compose-test.yml, apps/api/pytest.ini ou setup.cfg (marcadores).
5. Liste os diretórios sob apps/api/plane/tests/ e diga, na resposta, qual
   depende de serviço externo (MinIO, RabbitMQ, Redis) e por quê.

PROBLEMA
O CI roda só os testes do fork. Uma regressão que o fork causa em código upstream
(e o fork altera serializers, views de issue, celery, intake) passa direto.

TAREFA
1. O job api_tests passa a rodar pytest plane/tests/unit -q -m "unit"
   (inclui orca).
2. Se algum diretório exigir serviço ausente no runner, exclua com --ignore e
   registre cada exclusão, com o motivo em uma linha, numa seção nova
   "Exclusões no CI" de apps/api/tests/RUNNING_TESTS.md. Exclusão sem motivo
   escrito não entra.
3. Novo job manual (workflow_dispatch) que roda plane/tests/contract e
   plane/tests/smoke usando docker-compose-test.yml, com os serviços extras.
   Ele não bloqueia PR.

DEFINIÇÃO DE PRONTO
- O job de PR/stage roda a suíte unitária inteira, com no máximo as exclusões
  justificadas.
- A lista de exclusões está no RUNNING_TESTS.md e no comentário do YAML.

NÃO FAÇA
- Não altere teste algum para fazer o CI passar. Se um teste upstream falha por
  causa do fork, PARE e reporte: isso é um achado, não um obstáculo.
- Não rode a suíte você mesmo (00-context §6). Diga o comando ao desenvolvedor.

AO TERMINAR
- Marque P0.8 [x] e atualize a contagem no README do plano. O aceite "CI verde"
  fica [ ] até o desenvolvedor rodar.
- Commit: ci(orca): [P0.8] run the upstream unit suite in stage CI
- Responda no formato da seção 10 do 00-context.md.
```

---

## P0.10 — Validação completa do `id_token` do Entra e timeouts

```text
Você vai implementar o item P0.10 do plano Orca. É o item de maior risco da fase:
autenticação. Faça só este item, e faça devagar.

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/plans/orca-work-management/P0-platform-hardening.md, seção "P0.10".
3. apps/api/plane/authentication/provider/oauth/entra.py inteiro.
4. apps/api/plane/authentication/adapter/oauth.py e adapter/error.py.
5. apps/api/plane/tests/unit/orca/test_entra_provider.py — os testes que já existem.
6. docs/entra-directory-sync.md.
7. Um provider OAuth vizinho (google.py ou github.py) para o padrão de erro.

PROBLEMA
decode_id_token_claims lê o payload do JWT sem verificar assinatura; só o claim
tid é conferido. Um token forjado com o tid certo passa. Além disso, as chamadas
ao token endpoint e ao Microsoft Graph em adapter/oauth.py não têm timeout: uma
resposta pendurada prende o worker.

TAREFA
1. Validação real do id_token, com PyJWT (PyJWT==2.13.0 e cryptography já estão
   em apps/api/requirements/base.txt):
   - jwt.PyJWKClient(f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
     cache_keys=True) para obter a chave de assinatura pelo kid do header;
   - jwt.decode(id_token, key, algorithms=["RS256"], audience=client_id,
     issuer=f"https://login.microsoftonline.com/{tenant}/v2.0",
     options={"require": ["exp", "iat", "nbf", "aud", "iss", "tid"]}).
   Mantenha verify_tenant como defesa em profundidade e o /me do Graph.
2. Nonce: gere no início do fluxo, guarde na sessão no mesmo lugar do state,
   inclua no authorize e exija igualdade na volta. Divergência é erro, não aviso.
3. Timeouts: requests.post/get(..., timeout=(5, 15)) em todas as chamadas de
   adapter/oauth.py. Isso beneficia todos os providers — está no escopo.
4. Erros novos em authentication/adapter/error.py: ENTRA_ID_TOKEN_INVALID e
   ENTRA_NONCE_MISMATCH, mais os mapas de erro do web e do space
   (apps/web/helpers/authentication.helper.tsx,
   apps/space/helpers/authentication.helper.tsx,
   packages/constants/src/auth/core.ts), seguindo exatamente o padrão dos códigos
   Entra que já existem lá.
5. Testes em test_entra_provider.py, gerando um par de chaves RSA no próprio teste
   e mockando PyJWKClient: aud errado, iss errado, token expirado, assinatura
   inválida, nonce divergente, caminho feliz. Cada um verificando o código de erro
   específico, não só "levantou exceção".
6. docs/entra-directory-sync.md, seção Troubleshooting: os dois erros novos, o que
   os causa e o que fazer.

DEFINIÇÃO DE PRONTO
- Nenhum caminho decodifica id_token sem verificar assinatura, aud, iss e exp.
- O fluxo falha fechado: qualquer erro de validação é 4xx com código Orca, nunca
  login concedido.
- Os seis testes existem e são específicos.
- ruff check/format limpos.

NÃO FAÇA
- Não mude o fluxo de outros providers além de acrescentar timeout.
- Não relaxe validação "para o teste passar". Se o tenant de teste não existir, o
  teste usa chave gerada localmente e PyJWKClient mockado — é assim que se testa.
- Não guarde segredo nenhum no repositório.

AO TERMINAR
- Marque P0.10 [x] e atualize a contagem no README do plano. O aceite de validação
  contra tenant real fica [ ] (pendência de operação).
- Commit: fix(orca): [P0.10] verify Entra id_token signature, audience and nonce
- Responda no formato da seção 10 do 00-context.md, com uma seção extra
  "Superfície de autenticação tocada" listando cada caminho de login afetado.
```

---

## P0.13 — Versão 1.5.0, Release Please e runbook

```text
Você vai implementar o item P0.13 do plano Orca. Só este item. Ele depende de
P0.3 (promoção por digest) estar mesclado para o runbook descrever o fluxo real;
se P0.3 ainda não estiver em stage, escreva o runbook conforme o comportamento
alvo e marque cada trecho dependente com "(após P0.3)".

LEIA ANTES DE EDITAR
1. docs/plans/orca-work-management/codex/00-context.md.
2. docs/plans/orca-work-management/P0-platform-hardening.md, seção "P0.13".
3. package.json (versão atual 1.4.0-plane.1.4.1), .github/release-please-manifest.json,
   .github/release-please-config.json, .github/workflows/release-please.yml.
4. .github/PULL_REQUEST_TEMPLATE/release_candidate.md e FORK.md §3 fases 4 e 5.
5. .github/workflows/prod.yml — o que de fato dispara a promoção.

PROBLEMA
O template da RC afirma que o merge em prod dispara a promoção. Não é o que
acontece: prod.yml só promove no commit chore(prod): release, que vem do merge da
PR que o Release Please abre. Quem seguir o template hoje espera um deploy que
não acontece.

TAREFA
1. Versão: aplique 1.5.0-plane.1.4.2 em package.json e no
   .github/release-please-manifest.json, OU deixe o Release Please calcular a
   partir dos commits feat(orca). Escolha uma das duas, aplique de forma
   consistente nos três arquivos de config e documente a escolha em FORK.md.
   Diga na resposta qual escolheu e por quê.
2. release_candidate.md: descreva o fluxo real, em quatro passos numerados —
   merge de stage em prod → Release Please abre a PR de release → merge dessa PR
   gera o commit chore(prod): release → prod.yml promove por digest.
3. docs/release-runbook.md (criado ou completado): 
   - pré-requisitos (permissões de Actions, variáveis novas do release);
   - passo a passo da RC;
   - promoção por digest (P0.3);
   - verificação pós-deploy (o que olhar, em que ordem);
   - rollback: comandos docker pull <img>@sha256:... + retag :latest + redeploy
     Coolify, para os seis serviços, com os digests do release anterior;
   - checklist de variáveis de ambiente novas por release;
   - espaço datado para o registro do ensaio ("Ensaio de <data>: duração, o que
     falhou").
4. FORK.md §3 fase 4: corrija a descrição para o mesmo fluxo do template.

DEFINIÇÃO DE PRONTO
- Template, FORK.md e runbook descrevem exatamente o que os workflows executam.
  Se ainda houver divergência, ela está listada na sua resposta, não escondida.
- O runbook é executável por alguém que nunca fez o release: nenhum passo diz
  "faça o deploy", todos dizem qual comando ou qual botão.

NÃO FAÇA
- Não altere a lógica dos workflows aqui (P0.1–P0.5 já cuidaram disso).

AO TERMINAR
- Marque P0.13 [x] e atualize a contagem no README do plano. O aceite do ensaio
  fica [ ] — é do desenvolvedor.
- Commit: docs(orca): [P0.13] document the real release flow and add a runbook
- Responda no formato da seção 10 do 00-context.md.
```

---

## P0.11 — Sync com Plane CE 1.4.2 *(condução humana; Codex ajuda)*

O merge de upstream é humano: quem resolve conflito precisa de contexto do fork.
Use o Codex para o levantamento, antes do merge:

```text
Trabalho de reconhecimento, sem editar nada. Você vai preparar o sync do fork com
o Plane CE 1.4.2 (item P0.11 do plano Orca).

LEIA
1. docs/plans/orca-work-management/codex/00-context.md, seções 1 e 2.
2. FORK.md §3, fase 5 (procedimento de sync).

TAREFA (só leitura e relatório)
1. git fetch upstream (se o remote existir; se não, diga isso e pare) e liste o
   intervalo de commits entre a base atual do fork (5662b7610) e a tag v1.4.2.
2. Produza a lista de arquivos que o fork alterou em relação a 5662b7610:
   git diff --name-only 5662b7610 HEAD
3. Cruze as duas listas e produza a TABELA DE COLISÃO: arquivo, o que o fork mudou
   nele (uma linha), o que o upstream mudou nele (uma linha), risco (alto/médio/
   baixo) e quem deve resolver.
4. Destaque separadamente: migrações novas do upstream, mudanças em serializers de
   issue, mudanças em celery/settings, mudanças em packages/i18n.
5. Não edite arquivo nenhum. Não faça merge. Entregue só o relatório.

RESPOSTA
Tabela de colisão + ordem sugerida de resolução + o que testar depois de cada
grupo de conflitos.
```

---

## P0.12 — Apagar branches remotos obsoletos *(humano)*

Comando de verificação e ação, para o desenvolvedor — não é trabalho de agente:

```bash
for b in claude/azure-aad-integration-review-5if6pz claude/sync-remote-azure-auth-m6618f claude/aad-end-to-end-egj4dm; do
  echo "== $b"; git log --oneline origin/stage.."origin/$b" | head
done
# se nada relevante:
git push origin --delete claude/azure-aad-integration-review-5if6pz claude/sync-remote-azure-auth-m6618f claude/aad-end-to-end-egj4dm
git ls-remote --heads origin | grep claude/
```
