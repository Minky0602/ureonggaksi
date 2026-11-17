import argparse
import sys
from pathlib import Path

from .offline import install_network_guard


def main(argv=None):
    install_network_guard()
    parser = argparse.ArgumentParser(description="우렁각시 - 오프라인 HWPX 취합")
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

    from .ui.window import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("우렁각시")
    app.setOrganizationName("ureonggaksi")
    window = MainWindow()
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
    window.pool.waitForDone()
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
