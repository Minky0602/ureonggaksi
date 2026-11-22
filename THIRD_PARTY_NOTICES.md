# Third-party notices

Application source: Apache License 2.0; see LICENSE.

Runtime dependencies (exact versions: uv.lock):

- PySide6-Essentials / Shiboken6 / Qt: LGPL-3.0 / GPL and commercial alternatives, subject to the particular shipped files. Upstream: https://www.qt.io/licensing/open-source-lgpl-obligations and https://doc.qt.io/qtforpython-6/licenses.html
- openpyxl: MIT; https://openpyxl.readthedocs.io/
- et-xmlfile: MIT; https://pypi.org/project/et-xmlfile/
- defusedxml: PSF; https://pypi.org/project/defusedxml/
- regex: Apache-2.0 AND CNRI-Python; https://pypi.org/project/regex/
- kiwipiepy 0.24.1 / Kiwi (Korean morphological analyzer, used to read particles and negation in requests): Apache-2.0; the `_kiwipiepy` extension statically links Eigen (MPL-2.0, built with EIGEN_MPL2_ONLY), cpp-btree and streamvbyte (Apache-2.0), cpuinfo (BSD-2-Clause), mimalloc and nlohmann/json (MIT). Its NOTICE must be retained and is copied into `licenses/kiwipiepy/`. https://github.com/bab2min/kiwipiepy
- kiwipiepy_model 0.24.0 (Kiwi model files: morpheme dictionaries and n-gram statistics trained on corpora of the National Institute of Korean Language; no corpus text is distributed): Apache-2.0; https://pypi.org/project/kiwipiepy-model/
- tqdm (imported by kiwipiepy): MPL-2.0 AND MIT; https://github.com/tqdm/tqdm

numpy is installed with kiwipiepy for its training utilities but is not imported at runtime and is excluded from the bundle.

The build (`scripts/collect_licenses.py`) copies the license files that each installed runtime distribution ships into `licenses/` of the bundle and fails if a distribution ships none. The copied set has not yet been reviewed for completeness, in particular for Qt. PyInstaller uses its GPL license with a bootloader distribution exception. The app is distributed as an onedir bundle using dynamically loaded Qt libraries; retain the directory structure and notices. Review upstream obligations before public distribution; this file does not replace the license texts.

Optional local AI (not bundled; the user downloads and selects these files):

- llama.cpp `llama-completion` (tested release b12125): MIT; https://github.com/ggml-org/llama.cpp
- Qwen3.5-4B GGUF Q4_K_M (tested: unsloth/Qwen3.5-4B-GGUF, original Qwen/Qwen3.5-4B): Apache-2.0; https://huggingface.co/unsloth/Qwen3.5-4B-GGUF and https://huggingface.co/Qwen/Qwen3.5-4B

No third-party sample documents are stored in this repository or bundled with the application. Synthetic examples in this repository are authored for parser tests and contain no real personal information.
