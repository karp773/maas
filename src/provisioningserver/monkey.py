# Copyright 2015-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""
Monkey patch for the MAAS provisioning server, with code for rack and region
server patching.
"""

__all__ = [
    "add_term_error_code_to_tftp",
    "add_patches_to_twisted",
]


def add_term_error_code_to_tftp():
    """Add error code 8 to TFT server as introduced by RFC 2347.

    Manually apply the fix to python-tx-tftp landed in
    https://github.com/shylent/python-tx-tftp/pull/20
    """
    import tftp.datagram
    if tftp.datagram.errors.get(8) is None:
        tftp.datagram.errors[8] = (
            "Terminate transfer due to option negotiation")


def fix_twisted_web_client():
    """Properly create Host: header in twisted.web.client.HTTPPageGetter

       Specifically, IPv6 IP addresses need to be wrapped in [].

       See https://bugs.launchpad.net/ubuntu/+source/twisted/+bug/1604608
    """
    import twisted.web.client
    from twisted.python.compat import intToBytes

    def new_connectionMade(self):
        method = getattr(self.factory, 'method', b'GET')
        self.sendCommand(method, self.factory.path)
        # The standard for IPv6-based URLs is scheme://[ip:addr]:port/path,
        # with the ':port' being optional.  Since ':' is not a legal character
        # in hostnames, we just check for the existence of that to decide that
        # it is IPv6.
        host = self.factory.host
        if b':' in host:
            host = b'[' + host + b']:' + intToBytes(self.factory.port)
        else:
            host = host + b':' + intToBytes(self.factory.port)
        self.sendHeader(b'Host', self.factory.headers.get(b"host", host))
        self.sendHeader(b'User-Agent', self.factory.agent)
        data = getattr(self.factory, 'postdata', None)
        if data is not None:
            self.sendHeader(b"Content-Length", intToBytes(len(data)))

        cookieData = []
        for (key, value) in self.factory.headers.items():
            if key.lower() not in self._specialHeaders:
                # we calculated it on our own
                self.sendHeader(key, value)
            if key.lower() == b'cookie':
                cookieData.append(value)
        for cookie, cookval in self.factory.cookies.items():
            cookieData.append(cookie + b'=' + cookval)
        if cookieData:
            self.sendHeader(b'Cookie', b'; '.join(cookieData))
        self.endHeaders()
        self.headers = {}

        if data is not None:
            self.transport.write(data)

    twisted.web.client.HTTPPageGetter.connectionMade = new_connectionMade


def fix_twisted_web_http_Request():
    """Add ipv6 support to Request.getClientIP()

       Specifically, IPv6 IP addresses need to be wrapped in [], and return
       address.IPv6Address when needed.

       See https://bugs.launchpad.net/ubuntu/+source/twisted/+bug/1604608
    """
    from netaddr import IPAddress
    from netaddr.core import AddrFormatError
    from twisted.internet import address
    from twisted.python.compat import (
        intToBytes,
        networkString,
    )
    import twisted.web.http
    from twisted.web.server import Request
    from twisted.web.test.requesthelper import DummyChannel

    def new_getClientIP(self):
        from twisted.internet import address
        # upstream doesn't check for address.IPv6Address
        if isinstance(self.client, address.IPv4Address):
            return self.client.host
        elif isinstance(self.client, address.IPv6Address):
            return self.client.host
        else:
            return None

    def new_getRequestHostname(self):
        # Unlike upstream, support/require IPv6 addresses to be
        # [ip:v6:add:ress]:port, with :port being optional.
        # IPv6 IP addresses are wrapped in [], to disambigate port numbers.
        host = self.getHeader(b'host')
        if host:
            if host.startswith(b'[') and b']' in host:
                if host.find(b']') < host.rfind(b':'):
                    # The format is: [ip:add:ress]:port.
                    return host[:host.rfind(b':')]
                else:
                    # no :port after [...]
                    return host
            # No brackets, so it must be host:port or IPv4:port.
            return host.split(b':', 1)[0]
        host = self.getHost().host
        try:
            if isinstance(host, str):
                ip = IPAddress(host)
            else:
                ip = IPAddress(host.decode("idna"))
        except AddrFormatError:
            # If we could not convert the hostname to an IPAddress, assume that
            # it is a hostname.
            return networkString(host)
        if ip.version == 4:
            return networkString(host)
        else:
            return networkString('[' + host + ']')

    def new_setHost(self, host, port, ssl=0):
        try:
            ip = IPAddress(host.decode("idna"))
        except AddrFormatError:
            ip = None  # `host` is a host or domain name.
        self._forceSSL = ssl  # set first so isSecure will work
        if self.isSecure():
            default = 443
        else:
            default = 80
        if ip is None:
            hostHeader = host
        elif ip.version == 4:
            hostHeader = host
        else:
            hostHeader = b"[" + host + b"]"
        if port != default:
            hostHeader += b":" + intToBytes(port)
        self.requestHeaders.setRawHeaders(b"host", [hostHeader])
        if ip is None:
            # Pretend that a host or domain name is an IPv4 address.
            self.host = address.IPv4Address("TCP", host, port)
        elif ip.version == 4:
            self.host = address.IPv4Address("TCP", host, port)
        else:
            self.host = address.IPv6Address("TCP", host, port)

    request = Request(DummyChannel(), False)
    request.client = address.IPv6Address('TCP', 'fe80::1', '80')
    request.setHost(b"fe80::1", 1234)
    if request.getClientIP() is None:
        # Buggy code returns None for IPv6 addresses.
        twisted.web.http.Request.getClientIP = new_getClientIP
    if isinstance(request.host, address.IPv4Address):
        # Buggy code calls fe80::1 an IPv4Address.
        twisted.web.http.Request.setHost = new_setHost
    if request.getRequestHostname() == b'fe80':
        # The fe80::1 test address above was incorrectly interpreted as
        # address='fe80', port = ':1', because it does host.split(':', 1)[0].
        twisted.web.http.Request.getRequestHostname = new_getRequestHostname


def fix_twisted_web_server_addressToTuple():
    """Add ipv6 support to t.w.server._addressToTuple()

       Return address.IPv6Address where appropriate.

       See https://bugs.launchpad.net/ubuntu/+source/twisted/+bug/1604608
    """
    import twisted.web.server
    from twisted.internet import address

    def new_addressToTuple(addr):
        if isinstance(addr, address.IPv4Address):
            return ('INET', addr.host, addr.port)
        elif isinstance(addr, address.IPv6Address):
            return ('INET6', addr.host, addr.port)
        elif isinstance(addr, address.UNIXAddress):
            return ('UNIX', addr.name)
        else:
            return tuple(addr)

    test = address.IPv6Address("TCP", "fe80::1", '80')
    try:
        twisted.web.server._addressToTuple(test)
    except TypeError:
        twisted.web.server._addressToTuple = new_addressToTuple


def fix_twisted_internet_tcp():
    """Default client to AF_INET6 sockets.

       Specifically, strip any brackets surrounding the address.

       See https://bugs.launchpad.net/ubuntu/+source/twisted/+bug/1604608
    """
    import socket
    import twisted.internet.tcp
    from twisted.internet.tcp import _NUMERIC_ONLY

    def new_resolveIPv6(ip, port):
        # Remove brackets surrounding the address, if any.
        ip = ip.strip('[]')
        return socket.getaddrinfo(ip, port, 0, 0, 0, _NUMERIC_ONLY)[0][4]

    twisted.internet.tcp._resolveIPv6 = new_resolveIPv6


def add_patches_to_twisted():
    fix_twisted_web_client()
    fix_twisted_web_http_Request()
    fix_twisted_web_server_addressToTuple()
    fix_twisted_internet_tcp()