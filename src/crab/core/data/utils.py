import numpy as np
import pandas
import scipy.stats as st

from .containers import DataContainer


def check_CI(
    container_list: list[DataContainer], alpha: float, beta: float, converge_all: bool, run: int
) -> bool:
    """Checks statistical convergence based on Confidence Intervals (CI)."""
    for container in container_list:
        if not container.numeric:
            continue
        if (not container.converged) and (converge_all or container.conv_goal):
            samples = container.convergence_samples()
            n = len(samples)
            if n <= 1:
                continue

            mean = np.mean(samples)
            sem = st.sem(samples)

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
        if container.numeric and (converge_all or container.conv_goal):
            any_target = True
            check = check and container.converged
    return check and any_target


def log_data(out_format: str, path_prefix: str, data_containers: list[DataContainer]):
    """Write one `<prefix>_app_<id>.csv` per app: run_id, msg_size, key columns, then metrics.

    Containers of one app and one key value hold the same number of samples per run
    (collect_run records whole rows), so they line up sample by sample. csv is the only
    format; config_checks refuses others.
    """
    apps_data: dict[int, list[DataContainer]] = {}
    for container in data_containers:
        apps_data.setdefault(container.app_id, []).append(container)

    for app_id, containers in apps_data.items():
        app_msg_size = containers[0].msg_size

        # One frame per key value, in order of first appearance.
        groups: dict[tuple, list[DataContainer]] = {}
        for container in containers:
            if container.data:
                groups.setdefault(container.key, []).append(container)
        if not groups:
            continue

        frames = []
        for order, (key, members) in enumerate(groups.items()):
            frame = pandas.DataFrame({"run_id": members[0].run_ids})
            for position, (key_name, key_value) in enumerate(key, start=1):
                frame.insert(position, key_name, key_value)
            for container in members:
                frame[container.get_title()] = container.data
            frame["_group"] = order
            frame["_sample"] = frame.groupby("run_id").cumcount()
            frames.append(frame)

        dataframe = (
            pandas.concat(frames, ignore_index=True)
            .sort_values(["run_id", "_group", "_sample"], kind="stable")
            .drop(columns=["_group", "_sample"])
        )
        dataframe.insert(1, "msg_size", app_msg_size)

        file_name = f"{path_prefix}_app_{app_id}"
        if out_format == "csv":
            dataframe.to_csv(f"{file_name}.csv", index=False)
