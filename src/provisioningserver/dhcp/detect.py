
# Copyright 2013-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Utilities and helpers to help discover DHCP servers on your network."""

__all__ = [
    'probe_dhcp',
    ]

from contextlib import contextmanager
import errno
import fcntl
from random import randint
import socket
import struct

from provisioningserver.logger import get_maas_logger


maaslog = get_maas_logger("dhcp.detect")


def make_transaction_ID():
    """Generate a random DHCP transaction identifier."""
    transaction_id = b''
    for _ in range(4):
        transaction_id += struct.pack(b'!B', randint(0, 255))
    return transaction_id


class DHCPDiscoverPacket:
    """A representation of a DHCP_DISCOVER packet.

    :param my_mac: The MAC address to which the dhcp server should respond.
        Normally this is the MAC of the interface you're using to send the
        request.
    """

    def __init__(self, my_mac):
        self.transaction_ID = make_transaction_ID()
        self.packed_mac = self.string_mac_to_packed(my_mac)
        self._build()

    @classmethod
    def string_mac_to_packed(cls, mac):
        """Convert a string MAC address to 6 hex octets.

        :param mac: A MAC address in the format AA:BB:CC:DD:EE:FF
        :return: a byte string of length 6
        """
        packed = b''
        for pair in mac.split(':'):
            hex_octet = int(pair, 16)
            packed += struct.pack(b'!B', hex_octet)
        return packed

    def _build(self):
        self.packet = b''
        self.packet += b'\x01'  # Message type: Boot Request (1)
        self.packet += b'\x01'  # Hardware type: Ethernet
        self.packet += b'\x06'  # Hardware address length: 6
        self.packet += b'\x00'  # Hops: 0
        self.packet += self.transaction_ID
        self.packet += b'\x00\x00'  # Seconds elapsed: 0

        # Bootp flags: 0x8000 (Broadcast) + reserved flags
        self.packet += b'\x80\x00'

        self.packet += b'\x00\x00\x00\x00'  # Client IP address: 0.0.0.0
        self.packet += b'\x00\x00\x00\x00'  # Your (client) IP address: 0.0.0.0
        self.packet += b'\x00\x00\x00\x00'  # Next server IP address: 0.0.0.0
        self.packet += b'\x00\x00\x00\x00'  # Relay agent IP address: 0.0.0.0
        self.packet += self.packed_mac

        # Client hardware address padding: 00000000000000000000
        self.packet += b'\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'

        self.packet += b'\x00' * 67  # Server host name not given
        self.packet += b'\x00' * 125  # Boot file name not given
        self.packet += b'\x63\x82\x53\x63'  # Magic cookie: DHCP

        # Option: (t=53,l=1) DHCP Message Type = DHCP Discover
        self.packet += b'\x35\x01\x01'

        self.packet += b'\x3d\x06' + self.packed_mac

        # Option: (t=55,l=3) Parameter Request List
        self.packet += b'\x37\x03\x03\x01\x06'

        self.packet += b'\xff'   # End Option


class DHCPOfferPacket:
    """A representation of a DHCP_OFFER packet."""

    def __init__(self, data):
        self.transaction_ID = data[4:8]
        self.dhcp_server_ID = socket.inet_ntoa(data[245:249])


# UDP ports for the BOOTP protocol.  Used for discovery requests.
BOOTP_SERVER_PORT = 67
BOOTP_CLIENT_PORT = 68

# ioctl request for requesting IP address.
SIOCGIFADDR = 0x8915

# ioctl request for requesting hardware (MAC) address.
SIOCGIFHWADDR = 0x8927


def get_interface_MAC(sock, interface):
    """Obtain a network interface's MAC address, as a string."""
    ifreq = struct.pack(b'256s', interface.encode('ascii')[:15])
    info = fcntl.ioctl(sock.fileno(), SIOCGIFHWADDR, ifreq)
    mac = ''.join('%02x:' % char for char in info[18:24])[:-1]
    return mac


def get_interface_IP(sock, interface):
    """Obtain an IP address for a network interface, as a string."""
    ifreq = struct.pack(
        b'16sH14s', interface.encode('ascii')[:15],
        socket.AF_INET, b'\x00' * 14)
    info = fcntl.ioctl(sock, SIOCGIFADDR, ifreq)
    ip = struct.unpack(b'16sH2x4s8x', info)[2]
    return socket.inet_ntoa(ip)


@contextmanager
def udp_socket():
    """Open, and later close, a UDP socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # We're going to bind to the BOOTP/DHCP client socket, where dhclient may
    # also be listening, even if it's operating on a different interface!
    # The SO_REUSEADDR option makes this possible.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        yield sock
    finally:
        sock.close()


def request_dhcp(interface):
    """Broadcast a DHCP discovery request.  Return DHCP transaction ID."""
    with udp_socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        mac = get_interface_MAC(sock, interface)
        bind_address = get_interface_IP(sock, interface)
        discover = DHCPDiscoverPacket(mac)
        sock.bind((bind_address, BOOTP_CLIENT_PORT))
        sock.sendto(discover.packet, ('<broadcast>', BOOTP_SERVER_PORT))
    return discover.transaction_ID


def receive_offers(transaction_id):
    """Receive DHCP offers.  Return set of offering servers."""
    servers = set()
    with udp_socket() as sock:
        # The socket we use for receiving DHCP offers must be bound to IF_ANY.
        sock.bind(('', BOOTP_CLIENT_PORT))
        try:
            while True:
                sock.settimeout(3)
                data = sock.recv(1024)
                offer = DHCPOfferPacket(data)
                if offer.transaction_ID == transaction_id:
                    servers.add(offer.dhcp_server_ID)
        except socket.timeout:
            # No more offers.  Done.
            return servers


def probe_dhcp(interface):
    """Look for a DHCP server on the network.

    This must be run with provileges to broadcast from the BOOTP port, which
    typically requires root.  It may fail to bind to that port if a DHCP client
    is running on that same interface.

    :param interface: Network interface name, e.g. "eth0", attached to the
        network you wish to probe.
    :return: Set of discovered DHCP servers.

    :exception IOError: If the interface does not have an IP address.
    """
    # There is a small race window here, after we close the first socket and
    # before we bind the second one.  Hopefully executing a few lines of code
    # will be faster than communication over the network.
    # UDP is not reliable at any rate.  If detection is important, we should
    # send out repeated requests.
    transaction_id = request_dhcp(interface)
    return receive_offers(transaction_id)


def probe_interface(interface):
    """Probe the given interface for DHCP servers.

    :param interface: interface name
    :return: A set of IP addresses of detected servers.

    :note: Any servers running on the IP address of the local host are
        filtered out as they will be the MAAS DHCP server.
    """
    try:
        servers = probe_dhcp(interface)
    except IOError as e:
        servers = set()
        if e.errno == errno.EADDRNOTAVAIL:
            # Errno EADDRNOTAVAIL is "Cannot assign requested address"
            # which we need to ignore; it means the interface has no IP
            # and there's no need to scan this interface as it's not in
            # use.
            maaslog.debug(
                "Ignoring DHCP scan for %s, it has no IP address", interface)
        elif e.errno == errno.ENODEV:
            # Errno ENODEV is "no such device". This seems an odd situation
            # since we're scanning detected devices, so this is probably
            # a bug.
            maaslog.error(
                "Ignoring DHCP scan for %s, it no longer exists. Check "
                "your cluster interfaces configuration.", interface)
        else:
            raise
    return servers
