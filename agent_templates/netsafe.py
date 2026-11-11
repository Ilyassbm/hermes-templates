"""Fetching a template from a link, without letting the link reach anywhere it should not.

The page and the REST backend run inside the gateway, so a pasted URL is fetched
*from the box*. Without a guard, ``http://169.254.169.254/…`` or
``http://127.0.0.1:9119/…`` would be read on behalf of whoever can open the page.

Two postures, because the two callers are different people:

* ``restricted=True`` (the page): ``https`` only, and the target must be a public
  host. Every redirect hop is checked again, so a public URL cannot bounce the
  request to an internal one.
* ``restricted=False`` (the CLI): the person typing the command is on their own
  machine and may legitimately install from their LAN or a local server, so
  private hosts are allowed — but the scheme is still http(s) only, credentials in
  the URL are refused, redirects are capped, and an https link can never be
  downgraded to http.

Public-host check: Hermes' own ``tools.url_safety.is_safe_url`` when present (the
documented plugin surface, it also blocks cloud-metadata names and ambiguous
authorities), a local equivalent otherwise.

Known limit, stated rather than hidden: the name is resolved once for the check and
once by the connection, so a DNS answer that changes between the two is not caught.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.request
from urllib.parse import urlparse

from .errors import TemplateError

MAX_REDIRECTS = 3
USER_AGENT = "hermes-templates/1"

_METADATA_HOSTS = frozenset({
    "metadata.google.internal", "metadata.goog", "instance-data", "instance-data.ec2.internal",
})


class UnsafeUrl(TemplateError):
    """The link points somewhere this installer will not fetch from."""


def _host_is_public(host: str) -> bool:
    host = host.strip("[]").rstrip(".").lower()
    if not host or host in _METADATA_HOSTS or host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        except ValueError:
            return False
        mapped = getattr(ip, "ipv4_mapped", None)
        ip = mapped or ip
        if not ip.is_global:
            return False
    return True


def is_public_url(url: str) -> bool:
    try:
        from tools.url_safety import is_safe_url  # documented public plugin surface

        return bool(is_safe_url(url))
    except ImportError:
        pass
    except Exception:
        return False
    parsed = urlparse(url)
    return bool(parsed.hostname) and _host_is_public(parsed.hostname)


def check_url(url: str, *, restricted: bool = False) -> str:
    """Return *url* unchanged, or raise :class:`UnsafeUrl` saying why not."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrl("only http(s) links can be installed from")
    if not parsed.hostname:
        raise UnsafeUrl("that link has no host")
    if parsed.username or parsed.password:
        raise UnsafeUrl("links with a user name or password in them are refused")
    if restricted:
        if parsed.scheme != "https":
            raise UnsafeUrl("links must start with https://")
        if not is_public_url(url):
            raise UnsafeUrl("that link points to a private or internal address")
    return url


class _GuardedRedirects(urllib.request.HTTPRedirectHandler):
    """Re-check every hop; refuse a downgrade to http and more than MAX_REDIRECTS."""

    def __init__(self, restricted: bool):
        self.restricted = restricted
        self.hops = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.hops += 1
        if self.hops > MAX_REDIRECTS:
            raise UnsafeUrl(f"too many redirects (more than {MAX_REDIRECTS})")
        check_url(newurl, restricted=self.restricted)
        if urlparse(req.full_url).scheme == "https" and urlparse(newurl).scheme != "https":
            raise UnsafeUrl("refused a redirect from https to http")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url: str, *, timeout: int = 30, restricted: bool = False):
    """Open *url* through the guard. The caller closes the response."""
    check_url(url, restricted=restricted)
    opener = urllib.request.build_opener(_GuardedRedirects(restricted))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return opener.open(request, timeout=timeout)
