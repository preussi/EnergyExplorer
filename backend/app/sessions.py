"""Per-user dataset sessions.

Building a dataset creates a **session**: a short URL-safe id plus a *recipe* on
disk saying how to rebuild it (which polytope, how many samples, which seed). The
generated cloud itself is never written — hit-and-run is seeded, so replaying the
recipe reproduces the same points exactly. That buys three things at once:

* **Multi-tenancy.** Every request resolves its dataset from the id it carries,
  so two users can hold two different polytopes at the same time. Nothing about
  "the current dataset" lives in module state any more.
* **Persistence.** A refresh, a redeploy or an eviction doesn't lose anyone's
  work: the id still resolves, and a cold hit regenerates from the recipe
  (~5 s at 20k samples, ~13 s at 100k).
* **Bounded memory.** A 100k-sample dataset is tens of MB once its LP caches
  warm up, so only the ``MAX_SESSIONS`` most recently used are kept resident;
  the rest cost a few KB of disk each.

Layout::

    SESSION_DIR/<id>/session.json    {source, stem|filename, n_samples, seed, name}
    SESSION_DIR/<id>/polytope.npz    uploads only (~46 KB)
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np

from .data import DATA_DIR, Dataset, friendly_name

SESSION_DIR = Path(os.environ.get("SESSION_DIR", "/app/sessions"))
# Resident datasets. Beyond this the least-recently-used are dropped from memory
# (their recipe stays on disk, so the id keeps working — it just costs a rebuild).
MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "8"))
# Recipes older than this are swept on startup. 0 disables expiry.
SESSION_TTL_DAYS = float(os.environ.get("SESSION_TTL_DAYS", "30"))

# Session ids are used as directory names — never trust one from a request until
# it matches this. (Rejects "..", "/", and anything else that could escape.)
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")

_resident: OrderedDict[str, Dataset] = OrderedDict()   # id -> dataset, LRU order
_lock = threading.Lock()                                # guards _resident
_build_locks: dict[str, threading.Lock] = {}            # id -> rebuild lock


class UnknownSession(KeyError):
    """No such session id (never existed, or its recipe was swept)."""


def _dir(sid: str) -> Path:
    if not _ID_RE.match(sid):
        raise UnknownSession(sid)
    return SESSION_DIR / sid


def _build_lock(sid: str) -> threading.Lock:
    with _lock:
        return _build_locks.setdefault(sid, threading.Lock())


def _remember(sid: str, ds: Dataset) -> None:
    """Make `ds` resident, evicting the least-recently-used over the cap."""
    with _lock:
        _resident[sid] = ds
        _resident.move_to_end(sid)
        while len(_resident) > MAX_SESSIONS:
            old, _ = _resident.popitem(last=False)
            print(f"[sessions] evicted {old} from memory (cap {MAX_SESSIONS})", flush=True)


def create(poly, *, source: str, n_samples: int, name: str,
           stem: str | None = None, poly_bytes: bytes | None = None,
           seed: int = 42) -> tuple[str, Dataset]:
    """Generate the dataset, persist its recipe, and return ``(id, dataset)``.

    `poly` is the already-validated polytope mapping. For uploads pass the raw
    `poly_bytes` too — an uploaded polytope exists nowhere else, so it has to be
    stored for the session to survive a restart.
    """
    ds = Dataset(poly, name=name, n_samples=n_samples, seed=seed)
    sid = secrets.token_urlsafe(9)
    d = _dir(sid)
    d.mkdir(parents=True, exist_ok=True)
    if poly_bytes is not None:
        (d / "polytope.npz").write_bytes(poly_bytes)
    (d / "session.json").write_text(json.dumps({
        "source": source, "stem": stem, "n_samples": int(n_samples),
        "seed": int(seed), "name": name, "created": time.time(),
    }))
    _remember(sid, ds)
    return sid, ds


def get(sid: str) -> Dataset:
    """Resolve a session id to its dataset, rebuilding from the recipe if it is
    no longer resident. Raises :class:`UnknownSession` if the id is unknown."""
    with _lock:
        ds = _resident.get(sid)
        if ds is not None:
            _resident.move_to_end(sid)          # touch: it is the most recent now
            return ds

    d = _dir(sid)
    recipe_file = d / "session.json"
    if not recipe_file.is_file():
        raise UnknownSession(sid)

    # One rebuild at a time per id: without this, N concurrent requests arriving
    # after an eviction would each regenerate the same multi-second cloud.
    with _build_lock(sid):
        with _lock:                              # another thread may have won
            ds = _resident.get(sid)
            if ds is not None:
                _resident.move_to_end(sid)
                return ds
        try:
            recipe = json.loads(recipe_file.read_text())
        except (OSError, ValueError) as e:
            raise UnknownSession(sid) from e

        if recipe.get("source") == "upload":
            poly_path = d / "polytope.npz"
        else:
            poly_path = DATA_DIR / f"{recipe.get('stem')}.npz"
        if not poly_path.is_file():
            raise UnknownSession(sid)            # the polytope went away under us

        print(f"[sessions] rebuilding {sid} from recipe "
              f"({recipe.get('n_samples')} samples)", flush=True)
        ds = Dataset(np.load(poly_path, allow_pickle=True),
                     name=recipe.get("name") or friendly_name(str(recipe.get("stem"))),
                     n_samples=int(recipe["n_samples"]), seed=int(recipe.get("seed", 42)))
        _remember(sid, ds)
        return ds


def exists(sid: str) -> bool:
    try:
        return sid in _resident or (_dir(sid) / "session.json").is_file()
    except UnknownSession:
        return False


def sweep() -> int:
    """Delete recipes older than ``SESSION_TTL_DAYS``. Returns how many went."""
    if SESSION_TTL_DAYS <= 0 or not SESSION_DIR.is_dir():
        return 0
    cutoff = time.time() - SESSION_TTL_DAYS * 86400
    gone = 0
    for d in SESSION_DIR.iterdir():
        f = d / "session.json"
        if not f.is_file():
            continue
        try:
            if json.loads(f.read_text()).get("created", 0) < cutoff:
                for child in d.iterdir():
                    child.unlink()
                d.rmdir()
                gone += 1
        except (OSError, ValueError):
            continue
    if gone:
        print(f"[sessions] swept {gone} session(s) older than {SESSION_TTL_DAYS}d", flush=True)
    return gone


def stats() -> dict:
    on_disk = len([d for d in SESSION_DIR.iterdir() if (d / "session.json").is_file()]) \
        if SESSION_DIR.is_dir() else 0
    return {"resident": len(_resident), "on_disk": on_disk, "max_resident": MAX_SESSIONS}
