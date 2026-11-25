import argparse
import sys
from pathlib import Path

from . import __version__
from .offline import install_network_guard


def _utf8_output() -> None:
    """출력 인코딩이 한글을 못 쓰면(영문 Windows의 cp1252) UTF-8로 바꾼다. 그대로 두면 한글 메시지를 내다
    UnicodeEncodeError로 끝났다. 한글을 쓸 수 있는 cp949·UTF-8은 그대로 둔다. 창 모드 EXE처럼 출력이
    없으면(None) 건드리지 않는다."""
    for stream in (sys.stdout, sys.stderr):
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            "한글".encode(stream.encoding or "ascii")
        except (UnicodeEncodeError, LookupError):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv=None):
    _utf8_output()
    install_network_guard()
    parser = argparse.ArgumentParser(description="우렁각시 - 오프라인 HWPX 취합")
    parser.add_argument("--version", action="version", version=f"우렁각시 {__version__}")
    sub = parser.add_subparsers(dest="command")
    extract_parser = sub.add_parser("extract", help="워크플로를 이용한 명령행 일괄 추출")
    extract_parser.add_argument("--workflow", required=True, type=Path)
    extract_parser.add_argument("--output", required=True, type=Path)
    extract_parser.add_argument("files", nargs="+", type=Path)
    prepare_parser = sub.add_parser(
        "prepare", help="한 줄 요청을 해석해 미리보기와 계획 지문을 낸다(쓰기 없음)"
    )
    prepare_parser.add_argument("--request", default="", help='예: "이름 기준으로 이수시간 채워줘"')
    prepare_parser.add_argument("--target", required=True, type=Path, help="취합표 XLSX")
    prepare_parser.add_argument("--out", required=True, type=Path, help="계획 파일(.plan.json)")
    prepare_parser.add_argument("--output", type=Path, help="결과 XLSX 경로(기본: 취합표 폴더)")
    prepare_parser.add_argument(
        "--answer", action="append", default=[], metavar="SLOT=값", help="질문에 대한 답"
    )
    prepare_parser.add_argument(
        "--workflow", help="저장된 규칙 ID[:revision]. 주면 해석하지 않는다"
    )
    prepare_parser.add_argument(
        "--rules-dir", type=Path, help="규칙 저장 폴더(기본: 앱 데이터 폴더)"
    )
    prepare_parser.add_argument("--runtime", type=Path, help="llama-completion 실행 파일")
    prepare_parser.add_argument("--model", type=Path, help="GGUF 모델 파일")
    prepare_parser.add_argument("files", nargs="+", type=Path, help="원본 HWPX")
    commit_parser = sub.add_parser("commit", help="계획 지문이 같을 때만 결과를 저장한다")
    commit_parser.add_argument("--plan", required=True, type=Path, help="prepare가 쓴 계획 파일")
    commit_parser.add_argument("--plan-hash", required=True, help="prepare가 출력한 계획 지문")
    commit_parser.add_argument(
        "--rules-dir", type=Path, help="규칙 저장 폴더(기본: 앱 데이터 폴더)"
    )
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--screenshot", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.command == "extract":
        return _extract(args, parser)
    if args.command in ("prepare", "commit"):
        from .cli import run_commit, run_prepare

        return run_prepare(args, parser) if args.command == "prepare" else run_commit(args)
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer
    from PySide6.QtWidgets import QApplication

    from .ui.flow import FlowWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("우렁각시")
    app.setOrganizationName("ureonggaksi")
    window = FlowWindow()
    window.show()
    if args.smoke_test:

        def finished():
            if args.screenshot:
                window.grab().save(str(args.screenshot))
            app.quit()

        QTimer.singleShot(1200, finished)
    result = app.exec()
    # Destroy native widgets before QApplication/Python finalization. Signal
    # closures can otherwise retain windows until after the Qt runtime is gone.
    window.cancel()
    # 로컬 AI 호출은 끊을 수 없다(질문마다 최대 60초). 창을 닫은 뒤 오래 붙잡지 않는다(검수 M2)
    window.pool.waitForDone(5000)
    app.processEvents()
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return result


def _extract(args, parser):
    from .export import ExportError, export_excel
    from .extract import extract
    from .hwpx import HwpxError, read_hwpx
    from .models import DocumentResult
    from .storage import local_path
    from .workflow import Workflow, WorkflowError

    try:
        output = local_path(args.output)
        if output.exists():  # 덮어쓰기는 하지 않는다(api 2.9). 다른 이름을 고른다
            parser.error("출력 파일이 이미 있습니다. 다른 경로를 지정하세요.")
        if len(args.files) > 500:
            parser.error("한 번에 최대 500개 문서까지 처리할 수 있습니다.")
        workflow = Workflow.load(args.workflow)
        results = []
        for path in dict.fromkeys(args.files):
            try:
                results.append(extract(read_hwpx(path), workflow))
            except HwpxError as exc:
                results.append(DocumentResult(str(path), "", (), str(exc)))
        export_excel(results, workflow, output)
        good = sum(r.ok for r in results)
        print(f"{len(results)} documents; {good} OK; {len(results) - good} need review.")
        return 0 if good == len(results) else 1
    except (WorkflowError, ExportError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
