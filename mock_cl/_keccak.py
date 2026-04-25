"""Pure-Python keccak-256 (Ethereum flavor — pre-NIST padding 0x01, *not* SHA3-256's 0x06).

Self-contained because the system Python lacks pycryptodome / pysha3 and
hashlib.sha3_256 is the standardized SHA3 with different padding. Only used
for deposit source-hash + L1 attribute calldata — perf is irrelevant.
"""

_RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808a, 0x8000000080008000,
    0x000000000000808b, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008a, 0x0000000000000088, 0x0000000080008009, 0x000000008000000a,
    0x000000008000808b, 0x800000000000008b, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800a, 0x800000008000000a,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)
_R = (
     0,  1, 62, 28, 27,
    36, 44,  6, 55, 20,
     3, 10, 43, 25, 39,
    41, 45, 15, 21,  8,
    18,  2, 61, 56, 14,
)
_MASK = (1 << 64) - 1


def _rotl(x: int, n: int) -> int:
    n &= 63
    return ((x << n) | (x >> (64 - n))) & _MASK


def _keccak_f(state: list) -> None:
    for rc in _RC:
        # Theta
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(0, 25, 5):
                state[x + y] ^= d[x]
        # Rho + Pi
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + ((2 * x + 3 * y) % 5) * 5] = _rotl(state[x + 5 * y], _R[x + 5 * y])
        # Chi
        for y in range(0, 25, 5):
            t = b[y:y + 5]
            for x in range(5):
                state[x + y] = t[x] ^ ((~t[(x + 1) % 5]) & t[(x + 2) % 5]) & _MASK
        # Iota
        state[0] ^= rc


def keccak256(data: bytes) -> bytes:
    rate = 136  # 1088 bits / 8
    state = [0] * 25
    # Absorb
    offset = 0
    while offset + rate <= len(data):
        for i in range(rate // 8):
            state[i] ^= int.from_bytes(data[offset + 8 * i : offset + 8 * (i + 1)], "little")
        _keccak_f(state)
        offset += rate
    # Pad and absorb final block
    remaining = data[offset:]
    block = bytearray(rate)
    block[: len(remaining)] = remaining
    block[len(remaining)] ^= 0x01  # keccak padding
    block[rate - 1] ^= 0x80
    for i in range(rate // 8):
        state[i] ^= int.from_bytes(block[8 * i : 8 * (i + 1)], "little")
    _keccak_f(state)
    # Squeeze 32 bytes
    out = bytearray()
    for i in range(4):
        out += state[i].to_bytes(8, "little")
    return bytes(out)


if __name__ == "__main__":
    # quick self-test against well-known vectors
    assert keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert keccak256(b"abc").hex() == "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"
    print("keccak256 ok")
