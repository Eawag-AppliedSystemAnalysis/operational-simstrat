import os.path
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from .general import (call_url, adjust_temperature_for_altitude_difference, air_pressure_from_elevation, detect_gaps,
                      adjust_data_to_mean_and_std, clear_sky_solar_radiation, datetime_to_simstrat_time,
                      calculate_mean_wind_direction, interpolate_timeseries, fill_day_of_year, get_elevation_swisstopo,
                      vapor_pressure_from_relative_humidity_and_temperature)

import matplotlib.pyplot as plt


def metadata_from_forcing(forcing, api):
    if forcing[0]["type"].lower() == "meteoswiss_meteostation":
        start, end = metadata_from_meteoswiss_meteostation(forcing, api)
    else:
        raise ValueError("Not implemented for {}".format(forcing[0]["type"]))
    return start, end


def metadata_from_meteoswiss_meteostation(forcing, api):
    required = {key: {"start": [], "end": []} for key in
                ["fkl010h0", "dkl010h0", "rre150h0", "tre200h0", "gre000h0", "pva200h0"]}
    for f in forcing:
        if f["type"].lower() == "meteoswiss_meteostation":
            endpoint = "{}/meteoswiss/meteodata/metadata/{}".format(api, f["id"])
            data = call_url(endpoint)
            parameters = data["variables"]
            for parameter in parameters.keys():
                if parameter in required:
                    parameters[parameter]["start_date"] = datetime.strptime(parameters[parameter]["start_date"], '%Y-%m-%d').replace(tzinfo=timezone.utc)
                    parameters[parameter]["end_date"] = datetime.strptime(parameters[parameter]["end_date"], '%Y-%m-%d').replace(tzinfo=timezone.utc)
                    required[parameter]["start"].append(parameters[parameter]["start_date"])
                    required[parameter]["end"].append(parameters[parameter]["end_date"])
            f["parameters"] = parameters
            f["elevation"] = data["elevation"]
            f["latlng"] = [data["lat"], data["lng"]]

    for key in required.keys():
        if len(required[key]["start"]) == 0:
            raise ValueError("Parameter {} is required, no data can be found from the stations".format(key))
        else:
            required[key]["start"] = min(required[key]["start"])
            required[key]["end"] = max(required[key]["end"])
    start = max([r["start"] for r in required.values()])
    end = min([r["end"] for r in required.values()])
    return start, end


def download_forcing_data(output, start, end, forcing, forecast, forcing_forecast, elevation, latitude, longitude, reference_date, api, log):
    if forcing[0]["type"].lower() == "meteoswiss_meteostation":
        output = meteodata_from_meteoswiss_meteostations(start, end, forcing, elevation, latitude, longitude,
                                                         reference_date, output, api, log)
    else:
        raise ValueError("Unrecognised forcing type: {}".format(forcing[0]["type"].lower()))
    if forecast:
        if forcing_forecast["source"].lower() == "meteoswiss":
            output = meteodata_forecast_from_meteoswiss(forcing_forecast, elevation, latitude, longitude, reference_date, output, api, log)
    return output


def meteodata_from_meteoswiss_meteostations(start, end, forcing, elevation, latitude, longitude, reference_date, output,
                                            api, log):
    endpoint = api + "/meteoswiss/meteodata/measured/{}/{}/{}?variables={}"

    time = start + np.arange(0, (end - start).total_seconds() / 3600 + 1, 1).astype(int) * timedelta(hours=1)

    df_t = pd.DataFrame({'time': time})
    df_t['time'] = pd.to_datetime(df_t['time'])
    output["Time"]["data"] = np.array([datetime_to_simstrat_time(t, reference_date) for t in time])

    parameter_ids = ["fkl010h0", "dkl010h0", "rre150h0", "tre200h0", "gre000h0", "pva200h0"]
    raw_data = {}
    for p_id in parameter_ids:
        gaps = False
        df = False
        for f in forcing:
            if p_id in f["parameters"].keys():
                parameter = f["parameters"][p_id]
                if not gaps:
                    start_date = min(max(start, parameter["start_date"]), parameter["end_date"])
                    end_date = min(end, parameter["end_date"])
                    log.info(
                        "{}: Using data from station {} : {} - {}".format(p_id, f["id"], start_date.strftime('%Y%m%d'),
                                                                          end_date.strftime('%Y%m%d')), indent=1)
                    url = endpoint.format(f["id"], start_date.strftime('%Y%m%d'), end_date.strftime('%Y%m%d'), p_id)
                    print(url)
                    data = call_url(url)
                    values = np.array(data["variables"][p_id]["data"])
                    if p_id == "tre200h0":
                        values = adjust_temperature_for_altitude_difference(values, elevation - f["elevation"])
                    df = pd.DataFrame({'time': data["time"], 'values': values})
                    df['time'] = pd.to_datetime(df['time'])
                    df['values'] = pd.to_numeric(df['values'], errors='coerce')
                    df = df.dropna()
                    df = df.sort_values(by='time')
                    mean = df["values"].mean()
                    std = df["values"].std()
                    gaps = detect_gaps(df["time"], start, end)
                elif len(gaps) > 0:
                    for gap in gaps:
                        if gap[1] >= parameter["start_date"] and gap[0] <= parameter["end_date"]:
                            log.info("{}: Trying to complete with data from station {} : {} - {}".format(
                                p_id, f["id"], gap[0].strftime('%Y%m%d'), gap[1].strftime('%Y%m%d')), indent=2)
                            try:
                                url = endpoint.format(f["id"], gap[0].strftime('%Y%m%d'), gap[1].strftime('%Y%m%d'), p_id)
                                print(url)
                                data = call_url(url)
                                df_new = pd.DataFrame({'time': data["time"],
                                                       'values_new': adjust_data_to_mean_and_std(data["variables"][p_id]["data"], std, mean)})
                                df_new['time'] = pd.to_datetime(df_new['time'])
                                df_new['values_new'] = pd.to_numeric(df_new['values_new'], errors='coerce')
                                df = pd.merge(df, df_new, on='time', how='outer')
                                df['values'] = df['values'].combine_first(df['values_new'])
                                df = df[["time", "values"]]
                                df = df.dropna()
                                df = df.sort_values(by='time')
                                df.reset_index(inplace=True)
                            except Exception as e:
                                print("ERROR", e)
                    gaps = detect_gaps(df["time"], start, end)
        if not isinstance(df, pd.core.frame.DataFrame):
            raise ValueError("Failed to collect values for {}".format(p_id))
        df_m = pd.merge(df_t, df, on='time', how='left')
        raw_data[p_id] = np.array(df_m["values"])

    log.info("Processing wind from magnitude and direction to components", indent=1)
    wind_direction = raw_data["dkl010h0"]
    wind_magnitude = raw_data["fkl010h0"]
    wind_direction_mean = calculate_mean_wind_direction(wind_direction)
    log.info("Set missing direction values to average wind direction {}°".format(wind_direction_mean), indent=2)
    wind_direction[np.isnan(wind_direction)] = wind_direction_mean
    output["u"]["data"] = -wind_magnitude * np.sin(wind_direction * np.pi / 180)
    output["v"]["data"] = -wind_magnitude * np.cos(wind_direction * np.pi / 180)

    output["Tair"]["data"] = raw_data["tre200h0"]
    output["sol"]["data"] = raw_data["gre000h0"]
    output["vap"]["data"] = raw_data["pva200h0"]
    output["rain"]["data"] = raw_data["rre150h0"] * 0.001  # Convert mm to m

    log.info("Estimate cloudiness based on ratio between measured and theoretical solar radiation", indent=1)
    air_pressure = air_pressure_from_elevation(elevation)
    cssr = clear_sky_solar_radiation(time, air_pressure, output["vap"]["data"], latitude, longitude)
    df = pd.DataFrame({"cssr": cssr, "swr": output["sol"]["data"]})
    cssr_rolling = df['cssr'].rolling(window=24, center=True, min_periods=1).mean()
    swr_rolling = df['swr'].rolling(window=24, center=True, min_periods=1).mean()
    solar_index = np.interp(swr_rolling / cssr_rolling, [0, 1],
                            [0, 1])  # Flerchinger et al. (2009), Crawford and Duchon (1999)
    output["cloud"]["data"] = 1 - solar_index

    return output


def meteodata_forecast_from_meteoswiss(forcing_forecast, elevation, latitude, longitude, reference_date, output, api, log):
    parameters = ["Time", "u", "v", "Tair", "sol", "vap", "cloud", "rain"]
    df_c = pd.DataFrame({key: output[key]["data"] for key in output.keys()})
    df_c.set_index('Time', inplace=True)
    if forcing_forecast["model"].lower() == "cosmo":
        log.info("Extending forcing files using MeteoSwiss COSMO forecast.", indent=1)
        endpoint = api + "/meteoswiss/cosmo/point/forecast/VNXZ32/{}/{}/{}?variables=T_2M_MEAN&variables=U_MEAN&variables=V_MEAN&variables=GLOB_MEAN&variables=RELHUM_2M_MEAN&variables=CLCT_MEAN&variables=TOT_PREC_MEAN"
        today = datetime.now().strftime("%Y%m%d")
        try:
            data = call_url(endpoint.format(today, latitude, longitude))
        except Exception as e:
            print("ERROR", e)
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
            data = call_url(endpoint.format(yesterday, latitude, longitude))
        grid_elevation = get_elevation_swisstopo(data["lat"], data["lng"])
        data_dict = {key: data["variables"][key]["data"] for key in data["variables"].keys()}
        data_dict["utc_time"] = data["time"]
        df = pd.DataFrame(data_dict)
        df['Time'] = pd.to_datetime(df['utc_time'], utc=True)
        df['Time'] = df.apply(lambda row: datetime_to_simstrat_time(row['Time'], reference_date), axis=1)
        df["T_2M"] = df["T_2M"] - 273.15  # Kelvin to celsius
        df["T_2M"] = adjust_temperature_for_altitude_difference(df["T_2M"].values, elevation - grid_elevation)
        df["TOT_PREC"] = df["TOT_PREC"] * 0.001  # kg m-2 to m/hr
        df["CLCT"] = df["CLCT"] * 0.01  # Percentage to fraction
        df["u"] = df["U"]
        df["v"] = df["V"]
        df["Tair"] = df["T_2M"]
        df["sol"] = df["GLOB"]
        df["vap"] = vapor_pressure_from_relative_humidity_and_temperature(df["T_2M"].values, df["RELHUM_2M"])
        df["cloud"] = df["CLCT"]
        df["rain"] = df["TOT_PREC"]
        df = df[parameters]
    elif forcing_forecast["model"].lower() == "icon":
        log.info("Extending forcing files using MeteoSwiss ICON forecast.", indent=1)
        endpoint = api + "/meteoswiss/icon/point/forecast/icon-ch2-eps/{}/{}/{}?variables=T_2M&variables=U&variables=V&variables=GLOB&variables=RELHUM_2M&variables=CLCT&variables=TOT_PREC"
        today = datetime.now().strftime("%Y%m%d")
        try:
            data = call_url(endpoint.format(today, latitude, longitude))
        except Exception as e:
            print("ERROR", e)
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
            data = call_url(endpoint.format(yesterday, latitude, longitude))
        grid_elevation = get_elevation_swisstopo(data["lat"], data["lng"])
        data_dict = {key: data["variables"][key]["data"] for key in data["variables"].keys()}
        data_dict["utc_time"] = data["time"]
        df = pd.DataFrame(data_dict)
        df['Time'] = pd.to_datetime(df['utc_time'], utc=True)
        df['Time'] = df.apply(lambda row: datetime_to_simstrat_time(row['Time'], reference_date), axis=1)
        df["T_2M"] = df["T_2M"] - 273.15  # Kelvin to celsius
        df["T_2M"] = adjust_temperature_for_altitude_difference(df["T_2M"].values, elevation - grid_elevation)
        df["TOT_PREC"] = df["TOT_PREC"] * 0.001  # kg m-2 to m/hr
        df["CLCT"] = df["CLCT"] * 0.01  # Percentage to fraction
        df["u"] = df["U"]
        df["v"] = df["V"]
        df["Tair"] = df["T_2M"]
        df["sol"] = df["GLOB"]
        df["vap"] = vapor_pressure_from_relative_humidity_and_temperature(df["T_2M"].values, df["RELHUM_2M"])
        df["cloud"] = df["CLCT"]
        df["rain"] = df["TOT_PREC"]
        df = df[parameters]
    else:
        raise ValueError("MeteoSwiss forecast not implemented for model: {}".format(forcing_forecast["model"]))
    df.set_index('Time', inplace=True)
    df_o = df_c.fillna(df)
    df_o.reset_index(inplace=True)
    for key in df_o.columns:
        output[key]["data"] = df_o[key].values
    return output


def quality_assurance_forcing_data(forcing_data, log):
    log.info("Running quality assurance on forcing data", indent=1)
    for key in forcing_data.keys():
        if "negative_to_zero" in forcing_data[key] and forcing_data[key]["negative_to_zero"]:
            log.info("Setting negative {} values to 0".format(key), indent=2)
            forcing_data[key]["data"][forcing_data[key]["data"] < 0] = 0.0
        if "min" in forcing_data[key]:
            log.info("Setting {} values less than {} {} to nan".format(key, forcing_data[key]["min"],
                                                                       forcing_data[key]["unit"]), indent=2)
            forcing_data[key]["data"][forcing_data[key]["data"] < forcing_data[key]["min"]] = np.nan
        if "max" in forcing_data[key]:
            log.info("Setting {} values greater than {} {} to nan".format(key, forcing_data[key]["max"],
                                                                          forcing_data[key]["unit"]), indent=2)
            forcing_data[key]["data"][forcing_data[key]["data"] > forcing_data[key]["max"]] = np.nan
    return forcing_data


def interpolate_forcing_data(forcing_data):
    for key in forcing_data.keys():
        if "max_interpolate_gap" in forcing_data[key]:
            forcing_data[key]["data"] = interpolate_timeseries(forcing_data["Time"]["data"],
                                                               forcing_data[key]["data"],
                                                               max_gap_size=forcing_data[key]["max_interpolate_gap"])
    return forcing_data


def fill_forcing_data(forcing_data, simulation_dir, snapshot, reference_date, log):
    fill_required = False
    for key in forcing_data.keys():
        nan_values = np.isnan(forcing_data[key]["data"])
        if np.sum(nan_values) > 0:
            fill_required = True

    if fill_required:
        if snapshot:
            log.info("Reading previous forcing to generate fill statistics on full timeseries", indent=1)
            file_path = os.path.join(simulation_dir, "Forcing.dat")
            if not os.path.exists(file_path):
                raise ValueError("Unable to locate Forcing.dat files from previous run, unable to fill nan values. "
                                 "Please remove the snapshot and run the full simulation.")
            columns = ["Time", "u", "v", "Tair", "sol", "vap", "cloud", "rain"]
            time_min = forcing_data["Time"]["data"][0]
            df = pd.read_csv(file_path, skiprows=1, delim_whitespace=True, header=None)
            df.columns = columns
            df = df[df['Time'] < time_min]
            for key in forcing_data.keys():
                forcing_data[key]["data_extended"] = np.concatenate((df[key].values, forcing_data[key]["data"]))
    else:
        return forcing_data

    for key in forcing_data.keys():
        nan_values = np.isnan(forcing_data[key]["data"])
        if np.sum(nan_values) > 0:
            if "fill" in forcing_data[key]:
                if forcing_data[key]["fill"] == "mean":
                    if snapshot:
                        mean = np.nanmean(forcing_data[key]["data_extended"])
                    else:
                        mean = np.nanmean(forcing_data[key]["data"])
                    forcing_data[key]["data"][nan_values] = mean
                    log.info("Filling {} nan values in {} with mean value: {}".format(np.sum(nan_values), key, mean), indent=2)
                elif forcing_data[key]["fill"] == "doy":
                    log.info("Computing day of year values for {}".format(key), indent=2)
                    if snapshot:
                        forcing_data[key]["data"] = fill_day_of_year(forcing_data["Time"]["data"],
                                                                      forcing_data[key]["data"],
                                                                      forcing_data["Time"]["data_extended"],
                                                                      forcing_data[key]["data_extended"],
                                                                      reference_date)
                    else:
                        forcing_data[key]["data"] = fill_day_of_year(forcing_data["Time"]["data"],
                                                                      forcing_data[key]["data"],
                                                                      forcing_data["Time"]["data"],
                                                                      forcing_data[key]["data"],
                                                                      reference_date)
                elif forcing_data[key]["fill"] is None:
                    continue
                else:
                    raise ValueError("Fill not implemented for type: {}".format(forcing_data[key]["fill"]))
    return forcing_data
