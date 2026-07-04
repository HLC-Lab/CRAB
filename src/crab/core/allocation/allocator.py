import math
import random
from typing import Any


class NodeAllocator:
    """Encapsulates all strategies for mapping nodes to applications."""

    @staticmethod
    def _apply_largest_remainder(total_items: int, percentages: list[float]) -> list[int]:
        """Distributes integer items based on percentages using the Largest Remainder Method."""
        exact_shares = [total_items * (p / 100.0) for p in percentages]
        base_alloc = [int(math.floor(share)) for share in exact_shares]

        # Calculate the deficit (remainder) for each index
        remainders = [(i, exact_shares[i] - base_alloc[i]) for i in range(len(exact_shares))]
        missing = total_items - sum(base_alloc)

        # Sort by largest remainder descending
        remainders.sort(key=lambda x: x[1], reverse=True)

        # Distribute the missing nodes to those closest to a whole node.
        # Clamp to len(remainders): if percentages don't sum to 100, missing can
        # exceed the number of buckets — don't try to assign unaccounted nodes.
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

        while len(split_list) < num_apps:
            split_list.append(0.0)
        split_list = split_list[:num_apps]
        return NodeAllocator._apply_largest_remainder(num_nodes, split_list)

    @staticmethod
    def allocate_linear(apps: list[Any], node_list: list[str], split_counts: list[int]):
        """Allocates contiguous blocks of nodes to applications."""
        idx = 0
        for app, count in zip(apps, split_counts, strict=False):
            app.set_nodes(node_list[idx : idx + count])
            idx += count

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
        apps: list[Any], node_list: list[str], split_counts: list[int], seed: int = None
    ):
        """Shuffles nodes (optionally seeded) then applies linear allocation."""
        shuffled = list(node_list)
        random.Random(seed).shuffle(shuffled)
        NodeAllocator.allocate_linear(apps, shuffled, split_counts)

    @staticmethod
    def allocate_partitioned(apps: list[Any], node_list: list[str], allocation: dict[str, Any]):
        """
        Divides nodes into named partitions then allocates apps within each.
        `allocation` must contain a 'partitions' dict: {name: {share?, mode?, split?, stride?, seed?}}.
        Top-level 'mode' controls how partition node-blocks are laid out (linear/interleaved/random).
        Apps are matched to partitions via app.partition_id (string name).
        """
        if "partitions" not in allocation:
            raise ValueError("allocation dict must contain a 'partitions' key.")
        partitions_cfg = allocation["partitions"]
        layout_mode = allocation.get("mode", "linear")
        partition_names = list(partitions_cfg.keys())

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
            shuffled = list(node_list)
            random.Random(allocation.get("seed")).shuffle(shuffled)
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
            p_split = NodeAllocator.get_abs_split(
                p_cfg.get("split", "even"), len(p_apps), len(p_nodes)
            )
            if p_mode == "interleaved":
                NodeAllocator.allocate_interleaved(
                    p_apps, p_nodes, p_split, stride=p_cfg.get("stride", 1)
                )
            elif p_mode == "random":
                NodeAllocator.allocate_random(p_apps, p_nodes, p_split, seed=p_cfg.get("seed"))
            else:
                NodeAllocator.allocate_linear(p_apps, p_nodes, p_split)
