import os
import sys
import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt


def plot_absorption(lake):
    file_path = os.path.join("..", "runs", lake, "Absorption.dat")
    df = pd.read_csv(file_path, skiprows=3, delim_whitespace=True, header=None)
    df.columns = ["time", "data"]
    df['time'] = pd.to_datetime(df['time'], origin='19810101', unit='D')
    plt.plot(df['time'], df['data'])
    plt.title("Absorption")
    plt.ylabel("m")
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    plt.show()


def plot_forcing(lake):
    file_path = os.path.join("..", "runs", lake, "Forcing.dat")
    with open(file_path, 'r') as file:
        columns = [l.strip() + "]" for l in file.readline().strip().split("]")][:-1]
    df = pd.read_csv(file_path, skiprows=1, delim_whitespace=True, header=None)
    df.columns = columns
    df['time'] = pd.to_datetime(df['Time [d]'], origin='19810101', unit='D')
    variable_columns = df.columns[1:-1]
    num_subplots = len(variable_columns)
    num_rows = num_subplots // 2 + num_subplots % 2
    num_cols = 2
    fig, axes = plt.subplots(nrows=num_rows, ncols=num_cols, figsize=(15, 5 * num_rows))
    fig.suptitle('Forcing Data', fontsize=16)
    for i, col in enumerate(variable_columns):
        row_index = i // num_cols
        col_index = i % num_cols
        axes[row_index, col_index].plot(df['time'], df[col])
        axes[row_index, col_index].set_ylabel(col)
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    plt.show()


def plot_inflows(lake):
    fig, axes = plt.subplots(nrows=4, ncols=1, figsize=(15, 5 * 3))
    fig.suptitle('Inflow Data', fontsize=16)
    keys = ["Qin", "Tin", "Sin", "AED2_inflow/OXY_oxy_inflow"]
    for i in range(len(keys)):
        file_path = os.path.join("..", "runs", lake, "{}.dat".format(keys[i]))
        if os.path.exists(file_path):
            with open(file_path, 'r') as file:
                lines = file.readlines()
                if lines[0].strip() == "No inflows":
                    print("Inflow files are empty")
                    plt.show()
                    return
                deep_inflows, surface_inflows = [int(d.strip()) for d in lines[1].strip().split(" ") if d != ""]
            df = pd.read_csv(file_path, skiprows=2, delim_whitespace=True, header=None)
            df.columns = ["time"] + [str(c) for c in list(range(len(df.columns) - 1))]
            depths = df.iloc[0]
            df = df.iloc[1:]
            df['time'] = pd.to_datetime(df['time'], origin='19810101', unit='D')
            if keys[i] == "Qin":
                df_q = df
            for d in range(deep_inflows):
                axes[i].plot(df['time'], df[str(d)], label="Deep inflow {}".format(d))
                axes[i].set_ylabel(keys[i])
                axes[i].legend()
            for d in range(deep_inflows + 1, deep_inflows + surface_inflows, 3):
                if keys[i] == "Qin":
                    axes[i].plot(df['time'], df[str(d)] * abs(depths[d]), label="Surface inflow {}".format(d - deep_inflows))
                else:
                    axes[i].plot(df['time'], df[str(d)] / df_q[str(d)], label="Surface inflow {}".format(d - deep_inflows))
                axes[i].set_ylabel(keys[i])
                axes[i].legend()
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    plt.show()


def plot_output(lake, parameter):
    file_path = os.path.join("..", "runs", lake, "Results", "{}_out.dat".format(parameter))
    if not os.path.exists(file_path):
        print("Results file {}_out.dat is not available to plot".format(parameter))
    df = pd.read_csv(file_path)
    df["Datetime"] = pd.to_datetime(df['Datetime'], origin='19810101', unit='D')
    df.set_index('Datetime', inplace=True)
    if len(df.columns) > 2:
        fig, axes = plt.subplots(nrows=2, ncols=1, figsize=(15, 5 * 2))
        fig.suptitle('Results for {}'.format(parameter), fontsize=16)
        axes[0].plot(df.index, df.iloc[:, -1], label="Surface")
        axes[0].plot(df.index, df.iloc[:, 0], label="Bottom")
        axes[0].margins(x=0)
        axes[0].autoscale(axis='x', tight=True)
        axes[0].legend()
        heatmap = sns.heatmap(df.transpose()[::-1], cbar=False, ax=axes[1])
        cbar_ax = fig.add_axes([0.93, 0.15, 0.02, 0.7])  # [x, y, width, height]
        fig.colorbar(heatmap.get_children()[0], cax=cbar_ax, orientation='vertical')
        plt.show()
    else:
        plt.plot(df.index, df.iloc[:, 0])
        plt.title('Results for {}'.format(parameter))
        plt.show()


def plot_simstrat_files(lake):
    plot_forcing(lake)
    plot_inflows(lake)
    plot_absorption(lake)
    plot_output(lake, "T")
    plot_output(lake, "OXY_sat")
    plot_output(lake, "TotalIceH")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise ValueError("Usage: python plot.py lake_key")
    else:
        plot_simstrat_files(sys.argv[1])
