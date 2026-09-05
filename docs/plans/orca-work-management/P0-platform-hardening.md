# Fase P0 — Segurança da plataforma

**Objetivo:** tornar o release reproduzível e fechar os riscos operacionais
que hoje impediriam promover `stage` para produção, independentemente da
camada organizacional.
**Pré-requisitos:** nenhum. Corre em paralelo com D0.
**Referência:** RFC §2.3, §9 (Fase P0), §12.
**Prioridade interna:** P0.0 primeiro (sem ele, nada do que se valida no
código é comprovadamente o que está implantado), depois P0.6 e P0.7
(comprometimento de conta e de IP), depois P0.1–P0.4 (integridade da cadeia
de release), depois o resto.

**Hardening complementar já entregue fora deste plano** (PRs #5 e #6, merge
`3a4c769` em `stage`): kill switch lido na execução da tarefa Celery, nos
comandos e no SCIM; baseline manual capturado ao elevar papel; orçamento SCIM
cobrado só após autenticar, com bucket separado por IP para falhas; rejeição de
convidados Entra pelo `#EXT#` do `userPrincipalName`. Nenhum desses fecha um
item P0; estão registrados aqui para que a revisão externa de 04/09 não seja
lida como "nada foi feito" nem como "P0 concluído".

Cada item abaixo vira um PR. Comandos listados são para o desenvolvedor rodar
localmente; a sessão de agente não executa builds nem migrações (AGENTS.md).

---

## P0.0 — Compose implanta as imagens deste repositório `[x]`

**Problema.** `docker-compose-orca.yml` apontava as nove imagens para
`ghcr.io/prospect-development-team/plane-orca/<serviço>:stage`, enquanto
`stage.yml` publica em `ghcr.io/${{ github.repository }}` — hoje
`ghcr.io/vitordj/plane`. O README manda o Coolify usar exatamente esse
Compose. Salvo substituição externa no Coolify, o staging rodava imagens do
repositório-pai: olhando o commit deste repositório não era possível afirmar
qual código o ambiente executava. Achado da revisão externa de 04/09.

**Mudança** (entregue em `claude/wayfinder-areas-review-yt98v5`).
- Compose: `image: ${ORCA_IMAGE_REPOSITORY:-ghcr.io/vitordj/plane}/<serviço>:${TAG:-stage}` nos nove serviços; comentário no topo do arquivo explica a regra.
- Job `compose_provenance` em `stage.yml`: falha se o default divergir de `ghcr.io/<repositório em minúsculas>`, se houver `image: ghcr.io/...` fixo, se houver mais de um default ou se algum dos seis serviços não passar pela variável. `build-push` depende dele, então o drift bloqueia a publicação.
- README (tabela de variáveis) e `.env.example` documentam `ORCA_IMAGE_REPOSITORY` e `TAG`.
- Decisão: default com o namespace do fork em vez de `:?required`, para não derrubar o deploy Coolify existente na primeira subida. Promoção por digest fica em P0.2/P0.3; expor commit/digest no runtime fica em P0.15.

**Aceite.**
- [x] Script do check exercitado localmente contra o Compose atual (passa) e quatro variantes (namespace antigo, imagem fixa, dois defaults, serviço fora da variável — todas falham com a mensagem esperada).
- [x] `docker-compose-orca.yml` continua YAML válido.
- [ ] Primeiro deploy de staging após o merge: `docker inspect --format '{{.Config.Image}} {{index .RepoDigests 0}}' api` mostra `ghcr.io/vitordj/plane/api@sha256:...` (registrar data e digest no Gate P0). Só operação pode confirmar.

**Arquivos:** `docker-compose-orca.yml`, `.github/workflows/stage.yml`, `README.md`, `.env.example`.

---

## P0.1 — Build em pull request não publica imagem `[x]`

**Problema.** `.github/workflows/stage.yml`, job `build-push`, passo
`docker/build-push-action` tem `push: true` incondicional e o workflow roda em
`pull_request`. Um PR interno reescreve a tag mutável `:stage` antes de ser
revisado.

**Mudança** (entregue em `claude/loving-carson-n9x6eq`).
- No passo de build: `push: ${{ github.event_name != 'pull_request' }}` e `load: false`.
- Novo passo `Resolve Image Tags`: em PR a tag é `ghcr.io/<repo>/<service>:pr-<n>-<sha>` (inerte, nada é publicado); em push para `stage` são duas tags (P0.2).
- `Log in to GHCR` só roda quando há o que publicar ou retaguear — um PR não recebe credencial de registry.
- Novo passo `Plan Work For <service>` decide, por serviço, entre `build`, `retag` e `skip`; todos os passos seguintes leem essa decisão em vez de repetir a expressão de mudança de path.

**Aceite.**
- [x] `stage.yml` continua YAML válido; todo bloco `run:` passa em `bash -n`; os scripts embutidos (`Resolve Image Tags`, registro e merge de digests, log do deploy) foram exercitados a seco fora do CI, com `GITHUB_ENV`/`GITHUB_OUTPUT` simulados.
- [ ] Um PR de teste executa o job e o log mostra `push: false` (ou o passo de login pulado).
- [ ] `docker manifest inspect ghcr.io/<repo>/api:stage` antes e depois do PR retorna o mesmo digest.
- [ ] Merge em `stage` continua publicando `:stage`.

**Arquivos:** `.github/workflows/stage.yml`.

---

## P0.2 — Publicar tag imutável por SHA e registrar digests `[x]`

**Mudança** (entregue em `claude/loving-carson-n9x6eq`).
- Em push para `stage`, `tags:` passa a duas linhas: `:stage` e `:sha-<commit>` — mesmo digest, dois nomes.
- **Serviço sem mudança no commit não é reconstruído, mas é retagueado**: `docker buildx imagetools create --tag <img>:sha-<commit> <img>:stage` copia o manifesto por digest (sem pull, sem re-push de camadas, manifesto multi-arch preservado). Sem isso, um commit que toca só `apps/api` deixaria os outros cinco serviços sem tag imutável e a promoção por SHA (P0.3) encontraria um buraco no conjunto. Pelo mesmo motivo, `build-push` e `image_digests` passaram a rodar em **todo** push para `stage`, inclusive quando nenhum path de serviço mudou (um commit só de documentação pode ser a cabeça de um release candidate).
- Cada job da matriz grava `digests/<service>.json` (imagem, digest, tag e origem `build`/`retag`) e sobe o artifact `image-digest-<service>`; o job novo `image_digests` funde os seis em `image-digests.json`, publica como artifact `image-digests` (retenção 90 dias), expõe em `outputs.digests` e escreve a tabela no resumo do run. Serviço ausente vira `::warning` nomeando quais faltaram.
- Novo passo no job `deploy` que loga os digests que o Coolify vai puxar — `:stage` é mutável, então o log do run é o único lugar que registra o que entrou em staging.

**Aceite.**
- [x] Script de registro e de merge dos digests exercitados a seco (três serviços, um deles pela via `retag`): `image-digests.json`, `outputs.digests` e o resumo saem com o formato esperado e os ausentes viram warning.
- [ ] Após merge em `stage`, existem `api:stage` e `api:sha-<commit>` com o mesmo digest.
- [ ] Artifact `image-digests` presente no run com seis entradas.

**Arquivos:** `.github/workflows/stage.yml`.

---

## P0.3 — Promoção para produção por SHA, não por `:stage` `[x]`

**Problema.** `.github/workflows/prod.yml` (job "Promote Images", ~l.81) faz
`docker pull <img>:stage` e retagueia. A promoção não está vinculada ao commit
revisado.

**Mudança** (entregue em `claude/loving-carson-n9x6eq`).
- `Resolve The Promoted Stage Commit`: `git fetch --no-tags origin stage` e `git merge-base HEAD refs/remotes/origin/stage`. O commit de release está em `prod`; o commit mais recente que `prod` e `stage` compartilham é exatamente a cabeça de `stage` que o release candidate mesclou — funciona tanto para merge commit quanto para fast-forward, e não depende de `stage` ter parado de andar. O checkout passou a `fetch-depth: 0`.
- `Resolve Digests For The Promoted Commit`: resolve os seis digests de `<img>:sha-<commit>` **antes** de escrever qualquer tag. Se qualquer um faltar, o job falha nomeando os serviços e indicando o remédio (rodar o workflow de stage naquele commit); promoção parcial — três serviços na versão nova e três na antiga — é pior que nenhuma.
- `Promote Docker Images`: `docker buildx imagetools create --tag :latest --tag :<versão> --tag :v<versão> <img>@<digest>`. Copiar por digest garante que as tags de produção caem nos bytes exatos do commit e não achata manifestos multi-arch, o que o `pull`/`tag`/`push` anterior fazia.
- Resumo do run e corpo do GitHub Release passam a listar commit de stage e digest promovido por serviço — é a lista que um rollback lê.
- `:stage` continua existindo só como ponteiro de conveniência para o ambiente de staging.

**Aceite.**
- [x] `prod.yml` continua YAML válido; blocos `run:` passam em `bash -n`; a montagem do bloco de digests do release foi exercitada a seco (a substituição de comando come a quebra de linha final, por isso a linha é acrescentada explicitamente).
- [ ] Ensaio: merge de `stage` em `prod` com commit `chore(prod): release` num ambiente controlado promove exatamente os digests de `image-digests.json`.
- [ ] Ensaio negativo: apagar a tag `sha-<sha>` de um serviço faz o job falhar antes de qualquer retag.
- [ ] Primeira promoção após este merge: o commit de `stage` promovido precisa ter sido construído **por este workflow** para ter as tags `sha-`. Para um release cujo merge-base seja anterior a esta mudança, rodar o workflow de stage por `workflow_dispatch` naquele commit antes de promover.

**Arquivos:** `.github/workflows/prod.yml`, `docs/release-runbook.md` (novo, ver P0.13).

---

## P0.4 — Job `promote-rc` não pode ficar verde sem PR `[x]`

**Problema.** `stage.yml`, job "Ensure Release Candidate PR": `curl -sS` sem
`-f`, `python3 ... || true`, e `exit 0` em caminhos que não confirmam a
criação. O job passa mesmo sem PR.

**Mudança** (entregue em `claude/loving-carson-n9x6eq`).
- O bloco de `curl` + Python foi substituído por `gh pr list`/`gh pr create`/`gh pr edit` (o runner já tem `gh`), com `set -euo pipefail`: erro de API derruba o passo em vez de virar string vazia. O corpo continua vindo de `release_candidate.md`.
- `git fetch origin prod` mantém o `|| true` (o branch pode não existir num repositório novo), mas o passo passou a **verificar** `refs/remotes/origin/prod` e falhar com mensagem própria se não existir — antes, `git rev-list origin/prod..stage` num repositório sem `prod` derrubava o passo com um erro do git que não dizia o que fazer.
- Ao final, `PR_NUMBER` vazio é `::error::RC PR not found nor created` + `exit 1`. O número vem da URL impressa pelo `gh pr create`, com `gh pr list` como fallback — um PR criado com sucesso não pode virar job vermelho por eventual consistency da API.
- Atribuir o responsável ficou explicitamente não fatal (`::warning`): o entregável do job é a PR existir, e um `RELEASE_ASSIGNEE` desatualizado não pode bloquear o release.
- Link da RC e número de commits à frente de `prod` vão para o resumo do run.
- Comentário no workflow registra o modo de falha operacional: `gh pr create` devolve 403 quando "Allow GitHub Actions to create and approve pull requests" (Settings → Actions → General) está desligado e o run usa o `GITHUB_TOKEN` padrão; o remédio é ligar a opção ou definir `RELEASE_PLEASE_TOKEN`. Entra no runbook em P0.13.

**Aceite.**
- [x] Ensaio a seco do passo fora do CI, com `gh` simulado e um repositório git local (`stage` à frente de `prod`, PR já aberta, `gh pr create` devolvendo 403, `stage` igual a `prod`, `prod` inexistente): cria e reaproveita a PR nos dois primeiros casos, sai `1` com a mensagem esperada nos casos 403 e `prod` ausente, e sai `0` sem criar nada quando não há o que promover.
- [ ] Com `stage` à frente de `prod`, o job cria a PR ou falha com mensagem clara.
- [ ] Teste negativo: rodar com token sem permissão → job vermelho.
- [ ] Confirmar em Settings → Actions → General que "Allow GitHub Actions to create and approve pull requests" está ligado (ou que `RELEASE_PLEASE_TOKEN` está configurado). Só quem administra o repositório pode conferir.

**Arquivos:** `.github/workflows/stage.yml`.

---

## P0.5 — Lockfile congelado e permissões mínimas `[~]`

**Mudança.**
- ~~`pnpm install --no-frozen-lockfile` → `pnpm install --frozen-lockfile` em todos os jobs.~~ **Bloqueado, ver abaixo.**
- `permissions:` global em `stage.yml` e `prod.yml` passa a `contents: read`; cada job que precisa (labeler: `pull-requests: write`, `issues: write`; build-push: `packages: write`; promote-rc: `contents: write`, `pull-requests: write`; release: `contents: write`) declara o seu.

**Entregue** (`claude/loving-carson-n9x6eq`): a metade das permissões.
`permissions:` global de `stage.yml` e `prod.yml` é `contents: read`; cada job
declara o mínimo — `labeler` (`pull-requests`/`issues: write`), `build-push`
(`packages: write`), `promote-rc` (`pull-requests: write`; **não** precisa de
`contents: write`, porque abre PR e não empurra commit), `promote` do prod
(`contents: write` para editar o release + `packages: write`), `sync-stage`
(`contents: write`); os demais, `contents: read` explícito.
`release-please.yml` fica como está: a action precisa de `contents: write` e
`pull-requests: write` e não roda `pnpm install`.

**Bloqueio do lockfile (achado desta sessão).** `pnpm install
--frozen-lockfile` **falha hoje**, antes de qualquer mudança nossa:
`pnpm-workspace.yaml` tem 184 entradas de catálogo, `pnpm-lock.yaml` tem 173.
As 11 que faltam — `typescript`, `vite`, `axios`, `uuid`, `lodash-es`,
`postcss`, `express`, `@types/express`, `@react-router/node`,
`@tanstack/react-virtual`, `@tanstack/virtual-core` — foram movidas para
`catalog:` nos `package.json` pelo commit upstream `31853ab2` sem que o
lockfile fosse regenerado: 46 dependências de 17 workspaces têm
`"catalog:"` no `package.json` e o especificador resolvido (ex.: `5.8.3`) no
lockfile. As **versões** batem; o que diverge é a forma do especificador, que
é exatamente o que o `--frozen-lockfile` compara. O `--no-frozen-lockfile`
atual esconde isso reescrevendo o lockfile a cada run — é o próprio defeito
que o item quer fechar.

Trocar a flag sem regenerar o lockfile deixa o CI vermelho em todo PR, então
as duas coisas têm que entrar no mesmo commit. Comando para o desenvolvedor
(a sessão de agente não roda `pnpm install`, AGENTS.md):

```bash
pnpm install --lockfile-only   # regenera pnpm-lock.yaml a partir dos package.json
git diff --stat pnpm-lock.yaml # deve mostrar só as 46 linhas de specifier + a seção catalogs
```

Depois disso, trocar `--no-frozen-lockfile` por `--frozen-lockfile` no job
`ci` de `stage.yml` (o comentário no passo aponta para este item) e confirmar
que um PR sem mudança de dependência fica verde. Alternativa, se a
regeneração trouxer ruído demais: fazer a troca junto com o sync upstream
(P0.11), que já vai mexer no lockfile.

**Aceite.**
- [x] `permissions:` global `contents: read` em `stage.yml` e `prod.yml`, com cada job declarando o seu escopo mínimo.
- [ ] `pnpm install --frozen-lockfile` no CI (depende da regeneração do lockfile acima).
- [ ] CI verde em um PR que não altera dependências.
- [ ] CI vermelho em um PR que altera `package.json` sem atualizar `pnpm-lock.yaml` (teste descartável).

**Arquivos:** `.github/workflows/stage.yml`, `.github/workflows/prod.yml`, `.github/workflows/release-please.yml`.

---

## P0.6 — Remover a senha fixa da migração de usuários `[x]`

**Problema.** `tools/migration/create_users.py` definia uma constante
`DEFAULT_PASSWORD` com uma senha literal (l.23) e a aplicava a todos os
usuários criados (l.84), sem troca forçada. A mesma string, publicada no
repositório e repetida no README da ferramenta, abria toda conta migrada. A
literal continua no histórico do git — por isso o item inclui invalidar as
contas já criadas, não só parar de criar novas.

**Mudança** (entregue em `claude/loving-carson-n9x6eq`).
- Constante removida. Conta nova recebe `set_unusable_password()` + `is_password_autoset = True`, persistidos com `save(update_fields=[...])`. Divergência consciente do `AuthAdapter` (`authentication/adapter/base.py`), que usa `set_password(uuid4().hex)`: lá a conta nasce **durante** um login já verificado; aqui ninguém autenticou, então a conta fica sem senha alguma. O flag é o mesmo que os provedores OAuth gravam, então o fluxo de primeiro acesso (Entra ID ou magic link) e o "definir senha" sem senha antiga funcionam igual.
- Conta que já existe não é tocada: o dono pode ter definido a própria senha, e uma re-execução da migração não pode trancá-lo para fora.
- Script refatorado para expor `create_user_from_payload(member, workspace=None)` e um `MigratedMember` (NamedTuple), e o bootstrap do Django virou `bootstrap_django()`, que não faz nada quando o registro de apps já está pronto — sem isso, importar o módulo no teste apontaria `DJANGO_SETTINGS_MODULE` para produção e trocaria o banco debaixo da suíte.
- Timeout `(5, 30)` na chamada ao Plane de origem: uma migração que trava no meio é pior de diagnosticar que uma que falha.
- `tools/migration/README.md`: seção "First access for pre-created accounts" (Entra ID ou magic link, e como definir senha depois) e seção "If you ran this script before September 2026" com o comando de invalidação — lê a senha antiga de variável de ambiente (a literal não volta ao repositório; está no histórico do git) e devolve a lista de contas afetadas para conferência nos logs de autenticação.

**Testes.** `apps/api/plane/tests/unit/orca/test_migration_tools.py`: conta
criada fica sem senha utilizável e com o flag, relido do banco; conta
existente mantém a senha própria; membership entra com o papel mapeado;
segunda execução não cria nada; e uma guarda que lê o fonte do script e
falha se `DEFAULT_PASSWORD`, a literal antiga ou `set_password(` voltarem.
O script vive fora de `apps/api` e o stack Docker de teste monta só
`apps/api`, então os testes carregam o arquivo por caminho e pulam quando ele
não está montado (no CI, que roda do checkout completo, executam de verdade).

**Aceite.**
- [x] Nenhuma senha literal em `tools/`, `docs/` ou `README.md`: a constante saiu do script, a menção saiu do README da ferramenta, e o teste de regressão lê o fonte e falha se qualquer uma das duas voltar. As ocorrências que restam nesses arquivos são referências ao que foi removido (este enunciado e o comando de invalidação), nenhuma é uma credencial.
- [x] Teste unitário que importa a função de criação e verifica `has_usable_password() is False` e `is_password_autoset is True`. Escrito; a sessão não roda pytest (AGENTS.md) — confirmar no CI de `stage`.
- [x] Ruff limpo (`check` e `format --check`, linha 120) no script e no teste novos.
- [ ] Contas já criadas em qualquer ambiente com a senha antiga foram invalidadas (registrar data e ambiente no quadro). Só operação pode fazer; procedimento pronto no README da ferramenta.

**Achado adjacente (não corrigido).** O script deriva `username` de
`email.split("@")[0]`, e `username` é `unique`. Dois endereços com o mesmo
local part em domínios diferentes (`ana@a.com`, `ana@b.com`) abortam a
migração com `IntegrityError` no meio da execução. O upstream usa
`uuid4().hex` para isso. Fora do escopo deste item; vira item próprio se a
migração for reexecutada.

**Arquivos:** `tools/migration/create_users.py`, `tools/migration/README.md`, `apps/api/plane/tests/unit/orca/test_migration_tools.py`.

---

## P0.7 — `TRUSTED_PROXIES` sem fallback aberto `[ ]`

**Problema.** `apps/proxy/Caddyfile.ce` l.8: `trusted_proxies static {$TRUSTED_PROXIES:0.0.0.0/0}`; `.env.example` l.44 repete `0.0.0.0/0`.

**Mudança.**
- Caddyfile: `trusted_proxies static {$TRUSTED_PROXIES}` sem default. Caddy falha no boot se a variável estiver vazia, o que é o comportamento desejado em produção.
- `.env.example`: `TRUSTED_PROXIES=` vazio com comentário "obrigatório: CIDR da rede do Coolify/proxy externo; ex.: 10.0.0.0/8". Para desenvolvimento local, `docker-compose-local.yml` define `TRUSTED_PROXIES=127.0.0.1/32,172.16.0.0/12`.
- `docker-compose-orca.yml`: serviço `proxy` passa `TRUSTED_PROXIES=${TRUSTED_PROXIES:?TRUSTED_PROXIES is required}`.
- README: linha na tabela de variáveis com "Required: Yes".
- Confirmar que o Django recebe o IP via `X-Forwarded-For` já filtrado (o `SECURE_PROXY_SSL_HEADER` e o middleware de IP existentes continuam iguais).

**Aceite.**
- [ ] `docker compose -f docker-compose-orca.yml config` falha sem a variável.
- [ ] Com a variável correta, `curl -H "X-Forwarded-For: 1.2.3.4"` de fora da faixa não altera o IP visto pela aplicação (verificar em log de autenticação ou endpoint de debug temporário).

**Arquivos:** `apps/proxy/Caddyfile.ce`, `.env.example`, `docker-compose-orca.yml`, `docker-compose-local.yml`, `README.md`.

---

## P0.8 — Suíte upstream no CI `[ ]`

**Problema.** `stage.yml`, job `api_tests`, roda só
`pytest plane/tests/unit/orca/ -q`.

**Mudança.**
- Rodar `pytest plane/tests/unit -q -m "unit"` (inclui orca). Se algum diretório falhar por dependência ausente no runner (MinIO, RabbitMQ), excluir com `--ignore` e listar cada exclusão com motivo em `apps/api/tests/RUNNING_TESTS.md` (seção "Exclusões no CI").
- Job separado, manual (`workflow_dispatch`), para `plane/tests/contract` e `plane/tests/smoke` com os serviços extras, usando `docker-compose-test.yml`.

**Aceite.**
- [ ] CI de `stage` executa os testes upstream e fica verde.
- [ ] Lista de exclusões tem no máximo os diretórios que exigem serviço ausente, cada um justificado.

**Arquivos:** `.github/workflows/stage.yml`, `apps/api/tests/RUNNING_TESTS.md`.

---

## P0.9 — Ruff obrigatório `[ ]`

**Situação.** Nenhum workflow roda ruff. `apps/api/pyproject.toml` já exclui
`**/migrations/*`, seleciona `E`, `F`, linha 120. `ruff check .` em `apps/api`
hoje reporta 31 findings (arquivos de `serializers/project.py`,
`views/project_label.py`, `views/issue/sub_issue.py`, `views/issue/label.py`,
`serializers/intake.py`, `bgtasks/event_tracking_task.py`, etc.); `ruff format
--check .` reporta 66 arquivos.

**Mudança.**
- Passo no job `ci` (ou novo job `api_lint` condicionado a `needs.changes.outputs.api`): `pip install ruff==<versão do requirements dev>` e `ruff check . && ruff format --check .` em `apps/api`.
- Corrigir os 31 findings de lint. Para o format, corrigir apenas os arquivos tocados pelo fork (diff contra o commit upstream `5662b7610`); os demais 66 são upstream e entram numa exclusão temporária via `[tool.ruff.format] exclude` listada explicitamente, a ser removida na próxima sync.

**Aceite.**
- [ ] `ruff check .` limpo em `apps/api`.
- [ ] `ruff format --check .` limpo com a exclusão temporária documentada no `pyproject.toml`.
- [ ] Job vermelho num PR que introduz `import os` não usado (teste descartável).

**Arquivos:** `.github/workflows/stage.yml`, `apps/api/pyproject.toml`, arquivos apontados pelo ruff.

---

## P0.10 — Validação completa do `id_token` do Entra e timeouts `[ ]`

**Situação.** `apps/api/plane/authentication/provider/oauth/entra.py`:
`decode_id_token_claims` lê o payload sem verificar assinatura; só `tid` é
conferido. Chamadas ao token endpoint e ao Graph em `adapter/oauth.py` sem
timeout. `PyJWT==2.13.0` e `cryptography` já estão em
`apps/api/requirements/base.txt`.

**Mudança.**
- Usar `jwt.PyJWKClient(f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys", cache_keys=True)` e `jwt.decode(id_token, key, algorithms=["RS256"], audience=client_id, issuer=f"https://login.microsoftonline.com/{tenant}/v2.0", options={"require": ["exp","iat","nbf","aud","iss","tid"]})`.
- Gerar `nonce` no início do fluxo, guardar na sessão (mesmo lugar do `state`), incluir no `authorize`, exigir igualdade na volta.
- Manter `verify_tenant` (defesa em profundidade) e o `/me` do Graph.
- `requests.post/get(..., timeout=(5, 15))` em todas as chamadas do adapter OAuth (benefício para todos os providers).
- Erros novos em `authentication/adapter/error.py` (`ENTRA_ID_TOKEN_INVALID`, `ENTRA_NONCE_MISMATCH`) e nos mapas de erro do web e do space, como os códigos Entra existentes.
- Testes em `test_entra_provider.py`: token com `aud` errado, `iss` errado, expirado, assinatura inválida (par de chaves RSA gerado no teste, `PyJWKClient` mockado), `nonce` divergente, caminho feliz.

**Aceite.**
- [ ] Todos os testes acima verdes.
- [ ] Doc `docs/entra-directory-sync.md` §Troubleshooting descreve os dois erros novos.
- [ ] Validação de ponta a ponta contra tenant real registrada no quadro quando o tenant existir (não bloqueia o merge).
- [ ] `test_entra_provider.py` deixa de usar `StubProvider` (que pula o `__init__`) nos casos de tenant e e-mail: o construtor real, com a configuração de instância mockada, entra na cobertura (achado do PR #6).

**Arquivos:** `authentication/provider/oauth/entra.py`, `authentication/adapter/oauth.py`, `authentication/adapter/error.py`, `apps/web/helpers/authentication.helper.tsx`, `apps/space/helpers/authentication.helper.tsx`, `packages/constants/src/auth/core.ts`, testes, doc.

---

## P0.11 — Sync com Plane CE 1.4.2 `[ ]`

**Mudança.** Seguir FORK.md §Phase 5: atualizar branch `upstream` com
`makeplane/plane` tag `v1.4.2`, criar `sync/upstream-merge-2026-09`, resolver
conflitos, abrir PR para `stage`. Registrar no PR o SHA upstream usado.
Atualizar `package.json` para `...-plane.1.4.2` (ver P0.13).

**Aceite.**
- [ ] CI verde no PR de sync.
- [ ] `docs/` e `FORK.md` mencionam 1.4.2 como base.

---

## P0.12 — Apagar branches remotos obsoletos `[ ]`

Branches com abordagens superadas pelo PR #2:
`claude/azure-aad-integration-review-5if6pz`,
`claude/sync-remote-azure-auth-m6618f`, `claude/aad-end-to-end-egj4dm`.
Verificar antes com `git log origin/stage..<branch>` que nada ali é desejado
(o levantamento de 03/09 concluiu que não). Apagar também branches `claude/*`
já mesclados.

**Aceite.**
- [ ] `git ls-remote --heads origin | grep claude/` lista só branches com trabalho em andamento.

---

## P0.13 — Versão 1.5.0, Release Please e runbook `[ ]`

**Situação.** `package.json` em `1.4.0-plane.1.4.1`;
`.github/release-please-manifest.json` e `.github/release-please-config.json`
na mesma linha; o template `release_candidate.md` afirma que o merge dispara
a promoção, mas `prod.yml` só promove com commit `chore(prod): release`.

**Mudança.**
- Decidir e aplicar `1.5.0-plane.1.4.2` em `package.json` e no manifest (ou deixar o Release Please calcular a partir dos `feat(orca)`; documentar qual dos dois).
- `release_candidate.md`: descrever o fluxo real em duas etapas (merge em `prod` → Release Please abre PR de release → merge desse PR gera o commit `chore(prod): release` → `prod.yml` promove).
- Novo `docs/release-runbook.md`: passo a passo de RC, promoção por digest (P0.3), verificação pós-deploy, rollback para os seis digests anteriores (comandos `docker pull <img>@sha256:...` + retag `:latest` + redeploy Coolify), e checklist de variáveis de ambiente novas por release.
- Ensaiar o runbook uma vez de ponta a ponta em staging e anotar duração e problemas.

**Aceite.**
- [ ] Runbook ensaiado, com data e resultado no próprio arquivo.
- [ ] Template e FORK.md §Phase 4 descrevem o mesmo fluxo que os workflows executam.

**Arquivos:** `package.json`, `.github/release-please-manifest.json`, `.github/release-please-config.json`, `.github/PULL_REQUEST_TEMPLATE/release_candidate.md`, `FORK.md`, `docs/release-runbook.md`.

---

## P0.14 — Parser estrito do kill switch, guard no reconciliador e paridade de variáveis `[x]`

**Problema.** `ORCA_ORG_UNITS_ENABLED` era lido como `== "1"`: `true`, `yes`
ou `on` desligavam a camada em silêncio. O serviço `reconcile_access`, único
ponto que escreve `ProjectMember`, confiava nos chamadores para checar o
switch. O Compose não encaminhava a variável a nenhum serviço, então API e
worker podiam divergir (API desligada, worker no default ligado). Achados da
revisão externa de 04/09 e pendência registrada no PR #6.

**Mudança** (entregue em `claude/wayfinder-areas-review-yt98v5`).
- `apps/api/plane/utils/orca_env.py`: `parse_env_flag`/`env_flag` aceitam `1/true/yes/on` e `0/false/no/off` (case-insensitive, espaços tolerados); vazio → default; qualquer outro valor → `ImproperlyConfigured` no boot. `settings/common.py` usa `env_flag` só para o switch Orca; flags upstream não foram tocadas.
- `reconcile_access` retorna `[]` sem escrever quando a camada está desligada (defesa em profundidade; os chamadores mantêm o guard).
- `docker-compose-orca.yml` encaminha `ORCA_ORG_UNITS_ENABLED`, `ORCA_ORG_SYNC_MAX_EDGES`, `SCIM_RATE_LIMIT` e `SCIM_AUTH_FAILURE_RATE_LIMIT` a `api`, `worker`, `beat-worker` e `migrator` a partir de uma única variável cada.
- Docs: `organizational-units.md` §Settings, README, `.env.example` (raiz e `apps/api`).

**Testes.** `test_orca_env_flag.py` (grafias aceitas, default, recusa com o
nome da variável na mensagem); dois testes novos em `test_orca_hardening.py`
(`reconcile_access` direto com switch desligado não escreve; controle ligado
escreve).

**Aceite.**
- [x] Ruff limpo nos arquivos tocados. Testes escritos; a sessão não roda pytest (AGENTS.md) — confirmar no CI de `stage`.
- [ ] Em staging, `docker compose exec <api|bgworker|beatworker> printenv ORCA_ORG_UNITS_ENABLED` devolve o mesmo valor nos três (registrar no Gate P0).

**Arquivos:** `apps/api/plane/utils/orca_env.py`, `apps/api/plane/settings/common.py`, `apps/api/plane/app/services/orca/org_unit_reconciler.py`, `docker-compose-orca.yml`, testes, docs.

---

## P0.15 — Runtime expõe commit e versão implantados `[ ]`

**Problema.** Nada dentro de um contêiner diz de qual commit ele foi
construído. Sem isso, P0.0–P0.3 provam a cadeia até o registry, mas não que
o ambiente está executando aquele artefato.

**Mudança.**
- `build-push`: `build-args: GIT_SHA=${{ github.sha }}`; os seis Dockerfiles gravam `ORCA_BUILD_SHA` (e a tag `sha-<commit>` de P0.2) como variável de ambiente da imagem.
- API: endpoint interno `GET /api/orca/build-info/` (sessão, admin de instância) e comando `orca_build_info` devolvendo `{"version", "git_sha", "service", "orca_org_units_enabled"}`; o digest da imagem não é conhecido de dentro do contêiner, então a proveniência primária é o SHA.
- Web/admin: exibir o SHA no rodapé do god-mode (opcional nesta fase).

**Aceite.**
- [ ] Em staging, o endpoint devolve o SHA do merge que disparou o deploy.
- [ ] `docs/release-runbook.md` (P0.13) inclui o passo "conferir build-info após o deploy".

**Arquivos:** `.github/workflows/stage.yml`, Dockerfiles, `apps/api/plane/app/views/orca_build_info.py` (novo), `apps/api/plane/app/urls/orca.py`, docs.

---

## P0.16 — Fixar MinIO e alinhar a versão do PostgreSQL `[ ]`

**Situação.** `docker-compose-orca.yml` usa `minio/minio` sem tag; o CI
(`stage.yml`, job `api_tests`) usa `postgres:16-alpine` enquanto o Compose de
implantação e `docker-compose-test.yml` usam `postgres:15.7-alpine`.

**Mudança.**
- `minio/minio:RELEASE.<data>` (tag imutável) no Compose, com nota no runbook sobre como atualizar.
- CI passa a `postgres:15.7-alpine`, igual ao ambiente implantado; ou, se a decisão for migrar o ambiente para 16, registrar em `apps/api/tests/RUNNING_TESTS.md` a matriz suportada e o plano de upgrade do banco. Decidir e registrar aqui.

**Aceite.**
- [ ] `grep -n "image:" docker-compose-orca.yml` não mostra imagem sem tag.
- [ ] A versão de PostgreSQL do CI e a do Compose são a mesma, ou a matriz está documentada.

**Arquivos:** `docker-compose-orca.yml`, `.github/workflows/stage.yml`, `apps/api/tests/RUNNING_TESTS.md`.

---

## Gate P0

- [ ] Todos os 17 itens `[x]` ou `[-]` com motivo.
- [ ] CI de `stage` verde com suíte upstream (P0.8) e ruff (P0.9).
- [ ] Ensaio completo de RC documentado em `docs/release-runbook.md`: PR criada pelo job, promoção por digest, deploy em staging, rollback.
- [ ] Nenhuma conta com a senha antiga da migração em nenhum ambiente.
- [ ] `TRUSTED_PROXIES` configurado em staging e produção com a faixa real.
- [ ] Staging comprovadamente executando imagens de `ghcr.io/vitordj/plane` (P0.0, `docker inspect`), com data e digest registrados aqui.
- [ ] `ORCA_ORG_UNITS_ENABLED` com o mesmo valor em api, worker e beat em staging (P0.14).

Data do gate: ____
