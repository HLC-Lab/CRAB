import numpy as np  
import scipy.stats as st  
import pandas  
from typing import List  
from .containers import DataContainer  
  
def check_CI(container_list: List[DataContainer], alpha: float, beta: float, converge_all: bool, run: int) -> bool:  
    """Checks statistical convergence based on Confidence Intervals (CI)."""  
    for container in container_list:  
        if (not container.converged) and (converge_all or container.conv_goal):  
            n = len(container.data)  
            if n <= 1: continue   
              
            mean = np.mean(container.data)  
            sem = st.sem(container.data)  
              
            if sem == 0:  
                container.converged = True  
                container.conv_run = run  
                continue  
              
            CI_lb, CI_ub = st.t.interval(1 - alpha, n - 1, loc=mean, scale=sem)
            ref = abs(mean) if mean != 0 else 1e-9
            if (CI_ub - CI_lb) < beta * ref:
                container.converged = True
                container.conv_run = run

    any_target = False
    check = True
    for container in container_list:
        if (converge_all or container.conv_goal):
            any_target = True
            check = check and container.converged
    return check and any_target  
  
def log_data(out_format: str, path_prefix: str, data_containers: List[DataContainer]):  
    """Aggregates and saves data to CSV or HDF."""  
    apps_data = {}  
    for container in data_containers:  
        apps_data.setdefault(container.app_id, []).append(container)  
  
    for app_id, containers in apps_data.items():  
        all_metrics = []  
        app_msg_size = containers[0].msg_size if containers else 0  
  
        for container in containers:  
            if not container.data or not container.num_samples: continue  
  
            # Reconstruct run_id column  
            run_ids = []  
            for i, num in enumerate(container.num_samples):  
                run_ids.extend([i + 1] * num)  
  
            # Truncate mismatch  
            min_len = min(len(run_ids), len(container.data))  
            run_ids = run_ids[:min_len]  
            container.data = container.data[:min_len]  
  
            df = pandas.DataFrame({'run_id': run_ids, container.get_title(): container.data})  
            df = df.set_index(['run_id', df.groupby('run_id').cumcount()])  
            all_metrics.append(df)  
  
        if not all_metrics: continue  
  
        dataframe = pandas.concat(all_metrics, axis=1).reset_index()  
        if 'level_1' in dataframe.columns: dataframe = dataframe.drop(columns=['level_1'])  
          
        dataframe.insert(1, "msg_size", app_msg_size)  
          
        file_name = f"{path_prefix}_app_{app_id}"  
        if out_format == 'csv':  
            dataframe.to_csv(f"{file_name}.csv", index=False)  
        elif out_format == 'hdf':  
            dataframe.to_hdf(f"{file_name}.h5", key='df', index=False)
