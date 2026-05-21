import os
import subprocess  
import shlex  
import threading  
from typing import List  
from crab.log import CrabLogger  
  
def run_job(job, wlmanager, ppn: int, logger: CrabLogger, pre_commands: List[str] = None, live_stream: bool = False, data_path: str = None, launcher: str = None):  
    """  
    Launch an application process via the workload manager.  
    """  
    if not job.node_list:  
        raise Exception(f"Application {job.id_num} has 0 allocated nodes.")  
      
    # 1. Get the base launcher command from the workload manager
    cmd_string = wlmanager.run_job(job.node_list, ppn, job.run_app(), pre_commands=pre_commands, data_path=data_path, launcher=launcher)  
      
    if not cmd_string:  
        cmd_string = "echo a > /dev/null"  
        raise Exception

    # 2. Assemble a full shell pipeline. 
    # We prepend the pre_commands explicitly here because shlex.split() breaks logical operators (&&, export).
    full_script = []
    if pre_commands:
        full_script.extend(pre_commands)
    full_script.append(cmd_string)
    
    final_cmd_string = " && ".join(full_script)

    # 3. Sanitize Lmod environment corruption.
    # Lmod exports bash functions (like 'module') as variables (BASH_FUNC_module%%).
    # When Python spawns a bash subshell, it inherits these and crashes parsing them.
    # We strip them to guarantee a pristine execution environment.
    clean_env = os.environ.copy()
    lmod_keys = [k for k in clean_env.keys() if k.startswith('BASH_FUNC_')]
    for k in lmod_keys:
        del clean_env[k]

    # 4. Execute as a native shell script.
    process = subprocess.Popen(
        ["bash", "-c", final_cmd_string], 
        stdout=subprocess.PIPE,  
        stderr=subprocess.PIPE, 
        env=clean_env,
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
