"""Build an allow-listed source bundle, never a workspace-wide archive."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import json
import re

ROOT = Path(__file__).resolve().parent
FILES = ('app.py', 'postal.py', 'test_app.py', 'package_oss.py', 'prepare_remote.ps1', 'README.md', 'LICENSE', '.gitignore',
         'docs/images/households.png', 'docs/images/memorial-report.png',
         'web/index.html', 'web/app.js', 'web/style.css',
         'web/assets/noto-serif-subsets.css', 'web/assets/font-subsets.json', 'web/assets/FONT-SUBSETS.md', 'web/assets/OFL.txt',
         'scripts/build_fonts.py',
         'assets/postal/utf_ken_all.zip', 'assets/postal/SOURCE.md')


def distribution_files():
    manifest = json.loads((ROOT / 'web/assets/font-subsets.json').read_text(encoding='utf-8'))
    fonts = []
    for item in manifest['subsets']:
        name = item['file']
        if not re.fullmatch(r'noto-serif-jp-[a-z0-9-]+-[0-9a-f]{12}\.woff2', name):
            raise ValueError('フォントの配布ファイル名を確認してください。')
        fonts.append('web/assets/fonts/' + name)
    return FILES + tuple(fonts)


def main():
    paths = [(name, ROOT / name) for name in distribution_files()]
    for name, path in paths:
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            raise ValueError(f'配布対象を確認してください: {name}')
    output = ROOT / 'exports' / 'temple-management-source.zip'
    output.parent.mkdir(exist_ok=True)
    with ZipFile(output, 'w', ZIP_DEFLATED) as bundle:
        for name, path in paths:
            bundle.write(path, 'temple-management/' + name)
    print('許可リストの公開候補を作成しました。アプリのライセンスはMITです。')


if __name__ == '__main__':
    main()
