import math  
from typing import List, Dict, Any  
  
class NodeAllocator:  
    """Encapsulates all strategies for mapping nodes to applications."""  

    @staticmethod
    def _apply_largest_remainder(total_items: int, percentages: List[float]) -> List[int]:
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
    def get_abs_split(split_str: str, num_apps: int, num_nodes: int) -> List[int]:
        """Calculates absolute node counts based on percentage or equal split."""
        if split_str == 'e':
            split_list = [100.0 / num_apps] * num_apps
        else:
            split_list = [float(x) for x in split_str.split(':')]

        if sum(split_list) > 100.1: # float tolerance
            raise Exception("Splits percentages exceed 100.")
        
        # Pad with zeros if fewer splits are provided than apps
        while len(split_list) < num_apps:
            split_list.append(0.0)
            
        split_list = split_list[:num_apps]
        return NodeAllocator._apply_largest_remainder(num_nodes, split_list)
  
    @staticmethod  
    def allocate_linear(apps: List[Any], node_list: List[str], split_counts: List[int]):  
        """Allocates contiguous blocks of nodes to applications."""  
        idx = 0  
        for app, count in zip(apps, split_counts):  
            app.set_nodes(node_list[idx : idx + count])  
            idx += count  
  
    @staticmethod  
    def allocate_interleaved(apps: List[Any], node_list: List[str], split_counts: List[int]):  
        """Allocates nodes in a round-robin fashion."""  
        num_apps = len(apps)  
        alloc_lists = [[] for _ in range(num_apps)]  
        counts_copy = list(split_counts)  
          
        app_idx = 0  
        node_idx = 0  
          
        # While there are nodes to assign and demand exists  
        while any(counts_copy) and node_idx < len(node_list):  
            if counts_copy[app_idx] > 0:  
                alloc_lists[app_idx].append(node_list[node_idx])  
                counts_copy[app_idx] -= 1  
                node_idx += 1  
            app_idx = (app_idx + 1) % num_apps  
  
        for app, a_list in zip(apps, alloc_lists):  
            app.set_nodes(a_list)  
  
    @staticmethod  
    def allocate_partitioned(apps: List[Any], node_list: List[str], options: Dict[str, Any]):  
        """  
        Advanced allocation: divides nodes into partitions (Victim/Aggressor)   
        and applies sub-rules (Shared vs Dedicated) within partitions.  
        """  
        num_nodes = len(node_list)  
        partition_split = options.get('partitionsplit', '100')  
        layout = options.get('partitionlayout', 'l')  
        local_rules = [x.strip() for x in options.get('allocationsplit', 'e').split('-')]  
  
        # 1. Determine Partition Sizes  
        if partition_split == 'e':  
            # Auto-detect based on app partition_ids  
            used_ids = set(getattr(a, 'partition_id', 0) for a in apps)  
            max_p = max(used_ids) + 1 if used_ids else 1  
            percs = [100.0 / max_p] * max_p
        else:  
            percs = [float(x) for x in partition_split.split(':')]  
            
        pt_counts = NodeAllocator._apply_largest_remainder(num_nodes, percs)

        # 2. Assign nodes to Partitions (Linear vs Interleaved)  
        partitions_nodes = [[] for _ in range(len(pt_counts))]  
          
        if layout == 'i':  
            node_idx = 0  
            while node_idx < num_nodes:  
                for p_idx in range(len(pt_counts)):  
                    if len(partitions_nodes[p_idx]) < pt_counts[p_idx]:  
                        partitions_nodes[p_idx].append(node_list[node_idx])  
                        node_idx += 1  
                        if node_idx >= num_nodes: break  
        else:  
            idx = 0  
            for p_idx, count in enumerate(pt_counts):  
                partitions_nodes[p_idx] = node_list[idx : idx + count]  
                idx += count  
  
        # 3. Apply Local Rules to Apps in each Partition  
        if len(local_rules) == 1 and len(partitions_nodes) > 1:  
            local_rules = local_rules * len(partitions_nodes)  
  
        for p_id, (p_nodes, p_rule) in enumerate(zip(partitions_nodes, local_rules)):  
            p_apps = [a for a in apps if getattr(a, 'partition_id', 0) == p_id]  
            if not p_apps: continue  
  
            # Shared Mode: single app always gets the full partition regardless of
            # the sub-split rule (applying a 2-way split to 1 app is meaningless).
            if len(p_apps) == 1 or p_rule == '100' or (p_rule == 'e' and len(p_apps) <= 1):
                for app in p_apps:
                    app.set_nodes(p_nodes)
            else:
                # Space Sharing within partition
                sub_split = NodeAllocator.get_abs_split(p_rule, len(p_apps), len(p_nodes))
                NodeAllocator.allocate_linear(p_apps, p_nodes, sub_split)
