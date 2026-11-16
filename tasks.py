"""Task implementations for nero-compute.

Each task is a function fn(item, ctx) -> JSON-serialisable result.
ctx carries: "payload" (dir with read-only work material, may be ""),
"params" (spec-level parameters), "cache" (per-process dict).

To add a new kind of batch job: write a function here, then write a spec
JSON under jobs/ naming it in "task". No workflow changes needed.
"""
import hashlib
import os


def _load_engine(ctx):
    """Load a chess engine module from the payload dir (cached per process).

    The engine is expected to be a Python file whose top level defines a
    class E with methods fen(str), gen(), make(move) -> bool, unmake().
    It is executed in an isolated namespace with __name__ != "__main__",
    so its own entry point does not run.
    """
    if "engine_cls" in ctx["cache"]:
        return ctx["cache"]["engine_cls"]
    rel = ctx["params"].get("engine", "main.py")
    path = os.path.join(ctx["payload"], rel) if ctx["payload"] else rel
    ns = {"__name__": "payload_engine"}
    with open(path, "rb") as f:
        code = f.read()
    exec(compile(code, path, "exec"), ns)
    ctx["cache"]["engine_cls"] = ns["E"]
    return ns["E"]


def chess_perft(item, ctx):
    """Count leaf nodes of the legal move tree (perft) for a FEN position.

    item: {"fen": "...", "depth": int}
    Returns the exact node count (int). Pure CPU, fully deterministic:
    the same item always yields the same number on any machine.
    """
    E = _load_engine(ctx)
    e = E()
    e.fen(item["fen"])

    def perft(d):
        if d == 0:
            return 1
        n = 0
        for m in e.gen():
            if e.make(m):          # make() returns False for illegal moves
                n += perft(d - 1)  # (king left in check) and undoes them
                e.unmake()
        return n

    return perft(int(item["depth"]))


def _load_engine_url(ctx, cache_key, url):
    """Fetch an engine source file from a URL and exec it in an isolated
    namespace (cached per process). Returns the module namespace."""
    if cache_key in ctx["cache"]:
        return ctx["cache"][cache_key]
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "nero-compute"})
    with urllib.request.urlopen(req, timeout=120) as r:
        code = r.read()
    ns = {"__name__": "match_engine_" + cache_key}
    exec(compile(code, url, "exec"), ns)
    ctx["cache"][cache_key] = ns
    return ns


# --- chess_match: head-to-head engine gauntlet -----------------------------
# Self-contained replica of the tiny-chess v2work harness semantics:
# games start from the standard start position, book openings of 8 plies,
# max 200 plies, threefold / fifty-move / insufficient-material draws,
# material adjudication (margin 5 pawns) at the ply cap. Engines are
# driven through E.choose(fen, moves, start_fen).

_MC_START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_MC_VALS = [0, 100, 320, 330, 500, 900, 0]


def _mc_uci(m):
    def sn(sq):
        return "abcdefgh"[sq & 7] + str((sq >> 4) + 1)
    s = sn(m & 127) + sn((m >> 7) & 127)
    p = (m >> 14) & 7
    return s + " pnbrq"[p] if p else s


def _mc_fen_of(e, plies):
    rows = []
    for r in range(7, -1, -1):
        row, empty = "", 0
        for f in range(8):
            pc = e.b[r * 16 + f]
            if not pc:
                empty += 1
                continue
            if empty:
                row += str(empty)
                empty = 0
            ch = "pnbrqk"[(pc & 7) - 1]
            row += ch.upper() if pc < 8 else ch
        if empty:
            row += str(empty)
        rows.append(row)
    cast = "".join(c for c, bit in zip("KQkq", (1, 2, 4, 8)) if e.c & bit) or "-"
    ep = "-" if e.ep < 0 else "abcdefgh"[e.ep & 7] + str((e.ep >> 4) + 1)
    return "%s %s %s %s %d %d" % ("/".join(rows), "w" if e.s == 0 else "b",
                                  cast, ep, e.hm, plies // 2 + 1)


def _mc_dead_material(e):
    pieces = []
    for sq in range(128):
        if sq & 0x88:
            continue
        pc = e.b[sq]
        if pc and (pc & 7) != 6:
            pieces.append((pc & 7, pc >> 3, sq))
    if any(t in (1, 4, 5) for t, _, _ in pieces):
        return False
    if len(pieces) <= 1:
        return True
    if len(pieces) == 2 and all(t == 3 for t, _, _ in pieces):
        (t1, c1, s1), (t2, c2, s2) = pieces
        if c1 != c2 and ((s1 >> 4) + (s1 & 7)) % 2 == ((s2 >> 4) + (s2 & 7)) % 2:
            return True
    return False


def _mc_material_diff(e):
    d = 0
    for sq in range(128):
        if sq & 0x88:
            continue
        pc = e.b[sq]
        if pc:
            d += _MC_VALS[pc & 7] if pc < 8 else -_MC_VALS[pc & 7]
    return d / 100.0


class _McReferee:
    def __init__(self, rules_cls):
        self.e = rules_cls()
        self.e.fen(_MC_START)
        self.moves = []
        self.keys = {self.e.key(): 1}

    def apply(self, u):
        for m in self.e.gen():
            if _mc_uci(m) == u and self.e.make(m):
                self.moves.append(u)
                k = self.e.key()
                self.keys[k] = self.keys.get(k, 0) + 1
                return True
        return False

    def legal_ucis(self):
        out = []
        for m in self.e.gen():
            if self.e.make(m):
                out.append(_mc_uci(m))
                self.e.unmake()
        return out

    def fen(self):
        return _mc_fen_of(self.e, len(self.moves))


def _mc_play_game(cls_w, cls_b, opening_moves, bud, max_plies):
    """Returns (result, reason, plies, nodes): result from White's view
    (1.0 white win, 0.5 draw, 0.0 black win)."""
    ref = _McReferee(cls_w)
    eng = {0: cls_w(), 1: cls_b()}
    if bud is not None:
        for e in eng.values():
            e.bud = bud
    for u in opening_moves:
        assert ref.apply(u), "bad opening move " + u
    nodes = 0
    while True:
        side = ref.e.s
        if len(ref.moves) >= max_plies:
            d = _mc_material_diff(ref.e)
            if d >= 5:
                return 1.0, "adjudication", len(ref.moves), nodes
            if d <= -5:
                return 0.0, "adjudication", len(ref.moves), nodes
            return 0.5, "adjudication", len(ref.moves), nodes
        legal = ref.legal_ucis()
        if not legal:
            if ref.e.chk():
                return (0.0 if side == 0 else 1.0), "checkmate", len(ref.moves), nodes
            return 0.5, "stalemate", len(ref.moves), nodes
        if ref.e.hm >= 100:
            return 0.5, "fifty_moves", len(ref.moves), nodes
        if ref.keys[ref.e.key()] >= 3:
            return 0.5, "threefold", len(ref.moves), nodes
        if _mc_dead_material(ref.e):
            return 0.5, "insufficient_material", len(ref.moves), nodes
        e = eng[side]
        mv = e.choose(ref.fen(), list(ref.moves), _MC_START)
        nodes += e.n
        if mv not in legal:
            return (0.0 if side == 0 else 1.0), "illegal_move:" + str(mv), \
                len(ref.moves), nodes
        ref.apply(mv)


def chess_match(item, ctx):
    """Play one colour-swapped pair of games: engine A vs engine B over one
    book opening. item: {"pair": int, "opening": [uci, ...]}.
    params: {"engine_a_url": str, "engine_b_url": str,
             "bud": int|null, "max_plies": int (default 200)}.
    Returns both games' scores from engine A's perspective."""
    p = ctx["params"]
    ns_a = _load_engine_url(ctx, "a", p["engine_a_url"])
    ns_b = _load_engine_url(ctx, "b", p["engine_b_url"])
    bud = p.get("bud")
    max_plies = int(p.get("max_plies", 200))
    games = []
    nodes = 0
    for a_white in (True, False):
        cls_w, cls_b = (ns_a["E"], ns_b["E"]) if a_white else (ns_b["E"], ns_a["E"])
        res, reason, plies, nd = _mc_play_game(cls_w, cls_b, item["opening"],
                                               bud, max_plies)
        nodes += nd
        games.append({"a_score": res if a_white else 1.0 - res,
                      "a_white": a_white, "reason": reason, "plies": plies})
    return {"pair": item["pair"], "games": games, "nodes": nodes}


def sha256_chain(item, ctx):
    """Demo task: iterate SHA-256 over its own digest `rounds` times.

    item: {"seed": "...", "rounds": int}
    Returns the final digest as hex. Pure CPU, deterministic.
    """
    h = str(item["seed"]).encode()
    for _ in range(int(item["rounds"])):
        h = hashlib.sha256(h).digest()
    return h.hex()
