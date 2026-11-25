import socket
import subprocess
import sys


def test_network_guard_blocks_connection_dns_and_server():
    script = """
import socket
from ureonggaksi.offline import install_network_guard
install_network_guard()
install_network_guard()
for operation in [lambda: socket.socket(socket.AF_INET), lambda: socket.socket(socket.AF_INET6), lambda: socket.getaddrinfo('example.com', 443), lambda: socket.gethostbyname('example.com')]:
    try:
        operation()
    except PermissionError:
        pass
    else:
        raise AssertionError('network allowed')
print('blocked')
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "blocked"


def test_core_flow_with_network_forbidden(sample, tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", fail)
    monkeypatch.setattr(socket, "getaddrinfo", fail)
    from ureonggaksi.export import export_excel
    from ureonggaksi.extract import extract, verify
    from ureonggaksi.hwpx import read_hwpx
    from ureonggaksi.workflow import Rule, Workflow

    wf = Workflow("오프라인", (Rule("이름", "label", query="성명"),))
    result = extract(read_hwpx(sample), wf)
    assert verify(result.values[0]).ok
    export_excel((result,), wf, tmp_path / "offline.xlsx")


def test_request_flow_with_network_forbidden(tmp_path, monkeypatch):
    """배포되는 흐름(한 줄 요청 → prepare → commit)도 네트워크 없이 끝난다. 위 시험은 옛 extract 흐름만 본다."""
    from conftest import cell, para, table, write_hwpx, write_xlsx

    from ureonggaksi.rules import RuleStore
    from ureonggaksi.service import commit, prepare

    def fail(*args, **kwargs):
        raise AssertionError("network access attempted")

    rows = [("성명", "가나다"), ("이수시간", "12시간")]
    body = table([[cell(k, i, 0), cell(v, i, 1)] for i, (k, v) in enumerate(rows)])
    source = write_hwpx(
        tmp_path / "d.hwpx", [para("발급번호: A-1") + "<hp:p><hp:run>" + body + "</hp:run></hp:p>"]
    )
    target = write_xlsx(tmp_path / "t.xlsx", [["발급번호", "이수시간"], ["A-1", None]], sheet="표")
    monkeypatch.setattr(socket, "socket", fail)
    monkeypatch.setattr(socket, "getaddrinfo", fail)
    prepared = prepare("발급번호 기준으로 이수시간 채워줘", [source], target)
    package = commit(prepared, RuleStore(tmp_path / "rules"))
    assert package.status == "completed" and package.output_path.exists()
