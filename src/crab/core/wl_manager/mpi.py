import os

class wl_manager:
    # Returns a string that can be used to run command 'cmd'
    # on the nodes in 'node_list' with 'ppn' processes per node.
    def run_job(self, node_list, ppn, cmd, pre_commands=None, data_path=None):
        num_nodes=len(node_list)
        node_list_string=','.join(node_list)
        job_cmd = os.environ["CRAB_MPIRUN"] + " " + \
                  os.environ["CRAB_MPIRUN_MAP_BY_NODE_FLAG"] + " " + \
                  os.environ["CRAB_MPIRUN_ADDITIONAL_FLAGS"] + " " + \
                  os.environ["CRAB_PINNING_FLAGS"] + " " + \
                  os.environ["CRAB_MPIRUN_HOSTNAMES_FLAG"] + " " + node_list_string + " " + \
                  "-np " + str(ppn*num_nodes) + " " + cmd
        #TODO: capire se toglierlo per il logger
        # print("[DEBUG]: MPI command is: " + job_cmd)
        return job_cmd
