"""同主机服务的公开地址策略；不连接远程服务，不管理模型进程。"""
import errno
import ipaddress
import socket
from urllib.parse import urlsplit


def address(host):
    if host == 'localhost':
        return ipaddress.ip_address('127.0.0.1')
    try:
        value = ipaddress.ip_address(host)
    except ValueError as error:
        raise ValueError('服务地址须为本机 IP 或 localhost') from error
    if value.is_unspecified or value.is_multicast:
        raise ValueError('服务访问地址不能是监听通配或组播地址')
    return value


def endpoint(value, schemes=('http', 'https')):
    url = urlsplit(value)
    if (url.scheme not in schemes or not url.hostname or not url.port
            or url.username is not None or url.password is not None
            or url.query or url.fragment or url.path not in ('', '/')):
        raise ValueError('服务端点须有明确端口且无凭据或 API 路径')
    address(url.hostname)
    return url


def assert_local(host):
    ip = address(host)
    family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.bind((str(ip), 0))
    except OSError as error:
        raise ValueError('服务 IP 不属于当前执行主机或本机无法绑定') from error
    return str(ip)


def free_port(host, start):
    if type(start) is not int or not 1 <= start <= 65535:
        raise ValueError('端口范围无效')
    ip = ipaddress.ip_address(assert_local(host))
    family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    for port in range(start, 65536):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.bind((str(ip), port))
            return port
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
    raise ValueError('没有可用端口，已达到 65535')


def access_host(bind_host):
    ip = ipaddress.ip_address(bind_host)
    return ('127.0.0.1' if ip.version == 4 else '::1') if ip.is_unspecified else str(ip)


def base_url(host, port):
    ip = access_host(host)
    return 'http://' + ('[' + ip + ']' if ':' in ip else ip) + ':' + str(port)
