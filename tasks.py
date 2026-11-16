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


def sha256_chain(item, ctx):
    """Demo task: iterate SHA-256 over its own digest `rounds` times.

    item: {"seed": "...", "rounds": int}
    Returns the final digest as hex. Pure CPU, deterministic.
    """
    h = str(item["seed"]).encode()
    for _ in range(int(item["rounds"])):
        h = hashlib.sha256(h).digest()
    return h.hex()
