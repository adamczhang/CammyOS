"""Find Cammy's portal on the home network.

The camera takes its address from the router by DHCP. On 2026-11-24 it came back at a
new address and another device took the old one: C3, the CCC window and the tools all
pointed at the old address, so Cammy booted to a face that no PC ever reached.

C3 now keeps the last address that answered in private/c3/portal-address. When that
address stops answering, it sweeps the PC's own subnet for port 8787 and asks each open
one for /health; the camera answers {"service": "cammy-portal"}. A /22 sweep (1022
hosts) takes about 1-2 s with 128 parallel connects.
"""
import concurrent.futures
import ipaddress
import json
import logging
import socket
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger('c3.discover')

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / 'private/c3/portal-address'
DEFAULT = 'http://192.168.10.1:8787'   # the camera's own access point (fallback Wi-Fi)


def portal():
    """Best known portal URL without network traffic: the saved address, else DEFAULT."""
    return cached() or DEFAULT


def hosts():
    """Camera host names to try: the saved address, then the camera's access point."""
    return tuple(dict.fromkeys([urlsplit(portal()).hostname, urlsplit(DEFAULT).hostname]))
PORT = 8787
CONNECT_S = 0.4
HEALTH_S = 2.0
WORKERS = 128
MAX_HOSTS = 4094         # never sweep more than a /20


def is_portal(url, timeout=HEALTH_S):
    try:
        with urllib.request.urlopen(url.rstrip('/') + '/health', timeout=timeout) as r:
            return json.loads(r.read(4096)).get('service') == 'cammy-portal'
    except (OSError, ValueError, AttributeError):
        return False


def cached(cache=CACHE):
    try:
        url = cache.read_text().strip()
    except OSError:
        return None
    parts = urlsplit(url)
    return url if parts.scheme == 'http' and parts.hostname and parts.port else None


def remember(url, cache=CACHE):
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix('.tmp')
        tmp.write_text(url + '\n')
        tmp.replace(cache)
    except OSError as exc:
        log.warning('could not save the portal address: %s', exc)


def local_networks():
    """IPv4 networks of this PC's global-scope interfaces (no docker or loopback)."""
    try:
        out = subprocess.run(['ip', '-4', '-o', 'addr', 'show', 'scope', 'global'],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    nets = []
    for line in out.splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[1].startswith(('docker', 'br-', 'veth', 'virbr')):
            continue
        try:
            nets.append(ipaddress.ip_interface(fields[3]))
        except ValueError:
            continue
    return nets


def _open(host):
    try:
        with socket.create_connection((host, PORT), timeout=CONNECT_S):
            return host
    except OSError:
        return None


def sweep(networks=None, prefer=None):
    """URL of the first portal found on the local networks, or None."""
    hosts = []
    for iface in networks if networks is not None else local_networks():
        net = iface.network
        if net.num_addresses > MAX_HOSTS + 2:
            continue
        hosts += [str(h) for h in net.hosts() if h != iface.ip]
    if prefer in hosts:                       # the old address first
        hosts.remove(prefer)
        hosts.insert(0, prefer)
    with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
        for host in pool.map(_open, hosts):
            if host and is_portal('http://%s:%d' % (host, PORT)):
                return 'http://%s:%d' % (host, PORT)
    return None


def resolve(configured=None, cache=CACHE):
    """The portal URL C3 should use: an explicit --portal wins; otherwise the cached
    address if it answers, else a sweep, else the cache or the old default."""
    if configured:
        return configured
    last = cached(cache) or DEFAULT
    if is_portal(last):
        return last
    found = sweep(prefer=urlsplit(last).hostname)
    if found:
        if found != last:
            log.warning('Cammy moved: portal found at %s (was %s)', found, last)
        remember(found, cache)
        return found
    log.info('no Cammy portal on the network yet; using %s', last)
    return last
