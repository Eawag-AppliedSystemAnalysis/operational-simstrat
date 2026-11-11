import os.path
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from .general import (call_url, adjust_temperature_for_altitude_difference, air_pressure_from_elevation, detect_gaps,
                      clear_sky_solar_radiation, datetime_to_simstrat_time,
                      calculate_mean_wind_direction, interpolate_timeseries, fill_day_of_year,
                      vapor_pressure_from_relative_humidity_and_temperature, get_elevation_eudem25, calculate_vapor_pressure,
                      get_elevation)

import matplotlib.pyplot as plt


def metadata_from_forcing(forcing, api):
    required = [["air_temperature"], ["wind_speed"], ["wind_direction"], ["global_radiation"], ["vapour_pressure", "relative_humidity"]]
    parameter_dict = {}
    for f in forcing:
        source = f["type"].lower().split("_")[0]
        endpoint = "{}/{}/meteodata/metadata/{}".format(api, source, f["id"])
        data = call_url(endpoint)
        for key in data["variables"].keys():
            data["variables"][key]["start_date"] = datetime.strptime(data["variables"][key]["start_date"], '%Y-%m-%d').replace(tzinfo=timezone.utc) + timedelta(days=1)
            data["variables"][key]["end_date"] = datetime.strptime(data["variables"][key]["end_date"], '%Y-%m-%d').replace(tzinfo=timezone.utc)
            if key in parameter_dict:
                parameter_dict[key]["start"].append(data["variables"][key]["start_date"])
                parameter_dict[key]["end"].append(data["variables"][key]["end_date"])
            else:
                parameter_dict[key] = {"start": [data["variables"][key]["start_date"]], "end": [data["variables"][key]["end_date"]]}
        f["parameters"] = data["variables"]
        f["elevation"] = data["elevation"]
        f["latlng"] = [data["lat"], data["lng"]]
    start_list = []
    end_list = []
    for r in required:
        error = True
        for p in r:
            if p in parameter_dict:
                start_list.append(min(parameter_dict[p]["start"]))
                end_list.append(max(parameter_dict[p]["end"]))
                error = False
        if error:
            raise ValueError("Parameter {} is required, no data can be found from the stations".format(", ".join(r)))
    return max(start_list), min(end_list)


def download_forcing_data(output, start, end, forcing, forecast, forcing_forecast, elevation, latitude, longitude, reference_date, api, visualcrossing_key, log):
    output = meteodata_from_meteostations(start, end, forcing, elevation, latitude, longitude, reference_date, output, api, log)
    if forecast:
        if forcing_forecast["source"].lower() == "meteoswiss":
            output = meteodata_forecast_from_meteoswiss(forcing_forecast, elevation, latitude, longitude, reference_date, output, api, log)
        elif forcing_forecast["source"].lower() == "visualcrossing":
            output = meteodata_forecast_from_visualcrossing(forcing_forecast, elevation, latitude, longitude, reference_date, output, visualcrossing_key, log)
    estimate = output["cloud"].pop("estimate")
    missing = np.isnan(output["cloud"]["data"])
    output["cloud"]["data"][missing] = estimate[missing]
    return output


# Ratio keeps non-negative variables non-negative; wind direction is circular so it is not adjusted
GAP_FILL_ADJUSTMENT = {"air_temperature": "offset", "vapour_pressure": "offset", "relative_humidity": "offset",
                       "wind_speed": "ratio", "precipitation": "ratio", "global_radiation": "ratio",
                       "wind_direction": None}
GAP_FILL_WINDOW = timedelta(days=30)
GAP_FILL_MIN_OVERLAP = 72  # hours


def map_backup_to_primary(p_id, primary, backup):
    method = GAP_FILL_ADJUSTMENT.get(p_id)
    if method is None:
        return backup, "no adjustment"
    common = pd.concat([primary, backup], axis=1, join="inner").dropna()
    if len(common) < GAP_FILL_MIN_OVERLAP:
        return backup, "no adjustment, only {} h overlap".format(len(common))
    mean_primary, mean_backup = common.iloc[:, 0].mean(), common.iloc[:, 1].mean()
    if method == "offset":
        return backup + (mean_primary - mean_backup), "offset {:+.3f} from {} h overlap".format(
            mean_primary - mean_backup, len(common))
    if mean_backup <= 0:
        return backup, "no adjustment, backup mean is zero over {} h overlap".format(len(common))
    return backup * (mean_primary / mean_backup), "ratio {:.3f} from {} h overlap".format(
        mean_primary / mean_backup, len(common))


def meteodata_from_meteostations(start, end, forcing, elevation, latitude, longitude, reference_date, output, api, log):
    endpoint = api + "/{}/meteodata/measured/{}/{}/{}?variables={}&resample=hourly"

    time = start + np.arange(0, (end - start).total_seconds() / 3600 + 1, 1).astype(int) * timedelta(hours=1)
    df_t = pd.DataFrame({'time': time})
    df_t['time'] = pd.to_datetime(df_t['time'])
    output["Time"]["data"] = np.array([datetime_to_simstrat_time(t, reference_date) for t in time])

    parameter_ids = ["wind_speed", "wind_direction", "precipitation", "air_temperature", "global_radiation", "vapour_pressure", "relative_humidity"]
    configured_parameters = {p_id for f in forcing for p_id in f["parameters"].keys()}
    raw_data = {}
    for p_id in parameter_ids:
        gaps = None  # None until a primary station is chosen, [] means it has no gaps
        df = False
        for f in forcing:
            if p_id in f["parameters"].keys():
                source = f["type"].lower().split("_")[0]
                parameter = f["parameters"][p_id]
                if parameter["end_date"] < start:
                    continue
                if gaps is None:
                    start_date = min(max(start, parameter["start_date"]), parameter["end_date"])
                    end_date = min(end, parameter["end_date"])
                    log.info(
                        "{}: Using data from station {} : {} - {}".format(p_id, f["id"], start_date.strftime('%Y%m%d'),
                                                                          end_date.strftime('%Y%m%d')), indent=1)
                    url = endpoint.format(source, f["id"], start_date.strftime('%Y%m%d'), end_date.strftime('%Y%m%d'), p_id)
                    print(url)
                    data = call_url(url)
                    values = np.array(data["variables"][p_id]["data"])
                    if p_id == "air_temperature":
                        values = adjust_temperature_for_altitude_difference(values, elevation - f["elevation"])
                    df = pd.DataFrame({'time': data["time"], 'values': values})
                    df = df.drop_duplicates(subset=['time'])
                    df['time'] = pd.to_datetime(df['time'])
                    df['values'] = pd.to_numeric(df['values'], errors='coerce')
                    df = df.dropna()
                    df = df.sort_values(by='time')
                    gaps = detect_gaps(df["time"], start, end)
                elif len(gaps) > 0:
                    for gap in gaps:
                        if gap[1] >= parameter["start_date"] and gap[0] <= parameter["end_date"]:
                            log.info("{}: Trying to complete with data from station {} : {} - {}".format(
                                p_id, f["id"], gap[0].strftime('%Y%m%d'), gap[1].strftime('%Y%m%d')), indent=2)
                            try:
                                fetch_start = max(gap[0] - GAP_FILL_WINDOW, start, parameter["start_date"])
                                fetch_end = min(gap[1] + GAP_FILL_WINDOW, end, parameter["end_date"])
                                url = endpoint.format(source, f["id"], fetch_start.strftime('%Y%m%d'),
                                                      fetch_end.strftime('%Y%m%d'), p_id)
                                print(url)
                                data = call_url(url)
                                d_new = np.array(data["variables"][p_id]["data"], dtype=float)
                                if p_id == "air_temperature":
                                    d_new = adjust_temperature_for_altitude_difference(d_new, elevation - f["elevation"])
                                backup = pd.Series(d_new, index=pd.to_datetime(data["time"]))
                                backup = backup[~backup.index.duplicated()].dropna()
                                primary = df.set_index("time")["values"]
                                backup, how = map_backup_to_primary(p_id, primary, backup)
                                log.info("{}: Mapped {} onto primary station: {}".format(p_id, f["id"], how), indent=3)
                                backup = backup[(backup.index >= gap[0]) & (backup.index <= gap[1])]
                                df = primary.combine_first(backup).dropna().sort_index()
                                df = df.rename_axis("time").rename("values").reset_index()
                            except Exception as e:
                                log.warning("{}: Failed to fill gap from station {}: {}".format(p_id, f["id"], e))
                    gaps = detect_gaps(df["time"], start, end)
        if isinstance(df, pd.core.frame.DataFrame):
            df_m = pd.merge(df_t, df, on='time', how='left')
            raw_data[p_id] = np.array(df_m["values"])
        elif p_id in configured_parameters:
            log.info("{}: No measured data in requested period, deferring to forecast".format(p_id), indent=1)
            raw_data[p_id] = np.full(len(time), np.nan)

    log.info("Processing wind from magnitude and direction to components", indent=1)
    wind_direction = raw_data["wind_direction"]
    wind_magnitude = raw_data["wind_speed"]
    wind_direction_mean = calculate_mean_wind_direction(wind_direction)
    log.info("Set missing direction values to average wind direction {}°".format(wind_direction_mean), indent=2)
    wind_direction[np.isnan(wind_direction)] = wind_direction_mean
    output["u"]["data"] = -wind_magnitude * np.sin(wind_direction * np.pi / 180)
    output["v"]["data"] = -wind_magnitude * np.cos(wind_direction * np.pi / 180)

    output["Tair"]["data"] = raw_data["air_temperature"]
    output["sol"]["data"] = raw_data["global_radiation"]

    air_pressure = air_pressure_from_elevation(elevation)

    if "vapour_pressure" not in raw_data:
        raw_data["vapour_pressure"] = calculate_vapor_pressure(raw_data["air_temperature"], raw_data["relative_humidity"], air_pressure)
    output["vap"]["data"] = raw_data["vapour_pressure"]

    if "precipitation" in raw_data:
        output["rain"]["data"] = raw_data["precipitation"] * 0.001  # Convert mm to m
    else:
        output["rain"]["data"] = np.zeros(len(raw_data["air_temperature"]))

    log.info("Estimate cloudiness based on ratio between measured and theoretical solar radiation", indent=1)
    cssr = clear_sky_solar_radiation(time, air_pressure, output["vap"]["data"], latitude, longitude)
    df = pd.DataFrame({"cssr": cssr, "swr": output["sol"]["data"]})
    cssr_rolling = df['cssr'].rolling(window=24, center=True, min_periods=1).mean()
    swr_rolling = df['swr'].rolling(window=24, center=True, min_periods=1).mean()
    solar_index = np.interp(swr_rolling / cssr_rolling, [0, 1],
                            [0, 1])  # Flerchinger et al. (2009), Crawford and Duchon (1999)
    cloud = 1 - solar_index
    # The 24 h rolling window extends the estimate ~12 h beyond the last solar observation. Only keep it where solar
    # radiation was observed, so a forecast's cloud cover takes over after the observations; the full estimate is the
    # fallback where neither is available (applied in download_forcing_data).
    output["cloud"]["data"] = np.where(np.isnan(output["sol"]["data"]), np.nan, cloud)
    output["cloud"]["estimate"] = cloud
    return output


def meteodata_forecast_from_meteoswiss(forcing_forecast, elevation, latitude, longitude, reference_date, output, api, log):
    parameters = ["Time", "u", "v", "Tair", "sol", "vap", "cloud", "rain"]
    df_c = pd.DataFrame({key: output[key]["data"] for key in output.keys()})
    df_c = df_c.set_index('Time')
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
        grid_elevation = get_elevation(data["lat"], data["lng"])
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
        endpoint = api + "/meteoswiss/icon/point/forecast/icon-ch2-eps/{}/{}/{}?" + ICON_VARIABLES
        today = datetime.now().strftime("%Y%m%d")
        try:
            data = call_url(endpoint.format(today, latitude, longitude))
        except Exception as e:
            print("ERROR", e)
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
            data = call_url(endpoint.format(yesterday, latitude, longitude))
        check_icon_distance(data, "icon-ch2-eps")
        grid_elevation = get_elevation(data["lat"], data["lng"])
        data_dict = {key: data["variables"][key]["data"] for key in data["variables"].keys()}
        data_dict["utc_time"] = data["time"]
        df = icon_to_forcing(pd.DataFrame(data_dict), elevation, grid_elevation, reference_date)
    else:
        raise ValueError("MeteoSwiss forecast not implemented for model: {}".format(forcing_forecast["model"]))
    df = df.set_index('Time')
    df_o = df_c.fillna(df)
    df_o = df_o.reset_index()
    for key in df_o.columns:
        output[key]["data"] = df_o[key].to_numpy(copy=True)
    return output


ICON_VARIABLES = "variables=T_2M&variables=U&variables=V&variables=GLOB&variables=RELHUM_2M&variables=CLCT&variables=TOT_PREC"
ICON_MAX_DISTANCE = 5000  # m; the Alplakes API returns the nearest grid cell however far away it is
FORCING_COLUMNS = ["Time", "u", "v", "Tair", "sol", "vap", "cloud", "rain"]


def check_icon_distance(data, product):
    distance = data["distance"]["data"]
    if distance > ICON_MAX_DISTANCE:
        raise ValueError("The lake is {:.0f} km from the nearest {} grid cell, outside the model domain".format(
            distance / 1000, product))


def icon_to_forcing(df, elevation, grid_elevation, reference_date):
    """Convert an Alplakes ICON point forecast (columns T_2M, U, V, GLOB, RELHUM_2M, CLCT, TOT_PREC, utc_time)
    to Simstrat forcing columns. Shared by the ensemble-mean forecast and the individual ensemble members."""
    df = df.copy()
    if pd.isna(df.loc[0, 'GLOB']):
        df.loc[0, 'GLOB'] = 0.0
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
    return df[FORCING_COLUMNS]


def meteodata_forecast_members(elevation, latitude, longitude, reference_date, forecast_start, api, log):
    """Download every ICON-CH2-EPS member for the run starting at forecast_start (00 UTC) from the Alplakes API.
    Returns a list of (member number, forcing DataFrame) with the same conversion as the ensemble-mean forecast."""
    endpoint = api + "/meteoswiss/icon/point/forecast/icon-ch2-eps-members/{}/{}/{}?" + ICON_VARIABLES
    log.info("Downloading ICON-CH2-EPS members for {}".format(forecast_start.strftime("%Y-%m-%d")), indent=1)
    data = call_url(endpoint.format(forecast_start.strftime("%Y%m%d"), latitude, longitude))
    first_time = pd.to_datetime(data["time"][0], utc=True)
    if first_time != pd.Timestamp(forecast_start):
        raise ValueError("ICON members start at {}, expected {}".format(first_time, forecast_start))
    check_icon_distance(data, "icon-ch2-eps-members")
    grid_elevation = get_elevation(data["lat"], data["lng"])
    values = {key: np.array(data["variables"][key]["data"], dtype=float) for key in data["variables"].keys()}
    members = []
    for i, member in enumerate(data["members"]):
        data_dict = {key: values[key][:, i] for key in values.keys()}
        data_dict["utc_time"] = data["time"]
        members.append((member, icon_to_forcing(pd.DataFrame(data_dict), elevation, grid_elevation, reference_date)))
    log.info("Received {} members, {} time steps".format(len(members), len(data["time"])), indent=2)
    return members


def member_forcing_data(forcing_data, member_df, forecast_start, reference_date, history_days=2):
    """Forcing for one ensemble member: the final base forcing for the history_days before forecast_start (so the
    restart has forcing to interpolate from), then the member forecast. Member gaps (e.g. lead-0 rain) are filled
    from the base forcing at the same time. Returns a new forcing dict with the same metadata as forcing_data."""
    t0 = round(datetime_to_simstrat_time(forecast_start, reference_date), 4)
    base = pd.DataFrame({c: forcing_data[c]["data"] for c in FORCING_COLUMNS})
    base["Time"] = base["Time"].round(4)
    base = base.drop_duplicates(subset="Time").set_index("Time")
    member = member_df.copy()
    member["Time"] = member["Time"].round(4)
    member = member.set_index("Time")
    member = member[member.index >= t0]
    member = member.fillna(base.reindex(member.index))
    history = base[(base.index >= t0 - history_days) & (base.index < t0)]
    df = pd.concat([history, member]).reset_index()
    output = {}
    for key in FORCING_COLUMNS:
        output[key] = {k: v for k, v in forcing_data[key].items() if k not in ("data", "data_extended")}
        output[key]["data"] = df[key].to_numpy(dtype=float, copy=True)
    return output


def meteodata_forecast_from_visualcrossing(forcing_forecast, elevation, latitude, longitude, reference_date, output, key, log):
    parameters = ["Time", "u", "v", "Tair", "sol", "vap", "cloud", "rain"]
    df_c = pd.DataFrame({key: output[key]["data"] for key in output.keys()})
    df_c = df_c.set_index('Time')
    start = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    end = (datetime.now() + timedelta(days=forcing_forecast["days"])).strftime("%Y-%m-%d")
    log.info("Extending forcing files using Visual Crossing forecast.", indent=1)
    endpoint = "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/{}%2C{}/{}/{}?unitGroup=metric&include=hours&key={}&contentType=json"
    print(endpoint.format(latitude, longitude, start, end, key))
    data = call_url(endpoint.format(latitude, longitude, start, end, key))
    grid_elevation = get_elevation_eudem25(data["latitude"], data["longitude"])

    data_flat = [hour for day in data["days"] for hour in day["hours"]]
    data_dict = {key: [hour[key] for hour in data_flat] for key in ["datetimeEpoch", "temp", "humidity", "precip", "windspeed", "winddir", "cloudcover", "solarradiation"]}
    df = pd.DataFrame(data_dict)
    df['Time'] = df.apply(lambda row: datetime_to_simstrat_time(datetime.fromtimestamp(row['datetimeEpoch']).replace(tzinfo=timezone.utc), reference_date), axis=1)
    df["Tair"] = adjust_temperature_for_altitude_difference(df["temp"].values, elevation - grid_elevation)
    df["rain"] = df["precip"] * 0.001 # mm/hr to m/hr
    df["windspeed"] = df["windspeed"] * (1000/3600) # km/h to m/s
    df["sol"] = df["solarradiation"]
    df["cloud"] = df["cloudcover"] * 0.01 # % to decimal
    df["vap"] = vapor_pressure_from_relative_humidity_and_temperature(df["temp"].values, df["humidity"])
    df["u"] = -df["windspeed"] * np.sin(df["winddir"] * np.pi / 180)
    df["v"] = -df["windspeed"] * np.cos(df["winddir"] * np.pi / 180)
    df = df[parameters]
    df = df.set_index('Time')
    df_o = df_c.fillna(df)
    df_o = df_o.reset_index()
    for key in df_o.columns:
        output[key]["data"] = df_o[key].to_numpy(copy=True)
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
            df = pd.read_csv(file_path, skiprows=1, sep=r'\s+', header=None)
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
