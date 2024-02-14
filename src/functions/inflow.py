import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from .general import datetime_to_simstrat_time, call_url, interpolate_timeseries, fill_day_of_year


def collect_inflow_data(inflows, salinity, start, end, reference_date, simulation_dir, api, log):
    time = start + np.arange(0, (end - start).total_seconds() / 3600 + 1, 1).astype(int) * timedelta(hours=1)
    inflow_data = {
        "Time": np.array([datetime_to_simstrat_time(t, reference_date) for t in time]),
        "deep_inflows": [],
        "surface_inflows": []
    }
    for inflow in inflows:
        if inflow["type"] == "bafu_hydrostation":
            log.info("Downloading bafu hydrodata for station {}".format(inflow["Q"]["id"]), indent=2)
            inflow_data["deep_inflows"].append(
                download_bafu_hydrodata(inflow, start, end, time, salinity, api)
            )
        elif inflow["type"] == "simstrat_model_inflow":
            log.info("Collecting simulation outflows from {}".format(inflow["id"]), indent=2)
            lake_inflows = parse_lake_outflow(inflow, time, simulation_dir, reference_date)
            if "surface_inflow" in inflow:
                log.info("{} outflows will be treated as a surface inflow (direct lake connection down to {}m).".format(
                    inflow["id"], inflow["surface_inflow"]), indent=2)
                inflow_data["surface_inflows"] = inflow_data["surface_inflows"] + lake_inflows
            else:
                log.info("{} outflows will be treated as a deep inflow (river).".format(inflow["id"]), indent=2)
                inflow_data["deep_inflows"] = inflow_data["deep_inflows"] + lake_inflows
        else:
            raise ValueError("Inflow type {} not recognised.".format(inflow["type"]))
    return inflow_data


def quality_assurance_inflow_data(inflow_data, inflow_parameters, log):
    log.info("Running quality assurance on deep inflows", indent=1)
    for i in range(len(inflow_data["deep_inflows"])):
        for key in inflow_data["deep_inflows"][i].keys():
            if "negative_to_zero" in inflow_parameters[key] and inflow_parameters[key]["negative_to_zero"]:
                inflow_data["deep_inflows"][i][key][inflow_data["deep_inflows"][i][key] < 0] = 0.0
            if "min" in inflow_parameters[key]:
                inflow_data["deep_inflows"][i][key][
                    inflow_data["deep_inflows"][i][key] < inflow_parameters[key]["min"]] = np.nan
            if "max" in inflow_parameters[key]:
                inflow_data["deep_inflows"][i][key][
                    inflow_data["deep_inflows"][i][key] > inflow_parameters[key]["max"]] = np.nan
    return inflow_data


def interpolate_inflow_data(inflow_data, inflow_parameters):
    for i in range(len(inflow_data["deep_inflows"])):
        for key in inflow_data["deep_inflows"][i].keys():
            if "max_interpolate_gap" in inflow_parameters[key]:
                inflow_data["deep_inflows"][i][key] = interpolate_timeseries(inflow_data["Time"],
                                                                             inflow_data["deep_inflows"][i][key],
                                                                             max_gap_size=inflow_parameters[key][
                                                                                 "max_interpolate_gap"])
    for i in range(len(inflow_data["surface_inflows"])):
        for key in ["Q", "T", "S"]:
            inflow_data["surface_inflows"][i][key] = interpolate_timeseries(inflow_data["Time"],
                                                                            inflow_data["surface_inflows"][i][key])
    return inflow_data


def fill_inflow_data(inflow_data, inflow_parameters, simulation_dir, snapshot, reference_date, log):
    fill_required = False
    keys = ["Q", "T", "S"]
    for i in range(len(inflow_data["deep_inflows"])):
        for key in keys:
            if np.sum(np.isnan(inflow_data["deep_inflows"][i][key])) > 0:
                fill_required = True

    if fill_required:
        if snapshot:
            log.info("Reading previous inputs to generate fill statistics on full timeseries", indent=1)
            for key in keys:
                file_path = os.path.join(simulation_dir, "{}in.dat".format(key))
                if not os.path.exists(file_path):
                    raise ValueError("Unable to locate {}in.dat from previous run, unable to fill nan values. "
                                     "Please remove the snapshot and run the full simulation.".format(key))
                time_min = inflow_data["Time"][0]
                df = pd.read_csv(file_path, skiprows=3, delim_whitespace=True, header=None)
                df.columns = ["Time"] + [str(c) for c in list(range(len(df.columns) - 1))]
                df = df[df['Time'] < time_min]
                inflow_data["Time_extended"] = np.concatenate((df["Time"].values, inflow_data["Time"]))
                for i in range(len(inflow_data["deep_inflows"])):
                    inflow_data["deep_inflows"][i][key + "_extended"] = np.concatenate(
                        (df[str(i)].values, inflow_data["deep_inflows"][i][key]))
                log.info("Merged {} with existing inflow data".format(key), indent=2)
    else:
        return inflow_data

    for i in range(len(inflow_data["deep_inflows"])):
        for key in keys:
            nan_values = np.isnan(inflow_data["deep_inflows"][i][key])
            if np.sum(nan_values) > 0:
                if "fill" in inflow_parameters[key]:
                    if inflow_parameters[key]["fill"] == "mean":
                        if snapshot:
                            mean = np.nanmean(inflow_data["deep_inflows"][i][key + "_extended"])
                        else:
                            mean = np.nanmean(inflow_data["deep_inflows"][i][key])
                        inflow_data["deep_inflows"][i][key][nan_values] = mean
                        log.info(
                            "Filling {} nan values in {} with mean value: {}".format(np.sum(nan_values), key, mean),
                            indent=2)
                    elif inflow_parameters[key]["fill"] == "doy":
                        log.info("Computing day of year values for {}".format(key), indent=2)
                        if snapshot:
                            inflow_data["deep_inflows"][i][key] = fill_day_of_year(inflow_data["Time"],
                                                                                   inflow_data["deep_inflows"][i][key],
                                                                                   inflow_data["Time_extended"],
                                                                                   inflow_data["deep_inflows"][i][
                                                                                       key + "_extended"],
                                                                                   reference_date)
                        else:
                            inflow_data["deep_inflows"][i][key] = fill_day_of_year(inflow_data["Time"],
                                                                                   inflow_data["deep_inflows"][i][key],
                                                                                   inflow_data["Time"],
                                                                                   inflow_data["deep_inflows"][i][key],
                                                                                   reference_date)
                    elif inflow_parameters[key]["fill"] is None:
                        continue
                    else:
                        raise ValueError("Fill not implemented for type: {}".format(inflow_parameters[key]["fill"]))
    return inflow_data


def download_bafu_hydrodata(inflow, start_date, end_date, time, salinity, api):
    endpoint = api + "/bafu/hydrodata/measured/{}/{}/{}/{}?resample=hourly"
    df_t = pd.DataFrame({'time': time})
    df_t['time'] = pd.to_datetime(df_t['time'])
    deep_inflow = {"depth": 0.0}
    for p in ["Q", "T", "S"]:
        if p == "S" and "S" not in inflow:
            values = np.array([salinity] * len(time))
        else:
            data = call_url(endpoint.format(inflow[p]["id"],
                                            inflow[p]["parameter"],
                                            start_date.strftime('%Y%m%d'),
                                            end_date.strftime('%Y%m%d')))
            df = pd.DataFrame({'time': data["Time"], 'values': np.array(data[inflow[p]["parameter"]])})
            df['time'] = pd.to_datetime(df['time'])
            df['values'] = pd.to_numeric(df['values'], errors='coerce')
            df = df.dropna()
            df = df.sort_values(by='time')
            df = pd.merge(df_t, df, on='time', how='left')
            values = np.array(df["values"].values)
        deep_inflow[p] = values
    return deep_inflow


def parse_lake_outflow(inflow, time, simulation_dir, reference_date):
    if not os.path.exists(os.path.join(simulation_dir, "..", inflow["id"], "Results")):
        raise ValueError("{} must be run before it can be used as an inflow.")

    if "surface_inflow" in inflow:
        lake_inflow = [
            {"depth": -inflow["surface_inflow"], "Q": [], "T": [], "S": []},
            {"depth": -inflow["surface_inflow"], "Q": [], "T": [], "S": []},
            {"depth": 0.0, "Q": [], "T": [], "S": []}
        ]
    else:
        lake_inflow = [
            {"depth": 0.0, "Q": [], "T": [], "S": []}
        ]

    df_t = pd.DataFrame({'time': time})
    df_t['time'] = pd.to_datetime(df_t['time'])

    file_path = os.path.join(os.path.join(simulation_dir, "..", inflow["id"], "Qin.dat"))
    with open(file_path, 'r') as file:
        lines = file.readlines()
        if lines[0].strip() == "No inflows":
            print("Inflow files are empty")
            return []
        deep_inflows, surface_inflows = [int(d.strip()) for d in lines[1].strip().split(" ") if d != ""]
        depths = [float(d.strip()) for d in lines[2].strip().split(" ") if d != ""]
    df = pd.read_csv(file_path, skiprows=3, delim_whitespace=True, header=None)
    df.columns = ["time"] + [str(c) for c in list(range(len(df.columns) - 1))]
    df['time'] = pd.to_datetime(df['time'], origin=reference_date.strftime("%Y%m%d"), unit='D', utc=True).dt.round('H')
    df["values"] = df.iloc[:, 1:deep_inflows + 1].sum(axis=1)
    if surface_inflows > 0:
        df["values"] = df["values"] + df.iloc[:, deep_inflows + 2] * abs(depths[deep_inflows + 2])
    df = pd.merge(df_t, df, on='time', how='left')
    values = np.array(df["values"].values)

    if "surface_inflow" in inflow:
        lake_inflow[0]["Q"] = np.zeros(len(values))
        lake_inflow[1]["Q"] = values / abs(inflow["surface_inflow"])
        lake_inflow[2]["Q"] = values / abs(inflow["surface_inflow"])
    else:
        lake_inflow[0]["Q"] = values

    for key in ["T", "S"]:
        file_path = os.path.join(os.path.join(simulation_dir, "..", inflow["id"], "Results", "{}_out.dat".format(key)))
        df = pd.read_csv(file_path)
        df["time"] = pd.to_datetime(df['Datetime'], origin='19810101', unit='D', utc=True).dt.round('H')
        df = pd.merge(df_t, df, on='time', how='left')
        values = np.array(df.iloc[:, -1].values)

        depths = [abs(float(d)) for d in df.columns[2:]]
        surface_index = min(range(len(depths)), key=lambda i: abs(depths[i] - 0))

        if "surface_inflow" in inflow:
            bottom_index = min(range(len(depths)), key=lambda i: abs(depths[i] - abs(inflow["surface_inflow"])))
            lake_inflow[0][key] = np.zeros(len(values))
            lake_inflow[1][key] = np.array(df.iloc[:, bottom_index + 2].values) / abs(inflow["surface_inflow"])
            lake_inflow[2][key] = np.array(df.iloc[:, surface_index + 2].values) / abs(inflow["surface_inflow"])
        else:
            lake_inflow[0][key] = np.array(df.iloc[:, surface_index + 2].values)

    return lake_inflow
