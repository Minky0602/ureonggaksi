# 기여 안내

우렁각시를 받아서 실행하고, 고치고, PR을 올리는 방법이다. 구조는 [docs/architecture.md](docs/architecture.md), 함수 계약은 [docs/api.md](docs/api.md), 요구사항은 [docs/requirements.md](docs/requirements.md)에 있다.

## 개발 환경

[uv](https://docs.astral.sh/uv/)만 설치하면 된다. Python 3.12는 `.python-version`에 따라 uv가 맞춘다.

```powershell
uv sync --locked --extra dev
```

`uv.lock`에 의존성 버전이 고정되어 있다. `--locked`는 잠금 파일과 `pyproject.toml`이 다르면 다시 풀지 않고 실패한다. 의존성을 바꿀 때만 `uv lock`을 돌리고, 바뀐 `uv.lock`을 함께 커밋한다.

처음 설치할 때만 인터넷이 필요하다. 그 뒤의 실행·시험·빌드는 인터넷을 쓰지 않는다.

## 실행

```powershell
./scripts/run.ps1                      # 또는 ureonggaksi.cmd 두 번 클릭
.venv/Scripts/python.exe -m ureonggaksi
```

VS Code에서는 저장소 루트 폴더를 열고 `.vscode/launch.json`의 "우렁각시: GUI"로 실행한다.

## 시험

| OS | 명령 |
|---|---|
| Windows | `./scripts/test.ps1` |
| Linux | `./scripts/test.sh` |

두 스크립트는 pytest, `ruff check`, `ruff format --check`를 차례로 돌리고 하나라도 실패하면 멈춘다. GitHub Actions가 `main`에 push할 때와 PR마다 같은 스크립트를 Windows와 Ubuntu에서 실행한다([ci.yml](.github/workflows/ci.yml)).

Linux에서 화면 시험은 창 없이(offscreen) 돌지만 Qt가 쓰는 시스템 라이브러리가 필요하다.

```sh
sudo apt-get update
sudo apt-get install -y --no-install-recommends libgl1 libegl1 libxkbcommon0 libglib2.0-0t64 libdbus-1-3 libfontconfig1
```

- 시험은 합성 자료만 쓴다. 실제 업무 문서를 시험 자료로 넣지 않는다.
- pytest 임시 경로는 프로젝트 안의 `.pytest-tmp`다. 시험 자료가 프로젝트 밖에 남지 않게 하려는 것이고, Git에서 제외된다.
- 결함을 고칠 때는 그 결함을 드러내는 시험을 먼저 넣는다. 실제 문서에서 찾은 구조는 민감한 내용을 뺀 최소 구조로 줄여서 넣는다.

## 브랜치, 커밋, PR

1. `main`에서 브랜치를 만든다. 이름은 `종류/짧은-설명`이다. 예: `feat/xlsx-reader`, `fix/help-encoding`, `docs/architecture`.
2. 커밋 하나는 한 가지 이유로만 바꾼다. 제목은 `종류: 무엇을 했는지`다. 종류는 `feat`, `fix`, `docs`, `test`, `ci`, `chore`, `refactor`를 쓴다.
3. 설계가 바뀌면 `docs/architecture.md`나 `docs/api.md`를 먼저 고친다.
4. PR을 올리면 [PR 템플릿](.github/pull_request_template.md)이 채울 항목을 보여 준다. 실행한 시험과 결과, 알려진 한계를 적는다.
5. CI의 두 잡이 모두 통과해야 병합한다. 병합은 **Merge commit**으로 한다. squash하면 책임 단위로 나눈 커밋이 사라진다.

## 넣지 않는 것

- 실제 업무 문서, 개인정보, 결과 파일
- 모델 가중치(`*.gguf`)와 빌드 산출물(`dist/`, `build/`). 모델은 저장소와 따로 배포한다
- 네트워크, 웹서버, 원격 API, 원격 폰트, 사용 통계 수집 기능
- 문서 본문을 로그나 설정에 남기는 코드

## Windows 배포 빌드

```powershell
./scripts/build.ps1
```

`dist/Ureonggaksi/` 폴더가 만들어진다. 폴더 전체를 복사하면 Python이 없는 PC에서도 실행된다. 실행할 때마다 라이브러리를 임시 폴더에 푸는 onefile 방식 대신 onedir 방식을 쓴다. 관리자 권한, 한글이나 Excel 설치, 계정은 필요 없다.

인터넷이 없는 다른 개발 PC로 옮길 때는 같은 Windows·Python 구성에서 `requirements.lock.txt`에 맞는 wheel을 미리 받아 `pip --no-index --find-links`로 설치하거나, 완성된 `dist` 폴더를 쓴다. `.venv` 폴더를 그대로 복사하지 않는다.

## 참고한 1차 자료

[한컴 HWPX 구조](https://tech.hancom.com/hwpxformat/), [한컴 Python 파싱](https://tech.hancom.com/python-hwpx-parsing-2/), [Qt for Python](https://doc.qt.io/qtforpython-6/quickstart.html), [openpyxl](https://openpyxl.readthedocs.io/en/stable/)
