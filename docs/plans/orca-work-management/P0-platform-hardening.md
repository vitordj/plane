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
`docker/build-push-action` tinha `push: true` incondicional e o workflow roda em
`pull_request`. Um PR interno reescrevia a tag mutável `:stage` antes de ser
revisado.

**Mudança** (entregue em `claude/continue-implementations-bquse8`).
- No passo de build: `push: ${{ github.event_name != 'pull_request' }}` e `load: false`.
- Passo novo `Resolve Image Tags` monta a lista de tags em shell em vez de
  interpolar no `with:`: em `pull_request` a única tag é
  `<namespace>/<serviço>:pr-<número>-<sha>`, fora dele `:stage`. O build
  continua servindo para provar que compila.
- O passo "Log in to GHCR" ganhou `if:` excluindo `pull_request`: sem
  credencial o push é impossível mesmo que a condição acima regrida.
- Cabeçalho do workflow descreve a regra.

**Aceite.**
- [x] O passo de login e o `push:` do build são ambos condicionados ao evento; a
  tag de PR não é `:stage` em nenhum caminho (lido no arquivo, sem CI).
- [ ] Um PR de teste executa o job e o log mostra `push: false` (ou o passo de login pulado).
- [ ] `docker manifest inspect ghcr.io/<repo>/api:stage` antes e depois do PR retorna o mesmo digest.
- [ ] Merge em `stage` continua publicando `:stage`.

**Arquivos:** `.github/workflows/stage.yml`.

---

## P0.2 — Publicar tag imutável por SHA e registrar digests `[x]`

**Mudança** (entregue em `claude/continue-implementations-bquse8`).
- Em push para `stage`, `tags:` passa a duas linhas: `:stage` e `:sha-<commit>`.
- Cada job da matriz grava `steps.build.outputs.digest` e o publica como
  artifact `image-digest-<serviço>` (uma matriz não escreve output compartilhado).
- Job novo `image_digests` junta os seis num `image-digests.json`
  (`{commit, workflow_run, images: {serviço: {image, digest, tag, rebuilt}}}`),
  publicado como artifact `image-digests` e exposto em `outputs.digests`.
- **Serviço não reconstruído neste commit:** `build-push` só reconstrói o que
  mudou de caminho, então um commit só de API deixaria os outros cinco sem
  `sha-<commit>` e a promoção por SHA (P0.3) acharia um conjunto incompleto. O
  job resolve o digest para o qual `:stage` aponta, registra `rebuilt: false` e
  cria a tag `sha-<commit>` a partir dele por digest (`imagetools create`, não
  copia camada). Se nem `:stage` existir, falha com instrução de rodar o
  workflow uma vez por `workflow_dispatch`.
- `deploy` ganhou passo que loga commit, imagem e digest antes de acionar o
  Coolify, e o job passa a depender de `image_digests`.
- Resumo do run traz a tabela serviço/digest/reconstruído.

**Aceite.**
- [x] Lógica do passo `Collect Digests` exercitada localmente com `docker`
  stubado: caminho feliz (dois serviços reconstruídos, três herdados) e caminho
  de falha (`:stage` ausente → erro com o nome do serviço, antes de qualquer tag).
- [ ] Após merge em `stage`, existem `api:stage` e `api:sha-<commit>` com o mesmo digest.
- [ ] Artifact `image-digests` presente no run com seis entradas.

**Arquivos:** `.github/workflows/stage.yml`.

---

## P0.3 — Promoção para produção por SHA, não por `:stage` `[x]`

**Problema.** `.github/workflows/prod.yml` (job "Promote Images") fazia
`docker pull <img>:stage` e retagueava. A promoção não estava vinculada ao
commit revisado: promovia o build mais recente, não o aprovado.

**Mudança** (entregue em `claude/continue-implementations-bquse8`).
- Passo `Resolve The Stage Commit Under Release`: o commit sob release é o
  segundo pai do merge mais recente no histórico de `prod` (o merge da RC), e
  precisa ser ancestral de `origin/stage` — senão o job para. RC com squash
  (sem segundo pai) e commit que nunca esteve em `stage` falham com mensagem
  explícita apontando o runbook.
- `workflow_dispatch` novo com input `stage_sha`, para ensaio e re-promoção
  manual sem precisar de um merge.
- Passo `Verify Every Service Was Built For This Commit`: resolve os seis
  digests de `:sha-<commit>` **antes** de escrever qualquer tag; um serviço
  ausente aborta o job com o nome dele (meia promoção é pior que nenhuma).
- Promoção com `docker buildx imagetools create` a partir do digest, em vez de
  `pull`/`tag`/`push`: publica os mesmos bytes e preserva manifesto multi-arch.
- Corpo do GitHub Release passa a trazer o commit de stage, a tabela de digests
  e os comandos `docker pull` por digest; falha na edição das notas vira aviso,
  não bloqueia o deploy (os digests também estão no resumo do run e no artifact
  `promoted-digests`).
- `:stage` continua existindo só como ponteiro de conveniência do staging.
- `docs/release-runbook.md` novo: tabela de tags (quais podem se mover), passo
  a passo do release, verificação pós-deploy, promoção manual e rollback por
  digest. As seções de ensaio e de variáveis por release estão marcadas como
  pendência de P0.13.

**Aceite.**
- [x] Resolução do SHA exercitada em repositório git de teste: caminho feliz
  (merge da RC + commit de release → segundo pai é o topo de `stage`), RC com
  squash (sem merge commit → erro) e commit fora de `stage` (rejeitado pelo
  `merge-base --is-ancestor`).
- [ ] Ensaio: merge de `stage` em `prod` com commit `chore(prod): release` num ambiente controlado promove exatamente os digests de `image-digests.json`.
- [ ] Ensaio negativo: apagar a tag `sha-<sha>` de um serviço faz o job falhar antes de qualquer retag.

**Arquivos:** `.github/workflows/prod.yml`, `docs/release-runbook.md` (novo, seções restantes em P0.13).

---

## P0.4 — Job `promote-rc` não pode ficar verde sem PR `[ ]`

**Problema.** `stage.yml`, job "Ensure Release Candidate PR": `curl -sS` sem
`-f`, `python3 ... || true`, e `exit 0` em caminhos que não confirmam a
criação. O job passa mesmo sem PR.

**Mudança.**
- Trocar os `curl` por `curl -fsS` e checar código HTTP; remover `|| true` dos passos que buscam/criam a PR (manter só no `git fetch origin prod`).
- Ao final, `test -n "$PR_NUMBER" || { echo "::error::RC PR not found nor created"; exit 1; }`.
- Preferível: substituir o bloco Python por `gh pr list`/`gh pr create` (o runner já tem `gh`), mantendo o template `release_candidate.md` como corpo.
- Verificar em Settings → Actions → General que "Allow GitHub Actions to create and approve pull requests" está ligado; documentar no runbook.

**Aceite.**
- [ ] Com `stage` à frente de `prod`, o job cria a PR ou falha com mensagem clara.
- [ ] Teste negativo: rodar com token sem permissão → job vermelho.

**Arquivos:** `.github/workflows/stage.yml`.

---

## P0.5 — Lockfile congelado e permissões mínimas `[ ]`

**Mudança.**
- `pnpm install --no-frozen-lockfile` → `pnpm install --frozen-lockfile` em todos os jobs.
- `permissions:` global em `stage.yml` e `prod.yml` passa a `contents: read`; cada job que precisa (labeler: `pull-requests: write`, `issues: write`; build-push: `packages: write`; promote-rc: `contents: write`, `pull-requests: write`; release: `contents: write`) declara o seu.

**Aceite.**
- [ ] CI verde em um PR que não altera dependências.
- [ ] CI vermelho em um PR que altera `package.json` sem atualizar `pnpm-lock.yaml` (teste descartável).

**Arquivos:** `.github/workflows/stage.yml`, `.github/workflows/prod.yml`, `.github/workflows/release-please.yml`.

---

## P0.6 — Remover a senha fixa da migração de usuários `[x]`

**Problema.** `tools/migration/create_users.py` definia uma constante
`DEFAULT_PASSWORD` com uma senha em texto claro e a aplicava a todos os
usuários criados; o README a repetia. Nenhuma troca forçada. Quem lesse o
repositório entrava como qualquer pessoa migrada que ainda não tivesse
acessado.

**Mudança** (entregue em `claude/continue-implementations-bquse8`).
- Constante removida. Conta criada agora recebe `set_unusable_password()` e
  `is_password_autoset = True` (mesmo padrão do provider Entra), gravados com
  `update_fields`. Conta que já existe não é tocada — o script não pode
  redefinir a credencial de quem já acessou.
- Script refatorado para ser importável: `bootstrap_django()` só roda sob
  `__main__`, os imports de model são locais às funções, e a lógica está em
  `create_user_from_payload(payload)` e `add_user_to_workspace(user, workspace, role)`.
  `fetch_users_from_old_plane()` passou a devolver dicts e ganhou `timeout`.
- `tools/migration/README.md`: seção "First sign-in" (Entra ID ou magic link) e
  bloco "If you ran this script before this version" com o shell Django que
  invalida as credenciais das contas criadas pelo script, separando quem nunca
  acessou (invalidar direto) de quem já acessou (revisar log antes).

**Testes.** `apps/api/plane/tests/unit/orca/test_migration_tools.py`: carrega o
script pelo caminho (ele vive fora de `apps/api`; pula com motivo quando
`tools/` não está montado, como no `docker-compose-test.yml`) e verifica conta
nova sem senha utilizável e com `is_password_autoset`, nome/e-mail/username
preservados, segunda execução não redefinindo a senha de quem já existia,
ausência de qualquer constante de senha no módulo, e o papel da associação ao
workspace nos dois sentidos.

**Aceite.**
- [x] Busca pela senha antiga em `tools/`, `docs/` e `README.md` não retorna
  nada: a constante saiu do script, o README parou de citá-la e este item
  passou a descrevê-la sem transcrevê-la.
- [x] Teste unitário escrito; a sessão não roda pytest (AGENTS.md) — confirmar no CI de `stage`.
- [x] Ruff limpo nos arquivos tocados.
- [ ] Contas já criadas em qualquer ambiente com a senha antiga foram invalidadas (registrar data e ambiente no quadro).

**Arquivos:** `tools/migration/create_users.py`, `tools/migration/README.md`,
`apps/api/plane/tests/unit/orca/test_migration_tools.py`.

---

## P0.7 — `TRUSTED_PROXIES` sem fallback aberto `[x]`

**Problema.** `apps/proxy/Caddyfile.ce` tinha
`trusted_proxies static {$TRUSTED_PROXIES:0.0.0.0/0}` e `.env.example` repetia
`0.0.0.0/0`. Com todo mundo confiável, o Caddy preserva o `X-Forwarded-For` que
o cliente mandar, e `plane/utils/ip_address.py:get_client_ip` lê **a primeira
entrada** do cabeçalho: qualquer chamador escolhia o IP que o rate limit e o
log de autenticação registrariam.

**Mudança** (entregue em `claude/continue-implementations-bquse8`).
- Caddyfile: `trusted_proxies static {$TRUSTED_PROXIES}` sem default, com
  comentário explicando a cadeia até o `get_client_ip`. Vazio não derruba o
  Caddy — significa "nenhum proxy é confiável", e nesse caso ele descarta o
  `X-Forwarded-For` recebido e reescreve com o IP do par direto. Seguro, porém
  todo request parece vir do proxy; por isso a exigência ficou no Compose, onde
  falha de verdade.
- `docker-compose-orca.yml`: `TRUSTED_PROXIES: ${TRUSTED_PROXIES:?...}` no
  serviço `proxy`, com a mensagem dizendo o que preencher.
- `docker-compose.yml` (dev/self-host, que constrói o proxy localmente) passa
  `${TRUSTED_PROXIES:-127.0.0.1/32,172.16.0.0/12}`: sem isso, tirar o default do
  Caddyfile quebraria o desenvolvimento local. `docker-compose-local.yml` não
  tem serviço de proxy, então não foi tocado.
- `.env.example`: variável vazia com o comentário do que preencher e por quê.
- README: linha na tabela de variáveis com "Required: **Yes**".
- `SECURE_PROXY_SSL_HEADER` e o middleware de IP do Django continuam iguais: o
  que muda é o cabeçalho chegar filtrado.

**Aceite.**
- [x] `docker compose -f docker-compose-orca.yml config` falha sem a variável
  (verificado nesta sessão: `required variable TRUSTED_PROXIES is missing a
  value: ...`) e passa com `TRUSTED_PROXIES=10.0.0.0/8`.
- [ ] Com a variável correta, `curl -H "X-Forwarded-For: 1.2.3.4"` de fora da faixa não altera o IP visto pela aplicação (verificar em log de autenticação ou endpoint de debug temporário).
- [ ] Faixa real do proxy Coolify preenchida em staging e produção (pendência de operação, já listada no quadro).

**Arquivos:** `apps/proxy/Caddyfile.ce`, `.env.example`, `docker-compose-orca.yml`, `docker-compose.yml`, `README.md`.

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
- `docs/release-runbook.md` já existe (criado em P0.3, com a tabela de tags, o passo a passo do release, verificação pós-deploy, promoção manual e rollback por digest). Falta preencher as duas seções marcadas *(P0.13)*: checklist de variáveis de ambiente novas por release e o registro do ensaio.
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
