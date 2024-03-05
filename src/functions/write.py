import os
import json
import numpy as np
import pandas as pd


def write_grid(grid_cells, file_path):
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('Number of grid points\n')
        f.write('%d\n' % grid_cells)


def write_bathymetry(bathymetry, file_path):
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('%s    %s\n' % ('Depth [m]', 'Area [m2]'))
        for i in range(len(bathymetry["depth"])):
            f.write('%6.1f    %9.0f\n' % (-abs(bathymetry["depth"][i]), bathymetry["area"][i]))


def write_output_depths(output_depths, file_path):
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('Depths [m]\n')
        for z in -np.abs(output_depths):
            f.write('%.2f\n' % z)


def write_output_time_resolution(output_time_steps, file_path):
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('Number of time steps\n')
        f.write('%d\n' % np.floor(output_time_steps))


def write_initial_conditions(depth_arr, temperature_arr, salinity_arr, simulation_dir):
    file_path = os.path.join(simulation_dir, "InitialConditions.dat")
    if len(depth_arr) != len(temperature_arr) or len(temperature_arr) != len(salinity_arr):
        raise ValueError("All input arrays must be the same length")
    if depth_arr[0] != 0:
        raise ValueError("First depth must be zero")
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('%s    %s    %s    %s    %s    %s    %s\n' % ('Depth [m]', 'U [m/s]', 'V [m/s]', 'T [°C]', 'S [‰]', 'k [J/kg]', 'eps [W/kg]'))
        for i in range(len(depth_arr)):
            if not np.isnan(temperature_arr[i]):
                if np.isnan(salinity_arr[i]):
                    salinity_arr[i] = np.nanmean(salinity_arr)
                f.write('%7.2f    %7.3f    %7.3f    %7.3f    %7.3f    %6.1e    %6.1e\n' % (-abs(depth_arr[i]), 0, 0, temperature_arr[i], salinity_arr[i], 3E-6, 5E-10))


def write_initial_oxygen(depth_arr, oxygen_arr, simulation_dir):
    file_path = os.path.join(simulation_dir, "AED2_initcond", "OXY_oxy_ini.dat")
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    if depth_arr[0] != 0:
        raise ValueError("First depth must be zero")
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('%s    %s\n' % ('Depth [m]', 'O2 Conc. [mmol/m3]'))
        for i in range(len(depth_arr)):
            f.write('%7.2f    %7.3f\n' % (-abs(depth_arr[i]), oxygen_arr[i]))


def write_absorption(absorption, file_path, log):
    if len(absorption["Time"]) != len(absorption["Value"]):
        raise ValueError("All input arrays must be the same length")

    if os.path.exists(file_path):
        time_min = absorption["Time"][0]
        df = pd.read_csv(file_path, skiprows=3, delim_whitespace=True, header=None)
        df.columns = ["Time", "Value"]
        df = df[df['Time'] < time_min]
        if len(df) > 0:
            absorption["Time"] = np.concatenate((df["Time"].values, absorption["Time"]))
            absorption["Value"] = np.concatenate((df["Value"].values, absorption["Value"]))
            log.info("Merged with existing forcing data", indent=2)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('Time [d] (1.col)    z [m] (1.row)    Absorption [m-1] (rest)\n')
        f.write('%d\n' % 1)
        f.write('-1         0.0\n')
        for i in range(len(absorption["Time"])):
            f.write('%10.4f' % absorption["Time"][i])
            f.write(' %5.3f' % absorption["Value"][i])
            f.write('\n')


def write_par_file(simstrat_version, par, simulation_dir):
    if simstrat_version in ["3.0.3", "3.0.4"]:
        with open(os.path.join(simulation_dir, "Settings.par"), 'w') as f:
            json.dump(par, f, indent=4)
    else:
        raise ValueError("Writing par file not implemented for Simstrat version {}".format(simstrat_version))


def write_inflows(inflow_mode, simulation_dir, log, inflow_data=None):
    files = {
        "Q": {"file": "Qin.dat", "deep_unit": "m3/s", "surface_unit": "m2/s"},
        "T": {"file": "Tin.dat", "deep_unit": "°C", "surface_unit": "°C m2/s"},
        "S": {"file": "Sin.dat", "deep_unit": "ppt", "surface_unit": "ppt m2/s"}
    }
    for key in files.keys():
        file_path = os.path.join(simulation_dir, files[key]["file"])
        log.info("Writing {} to file".format(key), indent=1)
        if inflow_mode == 0:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write("No inflows")
        elif inflow_mode == 2:
            if os.path.exists(file_path):
                time_min = inflow_data["Time"][0]
                df = pd.read_csv(file_path, skiprows=3, delim_whitespace=True, header=None)
                df.columns = ["Time"] + [str(c) for c in list(range(len(df.columns) - 1))]
                df = df[df['Time'] < time_min]
                if len(df) > 0:
                    time = np.concatenate((df["Time"].values, inflow_data["Time"]))
                    for i in range(len(inflow_data["deep_inflows"])):
                        inflow_data["deep_inflows"][i][key] = np.concatenate((df[str(i)].values, inflow_data["deep_inflows"][i][key]))
                    for i in range(len(inflow_data["deep_inflows"]), len(inflow_data["deep_inflows"]) + len(inflow_data["surface_inflows"])):
                        inflow_data["surface_inflows"][i][key] = np.concatenate((df[str(i)].values, inflow_data["surface_inflows"][i][key]))
                    log.info("Merged {} with existing forcing data".format(key), indent=2)
                else:
                    time = inflow_data["Time"]
            else:
                time = inflow_data["Time"]

            with open(file_path, 'w', encoding='utf-8') as f:
                f.write('%10s %10s %10s %10s\n' % ('Time [d]', 'Depth [m]', 'Deep Inflows [{}]'.format(files[key]["deep_unit"]),
                                                   'Surface Inflows [{}]'.format(files[key]["surface_unit"])))
                f.write('%10d %10d\n' % (len(inflow_data["deep_inflows"]), len(inflow_data["surface_inflows"])))
                f.write('-1         ' + ' '.join(['%10.2f' % z["depth"] for z in inflow_data["deep_inflows"]]) + ' '.join(['%10.2f' % z["depth"] for z in inflow_data["surface_inflows"]]) + '\n')
                for i in range(len(time)):
                    if any(np.isnan([d[key][i] for d in inflow_data["deep_inflows"]])) or any(np.isnan([d[key][i] for d in inflow_data["surface_inflows"]])):
                        continue
                    f.write('%10.4f ' % time[i])
                    f.write(' '.join(['%10.2f' % z[key][i] for z in inflow_data["deep_inflows"]]))
                    f.write(' '.join(['%10.2f' % z[key][i] for z in inflow_data["surface_inflows"]]))
                    f.write('\n')


def write_oxygen_inflows(simulation_dir, inflow_data=None):
    file_path = os.path.join(simulation_dir, "AED2_inflow", "OXY_oxy_inflow.dat")
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    if inflow_data is None:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write("No inflows")
    else:
        if os.path.exists(file_path):
            time_min = inflow_data["Time"][0]
            df = pd.read_csv(file_path, skiprows=3, delim_whitespace=True, header=None)
            df.columns = ["Time"] + [str(c) for c in list(range(len(df.columns) - 1))]
            df = df[df['Time'] < time_min]
            if len(df) > 0:
                time = np.concatenate((df["Time"].values, inflow_data["Time"]))
                for i in range(len(inflow_data["deep_inflows"])):
                    inflow_data["deep_inflows"][i]["oxygen"] = np.concatenate(
                        (df[str(i)].values, inflow_data["deep_inflows"][i]["oxygen"]))
                for i in range(len(inflow_data["deep_inflows"]),
                               len(inflow_data["deep_inflows"]) + len(inflow_data["surface_inflows"])):
                    inflow_data["surface_inflows"][i]["oxygen"] = np.concatenate(
                        (df[str(i)].values, inflow_data["surface_inflows"][i]["oxygen"]))
            else:
                time = inflow_data["Time"]
        else:
            time = inflow_data["Time"]

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(
                '%10s %10s %10s %10s\n' % ('Time [d]', 'Depth [m]', 'Deep Inflows [mmol/m3]', 'Surface Inflows [mmol/m3]'))
            f.write('%10d %10d\n' % (len(inflow_data["deep_inflows"]), len(inflow_data["surface_inflows"])))
            f.write('-1         ' + ' '.join(['%10.2f' % z["depth"] for z in inflow_data["deep_inflows"]]) + ' '.join(
                ['%10.2f' % z["depth"] for z in inflow_data["surface_inflows"]]) + '\n')
            for i in range(len(time)):
                if any(np.isnan([d["oxygen"][i] for d in inflow_data["deep_inflows"]])) or any(
                        np.isnan([d["oxygen"][i] for d in inflow_data["surface_inflows"]])):
                    continue
                f.write('%10.4f ' % time[i])
                f.write(' '.join(['%10.2f' % z["oxygen"][i] for z in inflow_data["deep_inflows"]]))
                f.write(' '.join(['%10.2f' % z["oxygen"][i] for z in inflow_data["surface_inflows"]]))
                f.write('\n')


def write_outflow(simulation_dir):
    with open(os.path.join(simulation_dir, "Qout.dat"), 'w', encoding='utf-8') as f:
        f.write("Outflow not used, lake overflows to maintain water level")


def write_forcing_data(forcing_data, simulation_dir, log):
    columns = ["Time", "u", "v", "Tair", "sol", "vap", "cloud", "rain"]
    file_path = os.path.join(simulation_dir, "Forcing.dat")

    if os.path.exists(file_path):
        time_min = forcing_data["Time"]["data"][0]
        df = pd.read_csv(file_path, skiprows=1, delim_whitespace=True, header=None)
        df.columns = columns
        df = df[df['Time'] < time_min]
        if len(df) > 0:
            for key in forcing_data.keys():
                forcing_data[key]["data"] = np.concatenate((df[key].values, forcing_data[key]["data"]))
            log.info("Merged with existing forcing data", indent=2)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(' '.join(['%10s' % "{} [{}]".format(c, forcing_data[c]["unit"]) for c in columns]) + '\n')
        for i in range(len(forcing_data["Time"]["data"])):
            if any(np.isnan([forcing_data[c]["data"][i] for c in columns])):
                continue
            f.write(' '.join(['%10.4f' % forcing_data[c]["data"][i] for c in columns]) + '\n')


