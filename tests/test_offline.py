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
