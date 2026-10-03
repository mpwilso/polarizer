"""Reference verifier for docs/LEDGER-SPEC.md. Usage: reference_verify.py LEDGER [HEAD] -> JSON."""

import hashlib
import json
import re
import sys

import rfc8785

ZERO, MAX, CHAIN_ID = "0" * 64, 2**53 - 1, re.compile("[0-9a-f]{32}")
HEAD = re.compile(rb'\{"chain_id":"[0-9a-f]{32}","hash":"[0-9a-f]{64}","seq":(0|[1-9][0-9]*)\}\n')
V0 = {"id", "ts", "kind", "actor", "reason", "data", "prev", "hash"}
V1 = {"v": int, "seq": int, "ts": str, "kind": str, "data": dict, "prev": str, "hash": str}


def canonical(x, version=1):
    return rfc8785.dumps(x) if version else json.dumps(x, sort_keys=True).encode()


def entry_hash(e, version=1):
    body = canonical({k: e[k] for k in e if k != "hash"}, version)
    return hashlib.sha256((b"LEDGER-SPEC/1\n" if version else b"") + body).hexdigest()


def subset(x):
    if isinstance(x, dict):
        return all(k.isascii() and subset(v) for k, v in x.items())
    if isinstance(x, (list, str)):
        return all(map(subset, x)) if isinstance(x, list) else not re.search("[\ud800-\udfff]", x)
    return x is None or isinstance(x, bool) or (type(x) is int and -MAX <= x <= MAX)


def structure_ok(e, raw, n, version):
    if not isinstance(e, dict) or ("v" in e) != bool(version) or not version:  # v0: keys only
        return isinstance(e, dict) and "v" not in e and not version and set(e) == V0
    if len(raw) >= 16384 or set(e) != set(V1) or any(type(e[k]) is not t for k, t in V1.items()):
        return False
    chain_id = e["data"].get("chain_id")
    genesis_ok = n > 1 or isinstance(chain_id, str) and CHAIN_ID.fullmatch(chain_id)
    return e["v"] == 1 and subset(e) and (e["kind"] == "ledger.genesis") == (n == 1) and genesis_ok


def verify(ledger_path, head_path=None):
    lines = open(ledger_path, "rb").read().split(b"\n")
    tail, version, hashes = lines.pop(), None, []
    for n, raw in enumerate(lines, 1):
        try:
            e = json.loads(raw.decode("utf-8"))
        except ValueError:
            e = None
        version = (0 if isinstance(e, dict) and "v" not in e else 1) if version is None else version
        seq = e.get("seq") if version and isinstance(e, dict) else None
        r = {"line": n, "seq": seq if type(seq) is int else None}
        if not structure_ok(e, raw, n, version):
            return {**r, "status": "invalid", "check": "structure"}
        for check, status, good in [
            ("seq", "tampered", not version or e["seq"] == n - 1),
            ("prev", "tampered", e["prev"] == (hashes[-1] if hashes else ZERO)),
            ("hash", "tampered", e["hash"] == entry_hash(e, version)),
            ("canonical", "not canonical", raw == canonical(e, version)),
        ]:
            if not good:
                return {**r, "status": status, "check": check}
        hashes.append(e["hash"])
    if head_path:
        h = json.loads(raw) if HEAD.fullmatch(raw := open(head_path, "rb").read()) else None
        ok = h is not None and h["seq"] <= MAX
        chain = json.loads(lines[0])["data"]["chain_id"] if version and hashes else None
        r = {"line": None, "seq": h["seq"] if ok else None, "check": "head"}
        if not ok or version == 0 or (hashes and h["chain_id"] != chain):
            return {**r, "status": "invalid"}
        if (late := h["seq"] >= len(hashes)) or h["hash"] != hashes[h["seq"]]:
            return {**r, "status": "truncated" if late else "tampered"}
    r = {"line": len(lines), "seq": len(hashes) - 1 if version and hashes else None, "check": None}
    return {**r, "status": "torn tail" if tail else "intact"}


if __name__ == "__main__":
    print(json.dumps(verify(*sys.argv[1:3])))
