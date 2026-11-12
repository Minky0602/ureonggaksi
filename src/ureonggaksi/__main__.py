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
    extract_parser.add_argument("--overwrite", action="store_true")
    extract_parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--screenshot", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.command == "extract":
        return _extract(args, parser)
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
        if output.exists() and not args.overwrite:
            parser.error("출력 파일이 이미 있습니다. 덮어쓰려면 --overwrite를 지정하세요.")
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
