"""Reference anchor check for docs/EXPORT-SPEC.md, section 4.

Usage: reference_anchor.py LEDGER KEY TRAILER [TRAILER ...] -> JSON. LEDGER must already
verify intact (reference_verify.py); KEY is a checkpoint.key file; each TRAILER is a
Polarizer-Checkpoint line or its bare value, newest first. Of several, the one checked is the
furthest anchored position: the largest size of the ledger's chain (of all, if none is of it),
the newest on a tie. Standard library only, and independent of Polarizer's code."""

import hashlib
import hmac
import json
import re
import sys

VALUE = re.compile(r"([0-9a-f]{32}):([1-9][0-9]*):([0-9a-f]{64}):([0-9a-f]{64})")
KEY = re.compile(rb"[0-9a-f]{64}\n")


def sign(key, chain_id, size, head):
    message = b"POLARIZER-CHECKPOINT/1\n" + f"{chain_id}:{size}:{head}".encode("ascii")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def check(ledger_path, key_path, *trailers):
    lines = open(ledger_path, "rb").read().split(b"\n")[:-1]
    entries = [json.loads(line) for line in lines]
    chain = entries[0]["data"]["chain_id"]
    found = [VALUE.fullmatch(t.removeprefix("Polarizer-Checkpoint:").strip()) for t in trailers]
    values = [m.groups() for m in found if m]
    values = [v for v in values if v[0] == chain] or values
    chain_id, size, head, sig = sorted(values, key=lambda v: -int(v[1]))[0]  # stable: newest
    seq = int(size) - 1
    raw_key = open(key_path, "rb").read()
    assert KEY.fullmatch(raw_key), "not a checkpoint key"
    key = bytes.fromhex(raw_key[:64].decode())
    if not hmac.compare_digest(sign(key, chain_id, size, head), sig):
        return {"result": "bad signature", "seq": seq}
    if chain != chain_id:
        return {"result": "other chain", "seq": seq}
    if len(entries) <= seq:
        return {"result": "rewound", "seq": seq}
    if entries[seq]["hash"] != head:
        return {"result": "rewritten", "seq": seq}
    return {"result": "extends", "seq": seq}


if __name__ == "__main__":
    print(json.dumps(check(*sys.argv[1:])))
