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
1. AGENTS.md e FORK.md (regras do fork: sidecar, sem coluna em modelo core, não
   rodar builds/migrações/suíte completa na sessão, commits `feat(orca):` etc.)
2. docs/orca-work-management-rfc.md — seções 1, 2, 3 (conceito, estado atual,
   24 decisões fechadas F1–F24), depois as seções citadas pelo item que você
   vai executar. Não reabra decisão fechada sem registrar em §4.2.
3. docs/plans/orca-work-management/README.md — quadro de estado e ordem das
   fases. Confira qual é o próximo item `[ ]` da fase ativa.
4. O arquivo da fase do item (ex.: docs/plans/orca-work-management/D0-domain-foundation.md).

Item desta sessão: <FASE.ITEM — ex.: D0.1 — Área precisa cobrir o projeto>

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
  git. O que você não roda (AGENTS.md): pnpm check/build/check:types, pytest
  completo, docker, migrate. Liste os comandos exatos para o desenvolvedor.
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
- Defeitos conhecidos D1–D4 (RFC §2.2): cobertura área↔projeto não validada;
  API pública herda assignees do último item do criador; ranking sem lock;
  carga conta qualquer assignee.
- Plane CE não tem custom properties; a única fonte da verdade da área é
  IssueOrganizationalUnit.
```

---

## Variantes

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
