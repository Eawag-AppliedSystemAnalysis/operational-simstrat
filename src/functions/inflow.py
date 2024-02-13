import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from .general import datetime_to_simstrat_time, call_url, interpolate_timeseries


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
            surface_inflows = parse_lake_outflow(inflow, start, end, time, simulation_dir)
            inflow_data["surface_inflows"] = inflow_data["surface_inflows"] + surface_inflows
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
    return inflow_data


def fill_inflow_data():
    print("Hello world")


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


def parse_lake_outflow(inflow, start, end, time, simulation_dir):
    # Read Q from Qin for other lake
    # Read T & S from simulation result.
    return [
        {"depth": -5.0, "Q": [0, 0, 0], "T": [0, 0, 0], "S": [0, 0, 0]},
        {"depth": -5.0, "Q": [10.5, 11.2, 16.1], "T": [0, 0, 0], "S": [0, 0, 0]},
        {"depth": 0.0, "Q": [10.5, 11.2, 16.1], "T": [0, 0, 0], "S": [0, 0, 0]}
    ]
