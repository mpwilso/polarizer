"""Argument rules (docs/TAINT-SPEC.md, sections 2 to 4): reading a URL's host, matching it with
a tool's domains, checking a domains entry, and searching argument values with deny_values.

Polarizer reads a URL's host with its own small parser, never urllib: parsers disagree on
backslashes, on more than one "@", and on numeric hosts, and each disagreement is a way for a URL
to name one host here and another to the tool. Whatever these rules don't accept is held.
"""

import functools
import ipaddress
import json
import re

from polarizer.text import safe

MAX_URL = 8192
MAX_URLS = 256  # a list argument with more URLs than this is held
MAX_HOST = 253
MAX_LABEL = 63
SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
PORT = re.compile(r"[0-9]{1,5}")
HOST_LABEL = re.compile(r"[a-z0-9_-]+")
ENTRY_LABEL = re.compile(r"[a-z0-9-]+")
HEX_NUMBER = re.compile(r"0x[0-9a-f]*")
IP_WHY = "an IP address, which never matches a domain"
REASON_LIMIT = 1024


class NotChecked(Exception):
    """A URL Polarizer can't read a host from the way every common parser would; the message
    is the fixed reason."""


def _numeric(label: str) -> bool:
    """A label URL parsers read as part of an IPv4 number: all digits, or 0x and hex digits."""
    return label.isdigit() or bool(HEX_NUMBER.fullmatch(label))


def host_of(url: str) -> str:
    """The host a URL names, lowercased, with one trailing dot removed; NotChecked otherwise
    (section 3's table, in order)."""
    if len(url) > MAX_URL:
        raise NotChecked(f"longer than {MAX_URL} characters")
    if any(ch <= " " or ch == chr(0x7F) for ch in url):
        raise NotChecked("a space or control character")
    if chr(0x5C) in url:
        raise NotChecked("a backslash")
    scheme = SCHEME.match(url)
    if scheme is None:
        raise NotChecked("not an absolute URL with a host")
    rest = url[scheme.end() :]
    authority = re.split(r"[/?#]", rest, maxsplit=1)[0]
    if authority.count("@") > 1:
        raise NotChecked('more than one "@" before the host')
    hostport = authority.split("@", 1)[1] if "@" in authority else authority
    if hostport.startswith("["):
        raise NotChecked(IP_WHY)
    host, colon, port = hostport.partition(":")
    if colon and not (PORT.fullmatch(port) and int(port) <= 65535):
        raise NotChecked("a port that is not a number from 0 to 65535")
    if not host:
        raise NotChecked("no host")
    if not host.isascii():
        raise NotChecked("a host that is not ASCII; send its xn-- form")
    host = host.lower()
    if host.endswith("."):
        host = host[:-1]
    labels = host.split(".")
    if not all(HOST_LABEL.fullmatch(label) for label in labels):
        raise NotChecked("a host that is not a host name")
    if _numeric(labels[-1]):
        raise NotChecked(IP_WHY)
    return host


def listed(host: str, domains) -> bool:
    """Whether a host read by host_of matches an entry: exactly, or for "*.<name>", any host
    with at least one label before ".<name>"."""
    for entry in domains:
        if entry.startswith("*."):
            if host.endswith(entry[1:]) and len(host) > len(entry) - 1:
                return True
        elif host == entry:
            return True
    return False


def domain_problem(entry: str) -> str | None:
    """Why a domains entry is not a host name, or None (section 2's reasons, in order). The
    entry is compared lowercased."""
    if not entry:
        return "empty"
    if len(entry) > MAX_HOST:
        return f"longer than {MAX_HOST} characters"
    if not entry.isascii():
        return "not ASCII; write it in its xn-- form"
    name = entry.lower()
    if "*" in name:
        if not name.startswith("*.") or "*" in name[2:] or len(name) == 2:
            return 'a wildcard only as a leading "*."'
        name = name[2:]
    try:
        ipaddress.ip_address(name.strip("[]"))
        return "an IP address; list host names only"
    except ValueError:
        pass
    labels = name.split(".")
    if _numeric(labels[-1]):
        return "an IP address; list host names only"
    if any(not label for label in labels):
        return "an empty label"
    if any(len(label) > MAX_LABEL for label in labels):
        return f"a label longer than {MAX_LABEL} characters"
    if not all(ENTRY_LABEL.fullmatch(label) for label in labels):
        return 'a character other than a letter, a digit or "-"'
    if any(label.startswith("-") or label.endswith("-") for label in labels):
        return 'a label that starts or ends with "-"'
    return None


def _arg(name: str) -> str:
    return f'argument "{safe(name, REASON_LIMIT)}"'


def url_hold(url_args, domains, arguments) -> str | None:
    """The reason the first URL argument holds an egress call (rows 4a), or None when every URL
    names a listed host. A call with no URL at all is held: nothing would be checked."""
    found = 0
    for arg in url_args:
        if not isinstance(arguments, dict) or arg not in arguments:
            return f"{_arg(arg)} is missing"
        value = arguments[arg]
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, list) and all(isinstance(v, str) for v in value):
            values = value
        else:
            return f"{_arg(arg)} is not a string or a list of strings"
        if len(values) > MAX_URLS:
            return f"{_arg(arg)}: more than {MAX_URLS} URLs"
        for url in values:
            found += 1
            try:
                host = host_of(url)
            except NotChecked as e:
                return f"{_arg(arg)}: {e}"
            if not listed(host, domains):
                return f"{_arg(arg)}: host {safe(host, REASON_LIMIT)} is not in this tool's domains"
    if not found:
        return "no URL in url_args to check"
    return None


@functools.lru_cache(maxsize=1024)
def compiled(pattern: str) -> re.Pattern:
    return re.compile(pattern)


def _strings(value):
    """Every string searched in a value (section 4): a string as it is; in lists and objects,
    every string at any depth, keys included, in document order; other values as JSON text.
    Walked with a stack of its own, so no depth of nesting can exhaust Python's."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            yield item
        elif isinstance(item, list):
            stack.extend(reversed(item))
        elif isinstance(item, dict):
            pairs = []
            for k, inner in item.items():
                pairs += [str(k), inner]
            stack.extend(reversed(pairs))
        else:
            yield json.dumps(item)


def value_match(deny_values, arguments) -> str | None:
    """The deny tier's reason for the first match, or None. `deny_values` is a tuple of
    (argument name, patterns), in config order; a missing argument never matches."""
    if not isinstance(arguments, dict):
        return None
    for arg, patterns in deny_values:
        if arg not in arguments:
            continue
        strings = list(_strings(arguments[arg]))
        for pattern in patterns:
            regex = compiled(pattern)
            if any(regex.search(s) for s in strings):
                shown = safe(pattern, REASON_LIMIT)
                return f"{_arg(arg)} matches the deny_values pattern {shown}"
    return None
