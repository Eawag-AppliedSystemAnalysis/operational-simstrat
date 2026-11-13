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


# Linear matches mean and spread like the legacy rescaling, but per month; ratio keeps non-negative variables
# non-negative. Used only for gaps up to GAP_FILL_MAX_DAYS, where hold-out tests on real station data showed it beats
# the legacy rescaling in every case; all other gaps and variables keep the legacy rescaling.
GAP_FILL_MAPPING = {"air_temperature": "linear", "relative_humidity": "linear", "vapour_pressure": "ratio",
                    "global_radiation": "ratio"}
GAP_FILL_MAX_DAYS = {"air_temperature": 90, "vapour_pressure": 90, "relative_humidity": 30, "global_radiation": 90}
GAP_FILL_REFERENCE = timedelta(days=3 * 365)
GAP_FILL_MIN_MONTH_HOURS = 240
GAP_FILL_MIN_TOTAL_HOURS = 720
MID_MONTH_DOY = np.array([15.5, 45.0, 74.5, 105.0, 135.5, 166.0, 196.5, 227.5, 258.0, 288.5, 319.0, 349.5])


def legacy_rescale(arr, std, mean):
    arr = np.array(arr, dtype=float)
    data_mean = np.nanmean(arr)
    data_std = np.nanstd(arr)
    if np.isnan(data_mean) or np.isnan(data_std) or data_std == 0:
        print("Forcing data not adjusted")
        return arr

    return (arr - data_mean) / data_std * std + mean


def reference_window(year, overlap_start, overlap_end):
    """The GAP_FILL_REFERENCE period of the stations' common record nearest to the given year."""
    centre = datetime(year, 7, 2, tzinfo=timezone.utc)
    ref_start = centre - GAP_FILL_REFERENCE / 2
    ref_end = ref_start + GAP_FILL_REFERENCE
    if ref_start < overlap_start:
        ref_start, ref_end = overlap_start, overlap_start + GAP_FILL_REFERENCE
    if ref_end > overlap_end:
        ref_start, ref_end = overlap_end - GAP_FILL_REFERENCE, overlap_end
    return max(ref_start, overlap_start), min(ref_end, overlap_end)


def fit_monthly_mapping(p_id, primary, backup):
    """Monthly parameters mapping the backup onto the primary, from the hours both measured. Months with too few
    hours use the fit over all of them. Returns None if there is too little overlap to fit."""
    method = GAP_FILL_MAPPING[p_id]
    common = pd.concat([primary, backup], axis=1, join="inner").dropna()
    if len(common) < GAP_FILL_MIN_TOTAL_HOURS:
        return None

    def fit(p, b):
        if method == "linear":
            if b.std() > 0 and p.std() > 0:
                return {"mean_primary": p.mean(), "std_primary": p.std(), "mean_backup": b.mean(), "std_backup": b.std()}
            return None
        return {"ratio": p.sum() / b.sum()} if b.sum() > 0 else None

    overall = fit(common.iloc[:, 0], common.iloc[:, 1])
    if overall is None:
        return None
    params, own = {k: np.full(12, v, dtype=float) for k, v in overall.items()}, 0
    for month in range(1, 13):
        sel = common[common.index.month == month]
        if len(sel) >= GAP_FILL_MIN_MONTH_HOURS:
            value = fit(sel.iloc[:, 0], sel.iloc[:, 1])
            if value is not None:
                for k, v in value.items():
                    params[k][month - 1] = v
                own += 1
    return {"method": method, "params": params, "own_months": own, "hours": len(common)}


def apply_monthly_mapping(p_id, backup, mapping):
    """Map backup values with the monthly parameters interpolated by day of year between mid-month points."""
    doy = backup.index.day_of_year.to_numpy() - 1 + backup.index.hour.to_numpy() / 24
    xp = np.concatenate(([MID_MONTH_DOY[-1] - 365], MID_MONTH_DOY, [MID_MONTH_DOY[0] + 365]))
    p = {k: np.interp(doy, xp, np.concatenate(([v[-1]], v, [v[0]]))) for k, v in mapping["params"].items()}
    if mapping["method"] == "linear":
        mapped = (backup - p["mean_backup"]) / p["std_backup"] * p["std_primary"] + p["mean_primary"]
    else:
        mapped = backup * p["ratio"]
    if p_id == "relative_humidity":
        mapped = mapped.clip(0, 100)
    return mapped


def describe_mapping(mapping):
    p = mapping["params"]
    if mapping["method"] == "linear":
        offset = p["mean_primary"] - p["mean_backup"]
        scale = p["std_primary"] / p["std_backup"]
        return "offset {:.3f} to {:.3f}, scale {:.3f} to {:.3f}".format(offset.min(), offset.max(), scale.min(), scale.max())
    return "ratio {:.3f} to {:.3f}".format(p["ratio"].min(), p["ratio"].max())


def fetch_station_series(endpoint, station, p_id, start_date, end_date, elevation):
    source = station["type"].lower().split("_")[0]
    data = call_url(endpoint.format(source, station["id"], start_date.strftime('%Y%m%d'), end_date.strftime('%Y%m%d'), p_id))
    values = pd.to_numeric(pd.Series(data["variables"][p_id]["data"]), errors="coerce").to_numpy(dtype=float)
    if p_id == "air_temperature":
        values = adjust_temperature_for_altitude_difference(values, elevation - station["elevation"])
    series = pd.Series(values, index=pd.to_datetime(data["time"]))
    return series[~series.index.duplicated()].dropna().sort_index()


STATION_PARAMETERS = ["wind_speed", "wind_direction", "precipitation", "air_temperature", "global_radiation",
                      "vapour_pressure", "relative_humidity"]


def meteodata_from_meteostations(start, end, forcing, elevation, latitude, longitude, reference_date, output, api, log):
    """Hourly forcing from the lake's meteo stations. For each variable the first station in the list that measures it
    is the primary; later stations fill the primary's gaps."""
    endpoint = api + "/{}/meteodata/measured/{}/{}/{}?variables={}&resample=hourly"
    time = start + np.arange(0, (end - start).total_seconds() / 3600 + 1, 1).astype(int) * timedelta(hours=1)
    output["Time"]["data"] = np.array([datetime_to_simstrat_time(t, reference_date) for t in time])

    configured_parameters = {p_id for station in forcing for p_id in station["parameters"].keys()}
    raw_data = {}
    for p_id in STATION_PARAMETERS:
        series = station_series(p_id, start, end, forcing, elevation, endpoint, log)
        if series is not None:
            raw_data[p_id] = values_at_times(series, time)
        elif p_id in configured_parameters:
            log.info("{}: No measured data in requested period, deferring to forecast".format(p_id), indent=1)
            raw_data[p_id] = np.full(len(time), np.nan)

    wind_to_components(raw_data, output, log)
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
    estimate_cloud_cover(output, time, air_pressure, latitude, longitude, log)
    return output


def station_series(p_id, start, end, forcing, elevation, endpoint, log):
    """One variable from the stations in priority order: the first becomes the primary, later ones fill its gaps.
    Returns a DataFrame (time, values), or None if no station has data in the period."""
    primary = None
    for station in forcing:
        if p_id not in station["parameters"].keys():
            continue
        parameter = station["parameters"][p_id]
        if parameter["end_date"] < start:
            continue
        if primary is None:
            primary = download_primary(p_id, station, parameter, start, end, elevation, endpoint, log)
        elif len(primary["gaps"]) > 0:
            fill_gaps_from_backup(primary, p_id, station, parameter, start, end, elevation, endpoint, log)
    return None if primary is None else primary["df"]


def download_primary(p_id, station, parameter, start, end, elevation, endpoint, log):
    source = station["type"].lower().split("_")[0]
    start_date = min(max(start, parameter["start_date"]), parameter["end_date"])
    end_date = min(end, parameter["end_date"])
    log.info("{}: Using data from station {} : {} - {}".format(p_id, station["id"], start_date.strftime('%Y%m%d'),
                                                               end_date.strftime('%Y%m%d')), indent=1)
    url = endpoint.format(source, station["id"], start_date.strftime('%Y%m%d'), end_date.strftime('%Y%m%d'), p_id)
    print(url)
    data = call_url(url)
    values = np.array(data["variables"][p_id]["data"])
    if p_id == "air_temperature":
        values = adjust_temperature_for_altitude_difference(values, elevation - station["elevation"])
    df = pd.DataFrame({'time': data["time"], 'values': values})
    df = df.drop_duplicates(subset=['time'])
    df['time'] = pd.to_datetime(df['time'])
    df['values'] = pd.to_numeric(df['values'], errors='coerce')
    df = df.dropna()
    df = df.sort_values(by='time')
    # Statistics for the legacy rescaling, and caches of reference data and monthly fits per backup station
    return {"station": station, "parameter": parameter, "df": df, "adjust": True if len(df) > 1000 else False,
            "mean": df["values"].mean(), "std": df["values"].std(), "references": {}, "fits": {},
            "gaps": detect_gaps(df["time"], start, end)}


def fill_gaps_from_backup(primary, p_id, station, parameter, start, end, elevation, endpoint, log):
    overlap = (max(primary["parameter"]["start_date"], parameter["start_date"]),
               min(primary["parameter"]["end_date"], parameter["end_date"]))
    for gap in primary["gaps"]:
        if gap[1] >= parameter["start_date"] and gap[0] <= parameter["end_date"]:
            log.info("{}: Trying to complete with data from station {} : {} - {}".format(
                p_id, station["id"], gap[0].strftime('%Y%m%d'), gap[1].strftime('%Y%m%d')), indent=2)
            try:
                backup = backup_values_for_gap(primary, p_id, station, gap, overlap, elevation, endpoint, log)
                primary["df"] = fill_missing_values(primary["df"], backup)
            except Exception as e:
                log.warning("{}: Failed to fill gap from station {}: {}".format(p_id, station["id"], e))
    primary["gaps"] = detect_gaps(primary["df"]["time"], start, end)


def backup_values_for_gap(primary, p_id, station, gap, overlap, elevation, endpoint, log):
    """The backup station's data for the gap, mapped onto the primary: legacy rescaling, or the monthly mapping for
    the variables and gap lengths in GAP_FILL_MAPPING / GAP_FILL_MAX_DAYS."""
    source = station["type"].lower().split("_")[0]
    url = endpoint.format(source, station["id"], gap[0].strftime('%Y%m%d'), gap[1].strftime('%Y%m%d'), p_id)
    print(url)
    data = call_url(url)
    raw = pd.to_numeric(pd.Series(data["variables"][p_id]["data"]), errors="coerce").to_numpy(dtype=float)
    legacy = legacy_rescale(raw, primary["std"], primary["mean"]) if primary["adjust"] else raw
    backup = pd.Series(legacy, index=pd.to_datetime(data["time"]))
    if p_id not in GAP_FILL_MAPPING or gap[1] - gap[0] > timedelta(days=GAP_FILL_MAX_DAYS[p_id]):
        log.info("{}: {}: legacy rescaling".format(p_id, station["id"]), indent=3)
        return backup
    if p_id == "air_temperature":
        raw = adjust_temperature_for_altitude_difference(raw, elevation - station["elevation"])
    raw = pd.Series(raw, index=backup.index)
    for year in sorted(set(backup.index.year)):
        mapping, window = monthly_mapping_for_year(primary, p_id, station, year, overlap, elevation, endpoint, log)
        in_year = backup.index.year == year
        if mapping is None:
            how = "legacy rescaling (no overlap)" if window is None else "legacy rescaling (too little overlap)"
        else:
            backup[in_year] = apply_monthly_mapping(p_id, raw[in_year], mapping)
            how = "{} ({} own months, {} h, reference {} - {})".format(
                describe_mapping(mapping), mapping["own_months"], mapping["hours"],
                window[0].strftime('%Y%m%d'), window[1].strftime('%Y%m%d'))
        log.info("{}: {} {}: {}".format(p_id, station["id"], year, how), indent=3)
    return backup


def monthly_mapping_for_year(primary, p_id, station, year, overlap, elevation, endpoint, log):
    """The monthly mapping of the backup onto the primary for one year, fitted on the reference window nearest to
    it and cached. Returns (mapping or None, window or None)."""
    window = reference_window(year, overlap[0], overlap[1]) if overlap[1] > overlap[0] else None
    key = (station["id"], window)
    if key not in primary["fits"]:
        primary["fits"][key] = None
        if window is not None:
            try:
                for s in (primary["station"], station):
                    if (s["id"], window) not in primary["references"]:
                        primary["references"][(s["id"], window)] = fetch_station_series(
                            endpoint, s, p_id, window[0], window[1], elevation)
                primary["fits"][key] = fit_monthly_mapping(p_id, primary["references"][(primary["station"]["id"], window)],
                                                        primary["references"][(station["id"], window)])
            except Exception as e:
                log.warning("{}: Failed to fit {} onto {}, using legacy rescaling: {}".format(
                    p_id, station["id"], primary["station"]["id"], e))
    return primary["fits"][key], window


def fill_missing_values(df, backup):
    """Use the backup values where df (time, values) has none."""
    df_new = pd.DataFrame({'time': backup.index, 'values_new': backup.to_numpy()})
    df_new = df_new.drop_duplicates(subset=['time'])
    df = pd.merge(df, df_new, on='time', how='outer')
    df['values'] = df['values'].combine_first(df['values_new'])
    df = df[["time", "values"]]
    df = df.dropna()
    df = df.sort_values(by='time')
    return df.reset_index(drop=True)


def values_at_times(df, time):
    df_t = pd.DataFrame({'time': time})
    df_t['time'] = pd.to_datetime(df_t['time'])
    df_m = pd.merge(df_t, df, on='time', how='left')
    return np.array(df_m["values"])


def wind_to_components(raw_data, output, log):
    log.info("Processing wind from magnitude and direction to components", indent=1)
    wind_direction = raw_data["wind_direction"]
    wind_magnitude = raw_data["wind_speed"]
    wind_direction_mean = calculate_mean_wind_direction(wind_direction)
    log.info("Set missing direction values to average wind direction {}°".format(wind_direction_mean), indent=2)
    wind_direction[np.isnan(wind_direction)] = wind_direction_mean
    output["u"]["data"] = -wind_magnitude * np.sin(wind_direction * np.pi / 180)
    output["v"]["data"] = -wind_magnitude * np.cos(wind_direction * np.pi / 180)


def estimate_cloud_cover(output, time, air_pressure, latitude, longitude, log):
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
