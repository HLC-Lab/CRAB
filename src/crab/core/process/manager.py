import os
import subprocess  
import threading  
from typing import List  
from crab.log import CrabLogger  
  
def run_job(job, wlmanager, ppn: int, logger: CrabLogger, pre_commands: List[str] = None, live_stream: bool = False, data_path: str = None, launcher: str = None):  
    """  
    Launch an application process via the workload manager using a physical execution wrapper.
    """  
    if not job.node_list:  
        raise Exception(f"Application {job.id_num} has 0 allocated nodes.")  
      
    # 1. Get the base launcher command from the workload manager
    cmd_string = wlmanager.run_job(job.node_list, ppn, job.run_app(), pre_commands=pre_commands, data_path=data_path, launcher=launcher)  
      
    if not cmd_string:  
        cmd_string = "echo a > /dev/null"  

    # 2. Write the Execution Wrapper
    # Hide the scripts in a .wrappers folder to prevent directory pollution
    script_dir = os.path.join(data_path, ".wrappers")
    os.makedirs(script_dir, exist_ok=True)
    script_path = os.path.join(script_dir, f"app_{job.id_num}.sh")
    
    with open(script_path, "w") as f:
        f.write("#!/bin/bash\n")
        # Natively bring the cluster's module command to life
        f.write("if [ -f /etc/profile.d/modules.sh ]; then\n")
        f.write("    source /etc/profile.d/modules.sh\n")
        f.write("fi\n\n")
        
        # Inject application-specific hooks cleanly
        if pre_commands:
            for cmd in pre_commands:
                f.write(f"{cmd}\n")
        
        f.write("\n# Execute workload\n")
        f.write(f"{cmd_string}\n")

    # 3. Execute the physical script as a clean subprocess
    process = subprocess.Popen(
        ["bash", script_path], 
        stdout=subprocess.PIPE,  
        stderr=subprocess.PIPE, 
        shell=False
    )  
    job.set_process(process)  
      
    # Initialize the silent buffer  
    job.raw_stdout_buffer = []  
  
    # Define and start the silent reader thread  
    def _silent_reader():  
        try:  
            for line in iter(process.stdout.readline, b""):  
                job.raw_stdout_buffer.append(line)  
        except (ValueError, OSError):  
            pass # Process killed or pipe closed  
  
    job._stream_thread = threading.Thread(target=_silent_reader, daemon=True)  
    job._stream_thread.start()  
  
    logger.info(f"Launched App {job.id_num}  PID={process.pid}  nodes={job.node_list}")  

def end_job(job, logger: CrabLogger):  
    """Forcefully terminates a job and retrieves output."""  
    if hasattr(job, 'process') and job.process:  
        job.process.kill()  
          
        if hasattr(job, '_stream_thread') and job._stream_thread:  
            job._stream_thread.join(timeout=2.0)  
              
        _, err = job.process.communicate()  
          
        # Reconstruct stdout from the silent buffer  
        out = b"".join(getattr(job, 'raw_stdout_buffer', []))  
          
        job.set_output(out, err)  
        logger.debug(f"Killed App {job.id_num}")  
  
def wait_timed(job, timeout_sec: float, logger: CrabLogger) -> bool:  
    """Waits for a job with a timeout. Returns True if timed out."""  
    try:  
        out, err = job.process.communicate(timeout=timeout_sec)  
        job.set_output(out, err)  
        return False  
    except subprocess.TimeoutExpired:  
        end_job(job, logger)  
        return True
