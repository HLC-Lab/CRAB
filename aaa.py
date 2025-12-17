import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt
import pandas as pd
import csv
import os
from mpl_toolkits.axes_grid1.inset_locator import inset_axes
from matplotlib.ticker import ScalarFormatter
from matplotlib.colors import LinearSegmentedColormap
import argparse
import glob



import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.colors import LinearSegmentedColormap


def DrawLatencyHeatmap(
    data,
    name,
    nodes,
    sys,
    collective,
    msg
):
    df = pd.DataFrame(data)

    df = df[
        (df['nodes'] == nodes) &
        (df['system'] == sys) &
        (df['collective'] == collective) &
        (df['bytes'] == msg) &
        (df['burst_pause'] >= 0) &
        (df['burst_length'] >= 0)
    ]

    if df.empty:
        raise ValueError("No data left after filtering")

    df = (
        df
        .groupby(['burst_length', 'burst_pause'], as_index=False)
        .agg(max_latency=('avg_latency', 'max'))
    )

    pivot = df.pivot(
        index='burst_length',
        columns='burst_pause',
        values='max_latency'
    )

    sns.set_style("whitegrid")
    sns.set_context("talk")

    acid_cmap = LinearSegmentedColormap.from_list(
        "purple_acidgreen",
        ["#FC4F49", "#29C35F"]
    )

    fig, ax = plt.subplots(figsize=(10, 8))

    hm = sns.heatmap(
        pivot,
        annot=True,
        fmt=".3f",
        cmap=acid_cmap,
        annot_kws={"size": 30},
        ax=ax
    )

    ax.set_title(f"Message Size: {msg}", fontsize=32, pad=20)
    ax.set_xlabel("Burst Pause", fontsize=28)
    ax.set_ylabel("Burst Length", fontsize=28)
    ax.tick_params(axis='both', labelsize=24)

    cbar = hm.collections[0].colorbar
    cbar.ax.tick_params(labelsize=24)
    cbar.set_label("Mean Speedup", fontsize=26)

    plt.tight_layout()

    return fig




# -------------------------
# NEW STUFF
# -------------------------

def CleanData(data):
    for key in data.keys():
        data[key] = []
    return data


def to_bytes(size_str):
    size_str = size_str.strip().replace(" ", "").lower()

    i = 0
    while i < len(size_str) and (size_str[i].isdigit() or size_str[i] == '.'):
        i += 1

    number = float(size_str[:i])
    unit = size_str[i:]

    # Binary units (base 1024)
    binary_units = {
        'b': 1,
        'kib': 1024,
        'mib': 1024**2,
        'gib': 1024**3,
    }

    # SI units (base 1000)
    si_units = {
        'kb': 1000,
        'mb': 1000**2,
        'gb': 1000**3,
        'tb': 1000**4,
    }

    if unit in binary_units:
        return int(number * binary_units[unit])
    elif unit in si_units:
        return int(number * si_units[unit])
    else:
        raise ValueError(f"Unknown unit in size string: '{size_str}'")

def ComputeBandwidth(latency, bytes, collective, nodes):

    gbits = (bytes * 8) / 1e9  # Convert bytes to gigabits

    if collective.split(" ")[0] == 'All-to-All':
        total_data = (nodes - 1) * gbits
    elif collective.split(" ")[0] == 'All-Gather':
        total_data = ((nodes-1)/nodes) * gbits
    else:
        raise ValueError(f"Unknown collective: {collective}")

    bandwidth = total_data / latency
    return bandwidth



def DrawIterationsPlot(data, name):
    print(f"Plotting data collective: {name}")

    # Use a dark theme for the plot
    sns.set_style("whitegrid")  # darker background for axes
    sns.set_context("talk")

    # Create the figure and axes
    f, ax1 = plt.subplots(figsize=(35, 20))

    # Convert input data to a DataFrame
    df = pd.DataFrame(data)
    df['collective_system'] = df['collective'] + "_" + df['system']

    # Plot with seaborn
    fig = sns.scatterplot(
        data=df,
        x='iteration',
        y='latency',
        hue='collective_system',
        s=200,
        ax=ax1,
        alpha=0.9
    )

    # ax1.axhline(
    #     y=200,
    #     color='red',
    #     linestyle='--',
    #     linewidth=6,
    #     label=f'Nanjing Theoretical Peak {200} Gb/s'
    # )

    # ax1.axhline(
    #     y=100,
    #     color='red',
    #     linestyle=':',
    #     linewidth=6,
    #     label=f'HAICGU Theoretical Peak {100} Gb/s'
    # )

    # Labeling and formatting
    ax1.set_xlim(0, len(df["iteration"].unique()) - 1)
    ax1.tick_params(axis='both', which='major', labelsize=45)
    ax1.set_ylabel('Bandwidth (Gb/s)', fontsize=45, labelpad=20)
    ax1.set_xlabel('Iterations', fontsize=45, labelpad=20)
    #ax1.set_title(f'{name}', fontsize=45, pad=30)

    # Show legend and layout
    # Filtra legenda: solo cluster_collective unici + linea teorica

    ax1.legend(
        fontsize=45,           # grandezza testo etichette
        loc='upper center',
        bbox_to_anchor=(0.5, -0.2),  # più spazio sotto
        ncol=2,
        frameon=True,
        title=None,
        markerscale=2.0        # ingrandisce i marker nella legenda
    )
    plt.tight_layout()

    # Save the figure
    plt.savefig(f'plots/{name}_scatter.png', dpi=300)  # save with dark background

def DrawLatencyViolinPlot(data, name):
    print(f"Plotting violin plot: {name}")

    # Style
    sns.set_style("whitegrid")
    sns.set_context("talk")

    # Figure
    f, ax = plt.subplots(figsize=(40, 30))

    # DataFrame
    df = pd.DataFrame(data)
    df['collective_system'] = df['collective'] + "_" + df['system']

    palette_base = ["#4C72B0", "#55A868", "#C44E52"]

    # Build a palette where each color repeats for 3 categories
    unique_x = df["collective_system"].unique()
    palette = [palette_base[i // 3 % len(palette_base)] for i in range(len(unique_x))]

    sns.boxplot(
        data=df,
        x='collective_system',
        y='latency',
        ax=ax,
        showfliers=False,
        palette=palette
    )

    # Labels
    ax.set_xlabel("Collective", fontsize=40, labelpad=23)
    ax.set_ylabel("Latency (s)", fontsize=40, labelpad=23)
    ax.tick_params(axis='x', rotation=90, labelsize=32)
    ax.tick_params(axis='y', labelsize=40)
    # Save
    plt.tight_layout()
    plt.savefig(f"plots/{name}_violin.png", dpi=300, bbox_inches="tight")
    plt.close()


def DrawBandwidthPlot(data, name, nodes, sys):
    print(f"Plotting data collective: {name}")

    # Imposta stile e contesto
    sns.set_style("whitegrid")
    sns.set_context("talk")

    # Crea figura principale
    f, ax1 = plt.subplots(figsize=(30, 15))

    # Conversione e filtra dati in DataFrame
    df = pd.DataFrame(data)
    df = df[df['nodes'] == nodes]
    df = df[df['system'] == sys]
    df['collective_system'] = df['collective'] + "_" + df['system']

    # --- Lineplot principale ---
    sns.lineplot(
        data=df,
        x='message',
        y='bandwidth',
        hue='collective_system',
        style='collective_system',
        markers=True,
        markersize=10,
        linewidth=8,
        ax=ax1
    )

    # Linea teorica
    ax1.axhline(
        y=200,
        color='red',
        linestyle=':',
        linewidth=5,
        label=f'Theoretical Peak {200} Gb/s'
    )

    # Etichette
    ax1.set_xlim(0, len(df["message"].unique()) - 1)
    ax1.tick_params(axis='both', which='major', labelsize=40)
    ax1.set_ylabel('Bandwidth (Gb/s)', fontsize=40, labelpad=23)
    ax1.set_xlabel('Message Size', fontsize=40, labelpad=23)
    #ax1.set_title(f'{name}', fontsize=38, pad=30)

    # Legenda centrata in basso
    ax1.legend(
        fontsize=40,
        loc='upper center',
        bbox_to_anchor=(0.5, -0.2),
        ncol=2,
        frameon=True,
        title=None,
    )

    # --- Subplot zoom-in --- ["agtr", "agtr_con
    zoom_msgs = ['8', '64', '512', '4096']
    df_zoom = df[df['message'].isin(zoom_msgs)]
    #! This line creates a warning
    df_zoom['latency_scaled'] = df_zoom['latency'] * 1e6

    axins = inset_axes(ax1, width="43%", height="43%", loc='upper left', borderpad=7)
    sns.lineplot(
        data=df_zoom,
        x='message',
        y='latency_scaled',
        hue='collective_system',
        style='collective_system',
        markers=True,
        markersize=8,
        linewidth=7,
        ax=axins,
        legend=False  # no legend in zoom
    )

    # Optional: adjust ticks for zoom clarity
    #axins.set_ylim(1, 10)
    axins.set_xlim(0, len(df_zoom["message"].unique()) - 1)
    axins.tick_params(axis='both', which='major', labelsize=28)
    axins.set_title("")
    axins.set_xlabel('', fontsize=28, labelpad=23)
    axins.set_ylabel('Latency (us)', fontsize=28, labelpad=23)

    # --- Layout e salvataggio ---
    plt.savefig(f'plots/{name}_line.png', dpi=300, bbox_inches='tight')
    plt.close()

def SaveBigFig(coll, nodes, system):
    K = len(figures)
    cols = 3
    rows = (K + cols - 1) // cols

    fig, axes = plt.subplots(rows, cols, figsize=(6 * cols, 6 * rows))
    axes = axes.flatten()

    for ax, src_fig in zip(axes, figures):
        src_ax = src_fig.axes[0]

        for artist in src_ax.get_children():
            artist.remove()
            ax.add_artist(artist)

        ax.set_xlim(src_ax.get_xlim())
        ax.set_ylim(src_ax.get_ylim())
        ax.set_title(src_ax.get_title())

    # Hide unused subplots
    for ax in axes[K:]:
        ax.axis("off")

    plt.tight_layout()
    plt.savefig(f"plots/heatmap_{coll}_{nodes}_{system}.png", dpi=300)

def LoadData(data, data_folder):


    with open(data_folder, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            path = row["path"]
            system = row["system"]
            collective = row["extra"]
            data_nodes = row["numnodes"]

            data_path = os.path.join(path, f"data_app_0.csv")
            if not os.path.exists(data_path):
                continue
            
            nodes_for_bw = int(data_nodes) / 2
            print(f"Processing path: {path}, system: {system}, collective: {collective}, nodes: {data_nodes}")

            collective_string = collective.strip().split(" ")
            if len(collective_string) == 1:
                collective_name = collective_string[0]
            elif len(collective_string) > 1:
                collective_name = collective_string[0]+" "+collective_string[1]
            
            if len(collective_string) > 2:
                burst_pause = float(collective_string[2])
                burst_length = float(collective_string[3])

            csv_files = sorted(glob.glob(os.path.join(path, "data_app_*.csv")))
            for i in range(len(csv_files)):

                print("Accessing:", csv_files[i])
                avg_lat = 0
                with open(csv_files[i], newline="") as f:
                    reader = csv.DictReader(f)
                    row_counter = 0
                    for row in reader:

                        latency = float(row[f"{i}_Max-Duration_s"])
                        m_bytes = int(row["msg_size"])
                        avg_lat += latency
                        bandwidth = ComputeBandwidth(latency, m_bytes, collective_name, nodes_for_bw)
                        data['latency'].append(latency)
                        data['bandwidth'].append(bandwidth)
                        data['message'].append(str(m_bytes))
                        data['collective'].append(collective_name)
                        data['bytes'].append(m_bytes)
                        data['system'].append(system)
                        data['iteration'].append(row_counter)
                        data['nodes'].append(int(data_nodes))
                        data['burst_length'].append(burst_length if 'burst_length' in locals() else -1)
                        data['burst_pause'].append(burst_pause if 'burst_pause' in locals() else -1)
                        row_counter += 1
                data['avg_latency'].extend([avg_lat/row_counter] * row_counter)

if __name__ == "__main__":

    node_list = [8, 16, 32, 64, 128, 256, 512]
    data_folder = f"data/description.csv"

    data = {
        'message': [],
        'bytes': [],
        'latency': [],
        'bandwidth': [],
        'system': [],
        'collective': [],
        'iteration': [],
        'nodes': [],
        'burst_length': [],
        'burst_pause': [],
        'avg_latency': []
    }


    collectives_sustained = ['All-to-All', 'All-to-All A2A-Congested', 'All-to-All Inc-Congested',
                             'All-Gather', 'All-Gather A2A-Congested', 'All-Gather Inc-Congested']
    collectives_bursty = ['All-to-All Inc-Congested 0.01 0.1', 'All-to-All Inc-Congested 0.01 0.01', 'All-to-All Inc-Congested 0.01 0.001',
                          'All-to-All A2A-Congested 0.01 0.1', 'All-to-All A2A-Congested 0.01 0.01', 'All-to-All A2A-Congested 0.01 0.001',
                          'All-to-All Inc-Congested 0.0001 0.1', 'All-to-All Inc-Congested 0.0001 0.01', 'All-to-All Inc-Congested 0.0001 0.001',
                          'All-to-All A2A-Congested 0.0001 0.1', 'All-to-All A2A-Congested 0.0001 0.01', 'All-to-All A2A-Congested 0.0001 0.001'
                          'All-to-All Inc-Congested 0.000001 0.1', 'All-to-All Inc-Congested 0.000001 0.01', 'All-to-All Inc-Congested 0.000001 0.001',
                          'All-to-All A2A-Congested 0.000001 0.1', 'All-to-All A2A-Congested 0.000001 0.01', 'All-to-All A2A-Congested 0.000001 0.001']

    messages = ['8B', '64B', '512B', '4KiB', '32KiB', '256KiB', '2MiB', '16MiB'] # ,'128MiB']
    for i in range(len(messages)):
        messages[i] = to_bytes(messages[i])

    systems=["leonardo", "cresco8"]
    LoadData(data, data_folder)

    # for sys in systems:
    #     for nodes in node_list:
    #         DrawBandwidthPlot(data, f"PLOT_BW_{sys}_sustained_{nodes}", nodes, sys)
    for sys in systems:
        for nodes in node_list:
            for collective in collectives_sustained:
                figures = []
                for msg in messages:
                    if "Congested" not in collective:
                        continue
                    fig = DrawLatencyHeatmap(data, f"PLOT_HEATMAPS_{sys}_{collective}_{nodes}_{msg}", nodes, sys, collective, msg)
                    figures.append(fig)
                SaveBigFig(collective, nodes, sys)
        
    CleanData(data)
    


    # LoadData(data, data_folder, node_list, systems, collectives_sustained, messages)
    # for nodes in node_list:
    #     DrawBandwidthPlot(data, f"PLOT_BW__bursty_{nodes}", nodes)
    # CleanData(data)

    # LoadData(data, data_folder, nodes, systems, collectives_bursty, messages)
    # DrawIterationsPlot(data, f"PLOT_ITS_128MiB")
    # CleanData(data)

    # LoadData(data, data_folder, nodes, systems, collectives_bursty, messages)
    # DrawLatencyViolinPlot(data, f"PLOT_ITS_128MiB")
    # CleanData(data)

    # for i in ["0.01", "0.0001", "0.000001"]:
    #     for j in ["0.1", "0.01" , "0.001"]:
    #         LoadData(data, data_folder, nodes, systems, [f'All-to-All Congested {i} {j}'], messages)
    #         DrawIterationsPlot(data, f"PLOT_ITS_128MiB_{i}_{j}")
    #         CleanData(data)


