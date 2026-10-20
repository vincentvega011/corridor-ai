"""Merkle trees over ledger entry hashes (RFC 6962 style, docs/spec/modules.md 5.10).

Leaves are domain separated from inner nodes (0x00 and 0x01 prefixes) so a leaf can never be
mistaken for a node. The tree splits at the largest power of two below ``n``, so there is no
duplicated last leaf. A root summarises a day or a session of entries; an inclusion proof shows
that one entry hash is under a root without revealing the other entries.
"""

import hashlib

EMPTY_ROOT = hashlib.sha256(b"").hexdigest()


def _leaf(entry_hash: str) -> bytes:
    return hashlib.sha256(b"\x00" + bytes.fromhex(entry_hash)).digest()


def _node(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def _split(n: int) -> int:
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def _root(leaves: list[bytes]) -> bytes:
    if len(leaves) == 1:
        return leaves[0]
    k = _split(len(leaves))
    return _node(_root(leaves[:k]), _root(leaves[k:]))


def merkle_root(entry_hashes: list[str]) -> str:
    """Root hash (hex) over ``entry_hashes`` in order. An empty list has a fixed empty root."""
    if not entry_hashes:
        return EMPTY_ROOT
    return _root([_leaf(h) for h in entry_hashes]).hex()


def _path(leaves: list[bytes], index: int) -> list[tuple[str, bytes]]:
    if len(leaves) == 1:
        return []
    k = _split(len(leaves))
    if index < k:
        return [*_path(leaves[:k], index), ("right", _root(leaves[k:]))]
    return [*_path(leaves[k:], index - k), ("left", _root(leaves[:k]))]


def inclusion_proof(entry_hashes: list[str], index: int) -> list[tuple[str, str]]:
    """Proof for ``entry_hashes[index]`` as ``(side, sibling_hash_hex)`` pairs, leaf to root.

    ``side`` says on which side the sibling sits ("left" or "right")."""
    if not 0 <= index < len(entry_hashes):
        raise IndexError("index outside the leaves")
    path = _path([_leaf(h) for h in entry_hashes], index)
    return [(side, sib.hex()) for side, sib in path]


def verify_inclusion(entry_hash: str, proof: list[tuple[str, str]], root: str) -> bool:
    """True if ``entry_hash`` with ``proof`` hashes up to ``root``."""
    try:
        acc = _leaf(entry_hash)
        for side, sibling in proof:
            sib = bytes.fromhex(sibling)
            if side == "left":
                acc = _node(sib, acc)
            elif side == "right":
                acc = _node(acc, sib)
            else:
                return False
        return acc.hex() == root
    except ValueError:
        return False
