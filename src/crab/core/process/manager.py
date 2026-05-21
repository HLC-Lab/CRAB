import os
import subprocess  
import shlex  
import threading  
from typing import List  
from crab.log import CrabLogger  
  
def _evaluate_pre_commands(pre_commands: List[str]) -> dict:
    """
    Evaluates shell hooks natively in an isolated shell to return a precise
    environment variable dictionary without altering the main CRAB parent thread.
    """
    current_env = os.environ.copy()
    if not pre_commands:
        return current_env
        
    # Standardize initialization configurations for cluster layouts
    init_snippet = ". /etc/profile.d/modules.sh" if os.path.exists("/etc/profile.d/modules.sh") else "true"
    
    # Chain commands together and dump the resulting environment state
    full_chain = f"{init_snippet} && {' && '.join(pre_commands)} && env"
    
    try:
        result = subprocess.run(["bash", "-c", full_chain], capture_output=True, text=True, check=True)
        
        parsed_env = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                parsed_env[k] = v
        return parsed_env
    except Exception:
        # Fallback gracefully to parent environment state if compilation environment check trips
        return current_env

def run_job(job, wlmanager, ppn: int, logger: CrabLogger, pre_commands: List[str] = None, live_stream: bool = False, data_path: str = None, launcher: str = None):  
    """  
    Launch an application process via the workload manager with explicit environment isolation.  
    """  
    if not job.node_list:  
        raise Exception(f"Application {job.id_num} has 0 allocated nodes.")  
      
    cmd_string = wlmanager.run_job(job.node_list, ppn, job.run_app(), pre_commands=pre_commands, data_path=data_path, launcher=launcher)  
      
    if not cmd_string:  
        cmd_string = "echo a > /dev/null"  
        raise Exception

    cmd = shlex.split(cmd_string)  
    
    # CRITICAL FIX: Evaluate application hooks to generate an isolated path state map
    job_env = _evaluate_pre_commands(pre_commands)
  
    # Inject the generated dictionary straight into the process fork
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE,  
                               stderr=subprocess.PIPE, shell=False, env=job_env)  
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
