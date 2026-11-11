#!/usr/bin/env python3
"""Troca a versão em todos os arquivos que a declaram: `python tools/bump_version.py 0.2.0`."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = {
    'pyproject.toml': (r'(?m)^version = "[^"]+"', 'version = "{v}"'),
    'src/nfseai/__init__.py': (r"__version__ = '[^']+'", "__version__ = '{v}'"),
    'skills/nfseai/SKILL.md': (r'(?m)^  version: .+$', '  version: {v}'),
    'plugin.json': (r'"version": "[^"]+"', '"version": "{v}"'),
    '.claude-plugin/plugin.json': (r'"version": "[^"]+"', '"version": "{v}"'),
    '.codex-plugin/plugin.json': (r'"version": "[^"]+"', '"version": "{v}"'),
    'gemini-extension.json': (r'"version": "[^"]+"', '"version": "{v}"'),
}


def main():
    if len(sys.argv) != 2 or not re.fullmatch(r'\d+\.\d+\.\d+', sys.argv[1]):
        print(__doc__)
        return 2
    version = sys.argv[1]
    for name, (pattern, replacement) in TARGETS.items():
        path = ROOT / name
        text, count = re.subn(pattern, replacement.format(v=version), path.read_text(encoding='utf-8'), count=1)
        if count != 1:
            print(f'ERRO: versão não encontrada em {name}')
            return 1
        path.write_text(text, encoding='utf-8')
        print(f'{name}: {version}')
    print('agora abra a seção da versão no CHANGELOG.md e rode: python tools/check_release.py')
    return 0


if __name__ == '__main__':
    sys.exit(main())
