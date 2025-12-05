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



def plot_heatmaps(data, name):
    df = pd.DataFrame(data)

    # Ensure correct ordering of categories
    df['burst_length'] = pd.Categorical(df['burst_length'],
                                        categories=['1e-2', '1e-4', '1e-6'],
                                        ordered=True)
    df['burst_gap'] = pd.Categorical(df['burst_gap'],
                                     categories=['1ms', '10ms', '100ms'],
                                     ordered=True)

    sns.set_style("whitegrid")
    sns.set_context("talk")

    messages = df['message'].unique()
    n_msgs = len(messages)

    acid_cmap = LinearSegmentedColormap.from_list("purple_acidgreen",
                                              ["#FC4F49", "#29C35F"]) 

    # Create one subplot per message, stacked vertically
    fig, axes = plt.subplots(1, n_msgs, figsize=(9 * n_msgs, 8), sharex=True)

    if n_msgs == 1:
        axes = [axes]  # ensure axes is iterable

    heatmaps = []
    for ax, msg in zip(axes, messages):
        df_msg = df[df['message'] == msg]

        # Pivot: rows = burst_length, cols = burst_gap, values = factor
        pivot = df_msg.pivot(index="burst_length", columns="burst_gap", values="factor")

        hm = sns.heatmap(pivot, annot=True, fmt=".3f", cmap=acid_cmap,
                    vmin=0.6, vmax=1.1, cbar=False, annot_kws={"size": 40}, yticklabels=False,
                    ax=ax)
        
        heatmaps.append(hm)

        ax.set_title(f"Message Size: {msg}", fontsize=40, pad=30)
        ax.set_ylabel("", fontsize=40, labelpad=15)
        ax.set_xlabel("", fontsize=40, labelpad=15)
        ax.tick_params(axis='both', which='major', labelsize=40)

    cbar_ax = fig.add_axes([0.123, 1.15, 0.78, 0.03])  # [left, bottom, width, height]
    fig.colorbar(heatmaps[0].collections[0], cax=cbar_ax, orientation="horizontal")
    cbar_ax.tick_params(labelsize=40)  


    plt.savefig(f'plots/{name}_heatmaps.png', dpi=300, bbox_inches='tight')
    plt.close()



def DrawLinePlot2(data, name, palette):
    print(f"Plotting data collective: {name}")

    # Imposta stile e contesto
    sns.set_style("whitegrid")
    sns.set_context("talk")

    # Crea figura principale
    f, ax1 = plt.subplots(figsize=(30, 9))

    # Conversione dati in DataFrame
    df = pd.DataFrame(data)

    # Palette migliorata

    # --- Lineplot principale ---
    sns.lineplot(
        data=df,
        x='Message',
        y='bandwidth',
        hue='Cluster',
        style='Cluster',
        markers=True,
        markersize=10,
        linewidth=8,
        ax=ax1
    )

    # Linea teorica
    ax1.axhline(
        y=200,
        color='red',
        linestyle='--',
        linewidth=5,
        label=f'Nanjing Theoretical Peak {200} Gb/s'
    )

    # Example: horizontal line at y=100, from x=0.5 to x=1.5
    ax1.hlines(
        y=100,
        xmin='512 B', xmax='128 MiB',
        color='red',
        linestyle=':',
        linewidth=5,
        label=f'HAICGU Theoretical Peak {100} Gb/s'
    )

    # Etichette
    ax1.set_xlim(0, len(df["Message"].unique()) - 1)
    ax1.tick_params(axis='both', which='major', labelsize=40)
    ax1.set_ylabel('Bandwidth (Gb/s)', fontsize=40, labelpad=23)
    ax1.set_xlabel('Message Size', fontsize=40, labelpad=23)

    ax1.legend(
        fontsize=40,           
        loc='upper center',
        bbox_to_anchor=(0.5, -0.2),  # più spazio sotto
        ncol=2,
        frameon=True,
        title=None,
    )

    # --- Subplot zoom-in ---
    zoom_msgs = ['8 B', '64 B', '512 B', '4 KiB']
    df_zoom = df[df['Message'].isin(zoom_msgs)]

    axins = inset_axes(ax1, width="43%", height="43%", loc='upper left', borderpad=5.5)

    df_zoom['latency_scaled'] = df_zoom['latency'] * 1e6

    sns.lineplot(
        data=df_zoom,
        x='Message',
        y='latency_scaled',
        hue='Cluster',
        style='Cluster',
        markers=True,
        markersize=8,
        linewidth=7,
        ax=axins,
        legend=False  # no legend in zoom
    )

    # Optional: adjust ticks for zoom clarity
    axins.set_ylim(1, 35)
    axins.set_xlim(0, len(df_zoom["Message"].unique()) - 1)
    axins.tick_params(axis='both', which='major', labelsize=28)
    axins.set_title("")
    axins.set_xlabel('', fontsize=28, labelpad=23)
    axins.set_ylabel('Latency (us)', fontsize=28, labelpad=5)

    # --- Layout e salvataggio ---
    #plt.tight_layout()
    plt.savefig(f'plots/{name}_line.png', dpi=300, bbox_inches='tight')
    plt.close()





def DrawScatterPlot(data, name, palette):
    print(f"Plotting data collective: {name}")

    # Use a dark theme for the plot
    sns.set_style("whitegrid")  # darker background for axes
    sns.set_context("talk")

    # Create the figure and axes
    f, ax1 = plt.subplots(figsize=(30, 13))
    
    # Convert input data to a DataFrame
    df = pd.DataFrame(data)
    #df['cluster_collective'] = df['Cluster'].astype(str) + '_' + df['collective'].astype(str)

    # Plot with seaborn
    fig = sns.scatterplot(
        data=df,
        x='iteration',
        y='bandwidth',
        hue='Cluster',
        s=200,
        ax=ax1,
        alpha=0.9
    )

    ax1.axhline(
        y=200,
        color='red',
        linestyle='--',
        linewidth=6,
        label=f'Nanjing Theoretical Peak {200} Gb/s'
    )

    ax1.axhline(
        y=100,
        color='red',
        linestyle=':',
        linewidth=6,
        label=f'HAICGU Theoretical Peak {100} Gb/s'
    )

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



def LoadHeatmapData(data, cluster, path, coll):

    print (f"Loading data from {path}, coll={coll}")

    burst_length = ['1e-2', '1e-4', '1e-6']
    burst_gap = ['1ms', '10ms', '100ms']
    messages = ['32 KiB', '256 KiB', '2 MiB']

    for blen in burst_length:
        for bgap in burst_gap:
    
            folder_name = blen + "_" + bgap
            full_path = os.path.join(path, folder_name)

            for msg in messages:
                msg_mult = msg.strip().split(' ')[1]
                msg_value = msg.strip().split(' ')[0]
                if msg_mult == 'B':
                    multiplier = 1
                elif msg_mult == 'KiB':
                    multiplier = 1024
                elif msg_mult == 'MiB':
                    multiplier = 1024**2
                elif msg_mult == 'GiB':
                    multiplier = 1024**3
                else:
                    raise ValueError(f"Unknown message size unit in {msg}")

                message_bytes = int(msg_value) * multiplier

                cong_csv_path = os.path.join(full_path, f"{message_bytes}_{coll}_cong.csv")
                csv_path = os.path.join(full_path,  f"{message_bytes}_{coll}.csv")

                cong_iterations = 0
                cong_latencies = []
                with open(cong_csv_path, 'r') as file1:
                        lines = file1.readlines()[2:]  # Skip the first line
                        cong_iterations = len(lines)
                        for line in lines:
                            latency = float(line.strip())
                            cong_latencies.append(latency)

                mean_cong = sum(cong_latencies) / len(cong_latencies)


                iterations = 0
                latencies = []
                with open(csv_path, 'r') as file2:
                        lines = file2.readlines()[2:]  # Skip the first line
                        iterations = len(lines)
                        for line in lines:
                            latency = float(line.strip())
                            latencies.append(latency)

                mean_lat = sum(latencies) / len(latencies)

                print(f"Message: {msg}, Burst Length: {blen}, Burst Gap: {bgap}, Iterations: {iterations}, Congested Iterations: {cong_iterations}")

                factor = cong_iterations/iterations

                factor = mean_lat/mean_cong


                data['factor'].append(factor)
                data['message'].append(msg)
                data['cluster'].append(cluster)  
                data['burst_length'].append(blen)
                data['burst_gap'].append(bgap)
                data['collective'].append(coll)

    return data


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


def DrawLinePlot(data, name):
    print(f"Plotting data collective: {name}")

    # Imposta stile e contesto
    sns.set_style("whitegrid")
    sns.set_context("talk")

    # Crea figura principale
    f, ax1 = plt.subplots(figsize=(30, 15))

    # Conversione dati in DataFrame
    df = pd.DataFrame(data)
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

    # --- Subplot zoom-in ---
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
    axins.set_ylim(1, 10)
    axins.set_xlim(0, len(df_zoom["message"].unique()) - 1)
    axins.tick_params(axis='both', which='major', labelsize=28)
    axins.set_title("")
    axins.set_xlabel('', fontsize=28, labelpad=23)
    axins.set_ylabel('Latency (us)', fontsize=28, labelpad=23)

    # --- Layout e salvataggio ---
    plt.savefig(f'plots/{name}_line.png', dpi=300, bbox_inches='tight')
    plt.close()


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='CRAB Plotter')
    parser.add_argument('--nodes', type=int, default=4, help='Number of nodes (default: 4)')
    args = parser.parse_args()

    nodes = args.nodes

    data = {
        'message': [],
        'bytes': [],
        'latency': [],
        'bandwidth': [],
        'system': [],
        'collective': [],
        #'iteration': []
    }


    systems=["leonardo", "haicgu"]
    collectives = ['All-to-All', 'All-to-All Congested']
    messages = ['8B', '64B', '512B', '4KiB', '32KiB', '256KiB', '2MiB', '16MiB', '128MiB']
    data_folder = f"data/description.csv"

    with open(data_folder, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            path = row["path"]
            system = row["system"]
            collective = row["extra"]

            if system not in systems:
                continue
            if collective not in collectives:
                continue

            for i in range(8):
                data_path = os.path.join(path, f"data_app_{i}.csv")
                print("Accessing:", data_path)

                with open(data_path, newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:

                        latency = float(row[f"{i}_Max-Duration_s"])
                        m_bytes = int(row["msg_size"])
                        #to_bytes(message)
                        bandwidth = ComputeBandwidth(latency, m_bytes, collective, nodes)
                        #m_bytes = message 
                        data['latency'].append(latency)
                        data['bandwidth'].append(bandwidth)
                        data['message'].append(str(m_bytes))
                        data['collective'].append(collective)
                        data['bytes'].append(m_bytes)
                        data['system'].append(system)

    DrawLinePlot(data, f"Systems Comparison with All-to-All Noise")
    
                