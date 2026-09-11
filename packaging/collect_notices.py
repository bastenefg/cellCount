"""Copy installed dependency notices for the portable app without changing sources."""
from __future__ import annotations

import argparse
from importlib import metadata
from pathlib import Path
import re
import shutil
import sys


def collect(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    entries = []
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name = dist.metadata['Name']
        folder = destination / re.sub(r'[^A-Za-z0-9_.-]+', '_', name)
        folder.mkdir(exist_ok=True)
        copied = []
        for relative in dist.files or []:
            parts = tuple(part.lower() for part in relative.parts)
            basename = parts[-1]
            is_notice = (
                any(part in {'licenses', 'license', 'licences'} for part in parts)
                or basename.startswith(('license', 'licence', 'copying', 'notice', 'copyright'))
            )
            if not is_notice or '..' in parts:
                continue
            source = Path(dist.locate_file(relative))
            if not source.is_file() or source.suffix.lower() in {'.pyc', '.pyd', '.dll'}:
                continue
            target = folder / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(str(relative).replace('\\', '/'))
        # METADATA retains author, copyright/license declarations and source links.
        meta = dist.read_text('METADATA') or ''
        (folder / 'PACKAGE_METADATA.txt').write_text(meta, encoding='utf-8')
        entries.append(f"- {name} {dist.version}: {len(copied)} notice file(s); see {folder.name}/")

    python_candidates = [Path(sys.base_prefix) / 'LICENSE.txt', Path(sys.base_prefix) / 'LICENSE']
    python_license = next((path for path in python_candidates if path.is_file()), None)
    if python_license is None:
        raise FileNotFoundError('Python license was not found beside the base interpreter.')
    shutil.copy2(python_license, destination / 'PYTHON_LICENSE.txt')
    vendor_notices = Path(__file__).resolve().parent / 'vendor_licenses'
    if vendor_notices.is_dir():
        shutil.copytree(vendor_notices, destination / 'Qt_additional_notices', dirs_exist_ok=True)
    text = '''# Third-party components

This portable distribution includes Python and packages listed below. Their
original license/copyright notices and package metadata are retained here.
Build-time packages are also listed for transparency; listing does not imply
that every package is part of the executable. Individual components retain
their original licenses. This file does not assign a license to the analysis
or GUI source.

Qt/PySide6 and Shiboken are distributed as separate, dynamically loaded
libraries, used under LGPLv3. Their LGPLv3 and GPLv3 texts and upstream
attribution pages are in Qt_additional_notices. Wheel-provided notices and
metadata are in the PySide6_Essentials and shiboken6 subfolders. Unmodified
upstream source is available at https://github.com/pyside/pyside-setup/tree/v6.11.2
and https://download.qt.io/official_releases/qt/6.11/6.11.2/submodules/.
Compatible modified dynamic libraries may be substituted in the application
folder. The application source is included under _internal/source, and build
instructions are in APP_GUIDE.md. Keep these notices when redistributing.

Python's license is in PYTHON_LICENSE.txt.

'''
    (destination / 'THIRD_PARTY_NOTICES.md').write_text(text + '\n'.join(entries) + '\n', encoding='utf-8')
    print(f'Collected notices for {len(entries)} installed distributions in {destination}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    collect(parser.parse_args().destination.resolve())
