# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# vkcache.py : lit les vulkan.bin d'Eden (cache transférable), convertit v11/v16/v17 en v18, fusionne,
# passe des clés statiques en VIDS (les clés à attribut scaled restent statiques, repli Qualcomm).
# Usage : vkcache.py stats <fichier>... | merge <sortie> <base v18> <autre>... | combine <sortie> <a> <b>
#         | vids <sortie> <source v18> | eds2 <sortie> <source v18> [--logic-op]
import struct, sys

MAGIC = b"yuzucach"
COMPUTE = 5  # Shader::Stage::Compute
GEOMETRY = 3
CKEY = 24


def parse_env(b, o):
    code_size, ntt, ntpf, ncv, ncr = struct.unpack_from("<5Q", b, o)
    o += 40
    lms, tb, start, lo, hi, vts, stage = struct.unpack_from("<7I", b, o)
    o += 28
    o += code_size
    o += ntt * 8 + ntpf * 8 + ncv * 12 + ncr * 12
    if stage == COMPUTE:
        o += 16
    else:
        o += 0x50
        if stage == GEOMETRY:
            o += 32
    if code_size > 0x100000 or ntt > 4096 or stage > 6:
        raise ValueError("env invalide")
    return o, stage


def records(b, gkey):
    """[(début, fin_envs, fin, stage0)] ; gkey = taille de la clé graphique."""
    o, out = 12, []
    while o < len(b):
        start = o
        (n,) = struct.unpack_from("<I", b, o)
        if not 1 <= n <= 5:
            raise ValueError(f"num_envs={n} @ {o}")
        o += 4
        stage0 = None
        for _ in range(n):
            o, st = parse_env(b, o)
            stage0 = st if stage0 is None else stage0
        envs_end = o
        o += CKEY if stage0 == COMPUTE else gkey
        if o > len(b):
            raise ValueError("fin tronquée")
        out.append((start, envs_end, o, stage0))
    return out


def guess_gkey(b):
    for k in range(48, 4096, 4):
        try:
            records(b, k)
            return k
        except Exception:
            continue
    raise ValueError("taille de clé introuvable")


def load(path):
    b = open(path, "rb").read()
    assert b[:8] == MAGIC, path
    ver = struct.unpack_from("<I", b, 8)[0]
    k = guess_gkey(b)
    return b, ver, k, records(b, k)


def stats(path):
    b, ver, k, recs = load(path)
    g = [r for r in recs if r[3] != COMPUTE]
    keys = {b[r[1]:r[2]] for r in recs}
    print(f"{path}: version={ver} clé={k} records={len(recs)} graphiques={len(g)} "
          f"compute={len(recs) - len(g)} clés uniques={len(keys)}")
    if ver == 18 and g:
        r = g[0]
        did, dver = struct.unpack_from("<II", b, r[1] + 64)
        print(f"  driver_id={did} driver_version={dver:#x}")


def incomplete(b, s):
    """Un env v17/v11 sans format de pixel pour chaque type de texture lu."""
    (n,) = struct.unpack_from("<I", b, s)
    o = s + 4
    for _ in range(n):
        ntt, ntpf = struct.unpack_from("<2Q", b, o + 8)
        if ntt > ntpf:
            return True
        o, _ = parse_env(b, o)
    return False


def merge(out, base, others):
    b0, v0, k0, r0 = load(base)
    assert v0 == 18
    g = [r for r in r0 if r[3] != COMPUTE][0]
    drv = b0[g[1] + 64:g[1] + 72]
    seen = {b0[r[1]:r[2]] for r in r0}
    blob = bytearray(b0)
    for path in others:
        b, v, k, recs = load(path)
        added = 0
        for s, e, f, st in recs:
            key = b[e:f]
            if v == 11 and st != COMPUTE:
                # Citron v11 : même clé que v17 sans depth_bounds ni line_stipple (16 octets de fin, nuls ici)
                assert k + 24 == k0, (k, k0)
                key = key + bytes(16)
            if v in (11, 16, 17) and st != COMPUTE:
                assert len(key) + 8 == k0, (len(key), k0)
                key = bytearray(key[:64] + drv + key[64:])
                # v18 ajoute attachment0_dual_source_blend (bit 21 de raw1), recalculé depuis l'attachement 0
                (a0,) = struct.unpack_from("<I", key, 128)
                factors = [(a0 >> sh) & 31 for sh in (10, 15, 20, 25)]
                if (a0 >> 30) & 1 and any(11 <= x <= 14 for x in factors):
                    (raw1,) = struct.unpack_from("<I", key, 48)
                    struct.pack_into("<I", key, 48, raw1 | (1 << 21))
                key = bytes(key)
            if v not in (11, 16, 17, 18):
                raise ValueError(f"version {v} non gérée")
            if v != 18 and incomplete(b, s):
                continue  # recompilé en v18, le shader lit des formats de texture absents : échec au chargement
            if key in seen:
                continue
            seen.add(key)
            blob += b[s:e] + key
            added += 1
        print(f"{path}: +{added} pipelines")
    open(out, "wb").write(blob)
    stats(out)


TOPO_LUT = [0, 1, 2, 3, 4, 4, 4, 4, 4, 4, 10, 10, 12, 12, 14]
VIDS_LUT = [0, 1, 1, 2, 3, 1, 1, 1]
ST = 48  # début de FixedPipelineState dans la clé


def to_eds2(key, logic_op, eds=True):
    """Clé EDS désactivé -> clé EDS2 + VIDS (ou VIDS seul), comme FixedPipelineState::Refresh la produirait."""
    key = bytearray(key)
    (raw1,) = struct.unpack_from("<I", key, ST)
    if raw1 & 0b100011:
        return bytes(key)  # déjà dynamique
    if not eds:
        # VIDS seul : l'état dynamique reste dans la clé, seuls les attributs deviennent attribute_types.
        # Un attribut USCALED/SSCALED garde la clé statique (repli de Refresh sur pilote Qualcomm).
        for i in range(32):
            (a,) = struct.unpack_from("<I", key, ST + 112 + 4 * i)
            if a & 1 and ((a >> 20) & 7) in (5, 6):
                return bytes(key)
        struct.pack_into("<I", key, ST, raw1 | (1 << 5))
        types = 0
        for i in range(32):
            (a,) = struct.unpack_from("<I", key, ST + 112 + 4 * i)
            if a & 1:
                types |= VIDS_LUT[(a >> 20) & 7] << (2 * i)
        struct.pack_into("<Q", key, ST + 64, types)
        key[ST + 112:ST + 368] = bytes(256)  # attributes, binding_divisors (vertex_strides hors Size())
        return bytes(key)
    raw1 |= 1 | 2 | (1 << 5) | ((1 << 2) if logic_op else 0)
    topo = (raw1 >> 24) & 15
    raw1 = (raw1 & ~(15 << 24)) | (TOPO_LUT[topo] << 24)
    struct.pack_into("<I", key, ST, raw1)
    types = 0
    for i in range(32):
        (a,) = struct.unpack_from("<I", key, ST + 112 + 4 * i)
        if a & 1:
            types |= VIDS_LUT[(a >> 20) & 7] << (2 * i)
    struct.pack_into("<Q", key, ST + 64, types)
    (d1,) = struct.unpack_from("<I", key, ST + 72)
    keep = (7 << 10) | (0 if logic_op else (15 << 6))  # logic_op_enable, depth_clamp, line_stipple (+ logic_op)
    struct.pack_into("<II", key, ST + 72, d1 & keep, 0)
    key[ST + 112:ST + 432] = bytes(320)  # attributes, binding_divisors, vertex_strides
    return bytes(key)


def eds2(out, src, logic_op, eds=True):
    b, v, k, recs = load(src)
    assert v == 18
    blob, seen = bytearray(b[:12]), set()
    for s, e, f, st in recs:
        key = b[e:f] if st == COMPUTE else to_eds2(b[e:f], logic_op, eds)
        if st != COMPUTE:
            (raw1,) = struct.unpack_from("<I", key, ST)
            assert not (raw1 >> 6) & 1, "xfb non géré"
            dedup = key[:ST + 112]  # Size() = offsetof(attributes) avec VIDS sans EDS3 blend
        else:
            dedup = key
        if dedup in seen:
            continue
        seen.add(dedup)
        blob += b[s:e] + key
    open(out, "wb").write(blob)
    stats(out)


def combine(out, a, b2):
    """Concatène deux caches v18 en écartant les clés en double."""
    b0, v0, k0, r0 = load(a)
    blob, seen = bytearray(b0[:12]), set()
    for path in (a, b2):
        b, v, k, recs = load(path)
        assert v == 18
        for s, e, f, st in recs:
            if b[e:f] in seen:
                continue
            seen.add(b[e:f])
            blob += b[s:f]
    open(out, "wb").write(blob)
    stats(out)


if __name__ == "__main__":
    if sys.argv[1] == "stats":
        for p in sys.argv[2:]:
            stats(p)
    elif sys.argv[1] == "eds2":
        eds2(sys.argv[2], sys.argv[3], "--logic-op" in sys.argv)
    elif sys.argv[1] == "combine":
        combine(sys.argv[2], sys.argv[3], sys.argv[4])
    elif sys.argv[1] == "vids":
        eds2(sys.argv[2], sys.argv[3], False, eds=False)
    else:
        merge(sys.argv[2], sys.argv[3], sys.argv[4:])
