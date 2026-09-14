# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import runpy

project = Path(SPECPATH)
notices = project / 'build' / 'third_party_notices'
runpy.run_path(str(project / 'packaging' / 'collect_notices.py'))['collect'](notices)


def directory_data(path, destination):
    return [
        (str(item), str(Path(destination) / item.relative_to(path).parent))
        for item in sorted(path.rglob('*'))
        if item.is_file() and '__pycache__' not in item.parts and item.suffix != '.pyc'
    ]


datas = []
for folder in ('reference', 'configs', 'tests', 'validation'):
    datas += directory_data(project / folder, folder)
# The unchanged pipeline resolves ROOT from __file__ and hashes these sources.
datas += [(str(project / 'run_pipeline.py'), '.')]
datas += [(str(item), 'pipeline') for item in sorted((project / 'pipeline').glob('*.py'))]
for name in ('README.md', 'APP_GUIDE.md', 'requirements.txt', 'requirements-lock.txt',
             'requirements-gui.txt', 'requirements-build.txt', 'samples_template.csv',
             'samples_live_dead_template.csv', 'CHANGELOG.md', 'PACKAGE_SHA256.json',
             '.python-version'):
    datas.append((str(project / name), '.'))
datas += directory_data(notices, 'third_party_notices')
datas += directory_data(project / 'desktop' / 'assets', 'desktop/assets')
# Include editable interface/build sources for collaborators and rebuilds.
for name in ('run_app.py', 'CHO_Cell_Counter.spec', 'build_app.ps1', 'Launch App.cmd'):
    datas.append((str(project / name), 'source'))
datas += directory_data(project / 'desktop', 'source/desktop')
datas += directory_data(project / 'packaging', 'source/packaging')

a = Analysis(
    [str(project / 'run_app.py')],
    pathex=[str(project)],
    binaries=[],
    datas=datas,
    hiddenimports=['pipeline.cli', 'matplotlib.backends.backend_svg'],
    hookspath=[],
    hooksconfig={'matplotlib': {'backends': ['Agg', 'svg']}},
    runtime_hooks=[],
    excludes=['tkinter', 'PyQt5', 'PyQt6', 'PySide2'],
    noarchive=False,
)
# Qt's Windows build imports the unversioned ICU API supplied by Windows.
# The build interpreter also carries ICU 78 with renamed exports (*_78).
# PyInstaller can mistakenly collect that unrelated DLL, shadowing Windows ICU
# and causing Qt6Core/QtGui to fail with WinError 127. Keep using the OS library.
a.binaries = [
    entry for entry in a.binaries
    if Path(entry[0]).name.lower() not in {'icuuc.dll', 'icudt78.dll'}
]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Live-Dead Cell Counter',
    icon=str(project / 'desktop' / 'assets' / 'live-dead-cell-counter.ico'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='Live-Dead Cell Counter',
)
