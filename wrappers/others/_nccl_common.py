from crab.wrappers.base import base, sizeof_fmt

class ncclbase(base):  
    metadata = [
        {'name': 'time-oop' , 'unit': 'us'  , 'conv': True }, # Runtime (out-of-place)
        {'name': 'algbw-oop', 'unit': 'GB/s', 'conv': False}, # Algorithmic Bandwidth (out-of-place)
        {'name': 'busbw-oop', 'unit': 'GB/s', 'conv': False}, # Bus Bandwidth (out-of-place)
        {'name': 'time-ip'  , 'unit': 'us'  , 'conv': True }, # Runtime (in-place)
        {'name': 'algbw-ip' , 'unit': 'GB/s', 'conv': False}, # Algorithmic Bandwidth (in-place)
        {'name': 'busbw-ip' , 'unit': 'GB/s', 'conv': False}  # Bus Bandwidth (in-place)
    ]

    def read_data(self):  # return list (size num_metrics) of variable size lists
        output = self.stdout
        rows = []
        for l in output.split('\n'):
            if not l.strip() or l.strip().startswith('#'):
                continue
            l = ' '.join(l.strip().split())
            fields = l.split(' ')
            try:
                rows.append([
                    float(fields[5]), float(fields[6]), float(fields[7]),
                    float(fields[9]), float(fields[10]), float(fields[11])
                ])
            except (IndexError, ValueError):
                continue

        if not rows:
            return [[0]] * len(self.metadata)

        # Transpose: N rows × 6 columns → 6 per-metric value lists
        return [[row[i] for row in rows] for i in range(len(self.metadata))]
    
    def get_bench_input(self):
        if "-b" not in self.args or "-e" not in self.args:
            raise ValueError("No message size specified")
        else:
            args_values = self.args.split(" ") 
            lower = sizeof_fmt(int(args_values[args_values.index('-b') + 1]))
            upper = sizeof_fmt(int(args_values[args_values.index('-e') + 1]))
            if(lower != upper):
                raise ValueError("Benchmark was called with different lower and upper bounds (-b and -e)")
            return lower
