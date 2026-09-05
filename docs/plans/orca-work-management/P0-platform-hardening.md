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
- [x] Confirmado no PR #8: o job `Build & Push api` construiu com `IMAGE_TAGS: ghcr.io/vitordj/plane/api:pr-8-<sha>` e o buildx registrou `WARNING: No output specified with docker-container driver. Build result will only remain in the build cache. To push result image into registry use --push` — ou seja, **nada foi publicado**. Os seis builds passaram.
- [ ] `docker manifest inspect ghcr.io/<repo>/api:stage` antes e depois do PR retorna o mesmo digest (só quem tem acesso ao registry).
- [ ] Merge em `stage` continua publicando `:stage` (verificável no primeiro merge).

**Detalhe do `github.sha` em pull request.** Num evento `pull_request` o
`github.sha` é o **merge commit** que o GitHub cria, não a cabeça da branch, e
é ele que aparece na tag `pr-<n>-<sha>`. Isso é irrelevante aqui (a tag é
inerte e nada a publica) e não afeta P0.2/P0.3, que só produzem e promovem
`sha-<commit>` em eventos de `push`, onde `github.sha` é o commit real.

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

## P0.5 — Lockfile congelado e permissões mínimas `[x]`

**Mudança.**

- `pnpm install --no-frozen-lockfile` → `pnpm install --frozen-lockfile` no job `ci` de `stage.yml`.
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

**Correção do "bloqueio do lockfile" registrado em 05/09.** A sessão anterior
concluiu que `pnpm install --frozen-lockfile` **falharia** e deixou o item
travado. **Não falha.** O comando foi executado nesta sessão, na íntegra, no
commit `0e4ab05c`, com o pnpm 11.3.0 que o `packageManager` fixa: instalação
completa (1459 pacotes em `node_modules/.pnpm`), **exit 0**, e
`pnpm-lock.yaml` byte a byte idêntico depois. `pnpm install --lockfile-only`
antes disso também não produziu diff nenhum — não há o que regenerar.

O que a sessão anterior observou é real; a conclusão é que foi tirada dele.
`pnpm-workspace.yaml` tem 184 entradas de catálogo e `pnpm-lock.yaml` tem 173. As 11 ausentes são `typescript`, `vite`, `axios`, `uuid`, `lodash-es`,
`postcss`, `express`, `@types/express`, `@react-router/node`,
`@tanstack/react-virtual` e `@tanstack/virtual-core`. Duas delas
(`@tanstack/react-virtual`, `@tanstack/virtual-core`) **não são referenciadas
por nenhum workspace** — catálogo morto, que o pnpm nunca grava no lockfile.
As outras nove são referenciadas com `"catalog:"` e o lockfile grava, nos
`importers`, o especificador resolvido (`typescript: 5.8.3`) em vez de
`catalog:` — resíduo do commit upstream `31853ab2`.

Isso não é divergência porque **as nove estão pinadas no catálogo em versão
exata**, igual ao especificador gravado (`typescript 5.8.3`, `axios 1.18.1`,
`express 4.22.0`, ...), e o pnpm resolve `catalog:` antes de comparar. A
comparação que o `--frozen-lockfile` faz é entre valores resolvidos, não
entre as formas do texto.

Que a checagem é real, e não permissiva, foi verificado no sentido oposto:
apontando `chroma-js` em `packages/utils/package.json` para `^3.0.0` (o
catálogo tem `^3.2.0`), o comando falhou como esperado, com o diagnóstico
exato — e o `package.json` foi restaurado em seguida:

```text
[ERR_PNPM_OUTDATED_LOCKFILE] Cannot install with "frozen-lockfile" because
pnpm-lock.yaml is not up to date with <ROOT>/packages/utils/package.json
  specifiers in the lockfile don't match specifiers in package.json:
  - chroma-js (lockfile: catalog:, manifest: ^3.0.0)
```

**Fragilidade que fica registrada.** As nove passam porque o pino do catálogo
é exato. Trocar qualquer um deles por uma faixa (`^1.19.0` em vez de
`1.18.1`) faz este passo falhar até o lockfile ser regenerado — que é o
comportamento correto, e o regenerado já grava `catalog:` na forma nova. Não
é dívida a pagar antes do tempo; é o próximo `pnpm install` que a paga.

**Aceite.**

- [x] `permissions:` global `contents: read` em `stage.yml` e `prod.yml`, com cada job declarando o seu escopo mínimo.
- [x] `pnpm install --frozen-lockfile` no CI (`stage.yml`, job `ci`).
- [x] CI verde em um PR que não altera dependências — verificado localmente com o comando exato do CI (exit 0, lockfile intacto) e confirmado no CI: job `Code Quality Checks (CI)` do run 33974385564 (PR #10), **success** já com a flag trocada.
- [x] CI vermelho em um PR que altera `package.json` sem atualizar `pnpm-lock.yaml` (teste descartável): `ERR_PNPM_OUTDATED_LOCKFILE`, exit 1, transcrito acima.

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
- [x] Teste unitário que importa a função de criação e verifica `has_usable_password() is False` e `is_password_autoset is True`. Verde no CI do PR #8 (`API Tests (pytest)`).
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

## P0.7 — `TRUSTED_PROXIES` sem fallback aberto `[x]`

**Problema.** `apps/proxy/Caddyfile.ce` l.8:
`trusted_proxies static {$TRUSTED_PROXIES:0.0.0.0/0}`; `.env.example` l.44
repete `0.0.0.0/0`. **Pior do que o enunciado original:** nenhum dos dois
Composes encaminhava `TRUSTED_PROXIES` ao contêiner do proxy, então o default
valia sempre — Caddy confiava no `X-Forwarded-For` de qualquer origem em
staging e em produção, e quem chamasse a aplicação escolhia o IP que ela
registra e usa para rate limit. O mesmo default estava em
`Caddyfile.aio.ce` (imagem all-in-one), que o item não citava.

**Mudança** (entregue em `claude/loving-carson-n9x6eq`).

- `Caddyfile.ce` e `Caddyfile.aio.ce`: `trusted_proxies static {$TRUSTED_PROXIES}`, sem default, com comentário explicando por quê. Sem faixa nenhuma o resultado é fail-closed (nenhum proxy é confiável, o `X-Forwarded-For` de fora é ignorado) em vez de fail-open.
- `docker-compose-orca.yml`: o serviço `proxy` passa a receber `TRUSTED_PROXIES: "${TRUSTED_PROXIES:?...}"` — obrigatório, com a mensagem dizendo o que preencher.
- `docker-compose.yml` (stack padrão, Caddy publicado direto): recebe a variável com default de faixas privadas (`127.0.0.1/32,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`), para o `docker compose up` do repositório continuar funcionando sem confiar na internet inteira. Mudança mínima no Compose principal, justificada por ser correção de segurança (FORK.md §F).
- `.env.example`: `TRUSTED_PROXIES=` vazio, com comentário explicando o que é e um exemplo.
- README: linha na tabela de variáveis com **Required: Yes**.
- `docker-compose-local.yml` não tem serviço de proxy (o dev local sobe o Vite direto), então não há o que configurar lá — divergência do enunciado original do item.

**Pré-requisito operacional.** O próximo deploy do Compose Orca **falha** se
`TRUSTED_PROXIES` não estiver definido no ambiente de implantação. Definir a
faixa antes de mesclar em `stage` (pendência já registrada no quadro).

**Aceite.**

- [x] Os dois Caddyfiles e os dois Composes conferidos: nenhum deles tem mais faixa aberta, e ambos os Composes continuam YAML válido. O que resta de `0.0.0.0/0` no repositório são os comentários que explicam a remoção e os `nginx.conf` do web/admin/space (ver achado adjacente).
- [ ] `docker compose -f docker-compose-orca.yml config` falha sem a variável.
- [ ] Com a variável correta, `curl -H "X-Forwarded-For: 1.2.3.4"` de fora da faixa não altera o IP visto pela aplicação (verificar em log de autenticação ou endpoint de debug temporário).

**Achados adjacentes (não corrigidos).** `apps/web/nginx/nginx.conf`,
`apps/admin/nginx/nginx.conf` e `apps/space/nginx/nginx.conf` têm
`set_real_ip_from 0.0.0.0/0`. São arquivos upstream, servem estático atrás do
proxy e não são alcançáveis fora da rede do Docker, e o IP que importa para
autenticação e rate limit é o que chega na API — mas é a mesma classe de
defeito e merece a mesma faixa na próxima sync.

`plane/utils/ip_address.py::get_client_ip`
usa o **primeiro** elemento de `X-Forwarded-For`, que é a ponta mais distante
e portanto a mais fácil de forjar. Com o Caddy filtrando por proxy confiável
o cabeçalho que chega já é confiável, mas a leitura correta com N proxies
conhecidos é contar da direita para a esquerda. Fora do escopo deste item
(é código upstream e a correção depende de saber quantos proxies existem à
frente); candidato a item próprio se P0.7 não bastar na verificação de ponta
a ponta.

**Arquivos:** `apps/proxy/Caddyfile.ce`, `apps/proxy/Caddyfile.aio.ce`, `.env.example`, `docker-compose-orca.yml`, `docker-compose.yml`, `README.md`.

---

## P0.8 — Suíte upstream no CI `[x]`

**Problema.** `stage.yml`, job `api_tests`, roda só
`pytest plane/tests/unit/orca/ -q`.

**Mudança** (entregue nesta branch).

- `api_tests` passa a rodar `pytest plane/tests/unit -q -m unit`, o que inclui os 21 arquivos upstream em `bg_tasks/`, `middleware/`, `models/`, `serializers/`, `settings/`, `utils/` e `views/` além dos 32 de `orca/`. Sem `--ignore`: o job já sobe PostgreSQL 15.7 e Valkey, e nada fora de `contract/` e `smoke/` pede mais que isso. Se a execução real provar o contrário, cada exclusão entra na tabela de `RUNNING_TESTS.md` com o serviço que falta — nunca só no workflow.
- Job novo `api_integration_tests`, manual, roda `plane/tests/contract` e `plane/tests/smoke` pelo `docker-compose-test.yml` (RabbitMQ e MinIO inclusos), com `setup.sh` para gerar `apps/api/.env` e `down -v` em `always()`.
- **Gatilho `workflow_dispatch` declarado.** Achado desta sessão: `api_tests`, `ci`, `changes` e outros já tinham `if: ... || github.event_name == 'workflow_dispatch'`, mas o workflow **não declarava o gatilho**. Todas essas condições eram código morto e não havia como rodar nada à mão.
- `RUNNING_TESTS.md` ganha a seção "What CI runs" (os dois jobs, o comando de cada um, os serviços) e "Exclusions in CI", hoje vazia por decisão, não por omissão.

**Execução real: verde, e sem exclusão nenhuma.** A sessão não roda pytest
(sem daemon Docker no contêiner, sem PostgreSQL, sem as dependências Python),
então a lista vazia de exclusões era uma previsão. O CI do PR #10 a
confirmou: job `API Tests (pytest)` do run 33974385564, **success** em 9m45s
— contra o run só-Orca de antes, o tempo a mais é justamente a suíte upstream
executando, não um serviço faltando. Nenhum `--ignore` foi preciso, e a
tabela "Exclusions in CI" do `RUNNING_TESTS.md` continua vazia por resultado,
não só por decisão.

A previsão tinha base: antes do run, os candidatos óbvios a falhar sem MinIO
ou RabbitMQ foram lidos um a um. `settings/test_storage.py` e
`bg_tasks/test_copy_s3_objects.py` mockam `boto3` inteiro;
`bg_tasks/test_ssrf_advisories.py`, o mais suspeito de todos porque exercita
IPs de metadata e redirects, faz `patch` em
`plane.utils.ip_address.socket.getaddrinfo` e em
`plane.utils.url_security.requests.Session`. Nada na suíte unit toca a rede.

Os `ERROR` de constraint que aparecem no log do contêiner PostgreSQL do run
(`orca_policy_unique_unit_default`,
`issue_org_unit_assigned_requires_executor`,
`issue_org_unit_executor_only_when_assigned`) são as asserções **negativas**
dos testes do D0 — o banco recusando o que deve recusar. Registrado aqui
porque, lido fora de contexto, um log de erro num run verde parece defeito.

**Aceite.**

- [x] `api_tests` seleciona `plane/tests/unit` inteiro, e não só `orca/`.
- [x] Job manual separado para `contract/` e `smoke/` com os serviços extras.
- [x] CI executa os testes upstream e fica verde (run 33974385564, job `API Tests (pytest)`, PR #10 contra `stage` — mesmo job que roda no push).
- [x] Lista de exclusões tem no máximo os diretórios que exigem serviço ausente, cada um justificado: **zero exclusões**.

**Arquivos:** `.github/workflows/stage.yml`, `apps/api/tests/RUNNING_TESTS.md`.

---

## P0.9 — Ruff obrigatório `[x]`

**Situação.** Nenhum workflow rodava ruff. `apps/api/pyproject.toml` já
excluía `**/migrations/*`, selecionava `E`, `F`, linha 120 — configuração
decorativa, porque nada a executava. Com o ruff da versão fixada no
repositório (`0.9.7`, em `requirements/local.txt`), `ruff check .` em
`apps/api` reportava **30** achados e `ruff format --check .`, **61**
arquivos. (O enunciado dizia 31; a diferença é a versão do ruff usada na
contagem — outra razão para o CI instalar exatamente a versão fixada.)

**Mudança** (entregue em `claude/loving-carson-n9x6eq`).

- Job novo `api_lint` em `stage.yml`, condicionado a `needs.changes.outputs.api`, roda `ruff check .` e `ruff format --check .` em `apps/api`. `build-push` depende dele.
- A versão do ruff **não** está escrita no workflow: o job extrai o pin de `requirements/local.txt` com `sed` e falha se não achar. Ruff muda de resultado entre versões; CI e desenvolvedor têm que rodar a mesma.
- Os 30 achados de lint foram corrigidos: 7 imports não usados (autofix), 4 atribuições mortas, 2 nomes ambíguos `l` → `label`, 1 import no meio do arquivo (`slugify` subiu para o topo em `views/project_state.py`) e 16 linhas longas (a maioria some no `format`; duas docstrings foram quebradas à mão).
- Formatação: os **23** arquivos que o fork já tocou (diff contra o commit upstream `5662b7610`) foram formatados. Os **38** que vêm intactos do upstream entraram em `[tool.ruff.format] exclude`, **listados um a um** (nunca por diretório, para que um arquivo que o fork passe a editar tenha que sair da lista) e com comentário mandando apagar o bloco no sync do P0.11.

**Duas atribuições mortas mereciam mais que `del`.** `default_assignee_id`
era lido do contexto e ignorado em `api/serializers/issue.py` e
`app/serializers/issue.py`: é o rastro do defeito **D2** (RFC §2.2) — a
funcionalidade Orca de herdar os assignees do último item do criador
substituiu o padrão do projeto. A linha saiu e no lugar ficou um comentário
dizendo que o `default_assignee_id` continua chegando pelo contexto das views
e não é aplicado ali, apontando para D2. As outras duas (`request_data` em
`api/views/cycle.py`, `intake_id` em `app/views/intake/base.py`) eram sobras
sem uso.

**Custo aceito.** Vários dos 23 arquivos vinham formatados em 88 colunas
(padrão do upstream) e foram para 120, o que é um diff grande e aumenta a
área de conflito no próximo sync. É o que o item pedia; a alternativa
(excluir também os arquivos do fork) deixaria o `format --check` sem efeito
justamente onde o fork escreve.

**Aceite.**

- [x] `ruff check .` limpo em `apps/api` (ruff 0.9.7, a versão fixada). O job `API Lint (ruff)` rodou verde no PR #8, instalando a versão a partir de `requirements/local.txt`.
- [x] `ruff format --check .` limpo com a exclusão temporária documentada no `pyproject.toml`.
- [ ] Job vermelho num PR que introduz `import os` não usado (teste descartável).

**Arquivos:** `.github/workflows/stage.yml`, `apps/api/pyproject.toml`, e os arquivos apontados pelo ruff.

---

## P0.10 — Validação completa do `id_token` do Entra e timeouts `[x]`

**Situação.** `decode_id_token_claims` lia o payload com base64 sem verificar
assinatura; só `tid` era conferido — `aud`, `iss`, `exp` e `nbf` não eram
olhados, então um token emitido para **outra aplicação** do mesmo tenant, ou
expirado horas antes, era aceito. Chamadas ao token endpoint e ao Graph sem
timeout. Sem `nonce`: um id token capturado de outro login (mesmo tenant, mesma
aplicação, outra pessoa) era indistinguível do que o fluxo tinha pedido.

**Mudança** (entregue em `claude/loving-carson-n9x6eq`).

- `EntraOAuthProvider.decode_id_token`: `PyJWKClient` por tenant (cacheado no processo; o cliente guarda o key set por 5 min e só volta ao Microsoft quando aparece um `kid` novo, que é o que torna a rotação de chaves transparente), `timeout=10` na busca das chaves, e `jwt.decode(..., algorithms=["RS256"], audience=<client id>, issuer=https://login.microsoftonline.com/<tenant>/v2.0, options={"require": ["exp","iat","nbf","aud","iss","tid"]})`. Sem o `require`, o PyJWT só valida a claim que por acaso estiver presente: um token sem `exp` nunca expiraria.
- Falha de qualquer natureza — assinatura, claim, ou JWKS inalcançável — vira `ENTRA_ID_TOKEN_INVALID`. **Fail closed**: token que ninguém consegue conferir é recusado, não presumido bom. O motivo específico vai para o log (`Entra id token failed verification`), nunca para o navegador, que diria ao atacante qual verificação falta passar.
- `nonce` de uso único: gerado no início do fluxo nas duas views (app e space), guardado na sessão em `entra_nonce`, mandado no `authorize`, exigido igual na volta e **consumido mesmo quando não confere**, para que um callback capturado não possa ser repetido contra a mesma sessão. Erro próprio: `ENTRA_NONCE_MISMATCH`.
- `verify_tenant` mantido em cima da checagem de `iss` — defesa em profundidade declarada: o argumento de confiança não deve depender de uma única opção de biblioteca ter sido passada certo.
- **Tenant precisa ser o GUID.** O construtor recusa domínio (`contoso.onmicrosoft.com`) com `ENTRA_NOT_CONFIGURED`. Não é restrição nova de fato: `tid` e `iss` sempre trazem o GUID, então um tenant configurado por domínio já falhava todo login — a diferença é falhar dizendo o porquê, em vez de deixar uma instância onde ninguém entra.
- Timeouts `(5, 15)` em `adapter/oauth.py` (token endpoint e userinfo, portanto todos os providers) e nas três chamadas que os providers GitHub e Gitea fazem por conta própria: sem timeout, um provedor que aceita a conexão e trava segura o worker até o processo reiniciar.
- Códigos novos `ENTRA_ID_TOKEN_INVALID` (5127) e `ENTRA_NONCE_MISMATCH` (5128) em `adapter/error.py`, nos dois helpers de front (enum, mapa de mensagens e lista de agrupamento) e no catálogo i18n das **19 locales**, via skill `translate`.
- Correção de registro de tratamento: as strings existentes de Entra em `es` e `pl` usavam a forma informal (`tú` / `ty`), que a skill proíbe em UI de produto nessas duas línguas e que o resto do bloco não usa. Ajustadas para `usted` e forma com `proszę` no mesmo bloco que estava sendo estendido.

**Testes.** `test_entra_provider.py` reescrito: constrói o provider pelo
**construtor real** com a configuração de instância mockada (o `StubProvider`
que pulava o `__init__` saiu, achado do PR #6, e com isso as checagens de forma
do tenant entraram na cobertura). Par de chaves RSA gerado no teste e
`get_jwks_client` mockado. Cobre: URL de autorização com `state` e `nonce`;
recusa de autoridade multi-tenant, de tenant por domínio e de instância não
configurada; token válido; assinatura de outra chave; `aud` de outra aplicação;
`iss` de outro tenant; expirado; ainda não válido; **cada uma** das seis claims
obrigatórias ausente; token malformado; JWKS inalcançável; `tid` estrangeiro;
nonce correto, divergente, ausente no token, ausente na sessão, uso único e
consumo em caso de falha; `set_token_data` completo (guarda o token no caminho
feliz, e não deixa `token_data` ser escrito quando qualquer verificação falha);
e as regras de e-mail que já existiam. Mais uma classe de paridade dos quatro
códigos Entra entre a tabela Python, os dois helpers e as 19 locales — nada no
build comparava isso.

**Aceite.**

- [x] Ruff (`check` e `format --check`, versão fixada) limpo nos arquivos tocados; paridade dos códigos e das 19 locales verificada fora do pytest; comportamento do PyJWT para **todos** os 16 casos de token exercitado num harness com a versão fixada (`PyJWT==2.13.0`, `cryptography==50.0.0`) antes de escrever as asserções. Suíte verde no CI do PR #8 (`API Tests (pytest)`), e `Code Quality Checks` verde cobre `check:sync` das 19 locales e os tipos dos dois helpers.
- [x] Doc `docs/entra-directory-sync.md` §Troubleshooting descreve os dois erros novos, com as causas em ordem de probabilidade, e §"Why the tenant is pinned" ganhou a verificação completa do token, o nonce e a exigência do GUID.
- [x] `test_entra_provider.py` não usa mais `StubProvider`.
- [ ] Validação de ponta a ponta contra tenant real registrada no quadro quando o tenant existir (não bloqueia o merge).

**Nota de implantação.** Quem estiver no meio de um login exatamente na hora do
deploy recebe `ENTRA_NONCE_MISMATCH` uma vez: o fluxo começou antes de o nonce
existir. Basta recomeçar; está descrito no Troubleshooting.

**Arquivos:** `authentication/provider/oauth/entra.py`, `authentication/adapter/oauth.py`, `authentication/adapter/error.py`, `authentication/provider/oauth/{github,gitea}.py`, `authentication/views/{app,space}/entra.py`, `apps/web/helpers/authentication.helper.tsx`, `apps/space/helpers/authentication.helper.tsx`, `packages/i18n/src/locales/*/auth.json`, teste, doc.

---

## P0.11 — Sync com Plane CE 1.4.2 `[x]`

**Mudança.** Seguir FORK.md §Phase 5: atualizar branch `upstream` com
`makeplane/plane` tag `v1.4.2`, criar `sync/upstream-merge-2026-09`, resolver
conflitos, abrir PR para `stage`. Registrar no PR o SHA upstream usado.
Atualizar `package.json` para `...-plane.1.4.2` (ver P0.13).

**Entregue** (PR #11, mesclado em `stage` como `af571341`). O item estava
marcado como o mais pesado que restava em P0; é o mais leve. Upstream
`v1.4.2` = `5f7d92784c403f76284f0f16718f320221dc7fec`, três commits sobre o
`v1.4.1` (`5662b7610`) em que o fork estava, e só um deles é mudança real:

- `1c8a60f8` [WEB-8632] — `apps/web` recarrega a página uma vez quando uma navegação falha por chunk obsoleto depois de um deploy, em vez de deixar o usuário numa rota quebrada. Novo `apps/web/core/lib/stale-asset-error.ts`, mais `entry.client.tsx` e `root.tsx`. Vale para este fork em particular: o modelo de deploy troca os assets a cada push em `stage`.
- `e056bbf9` e `5f7d9278` — bump de versão do próprio upstream em todos os `package.json`, e o commit de release.

**Um conflito só**, na versão do `package.json` raiz: a convenção do fork
(`1.4.0-plane.1.4.1`) contra a do upstream (`1.4.2`). Resolvido para
`1.5.0-plane.1.4.2` — minor porque o sync traz feature, e o sufixo passa a
nomear a base que o código de fato tem. `.github/release-please-manifest.json`
foi junto; sem ele o Release Please proporia a partir da versão velha.

**Detalhe do procedimento que ficou pela metade.** O merge tomou a tag
`v1.4.2` diretamente em vez do mirror `origin/upstream` (FORK.md §Phase 5
passo 1), porque a sessão de agente não tem autorização para empurrar naquela
branch. Mesmo conteúdo, mesmo commit — mas **o mirror está uma release
atrás**. Um comando resolve:

```bash
git push origin refs/tags/v1.4.2^{commit}:refs/heads/upstream
```

**Aceite.**

- [x] CI verde no PR de sync — 16 checks no run 33974605256, incluindo `check:types` e os seis builds; merge com os dois pais confirmado por `git merge-base --is-ancestor v1.4.2 HEAD`.
- [x] `docs/` menciona 1.4.2 como base (`HANDOFF-PROMPT.md`, com o SHA novo). `FORK.md` nunca afirmou uma versão-base: descreve a convenção `v<fork>-plane.<upstream>`, que continua correta.
- [ ] Mirror `origin/upstream` atualizado para `v1.4.2` (comando acima; a sessão não empurra naquela branch).

---

## P0.12 — Apagar branches remotos obsoletos `[~]`

**Verificação feita** (`claude/loving-carson-n9x6eq`, 05/09). O levantamento de
03/09 confirmado contra o código, e não só contra os títulos dos commits:

| Branch                                       | Commits à frente de `stage` | Veredito                                                                                      |
| -------------------------------------------- | --------------------------- | --------------------------------------------------------------------------------------------- |
| `claude/azure-aad-integration-review-5if6pz` | 34                          | Superado. Abordagem "oidc-free", substituída pelo provider Entra que está em `stage` (PR #2). |
| `claude/sync-remote-azure-auth-m6618f`       | 14                          | Superado. Mesma abordagem, port para a base Orca.                                             |
| `claude/aad-end-to-end-egj4dm`               | 1                           | Superado. Versão anterior de "sign in with Microsoft Entra ID".                               |

O único commit desses três que parecia valer um port —
`16494934 fix(api): normalise SECURE_PROXY_SSL_HEADER and document the proxy
vars` — **não se aplica**: ele corrige um bloco `USE_X_FORWARDED_*` /
`SECURE_PROXY_SSL_HEADER` lido de variável de ambiente em
`settings/common.py` que **não existe** nem no `stage` nem no upstream 1.4.1
(foi introduzido pelo próprio branch). Em `stage`, o header é fixo em
`settings/production.py` (`("HTTP_X_FORWARDED_PROTO", "https")`), e a falsificação
que ele permitiria é justamente o que o P0.7 fechou no Caddy. Nada a portar.

**Levantamento refeito em 05/09** contra `origin/stage` = `0e4ab05c`, com
`git merge-base --is-ancestor` em cada branch remoto — e não pela lista
anterior. Duas mudanças em relação ao registro de antes: os tips dos três
branches superados agora estão anotados (faltavam dois), e **dois branches
novos entraram na lista de apagar**, porque seus PRs foram mesclados desde
então (`claude/loving-carson-n9x6eq`, PR #8; `feat/orca-unit-project-coverage`,
PR #9).

**Totalmente contidos em `stage`** (todo commit já está lá; apagar não perde
nada), com o SHA do tip para poder recriar:

```text
dc4a596d  claude/area-membership-extension-ndwdoq
848bbf65  claude/avaliacao-implementacoes-pendencias-wt5ign
cd5a8419  claude/azure-areamembership-cleanup-gfep3c
ca0ab6a0  claude/entra-id-directory-sync-rhoo9f
ccf8618a  claude/orca-i18n-default-language-9xvxhr
ca0ab6a0  claude/parecer-final-arquitetura-4ng4yo
dc4a596d  claude/pending-tests-xnxc3s
343e7f9e  claude/repository-evaluation-s419b6
a349fd4c  claude/wayfinder-areas-review-yt98v5
a495565e  claude/loving-carson-n9x6eq            (PR #8, mesclado)
d1ec40ba  feat/orca-unit-project-coverage        (PR #9, mesclado)
```

**Superados, com commits que se perdem ao apagar** — daí os SHAs, que são a
única forma de recriá-los:

```text
2ac95330  claude/aad-end-to-end-egj4dm            ( 1 commit à frente)
16494934  claude/azure-aad-integration-review-5if6pz (34)
7abf01b8  claude/sync-remote-azure-auth-m6618f    (14)
```

**Não apagar:** `claude/codex-prompts-bocxeh` (3 à frente) e
`claude/continue-implementations-bquse8` (7) não estão mesclados e não constam
do enunciado; `feat/orca-work-management` (34) idem;
`release-please--branches--prod--components--plane-orca` é branch de trabalho
da action, não lixo.

**Por que continua `[~]`.** A sessão de agente continua sem permissão para
apagar branch remoto: `git push origin --delete` foi barrado de novo nesta
sessão, pelo classificador, e não há ferramenta de deleção de branch no
servidor MCP do GitHub. Comandos prontos, para quem tiver:

```bash
# Contidos em stage (nada se perde)
git push origin --delete claude/area-membership-extension-ndwdoq \
  claude/avaliacao-implementacoes-pendencias-wt5ign \
  claude/azure-areamembership-cleanup-gfep3c \
  claude/entra-id-directory-sync-rhoo9f \
  claude/orca-i18n-default-language-9xvxhr \
  claude/parecer-final-arquitetura-4ng4yo \
  claude/pending-tests-xnxc3s \
  claude/repository-evaluation-s419b6 \
  claude/wayfinder-areas-review-yt98v5 \
  claude/loving-carson-n9x6eq \
  feat/orca-unit-project-coverage

# Superados (verificados acima; os commits se perdem)
git push origin --delete claude/azure-aad-integration-review-5if6pz \
  claude/sync-remote-azure-auth-m6618f claude/aad-end-to-end-egj4dm

# Recriar um deles, se for preciso: git push origin <sha>:refs/heads/<nome>
```

**Aceite.**

- [x] Conteúdo dos três branches do enunciado verificado contra o código de `stage`; nada a portar (e o motivo registrado, não só a conclusão).
- [ ] `git ls-remote --heads origin | grep claude/` lista só branches com trabalho em andamento.

---

## P0.13 — Versão 1.5.0, Release Please e runbook `[~]`

**Situação.** `package.json` em `1.4.0-plane.1.4.1`;
`.github/release-please-manifest.json` e `.github/release-please-config.json`
na mesma linha; o template `release_candidate.md` afirmava que o merge dispara
a promoção, mas `prod.yml` só promove com commit `chore(prod): release`.

**Entregue** (`claude/loving-carson-n9x6eq`): a parte de documentação.

- `docs/release-runbook.md` (novo): o fluxo em duas etapas, o que checar antes de cortar a RC, o que o job de promoção faz e como ele falha, verificação pós-deploy (`docker inspect`, `/api/orca/build-info/`, `orca_build_info` em api/worker/beat, paridade do kill switch), variáveis de ambiente, rollback por digest com os comandos prontos, e a manutenção de que o release depende (tags fixas, PostgreSQL igual no CI e na implantação). Tabela de ensaio no fim, vazia — é ela que fecha o critério.
- `release_candidate.md`: descreve o fluxo real. O aviso principal agora é "**mesclar este PR não implanta nada**", com os dois passos e a conferência da versão proposta pelo Release Please.
- `FORK.md` §Phase 4: reescrita para o mesmo fluxo (a versão anterior dizia que o merge da RC puxava `:stage` e reimplantava).

**Achado que muda a decisão da versão.** O convênio do fork
(`v<fork>-plane.<upstream>`, FORK.md) é, em semver, um **prerelease**:
`1.4.0-plane.1.4.1` é um pré-lançamento de `1.4.0`. O Release Please está
configurado **sem** estratégia de prerelease, então há um risco concreto de ele
propor uma versão **sem** o sufixo `-plane.<upstream>` — o que apagaria o
marcador de base upstream. Não dá para confirmar sem rodar; o PR de release é
onde isso aparece, e ele é editável antes do merge. Registrado como passo
explícito no runbook §2, com as duas saídas (corrigir no PR ou configurar
`prerelease: true`).

**O bump saiu, junto com o P0.11.** A versão dependia do sync: marcar
`-plane.1.4.2` antes dele afirmaria uma base que o código não tinha. Com o
PR #11 mesclado (`af571341`), `package.json` e
`.github/release-please-manifest.json` estão em `1.5.0-plane.1.4.2` e a
afirmação é verdadeira.

**Resta o que só o primeiro release responde.** A decisão de prerelease
continua aberta e não é decidível sem rodar: o Release Please pode propor uma
versão **sem** o sufixo `-plane.<upstream>`, porque em semver ele é um
prerelease tag e a configuração não declara `prerelease: true`. O PR de
release é onde isso aparece e é editável antes do merge — runbook §2 descreve
as duas saídas. O ensaio do runbook também continua pendente, e depende de
ambiente.

**Aceite.**

- [x] Template e FORK.md §Phase 4 descrevem o mesmo fluxo que os workflows executam.
- [x] Runbook escrito, com verificação pós-deploy e rollback por digest.
- [ ] Runbook ensaiado, com data e resultado no próprio arquivo (tabela "Rehearsal log").
- [x] `package.json` e manifest em `1.5.0-plane.1.4.2` — entrou no PR do sync (P0.11, PR #11), como previsto aqui.
- [ ] Decidido e registrado se o Release Please calcula a versão ou se o sufixo é mantido à mão (o runbook descreve as duas saídas; a decisão sai no primeiro release).

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

## P0.15 — Runtime expõe commit e versão implantados `[x]`

**Problema.** Nada dentro de um contêiner dizia de qual commit ele foi
construído. Sem isso, P0.0–P0.3 provam a cadeia até o registry, mas não que
o ambiente está executando aquele artefato.

**Mudança** (entregue em `claude/loving-carson-n9x6eq`).

- `build-push` passa `build-args: GIT_SHA=<commit>` e `IMAGE_TAG=sha-<commit>`; os seis Dockerfiles gravam `ORCA_BUILD_SHA` e `ORCA_IMAGE_TAG` como variáveis de ambiente da imagem. O bloco fica **no fim** do estágio final de cada Dockerfile de propósito: o valor muda a cada commit, e tudo o que estivesse abaixo dele seria reconstruído sem necessidade.
- `plane/utils/orca_build_info.py`: `build_info()` devolve `{"service", "version", "git_sha", "image_tag", "orca_org_units_enabled"}`. Versão lida como o `register_instance` lê (`APP_VERSION`, senão `package.json`). Imagem construída fora do CI devolve `git_sha` e `image_tag` vazios — que é a resposta certa, não um palpite.
- `GET /api/orca/build-info/` (`OrcaBuildInfoEndpoint`), restrito a admin de instância (`InstanceAdminPermission`) e **fora** do kill switch: o endpoint precisa responder justamente quando algo parece errado, switch mal configurado incluído.
- Comando `manage.py orca_build_info` imprime o mesmo JSON. É o que permite perguntar ao worker e ao beat, que não têm superfície HTTP e são exatamente onde uma imagem velha se esconde.
- `docker-compose-orca.yml` define `ORCA_SERVICE_NAME` em api, worker, beat e migrator: os quatro rodam a **mesma** imagem, então a variável é a única coisa que diz qual deles respondeu.
- O digest não é conhecido de dentro do contêiner; a prova primária no runtime é o SHA, e o digest continua sendo o lado do registry (P0.2/P0.3).
- Rodapé do god-mode com o SHA: não feito (era opcional nesta fase).

**Testes.** `apps/api/plane/tests/unit/orca/test_build_info.py`: payload
completo com as variáveis definidas; imagem sem CI devolve vazio em vez de
adivinhar; o kill switch aparece no payload; endpoint responde 200 para admin
de instância, recusa membro comum e anônimo, e continua respondendo com a
camada desligada; o comando imprime exatamente o mesmo JSON.

**Aceite.**

- [x] Ruff limpo (`check` e `format --check`) nos arquivos novos; testes verdes no CI do PR #8 (`API Tests (pytest)`). Os seis builds passaram com `build-arg:GIT_SHA` presente no metadata, então o `ARG`/`ENV` novo nos Dockerfiles não quebrou nenhuma imagem.
- [ ] Em staging, o endpoint devolve o SHA do merge que disparou o deploy.
- [x] `docs/release-runbook.md` (P0.13) inclui o passo "conferir build-info após o deploy" — §4, exigindo o mesmo SHA em api, worker e beat.

**Arquivos:** `.github/workflows/stage.yml`, os seis Dockerfiles, `docker-compose-orca.yml`, `apps/api/plane/utils/orca_build_info.py` (novo), `apps/api/plane/app/views/orca_build_info.py` (novo), `apps/api/plane/db/management/commands/orca_build_info.py` (novo), `apps/api/plane/app/urls/orca.py`, `apps/api/plane/app/views/__init__.py`, teste novo.

---

## P0.16 — Fixar MinIO e alinhar a versão do PostgreSQL `[x]`

**Situação.** `docker-compose-orca.yml` usava `minio/minio` sem tag; o CI
(`stage.yml`, job `api_tests`) usava `postgres:16-alpine` enquanto o Compose de
implantação e `docker-compose-test.yml` usam `postgres:15.7-alpine`.

**Mudança** (entregue em `claude/loving-carson-n9x6eq`).

- `minio/minio:RELEASE.2025-09-07T16-13-09Z` no Compose Orca e também em `docker-compose-test.yml` — o mesmo argumento vale mais forte para o stack de teste, onde uma imagem móvel faz o veredito de um run depender do dia. A tag escolhida é a que o `:latest` apontava no momento da fixação, então a troca não muda comportamento.
- **Decisão registrada:** o CI passa a `postgres:15.7-alpine`, igual ao ambiente implantado. Testar contra um major diferente do que roda em produção é uma diferença que a suíte não enxerga e o deploy enxerga. Migrar para 16 continua possível, mas então os quatro (Compose padrão, Orca, teste e CI) mudam juntos, com plano de upgrade do banco registrado em `RUNNING_TESTS.md`.
- `apps/api/tests/RUNNING_TESTS.md`: tabela de imagens atualizada e seção "Image versions" com as duas regras acima.
- Não tocados: `docker-compose-local.yml` e `docker-compose.yml` (o Compose principal é upstream, FORK.md §F). O `minio/minio` sem tag continua nos dois; vale corrigir no sync do P0.11.

**Aceite.**

- [x] `grep -n "image:" docker-compose-orca.yml` não mostra imagem sem tag.
- [x] A versão de PostgreSQL do CI e a do Compose são a mesma (15.7), e a matriz está documentada em `RUNNING_TESTS.md`.

**Arquivos:** `docker-compose-orca.yml`, `docker-compose-test.yml`, `.github/workflows/stage.yml`, `apps/api/tests/RUNNING_TESTS.md`.

---

## P0.17 — Documentação de implantação não presume Coolify `[~]`

**Situação.** README, `docker-compose-orca.yml`, os workflows e este plano
falam em Coolify como se fosse o alvo de implantação. Isso vem do ambiente da
Orca; a 4UM não usa Coolify. O texto novo escrito nesta branch já foi
neutralizado ("ingress ou proxy reverso à frente do Caddy"), mas o restante
continua com o nome.

**Mudança.** Decidir o alvo real de implantação e então: ou generalizar as
menções (README §Self-Hosting e Quick Start, comentários do Compose, o job
`deploy` de `stage.yml`/`prod.yml` que chama a API do Coolify), ou registrar
explicitamente que o Coolify é um caminho suportado entre outros. Os jobs de
deploy já são opt-in por `COOLIFY_DEPLOY_ENABLED`, então nada quebra enquanto
a decisão não sai — o que existe é documentação que descreve outro ambiente.

**Entregue nesta branch: a metade da qualificação.**

- README §Self-Hosting: o Compose deixa de ser apresentado como coisa de Coolify e passa a ser o que é — um arquivo Compose que qualquer runtime de Compose sobe. O "Quick Start (Coolify)" vira um Quick Start em três condições neutras (o Compose é a unidade de implantação; o tráfego chega no serviço `proxy` na porta 80 e `TRUSTED_PROXIES` é obrigatório; segredos antes do primeiro boot), com os passos do Coolify preservados num `<details>` rotulado como exemplo. Fica dito ali que `SERVICE_FQDN_PROXY` — de onde `DOMAIN_NAME` se resolve por padrão — é uma variável que o Coolify fornece, e que em qualquer outro alvo `DOMAIN_NAME` é explícito.
- Tabela de destaques: "VPS & Coolify Ready" → "VPS & PaaS Ready".
- `FORK.md` §Phase 3: o passo "Staging Deploy" dizia que o merge dispara o redeploy do Coolify, sem qualificar. Agora diz que é opt-in por `COOLIFY_DEPLOY_ENABLED` e que, com a variável desligada, as imagens são publicadas do mesmo jeito e um ambiente que não é Coolify as puxa por digest.
- README aponta este item para quem for registrar a decisão.

**Não entregue, e por quê.** Qual é o alvo real da 4UM é decisão de
negócio/operação, não achado de código. Enquanto ela não sai, os jobs de
deploy continuam opt-in e nada quebra — o que existia era documentação
descrevendo outro ambiente, e isso é o que foi corrigido.

**Aceite.**

- [ ] Alvo de implantação da 4UM registrado (aqui e no README) — **pendência de negócio**.
- [x] Nenhuma instrução de implantação afirma Coolify sem qualificar.

**Arquivos:** `README.md`, `FORK.md`, `docker-compose-orca.yml`, `.github/workflows/{stage,prod}.yml`, este plano.

---

## Gate P0

- [ ] Todos os 18 itens (P0.0–P0.17) `[x]` ou `[-]` com motivo.
- [ ] CI de `stage` verde com suíte upstream (P0.8) e ruff (P0.9).
- [ ] Ensaio completo de RC documentado em `docs/release-runbook.md`: PR criada pelo job, promoção por digest, deploy em staging, rollback.
- [ ] Nenhuma conta com a senha antiga da migração em nenhum ambiente.
- [ ] `TRUSTED_PROXIES` configurado em staging e produção com a faixa real.
- [ ] Staging comprovadamente executando imagens de `ghcr.io/vitordj/plane` (P0.0, `docker inspect`), com data e digest registrados aqui.
- [ ] `ORCA_ORG_UNITS_ENABLED` com o mesmo valor em api, worker e beat em staging (P0.14).

Data do gate: \_\_\_\_
