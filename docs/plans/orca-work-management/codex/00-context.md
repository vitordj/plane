# Contexto obrigatório para qualquer sessão do Codex neste repositório

Todo prompt em `docs/plans/orca-work-management/codex/` começa mandando ler
este arquivo. Ele é o contrato de base: o que é este repositório, o que não
se toca, o que se roda e o que não se roda, e como um item termina.

> Se algo neste arquivo contradisser `AGENTS.md` ou `FORK.md`, esses dois
> vencem. Diga na resposta que encontrou a contradição.

---

## 1. O que é este repositório

Fork de **Plane Community Edition v1.4.1** (commit upstream `5662b7610`),
monorepo pnpm + Turbo:

| Caminho | O que é |
| --- | --- |
| `apps/api` | Django 5 + DRF. API interna em `plane/app/` (`/api/...`, sessão), API pública em `plane/api/` (`/api/v1/...`, `APIKeyAuthentication`), modelos em `plane/db/models/`. |
| `apps/web` | React + Vite (não é Next.js). Env vars com prefixo `VITE_`. |
| `apps/space`, `apps/admin`, `apps/live`, `apps/proxy` | Portal público, god-mode, colaboração em tempo real, Caddy. |
| `packages/*` | `@plane/ui`, `@plane/propel`, `@plane/types`, `@plane/constants`, `@plane/i18n`, shared-state (MobX). |
| `tools/migration` | Scripts de importação de dados legados. |

A camada custom do fork chama-se **Orca**. O que ela já entrega hoje:
áreas (`OrganizationalUnit`) com membros e projetos cobertos, reconciliador
de acesso (`ProjectMember` derivado), SCIM 2.0, login e sync com Microsoft
Entra ID, idioma padrão da organização, ciclos paralelos, labels/estados de
workspace, operações em massa.

Rotas Orca internas: `apps/api/plane/app/urls/orca.py`, sob `/api/orca/`.
Kill switch: `ORCA_ORG_UNITS_ENABLED` via `OrganizationalUnitFeatureMixin`
(responde 404 quando desligado). Plane CE **não** tem custom properties: a
única fonte da verdade da área de um item é `IssueOrganizationalUnit`.

## 2. Regras do fork que não se negociam (FORK.md)

1. **Não modificar tabelas core.** Nada de coluna nova em `Issue`,
   `Project`, `Workspace`, `User`. Modelo lateral com FK, ou campo JSON já
   existente. A exceção autorizada pelo RFC é `IssueOrganizationalUnit`, que
   já é uma tabela do fork.
2. **Não apagar migração** nem tabela core. Desligar, nunca destruir.
3. **Rotas custom em namespace próprio** (`/api/orca/`, `/api/v1/orca/`),
   nunca dentro dos routers nativos.
4. **Branding por override de asset/CSS**, nunca reescrevendo import do core.
5. **Feature nova grande = sidecar** fora do monorepo, falando por REST e
   webhook.
6. **Desligar por flag**, nunca apagando bloco de código do upstream.
7. **Não tocar**: `apps/api/plane/db/models/{issue,project,workspace,user}.py`
   além de exports; `apps/api/plane/app/views/issue/` além do que o fork já
   alterou.
8. **`ProjectMember` só é escrito pelos reconciliadores** (`org_unit_reconciler.py`,
   `directory_projector.py`). Invariante I10 do RFC. Nenhum serviço, view ou
   tarefa nova escreve nessa tabela.

## 3. Convenções de código

- **Commits**: Conventional Commit com escopo `orca` e o identificador do
  item no início do assunto:
  `feat(orca): [D0.5] assignment service with policy resolution`.
  Tipos usados: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `style`.
  O assunto do commit é em **inglês**; a conversa e os docs de plano são em
  **português**.
- **Branch**: uma por item, a partir de `origin/stage`, nome
  `feat/orca-<tema>` (ou `fix/`, `docs/`). Nunca commitar direto em `stage`.
- **Python**: Ruff, `line-length = 120`, aspas duplas, regras `E`/`F`.
  Docstrings no formato `@description` / `@param` / `@returns`, como nos
  arquivos Orca existentes.
- **TypeScript**: oxfmt + oxlint, strict. Nada de `any` novo.
- **Copyright**: todo arquivo novo `.py`/`.ts`/`.tsx` precisa do header
  (ver `COPYRIGHT_CHECK.md`); o workflow `copyright-check.yml` reprova sem
  ele. Copie o header de um arquivo Orca vizinho.
- **Comentar override**: todo trecho que altera comportamento do upstream
  leva comentário `# ORCA CUSTOM FEATURE: <por quê>`, no padrão já usado no
  repositório. Isso é o que salva a próxima sync.
- **Não apagar docstring/JSDoc existente** ao refatorar.

## 4. Códigos de erro Orca (três lugares, com teste de paridade)

Todo código novo entra nos três, no mesmo PR:

1. `apps/api/plane/utils/orca_error_codes.py`
2. `packages/constants/src/orca/error-codes.ts`
3. catálogo i18n (`packages/i18n/src/locales/*/`), em **todas** as locales

`apps/api/plane/tests/unit/orca/test_orca_error_codes.py` falha se algum dos
três divergir. Strings novas de UI seguem o mesmo caminho: todas as locales,
plurais no formato CLDR, placeholders preservados (ver `docs/i18n.md`).

## 5. Migrações

- Última migração Orca aplicada: `0134_orca_user_language_preference`.
  A numeração desta linha de trabalho segue: `0135`, `0136`, `0137`, ...
- Gerar com `python3 apps/api/manage.py makemigrations db -n <nome>`.
  **Você não roda esse comando** (seção 6). Escreva o arquivo à mão seguindo
  o padrão das migrações Orca existentes e diga explicitamente na resposta
  que ele foi escrito à mão e precisa ser conferido com
  `makemigrations --check` pelo desenvolvedor.
- `dependencies` sempre explícito na migração Orca anterior.
- `RunPython` sempre idempotente e sempre com função reversa (no-op quando o
  reverso é a remoção dos campos).
- CHECK constraint que depende de dado migrado entra **depois** do `RunPython`.
- Nunca editar migração já mesclada em `stage`.

## 6. O que você roda e o que você não roda

**Pode rodar**, e deve:

```bash
cd apps/api && ruff check . && ruff format --check .   # e ruff format . para corrigir
git diff, git log, grep, leitura de arquivo
pnpm oxlint <arquivos tocados>          # se estiver disponível sem instalar nada
```

**Não rode** (regra de `AGENTS.md`, custo/ruído; são comandos do desenvolvedor):

```bash
pnpm check | pnpm check:types | pnpm build | pnpm dev
pytest da suíte completa
docker compose ... (build, up, migrate)
python3 manage.py makemigrations | migrate
```

Se um item precisa de verificação por um desses comandos, **liste o comando
exato** na sua resposta final, na seção "Comandos para o desenvolvedor", e
diga o que deve sair verde.

## 7. Testes

- Ficam em `apps/api/plane/tests/unit/orca/`, fixtures em `conftest.py`.
  Leia dois ou três testes vizinhos antes de escrever o seu; siga o estilo.
- Marcar `@pytest.mark.unit`.
- **Todo item que toca alocação termina com teste de concorrência**
  (`@pytest.mark.django_db(transaction=True)`, `ThreadPoolExecutor`, uma
  conexão por thread com `connection.close()` no fim de cada). O padrão de
  referência é o do item D0.5.
- Nomear o teste com o identificador da invariante quando houver:
  `test_i2_unit_not_covering_project_rejected`.
- Comando (para o desenvolvedor):
  `docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/orca/ -q`

## 8. Documentos de referência, em ordem de leitura

1. `AGENTS.md` e `FORK.md` — regras do fork.
2. `docs/orca-work-management-rfc.md` — especificação. Seções 1–3 sempre;
   depois as seções que o item citar. As 24 decisões fechadas F1–F24 estão
   em §3: **não reabra nenhuma**. Se achar que uma está errada, pare, escreva
   o achado em §4.2 (changelog de decisões) e diga na resposta final.
3. `docs/plans/orca-work-management/README.md` — quadro de estado.
4. O arquivo da fase do item (ex.: `D0-domain-foundation.md`).

## 9. Como um item termina

1. Código + testes no lugar.
2. `ruff check` e `ruff format --check` limpos em `apps/api`.
3. No arquivo da fase, o item passa de `[ ]` para `[x]`; no
   `docs/plans/orca-work-management/README.md`, a contagem da fase é
   atualizada. **No mesmo commit da entrega.**
4. Se o trabalho revelou algo que muda o desenho, uma linha em
   `docs/orca-work-management-rfc.md` §4.2.
5. Commit na branch do item. **Não abrir PR nem fazer merge** sem pedido
   explícito; descreva o PR proposto (título + corpo) na resposta.

## 10. Formato da resposta final (sempre, em português)

```text
## Feito
<o que mudou, por arquivo, uma linha cada>

## Verificado
<o que você rodou e o resultado — comando e saída resumida>

## Não verificado
<o que depende de comando que você não roda, e por quê>

## Comandos para o desenvolvedor
<comandos exatos, com o resultado esperado>

## Riscos e decisões
<o que você decidiu sozinho e poderia ter ido para outro lado>

## PR proposto
<título Conventional Commit com o id do item + corpo curto>
```

## 11. Fronteira de escopo

Faça **o item, e só o item**. Se encontrar um defeito fora do escopo, não
conserte: registre em "Riscos e decisões" com arquivo e linha. Se o item não
for executável como está escrito (arquivo não existe, o código já mudou,
premissa falsa), **pare antes de codar** e responda só com o bloqueio:
sintoma, o que você verificou, hipótese, pergunta objetiva.
