# Contribuindo

Obrigado por querer melhorar o nfseai. Como o projeto emite documento fiscal, as contribuições seguem
algumas regras a mais do que o habitual.

## Regras que valem para todo pull request

1. **Nenhum dado real no repositório.** CNPJ, CPF, razão social, nome de cliente, endereço, chave de acesso,
   token ou cookie de uma conta de produção não entram em código, teste, fixture, issue ou descrição de PR.
   As páginas e capturas do portal ficam em `.state/`, que está no `.gitignore`.
2. **Diga o que foi validado.** Em cada mudança que toca o portal, separe o que foi executado contra o portal
   (validado) do que foi apenas lido no HTML ou no JavaScript (mapeado).
3. **Não afrouxe as proteções.** A verificação de contrato, a exigência de `confirm=True`, o registro por
   referência e a regra de nunca repetir uma emissão não são removidos para simplificar um caso.
4. **A senha nunca aparece.** Nenhuma mensagem de erro, log, `repr` ou saída de comando pode conter a senha
   do portal. Os testes conferem isso.

## Ambiente

```bash
git clone https://github.com/pycodebr/nfseai && cd nfseai
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/python tools/check_release.py
```

Os testes rodam sem rede. Python 3.10 ou mais novo, PEP 8, linhas de até 120 colunas e aspas simples.

## Portal mudou ou cenário novo

Siga [`docs/remapeamento.md`](docs/remapeamento.md): capturar o passo no navegador, atualizar o contrato e o
cliente, regerar as fixtures com dados fictícios e validar com um ensaio no portal. Atualize também
[`docs/api-map.md`](docs/api-map.md) e [`skills/nfseai/references/cenarios.md`](skills/nfseai/references/cenarios.md).

## Skill e documentação

- O `SKILL.md` e as referências são escritos em inglês; comandos, chaves do JSON e mensagens ao usuário ficam
  em português. O README é em português, com um resumo em inglês no fim.
- A `description:` do frontmatter começa por "Use when", fica entre aspas simples (texto com `: ` sem aspas
  quebra o YAML) e tem até 1024 caracteres. Um apóstrofo dentro do texto vira `''`.
- Todo comando citado na skill precisa existir na linha de comando, com as mesmas opções.

## Versões e releases

Toda alteração publicada no `main` sai como versão, com tag e release no GitHub.

1. `python tools/bump_version.py X.Y.Z` atualiza a versão no pacote, na skill e nos manifestos (semver: patch
   para correções e documentação, minor para comandos, cenários e recursos novos, major para mudanças
   incompatíveis).
2. Abra a seção `## X.Y.Z (AAAA-MM-DD)` no topo do `CHANGELOG.md`.
3. `python tools/check_release.py`, `pytest` e `ruff check .` sem erros.
4. Commit com a mensagem `nfseai X.Y.Z: <resumo>`, push no `main` e CI aprovado nesse commit.
5. Tag anotada e release com as notas da seção do CHANGELOG:
   `git tag -a vX.Y.Z -m "nfseai X.Y.Z: <resumo>" && git push origin vX.Y.Z`
   `gh release create vX.Y.Z --verify-tag --title "nfseai X.Y.Z: <resumo>" --notes-file <notas.md> --latest`

O `setup.sh` da skill instala o pacote pela tag `vX.Y.Z` quando a skill não está dentro de um checkout, então
a tag precisa existir para cada versão publicada.

## Issues

Descreva o sistema operacional, o agente usado, a saída de `nfseai doctor` e de `nfseai --version`, o comando
que falhou e a mensagem de erro. Remova CNPJ, CPF, nomes e chaves de acesso antes de colar qualquer saída.
Falha de segurança: veja [SECURITY.md](SECURITY.md).
