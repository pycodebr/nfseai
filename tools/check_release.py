#!/usr/bin/env python3
"""Confere o que toda versão publicada precisa ter em ordem. Roda no CI e antes de cada release.

- a mesma versão no pacote, na skill e nos manifestos de plugin, com seção no CHANGELOG;
- frontmatter da skill válido (nome, descrição começando por "Use when", até 1024 caracteres);
- referências citadas no SKILL.md existentes;
- fixtures só com documentos fictícios e chaves de acesso do emitente fictício.
"""
import json
import re
import sys
from pathlib import Path

import tomllib
import yaml

ROOT = Path(__file__).resolve().parent.parent
FAKE_DOCUMENTS = {'11222333000181', '11444777000161', '52998224725', '11144477735', '39053344705'}
problems = []


def check(condition, message):
    if not condition:
        problems.append(message)


def main():
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    check(re.fullmatch(r'\d+\.\d+\.\d+', version), f'versão fora do padrão semver: {version}')

    init = (ROOT / 'src/nfseai/__init__.py').read_text(encoding='utf-8')
    check(f"__version__ = '{version}'" in init, 'src/nfseai/__init__.py com versão diferente')

    skill = (ROOT / 'skills/nfseai/SKILL.md').read_text(encoding='utf-8')
    frontmatter = yaml.safe_load(re.match(r'^---\n(.*?)\n---\n', skill, re.S).group(1))
    check(isinstance(frontmatter, dict), 'frontmatter da skill não é um mapeamento YAML')
    check(frontmatter.get('name') == 'nfseai', 'nome da skill diferente de nfseai')
    description = frontmatter.get('description') or ''
    check(description.startswith('Use when'), 'a descrição da skill deve começar por "Use when"')
    check(len(description) <= 1024, f'descrição da skill com {len(description)} caracteres (máximo 1024)')
    check(str(frontmatter.get('metadata', {}).get('version')) == version, 'SKILL.md com versão diferente')
    for reference in sorted(set(re.findall(r'`(references/[a-z0-9-]+\.md)`', skill))):
        check((ROOT / 'skills/nfseai' / reference).is_file(), f'referência citada e ausente: {reference}')
    for script in ('skills/nfseai/scripts/setup.sh', 'install.sh'):
        check((ROOT / script).is_file(), f'arquivo ausente: {script}')

    for manifest in ('plugin.json', '.claude-plugin/plugin.json', '.codex-plugin/plugin.json', 'gemini-extension.json'):
        data = json.loads((ROOT / manifest).read_text(encoding='utf-8'))
        check(data.get('name') == 'nfseai', f'{manifest}: nome diferente de nfseai')
        check(data.get('version') == version, f'{manifest}: versão diferente de {version}')
    for marketplace in ('.claude-plugin/marketplace.json', '.agents/plugins/marketplace.json'):
        data = json.loads((ROOT / marketplace).read_text(encoding='utf-8'))
        check([plugin['name'] for plugin in data['plugins']] == ['nfseai'], f'{marketplace}: lista de plugins')

    changelog = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    check(f'## {version} ' in changelog, f'CHANGELOG.md sem a seção da versão {version}')

    for path in sorted((ROOT / 'tests/fixtures').iterdir()):
        text = path.read_text(encoding='utf-8')
        formatted = re.findall(r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{3}\.\d{3}\.\d{3}-\d{2}', text)
        raw = re.findall(r'(?<!\d)(?:\d{14}|\d{11})(?!\d)', text)
        unknown = {re.sub(r'\D', '', value) for value in formatted + raw} - FAKE_DOCUMENTS
        check(not unknown, f'{path.name}: documento que não é dos fictícios: {sorted(unknown)}')
        for key in re.findall(r'(?<!\d)\d{50}(?!\d)', text):
            check(key[9:23] in FAKE_DOCUMENTS, f'{path.name}: chave de acesso de emitente não fictício')

    if problems:
        print('\n'.join(f'ERRO: {problem}' for problem in problems))
        return 1
    print(f'ok: versão {version} consistente em pacote, skill, manifestos e CHANGELOG; fixtures sem dados reais')
    return 0


if __name__ == '__main__':
    sys.exit(main())
