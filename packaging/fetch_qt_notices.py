"""Refresh Qt's official redistribution notices; run manually, never during build."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import re
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent / 'vendor_licenses'
BASE = 'https://doc.qt.io/qt-6/'


def fetch(url):
    with urlopen(url, timeout=30) as response:
        return response.read()


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    urls = {'https://doc.qt.io/qtforpython-6/licenses.html'}
    for module in ('qtcore', 'qtgui', 'qtwidgets', 'qtnetwork', 'qtimageformats'):
        page = fetch(BASE + module + '-index.html').decode('utf-8')
        urls.update(BASE + path for path in re.findall(r'href="([^"#]*-attribution-[^"#]+\.html)"', page))
    for name in ('LGPL-3.0-only.txt', 'GPL-3.0-only.txt', 'GFDL-1.3-no-invariants-only.txt'):
        urls.add('https://raw.githubusercontent.com/pyside/pyside-setup/v6.11.2/LICENSES/' + name)
    sources = {}

    def save(url):
        name = 'pyside-licenses.html' if url.endswith('/licenses.html') else url.rsplit('/', 1)[1]
        (ROOT / name).write_bytes(fetch(url))
        return name, url

    with ThreadPoolExecutor(max_workers=4) as executor:
        for name, url in executor.map(save, sorted(urls)):
            sources[name] = url
    (ROOT / 'SOURCES.json').write_text(json.dumps(sources, indent=2) + '\n', encoding='utf-8')
    (ROOT / 'README.txt').write_text(
        'Qt/PySide 6.11.2 notices retrieved from official upstream pages.\n'
        'Full LGPLv3/GPLv3 license texts are included. HTML files preserve Qt\n'
        'third-party attributions; some describe components unused by this app.\n'
        'Documentation content retains its original copyright and GFDL notice;\n'
        'the GFDL text is included. See SOURCES.json for each original URL.\n',
        encoding='utf-8',
    )
    print(f'Saved {len(sources)} upstream notice files.')


if __name__ == '__main__':
    main()
