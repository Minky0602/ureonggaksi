"""Defense in depth for Python Internet sockets. OS policy remains authoritative."""

import socket
import sys

_installed = False


def install_network_guard():
    global _installed
    if _installed:
        return

    def guard(event, args):
        if event == "socket.__new__" and args[1] in (socket.AF_INET, socket.AF_INET6):
            raise PermissionError("우렁각시는 네트워크 소켓을 사용하지 않습니다.")
        if event in {
            "socket.connect",
            "socket.bind",
            "socket.getaddrinfo",
            "socket.gethostbyname",
            "socket.gethostbyaddr",
            "socket.getnameinfo",
            "socket.sendto",
            "socket.getservbyname",
            "socket.getservbyport",
        }:
            raise PermissionError("우렁각시의 네트워크 접근이 차단되었습니다.")

    sys.addaudithook(guard)
    _installed = True
