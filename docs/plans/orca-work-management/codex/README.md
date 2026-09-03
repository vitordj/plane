# Prompts do Codex — Gestão de trabalho por área (Orca)

Este diretório traduz o [plano de execução](../README.md) em prompts prontos
para despachar ao Codex, **um por item**. O plano diz o que fazer e com que
critério de aceite; aqui está o texto que se cola numa sessão do Codex para
que ele faça, sem redescobrir o repositório e sem inventar escopo.

| Arquivo | Fase | Itens |
| --- | --- | --- |
| [`00-context.md`](./00-context.md) | — | contexto obrigatório, lido por todo prompt |
| [`P0-prompts.md`](./P0-prompts.md) | P0 Segurança da plataforma | 13 |
| [`D0-prompts.md`](./D0-prompts.md) | D0 Fundação do domínio | 10 |
| [`01-prompts.md`](./01-prompts.md) | 1 Contrato público | 8 |
| [`02-prompts.md`](./02-prompts.md) | 2 Fila e coordenador | 6 (2.3 dividido em 2.3a/2.3b) |
| [`03-prompts.md`](./03-prompts.md) | 3 Disponibilidade | 6 |
| [`04-prompts.md`](./04-prompts.md) | 4 Processos | 7 |
| [`05-prompts.md`](./05-prompts.md) | 5 Visão executiva | 4 |

## Como despachar

Cada prompt é o bloco ```text``` da seção do item. Copie o bloco inteiro —
ele já manda ler `00-context.md`, o RFC e o arquivo da fase.

Pelo CLI, a partir da raiz do repositório, na branch do item:

```bash
git checkout stage && git pull origin stage
git checkout -b feat/orca-<tema>

codex exec -p heavy --full-auto --skip-git-repo-check -o /tmp/codex-D0.5.md \
  "$(sed -n '/^## D0.5/,/^```$/p' docs/plans/orca-work-management/codex/D0-prompts.md)" \
  </dev/null 2>/dev/null

# leia só o arquivo de saída, não a transcrição
cat /tmp/codex-D0.5.md
```

Na interface web, cole o bloco como primeira mensagem. O prompt é o mesmo.

**Perfil sugerido** está na tabela no topo de cada arquivo de fase. `heavy`
para migração, concorrência, autenticação e transação composta; `standard`
para o resto; `scout` para os itens que são só leitura e relatório. Os
perfis vêm do seu `~/.codex/config.toml` — confira o mapeamento antes de
supor nome de modelo.

## O laço de trabalho

1. **Um item por vez, uma branch por item, um PR pequeno contra `stage`.**
   Título do PR com o identificador: `feat(orca): [D0.5] assignment service…`.
2. **Você revisa o diff, não a transcrição.** `git diff --stat`, depois
   `git diff` nos arquivos que importam. O que olhar, em ordem:
   - o critério de aceite do item, um a um;
   - as invariantes do RFC §6.1 tocadas pelo diff (I1–I10);
   - **nenhuma escrita em `ProjectMember`** fora dos reconciliadores (I10);
   - paridade dos códigos de erro nos três lugares;
   - teste de concorrência presente quando o diff toca alocação;
   - migração: dependência explícita, `RunPython` idempotente e reversível,
     CHECK depois do dado migrado;
   - i18n: todas as locales, nenhuma string literal em componente;
   - nada de segredo, `.env` ou credencial no diff.
3. **O veredito é seu**: aprovar, pedir mudança (lista curta e acionável) ou
   decidir a questão de desenho que o Codex levantou. Correção de uma linha
   que você já sabe fazer, faça você — despachar um round inteiro para isso
   custa mais que o `Edit`.
4. **Rode o que o Codex não roda** (é a regra do `AGENTS.md`, e vale para
   você também dentro de sessão de agente — rode no seu terminal):

```bash
cd apps/api && ruff check . && ruff format --check .
docker compose -f docker-compose-test.yml run --rm api-tests pytest plane/tests/unit/orca/ -q
pnpm --filter web check:types && pnpm --filter web check:lint
python3 apps/api/manage.py makemigrations --check --dry-run
```

5. **Feche o item**: o Codex já marca `[x]` no arquivo da fase e atualiza a
   contagem no `README.md` do plano. Confira que fez.

## Sessão de revisão (quando quiser um segundo par de olhos)

```text
Revise o PR <n> contra o item <FASE.ITEM> de docs/plans/orca-work-management/.
Não implemente nada; produza findings.

Verifique, nesta ordem:
1. Cada critério de aceite do item, um a um, dizendo onde no diff ele é atendido.
2. As invariantes I1–I10 do RFC §6.1 tocadas pelo diff.
3. Que nenhuma escrita em ProjectMember ocorre fora dos reconciliadores.
4. Paridade dos códigos de erro nos três lugares e i18n em todas as locales.
5. Teste de concorrência presente e determinístico, se o diff toca alocação.
6. Migração: dependência, idempotência, reversibilidade, ordem dos CHECKs.
7. Segredo, credencial ou .env no diff.

Responda com findings ordenados por severidade (cada um com arquivo:linha e o
cenário concreto de falha) e o veredito de gate: aprovar, pedir mudança ou
decidir. Se não achar nada, diga isso — não invente finding.
```

## Sessão de fechamento de gate

```text
Feche o Gate <P0|D0|1|2|3|4|5> do plano docs/plans/orca-work-management/.
Para cada critério do gate, diga se está atendido e qual é a evidência (nome do
teste, run de CI, comando com saída). Preencha a data do gate no arquivo da fase
e a coluna "Gate fechado em" no README.md do plano.
Se algum critério não estiver atendido, liste o que falta como itens [ ] novos no
final do arquivo da fase, sem alterar os itens existentes.
Não invente evidência: critério sem prova é critério não atendido.
```

## Regras que não mudam, despache o que despachar

- **Um item, um PR.** Item que cresce vira dois itens, não um PR grande.
- **Decisão fechada (RFC §3, F1–F24) não se reabre** numa sessão de
  implementação. Achado que contradiz decisão vai para o RFC §4.2 e sobe
  para gente.
- **O agente não roda** `pnpm check/build/check:types`, suíte completa,
  docker ou migração. Ele lista os comandos; quem roda é você.
- **Migração escrita à mão** precisa de `makemigrations --check` seu antes
  do merge.
- **Nada de segredo no repositório**, em nenhuma hipótese, nem em exemplo.

## Ordem recomendada de despacho

```text
P0.6 ─ P0.7 ─ P0.1 ─ P0.2 ─ P0.3 ─ P0.4 ─ P0.5 ─ P0.9 ─ P0.8 ─ P0.10 ─ P0.13 ─ P0.11 ─ P0.12
                                                                                    └─> Gate P0 ─┐
D0.1 ─ D0.2 ─ D0.3 ─ D0.4 ─ D0.5 ─ D0.6 ─ D0.7 ─ D0.8 ─ D0.9 ─ D0.10                            │
                                                              └─> Gate D0 ──────────────────────┤
                                                                                                 ▼
                                    1.1 … 1.8 ─> Gate 1 ─> 2.1, 2.2, 2.3a ─> Gate 2-mínimo
                                                            └─> 2.4, 2.3b, 2.5, 2.6 ─> Gate 2
                                                                 └─> Fase 3 ─> Fase 4 ─> Fase 5
```

P0 e D0 correm em paralelo — são pessoas e sessões diferentes, sem
dependência entre si. Dentro de cada fase, a ordem é a do arquivo.
