# Third-party notices

Application source: Apache License 2.0; see LICENSE.

Runtime dependencies (exact versions: uv.lock):

- PySide6-Essentials / Shiboken6 / Qt: LGPL-3.0 / GPL and commercial alternatives, subject to the particular shipped files. Upstream: https://www.qt.io/licensing/open-source-lgpl-obligations and https://doc.qt.io/qtforpython-6/licenses.html
- openpyxl: MIT; https://openpyxl.readthedocs.io/
- et-xmlfile: MIT; https://pypi.org/project/et-xmlfile/
- defusedxml: PSF; https://pypi.org/project/defusedxml/
- regex: Apache-2.0 AND CNRI-Python; https://pypi.org/project/regex/

The build (`scripts/collect_licenses.py`) copies the license files that each installed runtime distribution ships into `licenses/` of the bundle and fails if a distribution ships none. The copied set has not yet been reviewed for completeness, in particular for Qt. PyInstaller uses its GPL license with a bootloader distribution exception. The app is distributed as an onedir bundle using dynamically loaded Qt libraries; retain the directory structure and notices. Review upstream obligations before public distribution; this file does not replace the license texts.

No third-party sample documents are stored in this repository or bundled with the application. Synthetic examples in this repository are authored for parser tests and contain no real personal information.
