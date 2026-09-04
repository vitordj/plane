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

## P0.3 — Promoção para produção por SHA, não por `:stage` `[ ]`

**Problema.** `.github/workflows/prod.yml` (job "Promote Images", ~l.81) faz
`docker pull <img>:stage` e retagueia. A promoção não está vinculada ao commit
revisado.

**Mudança.**
- O job lê o SHA do merge de `stage` em `prod` (`git log -1 --format=%H origin/stage` no momento do release, ou o segundo pai do merge commit) e faz `docker pull <img>:sha-<sha>`.
- Falha explícita se a tag `sha-<sha>` não existir (a imagem não passou pelo CI de `stage`).
- Grava no corpo do GitHub Release os digests promovidos.
- `:stage` continua existindo só como ponteiro de conveniência para o ambiente de staging.

**Aceite.**
- [ ] Ensaio: merge de `stage` em `prod` com commit `chore(prod): release` num ambiente controlado promove exatamente os digests de `image-digests.json`.
- [ ] Ensaio negativo: apagar a tag `sha-<sha>` de um serviço faz o job falhar antes de qualquer retag.

**Arquivos:** `.github/workflows/prod.yml`, `docs/release-runbook.md` (novo, ver P0.13).

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

## P0.6 — Remover a senha fixa da migração de usuários `[ ]`

**Problema.** `tools/migration/create_users.py` l.23 define
`DEFAULT_PASSWORD = "TemporaryOrca123!"` e l.84 aplica a todos os usuários
criados. Nenhuma troca forçada.

**Mudança.**
- Remover a constante. Para cada usuário criado: `user.set_unusable_password()` e `user.is_password_autoset = True` (mesmo padrão do provider Entra em `apps/api/plane/authentication/provider/oauth/entra.py`).
- `tools/migration/README.md`: seção "Primeiro acesso" explicando Entra ID ou magic link; remover qualquer menção à senha antiga.
- Adicionar ao README um bloco "Se este script já foi executado antes desta versão": comando Django para invalidar as credenciais dos usuários criados pelo script (filtrar por `created_at` da execução ou por lista de e-mails) com `set_unusable_password()`, e orientação para revisar logs de autenticação.

**Aceite.**
- [ ] `grep -rn "TemporaryOrca" tools/ docs/ README.md` vazio.
- [ ] Teste unitário simples em `apps/api/plane/tests/unit/orca/test_migration_tools.py` que importa a função de criação (refatorar o script para expor `create_user_from_payload`) e verifica `has_usable_password() is False` e `is_password_autoset is True`.
- [ ] Contas já criadas em qualquer ambiente com a senha antiga foram invalidadas (registrar data e ambiente no quadro).

**Arquivos:** `tools/migration/create_users.py`, `tools/migration/README.md`, novo teste.

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
