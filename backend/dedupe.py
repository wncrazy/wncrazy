"""Groups near-duplicate photos together using perceptual-hash hamming distance."""

from . import config
from .quality import hamming_distance


class _UnionFind:
    def __init__(self, ids):
        self.parent = {i: i for i in ids}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def group_duplicates(photos: list[dict], threshold: int = config.DUPLICATE_PHASH_THRESHOLD) -> dict[int, int]:
    """
    photos: list of dicts with at least 'id' and 'phash'.
    Returns {photo_id: group_id}. Photos with no near-duplicates get their own singleton group.
    """
    valid = [p for p in photos if p.get("phash")]
    uf = _UnionFind([p["id"] for p in valid])

    for i in range(len(valid)):
        for j in range(i + 1, len(valid)):
            a, b = valid[i], valid[j]
            if hamming_distance(a["phash"], b["phash"]) <= threshold:
                uf.union(a["id"], b["id"])

    root_to_group = {}
    result = {}
    next_group_id = 1
    for p in valid:
        root = uf.find(p["id"])
        if root not in root_to_group:
            root_to_group[root] = next_group_id
            next_group_id += 1
        result[p["id"]] = root_to_group[root]

    return result
