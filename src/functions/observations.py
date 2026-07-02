import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

from .general import call_url, datetime_to_simstrat_time

DATALAKES_API = "https://api.datalakes-eawag.ch"


def _closest_profile(csv_path, start_date, log, max_days=32, direction="closest"):
    df = pd.read_csv(csv_path)
    df.columns = [c.strip().lower() for c in df.columns]
    if not {"time", "depth", "value"}.issubset(df.columns):
        return None
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.dropna(subset=["time", "depth", "value"])
    if df.empty:
        return None
    start = pd.Timestamp(start_date)
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    times = df["time"].drop_duplicates().reset_index(drop=True)
    name = os.path.basename(csv_path)
    if direction == "forward":
        forward_times = times[times >= start].reset_index(drop=True)
        if forward_times.empty:
            log.warning("No profile in {} on or after start_date.".format(name))
            return None
        diffs = forward_times - start
        idx = diffs.idxmin()
        diff_days = diffs.iloc[idx].total_seconds() / 86400.0
        if diff_days > max_days:
            log.warning("No profile in {} within {} days after start_date (earliest is {:.1f} days away).".format(name, max_days, diff_days))
            return None
        chosen = forward_times.iloc[idx]
        log.info("Using {} profile {:.1f} days after start_date.".format(name, diff_days), indent=2)
    else:
        diffs = (times - start).abs()
        idx = diffs.idxmin()
        diff_days = diffs.iloc[idx].total_seconds() / 86400.0
        if diff_days > max_days:
            log.warning("No profile in {} within {} days of start_date (closest is {:.1f} days away).".format(name, max_days, diff_days))
            return None
        chosen = times.iloc[idx]
        log.info("Using {} profile {:.1f} days from start_date.".format(name, diff_days), indent=2)
    profile = df[df["time"] == chosen].sort_values("depth").reset_index(drop=True)
    return {"profile": profile, "time": chosen}


def initial_conditions_from_observations(observations_dir, key, start_date, max_depth, log, salinity=0.15):
    try:
        lake_dir = os.path.join(observations_dir, key)
        temp_path = os.path.join(lake_dir, "temperature.csv")
        if not os.path.isfile(temp_path):
            return False
        t_result = _closest_profile(temp_path, start_date, log, max_days=183, direction="forward")
        if t_result is None:
            return False
        t_profile = t_result["profile"]
        chosen_time = t_result["time"]
        if t_profile.empty:
            return False
        t_profile["depth"] = t_profile["depth"].abs()
        t_profile = t_profile.sort_values("depth").reset_index(drop=True)
        t_profile = t_profile[t_profile["depth"] <= max_depth].reset_index(drop=True)
        if t_profile.empty:
            return False
        if t_profile["depth"].iloc[0] > 0:
            log.warning("Shallowest temperature observation depth {:.3f} m > 0; snapping to 0.".format(t_profile["depth"].iloc[0]))
            t_profile.loc[0, "depth"] = 0.0
        depth_arr = t_profile["depth"].to_numpy()
        temperature_arr = t_profile["value"].to_numpy()

        salinity_arr = np.full(len(depth_arr), salinity)
        sal_path = os.path.join(lake_dir, "salinity.csv")
        if os.path.isfile(sal_path):
            s_result = _closest_profile(sal_path, chosen_time, log)
            if s_result is not None:
                s_profile = s_result["profile"]
                if not s_profile.empty:
                    s_profile["depth"] = s_profile["depth"].abs()
                    s_profile = s_profile.sort_values("depth")
                    salinity_arr = np.interp(depth_arr, s_profile["depth"].to_numpy(), s_profile["value"].to_numpy())

        return {"depth": depth_arr, "temperature": temperature_arr, "salinity": salinity_arr,
                "start_date": chosen_time.to_pydatetime()}
    except Exception:
        return False


def climatology_initial_conditions_from_observations(observations_dir, key, start_date, max_depth, log,
                                                     salinity=0.15, min_years=5, day_window=14):
    try:
        lake_dir = os.path.join(observations_dir, key)
        temp_path = os.path.join(lake_dir, "temperature.csv")
        if not os.path.isfile(temp_path):
            return False
        df = pd.read_csv(temp_path)
        df.columns = [c.strip().lower() for c in df.columns]
        if not {"time", "depth", "value"}.issubset(df.columns):
            return False
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.dropna(subset=["time", "depth", "value"])
        if df.empty:
            return False

        target_doy = start_date.timetuple().tm_yday
        doy = df["time"].dt.dayofyear.to_numpy()
        diff = np.abs(doy - target_doy)
        circ_diff = np.minimum(diff, 365 - diff)
        df = df.loc[circ_diff <= day_window].copy()
        if df.empty:
            log.warning("No temperature observations within ±{} days of day-of-year {}.".format(day_window, target_doy))
            return False

        df["depth"] = df["depth"].abs()

        standard_depths = np.array([0, 2, 5, 10, 15, 20, 30, 40, 50, 75, 100, 150, 200, 300], dtype=float)
        depth_arr = np.append(standard_depths[standard_depths < max_depth], max_depth)

        years = set()
        interpolated = []
        for chosen_time, group in df.groupby("time"):
            prof = group.sort_values("depth").reset_index(drop=True)
            prof = prof[prof["depth"] <= max_depth].reset_index(drop=True)
            if len(prof) < 2:
                continue
            if prof["depth"].iloc[0] > 0:
                prof.loc[0, "depth"] = 0.0
            t_interp = np.interp(depth_arr, prof["depth"].to_numpy(), prof["value"].to_numpy())
            interpolated.append(t_interp)
            years.add(chosen_time.year)

        if len(years) < min_years:
            log.warning("Only {} distinct years of observations within ±{} days of day-of-year {} "
                        "(need {}); skipping climatology.".format(len(years), day_window, target_doy, min_years))
            return False

        temperature_arr = np.mean(np.vstack(interpolated), axis=0)
        salinity_arr = np.full(len(depth_arr), salinity)
        log.info("Built climatological initial profile from {} profiles across {} years.".format(
            len(interpolated), len(years)), indent=2)
        return {"depth": depth_arr, "temperature": temperature_arr, "salinity": salinity_arr}
    except Exception:
        return False


def default_initial_conditions(doy, elevation, max_depth, salinity=0.15):
    depths = np.array([0, 10, 20, 30, 40, 50, 100, 150, 200, 300])
    depth_arr = np.append(depths[depths < max_depth], max_depth)
    salinity_arr = [salinity] * len(depth_arr)
    temperature_profile_500m = np.array(
        [[5.5, 5.5, 5.0, 5.0, 5.0, 4.5, 4.5, 4.5, 4.5, 4.5],  # ~Jan 1st
         [8., 6.0, 5.0, 5.0, 5.0, 4.5, 4.5, 4.5, 4.5, 4.5],  # ~Apr 1st
         [20., 18., 14., 8.0, 6.0, 4.5, 4.5, 4.5, 4.5, 4.5],  # ~Jul 1st
         [9.5, 9.5, 9.0, 8.0, 7.0, 5.0, 4.5, 4.5, 4.5, 4.5],  # ~Oct 1st
         [5.5, 5.5, 5.0, 5.0, 5.0, 4.5, 4.5, 4.5, 4.5, 4.5]])  # ~Dec 31st
    temperature_profile_1500m = np.array(
        [[0.0, 2.5, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0],  # ~Jan 1st
         [0.0, 2.5, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0],  # ~Apr 1st
         [14., 9.0, 6.0, 4.5, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0],  # ~Jul 1st
         [8.0, 8.0, 7.0, 6.0, 5.0, 4.0, 4.0, 4.0, 4.0, 4.0],  # ~Oct 1st
         [0.0, 2.5, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0]])  # ~Dec 31st

    t_500 = np.concatenate(
        [np.interp([doy], [0, 91, 182, 273, 365], temperature_profile_500m[:, i]) for i in range(len(depths))])
    t_1500 = np.concatenate(
        [np.interp([doy], [0, 91, 182, 273, 365], temperature_profile_1500m[:, i]) for i in range(len(depths))])
    temperature_arr = np.concatenate(
        [np.interp([elevation], [500, 1500], [t_500[k], t_1500[k]]) for k in range(len(depths))])
    temperature_arr = np.interp(depth_arr, depths, temperature_arr)
    return {"depth": depth_arr, "temperature": temperature_arr, "salinity": salinity_arr}


def absorption_from_observations(key, start_date, end_date, api, reference_date, days_from_observation=60):
    try:
        data = call_url("{}/insitu/secchi/{}".format(api, key))
        df = pd.DataFrame({"time": data["time"], "value": data["variable"]["data"]})
        df.loc[df['value'] < 0.05, 'value'] = 0.05 # Prevent zero values from becoming infinite
        df["value"] = 1.7 / df["value"]  # Convert from Secchi depth [m] to absorption [m-1]
        df["time"] = pd.to_datetime(df["time"])
        secchi_mean = df['value'].mean()

        # Create monthly secchi depth array
        df["month"] = df["time"].dt.month
        month_dict = df.groupby(['month'])['value'].mean().to_dict()
        monthly_values = [month_dict[m] if m in month_dict else secchi_mean for m in range(1, 13)]

        df = df[(df['time'] >= start_date) & (df['time'] <= end_date)]
        time = np.array([datetime(year=start_date.year, month=1, day=15).replace(tzinfo=timezone.utc) + relativedelta(months=n) for n in range((end_date.year + 1 - start_date.year) * 12)])
        time = time[(time > start_date) & (time < end_date)]
        value = [monthly_values[t.month - 1] for t in time]
        df_ave = pd.DataFrame({"time": time, "value": value})

        # Replace monthly values with real data where available
        if not df.empty:
            df_ave = df_ave[df_ave['time'].apply(lambda x: any(abs((x - ref).days) > days_from_observation for ref in df['time']))]
        df_m = pd.concat([df, df_ave], ignore_index=True)
        df_m = df_m.sort_values(by='time')

        start = datetime_to_simstrat_time(start_date, reference_date)
        end = datetime_to_simstrat_time(end_date, reference_date)

        if not df_m.empty:
            t = [start] + [datetime_to_simstrat_time(d, reference_date) for d in df_m["time"].tolist()] + [end]
            v = [df_m['value'].iloc[0]] + df_m["value"].tolist() + [df_m['value'].iloc[-1]]
        else:
            t = [start, end]
            v = [monthly_values[start_date.month - 1], monthly_values[end_date.month - 1]]

        return {"Time": np.array(t), "Value": np.array(v)}
    except Exception as e:
        return False



def default_absorption(trophic_state, elevation, start_date, end_date, absorption, reference_date):
    if not absorption:
        if trophic_state.lower() == 'oligotrophic':
            absorption = 0.15
        elif trophic_state.lower() == 'eutrophic':
            absorption = 0.50
        else:
            absorption = 0.25
        if elevation > 2000:
            absorption = 1.00
    start = datetime_to_simstrat_time(start_date, reference_date)
    end = datetime_to_simstrat_time(end_date, reference_date)
    return {"Time": [start, end], "Value": [absorption, absorption]}



def _datalakes_to_long(data, axis):
    """Reshape one Datalakes processed-file payload into long format (time, depth, value).
    `axis` is the data axis to read — the depth-resolved 2D grid (usually 'z'); time comes from
    'x' (epoch seconds) and depth from 'y', and the grid is laid out as [depth][time]."""
    if axis not in data:
        raise ValueError("Datalakes axis '{}' not in payload (available: {})"
                         .format(axis, sorted(data.keys())))
    times = pd.to_datetime(np.asarray(data["x"], dtype="int64"), unit="s", utc=True)
    depths = np.asarray(data["y"], dtype=float)
    values = np.asarray(data[axis], dtype=float)       # nulls -> NaN
    if values.ndim != 2:
        raise ValueError("Datalakes axis '{}' is a 1D series, not a depth-resolved 2D grid; "
                         "provide the 2D axis (usually 'z').".format(axis))
    if values.shape != (len(depths), len(times)):
        if values.shape == (len(times), len(depths)):  # tolerate time-major orientation
            values = values.T
        else:
            raise ValueError("Datalakes axis '{}' shape {} matches neither (depths {}, times {})"
                             .format(axis, values.shape, len(depths), len(times)))
    df = pd.DataFrame(values, index=pd.Index(depths, name="depth"), columns=pd.Index(times, name="time"))
    return df.stack().rename("value").reset_index()[["time", "depth", "value"]]


def fetch_datalakes(source_cfg, args, since=None):
    """Fetch in-situ profiles from Datalakes (https://www.datalakes-eawag.ch). The run's
    `observations.id` is the Datalakes *dataset* id and `axis` selects which data
    axis to assimilate — the depth-resolved 2D grid, usually 'z'. (A dataset's axis->variable map
    is at `https://api.datalakes-eawag.ch/datasetparameters?datasets_id=<id>`: e.g. for 1334
    x=time, y=depth, z=temp [degC], y1=surface_temp, …) Processed `json` files are served at
    /download/<file id>.

    The processed json files are time-chunked (typically monthly) and carry per-file
    `maxdatetime`. When `since` is given (a warm, incremental run), only files that can hold
    data on or after `since` are downloaded; files entirely older than `since` are skipped.
    Files without a `maxdatetime` are always downloaded (their range is unknown)."""
    dataset_id = source_cfg.get("id")
    if dataset_id is None:
        raise ValueError("datalakes source for lake '{}' needs an 'id' (Datalakes dataset id) in its "
                         "assimilation observations block".format(source_cfg.get("key")))
    axis = source_cfg.get("axis", "z")
    base = source_cfg.get("api", DATALAKES_API)

    files = call_url("{}/files?datasets_id={}".format(base, dataset_id))
    json_files = [f for f in files if f.get("filetype") == "json"]
    if not json_files:
        raise ValueError("No processed (json) files for Datalakes dataset {}".format(dataset_id))
    json_files.sort(key=lambda f: f.get("maxdatetime") or "")

    if since is not None:
        cutoff = pd.Timestamp(since)
        cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
        json_files = [f for f in json_files
                      if f.get("maxdatetime") is None
                      or pd.to_datetime(f["maxdatetime"], utc=True) >= cutoff]

    if not json_files:
        return pd.DataFrame(columns=["time", "depth", "value"])

    frames = [_datalakes_to_long(call_url("{}/download/{}".format(base, f["id"])), axis)
              for f in json_files]
    df = pd.concat(frames, ignore_index=True)
    return df.drop_duplicates(subset=["time", "depth"]).dropna(subset=["value"])


OBSERVATION_SOURCES = {
    "datalakes": fetch_datalakes,
}

def decimate_observations(df, decimation):
    """Temporally resample each depth series to `decimation['time']` (a pandas offset, e.g. '1H',
    '1D') using `decimation['aggregation']` (mean/median/min/max/first/last, or 'nearest'). No-op
    when decimation is unset. Structured so depth/QA decimation can be added later."""
    if df.empty or not decimation or not decimation.get("time"):
        return df.sort_values("time").reset_index(drop=True)
    freq = decimation["time"]
    agg = (decimation.get("aggregation") or "mean").lower()

    parts = []
    for depth, group in df.groupby("depth"):
        s = group.set_index("time")["value"].sort_index()
        s = s[~s.index.duplicated()]
        if agg == "nearest":
            idx = pd.date_range(s.index.min().floor(freq), s.index.max().floor(freq), freq=freq)
            s = s.reindex(idx, method="nearest", tolerance=pd.Timedelta(freq))
        else:
            s = getattr(s.resample(freq), agg)()
        s = s.dropna()
        s.index.name = "time"
        part = s.rename("value").reset_index()
        part["depth"] = depth
        parts.append(part)
    if not parts:
        return df.iloc[0:0][["time", "depth", "value"]]
    return pd.concat(parts, ignore_index=True).sort_values("time").reset_index(drop=True)[["time", "depth", "value"]]


def _read_existing_observations(out_csv):
    """Load a previously written observation CSV as a clean (time, depth, value) frame, or None."""
    if not os.path.isfile(out_csv):
        return None
    df = pd.read_csv(out_csv)
    df.columns = [c.strip().lower() for c in df.columns]
    if not {"time", "depth", "value"}.issubset(df.columns):
        return None
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.dropna(subset=["time", "depth", "value"])[["time", "depth", "value"]]
    return df if not df.empty else None


def fetch_observations(key, observations_cfg, args, out_csv):
    """Resolve the assimilation run's observation source, fetch -> decimate -> write the
    time,depth,value CSV. Returns (first_obs, last_obs) datetimes, or (None, None) if there are no
    observations.

    On a cold start (no existing CSV) the full time series is fetched. On a warm start the
    existing CSV already holds the history, so only observations newer than its last day are
    fetched from the source and appended — the last stored day is re-fetched so its partial
    values get refreshed.

    Source config is the run's `observations` block in lake_parameters.json
    ({source, id, parameter, decimation:{time, aggregation}})."""
    source_cfg = dict(observations_cfg or {})
    if not source_cfg:
        raise ValueError("Assimilation run for lake '{}' has no 'observations' block in "
                         "lake_parameters.json".format(key))
    source_cfg["key"] = key
    source_cfg.setdefault("parameter", "temperature")

    source = source_cfg.get("source")
    if source not in OBSERVATION_SOURCES:
        raise ValueError("Unknown observation source '{}' for lake '{}'. Available: {}"
                         .format(source, key, sorted(OBSERVATION_SOURCES)))

    earliest = None
    if args.get("first_obs_date"):
        earliest = pd.Timestamp(datetime.strptime(args["first_obs_date"], "%Y%m%d")).tz_localize("UTC")

    existing = _read_existing_observations(out_csv)
    since = existing["time"].max().floor("D") if existing is not None else None

    # Lower bound handed to the source so old time-chunked files aren't downloaded.
    fetch_since = since
    if earliest is not None:
        fetch_since = earliest if fetch_since is None else max(fetch_since, earliest)

    df = OBSERVATION_SOURCES[source](source_cfg, args, since=fetch_since)
    df = df.dropna(subset=["time", "depth", "value"])
    if since is not None:
        df = df[df["time"] >= since]
    df = decimate_observations(df, source_cfg.get("decimation"))

    if existing is not None:
        df = pd.concat([existing[existing["time"] < since], df], ignore_index=True)
        df = df.drop_duplicates(subset=["time", "depth"], keep="last")
        df = df.sort_values("time").reset_index(drop=True)

    if earliest is not None:
        df = df[df["time"] >= earliest].reset_index(drop=True)

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    df.to_csv(out_csv, index=False)
    if df.empty:
        return None, None
    return df["time"].iloc[0].to_pydatetime(), df["time"].iloc[-1].to_pydatetime()
    