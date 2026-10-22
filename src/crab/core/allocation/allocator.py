import math
import random
from typing import Any

from crab.core.allocation.chains import resolve_chains

# A shuffle seed as a config gives it: an int, a string, or None for "draw one".
Seed = int | str | None


def _resolve_seed(seed: Seed) -> int | str:
    """Return a given seed unchanged; with None, draw one from the OS so it can be recorded."""
    if seed is None:
        return random.SystemRandom().randrange(2**32)
    return seed


class _Slot:
    """A chain head's place in the allocation; its nodes are copied to the real apps after."""

    def __init__(self, partition_id: Any) -> None:
        self.partition_id = partition_id
        self.nodes: list[str] = []

    def set_nodes(self, nodes: list[str]) -> None:
        self.nodes = list(nodes)


class NodeAllocator:
    """Encapsulates all strategies for mapping nodes to applications."""

    @staticmethod
    def _apply_largest_remainder(total_items: int, percentages: list[float]) -> list[int]:
        """Distributes integer items based on percentages using the Largest Remainder Method."""
        exact_shares = [total_items * (p / 100.0) for p in percentages]
        base_alloc = [int(math.floor(share)) for share in exact_shares]

        # Calculate the remainder of each index
        remainders = [(i, exact_shares[i] - base_alloc[i]) for i in range(len(exact_shares))]

        # Assign only the nodes the percentages cover (rounded half up): sums of 100 (up to
        # the 100.1 tolerance) assign every node, a smaller sum leaves the rest idle.
        target = min(total_items, math.floor(sum(exact_shares) + 0.5))
        missing = max(0, target - sum(base_alloc))

        # Sort by largest remainder descending
        remainders.sort(key=lambda x: x[1], reverse=True)

        # Hand the missing nodes to the buckets closest to a whole node.
        for i in range(min(missing, len(remainders))):
            base_alloc[remainders[i][0]] += 1

        return base_alloc

    @staticmethod
    def get_abs_split(split_val, num_apps: int, num_nodes: int) -> list[int]:
        """Calculates absolute node counts from 'even' (or 'e') or a list of percentages."""
        if num_apps == 0:
            return []
        if split_val in ("even", "e") or split_val is None:
            split_list = [100.0 / num_apps] * num_apps
        elif isinstance(split_val, str):
            raise TypeError(f"split_val must be 'even', None, or a list[float]; got {split_val!r}")
        else:
            split_list = [float(x) for x in split_val]

        if sum(split_list) > 100.1:
            raise ValueError("Split percentages exceed 100.")

        # config_checks.check_config rejects a mismatched split before any run.
        if len(split_list) != num_apps:
            raise ValueError(f"split has {len(split_list)} entries for {num_apps} apps")
        return NodeAllocator._apply_largest_remainder(num_nodes, split_list)

    @staticmethod
    def allocate_experiment(
        apps: list[Any], node_list: list[str], allocation: dict[str, Any]
    ) -> dict[str, Any]:
        """Give every app of one experiment its nodes, per the experiment's `allocation`.

        The single entry point used by the runner and by the pre-submit config checks. Only
        chain heads get a share; an app that reuses nodes (ADR-032) gets its head's nodes.
        Apps are read through `start_string` (default "0") and `partition_id`.

        Returns the allocation record: `{"mode": str, "apps": {"<index>": [nodes]}}`, plus
        `"seed"` when a top-level random shuffle ran (the given seed as given, else a drawn
        one) and `"partition_seeds": {name: seed}` when a partition shuffled. With partitions,
        `"mode"` is the top-level layout mode and `"partitioned"` is True. Passing the
        recorded seeds back reproduces the layout on the same node list.
        """
        reuse = resolve_chains(
            [str(getattr(app, "start_string", "0")) for app in apps],
            [getattr(app, "partition_id", None) for app in apps],
        )
        heads = {
            i: _Slot(getattr(app, "partition_id", None))
            for i, app in enumerate(apps)
            if i not in reuse
        }
        slots = list(heads.values())
        record: dict[str, Any] = {"mode": allocation.get("mode", "linear")}
        if "partitions" in allocation:
            record["partitioned"] = True
            record.update(NodeAllocator.allocate_partitioned(slots, node_list, allocation))
        elif allocation.get("mode") == "shared":
            NodeAllocator.allocate_shared(slots, node_list)
        else:
            mode = record["mode"]
            split = NodeAllocator.get_abs_split(
                allocation.get("split", "even"), len(slots), len(node_list)
            )
            if mode == "interleaved":
                NodeAllocator.allocate_interleaved(
                    slots, node_list, split, stride=allocation.get("stride", 1)
                )
            elif mode == "random":
                record["seed"] = NodeAllocator.allocate_random(
                    slots, node_list, split, seed=allocation.get("seed")
                )
            else:  # linear (default)
                NodeAllocator.allocate_linear(slots, node_list, split)
        record["apps"] = {}
        for i, app in enumerate(apps):
            nodes = list(heads[reuse.get(i, i)].nodes)
            app.set_nodes(nodes)
            record["apps"][str(i)] = nodes
        return record

    @staticmethod
    def allocate_linear(apps: list[Any], node_list: list[str], split_counts: list[int]):
        """Allocates contiguous blocks of nodes to applications."""
        idx = 0
        for app, count in zip(apps, split_counts, strict=False):
            app.set_nodes(node_list[idx : idx + count])
            idx += count

    @staticmethod
    def allocate_shared(apps: list[Any], node_list: list[str]):
        """Gives every application all of the nodes, in node-list order."""
        for app in apps:
            app.set_nodes(node_list)

    @staticmethod
    def allocate_interleaved(
        apps: list[Any], node_list: list[str], split_counts: list[int], stride: int = 1
    ):
        """Allocates nodes in a round-robin fashion, assigning `stride` nodes per turn."""
        if stride < 1:
            raise ValueError(f"stride must be >= 1, got {stride!r}")
        num_apps = len(apps)
        alloc_lists = [[] for _ in range(num_apps)]
        counts_copy = list(split_counts)

        app_idx = 0
        node_idx = 0

        while any(counts_copy) and node_idx < len(node_list):
            if counts_copy[app_idx] > 0:
                nodes_to_assign = min(stride, counts_copy[app_idx])
                for _ in range(nodes_to_assign):
                    alloc_lists[app_idx].append(node_list[node_idx])
                    node_idx += 1
                counts_copy[app_idx] -= nodes_to_assign
            app_idx = (app_idx + 1) % num_apps

        for app, a_list in zip(apps, alloc_lists, strict=False):
            app.set_nodes(a_list)

    @staticmethod
    def allocate_random(
        apps: list[Any], node_list: list[str], split_counts: list[int], seed: Seed = None
    ) -> int | str:
        """Shuffles nodes then applies linear allocation; returns the seed used.

        A given seed is used as is (any type `random.Random` takes); with `seed=None` one is
        drawn from the OS, so the caller can record it and replay the layout.
        """
        seed = _resolve_seed(seed)
        shuffled = list(node_list)
        random.Random(seed).shuffle(shuffled)
        NodeAllocator.allocate_linear(apps, shuffled, split_counts)
        return seed

    @staticmethod
    def allocate_partitioned(
        apps: list[Any], node_list: list[str], allocation: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Divides nodes into named partitions then allocates apps within each.
        `allocation` must contain a 'partitions' dict: {name: {share?, mode?, split?, stride?, seed?, cores?}}.
        Top-level 'mode' controls how partition node-blocks are laid out (linear/interleaved/random).
        Apps are matched to partitions via app.partition_id (string name).

        Returns the seeds used: `"seed"` for a random top-level layout and
        `"partition_seeds"` ({name: seed}) for partitions that shuffled; absent when none did.
        """
        if "partitions" not in allocation:
            raise ValueError("allocation dict must contain a 'partitions' key.")
        partitions_cfg = allocation["partitions"]
        layout_mode = allocation.get("mode", "linear")
        if layout_mode == "shared":
            raise ValueError(
                "allocation mode 'shared' cannot be combined with partitions: the top-level "
                "mode lays out the partition blocks; set mode 'shared' inside a partition"
            )
        partition_names = list(partitions_cfg.keys())
        seeds: dict[str, Any] = {}
        partition_seeds: dict[str, int | str] = {}

        # 1. Determine partition sizes
        shares = [partitions_cfg[name].get("share") for name in partition_names]
        if all(s is None for s in shares):
            percs = [100.0 / len(partition_names)] * len(partition_names)
        elif any(s is None for s in shares):
            raise ValueError(
                "Either all partitions must specify 'share' or none must. "
                f"Got mixed: {dict(zip(partition_names, shares, strict=False))}"
            )
        else:
            percs = [float(s) for s in shares]

        if sum(percs) > 100.1:
            raise ValueError(f"Partition shares sum to {sum(percs):.1f}, must not exceed 100.")

        pt_counts = NodeAllocator._apply_largest_remainder(len(node_list), percs)

        # 2. Assign nodes to partitions using layout_mode
        partitions_nodes: list[list[str]] = [[] for _ in range(len(pt_counts))]

        if layout_mode == "interleaved":
            stride = allocation.get("stride", 1)
            node_idx = 0
            while node_idx < len(node_list):
                advanced = False
                for p_idx in range(len(pt_counts)):
                    capacity = pt_counts[p_idx] - len(partitions_nodes[p_idx])
                    for _ in range(min(stride, capacity)):
                        if node_idx < len(node_list):
                            partitions_nodes[p_idx].append(node_list[node_idx])
                            node_idx += 1
                            advanced = True
                if not advanced:
                    break
        elif layout_mode == "random":
            seed = _resolve_seed(allocation.get("seed"))
            seeds["seed"] = seed
            shuffled = list(node_list)
            random.Random(seed).shuffle(shuffled)
            idx = 0
            for p_idx, count in enumerate(pt_counts):
                partitions_nodes[p_idx] = shuffled[idx : idx + count]
                idx += count
        else:  # linear
            idx = 0
            for p_idx, count in enumerate(pt_counts):
                partitions_nodes[p_idx] = node_list[idx : idx + count]
                idx += count

        # 3. Allocate apps within each partition
        for p_name, p_nodes in zip(partition_names, partitions_nodes, strict=False):
            p_cfg = partitions_cfg[p_name]
            p_apps = [a for a in apps if getattr(a, "partition_id", None) == p_name]
            if not p_apps:
                continue
            if len(p_apps) == 1:
                p_apps[0].set_nodes(p_nodes)
                continue
            p_mode = p_cfg.get("mode", "linear")
            if p_mode == "shared":
                NodeAllocator.allocate_shared(p_apps, p_nodes)
                continue
            p_split = NodeAllocator.get_abs_split(
                p_cfg.get("split", "even"), len(p_apps), len(p_nodes)
            )
            if p_mode == "interleaved":
                NodeAllocator.allocate_interleaved(
                    p_apps, p_nodes, p_split, stride=p_cfg.get("stride", 1)
                )
            elif p_mode == "random":
                partition_seeds[p_name] = NodeAllocator.allocate_random(
                    p_apps, p_nodes, p_split, seed=p_cfg.get("seed")
                )
            else:
                NodeAllocator.allocate_linear(p_apps, p_nodes, p_split)
        if partition_seeds:
            seeds["partition_seeds"] = partition_seeds
        return seeds
