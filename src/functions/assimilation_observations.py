"""The observations a lake's assimilation runs on: the raw record fetched from its source into
runs/<lake>_<run>/observations/raw.csv, and the series it assimilates, preprocessed by the
submodule (assimilator.observations) into temperature.csv.

Sources are pluggable: register a (source_cfg, args, since=None) -> DataFrame[time, depth, value]
function in OBSERVATION_SOURCES.
"""
import os
import json
import numpy as np
import pandas as pd
from datetime import datetime

from .assimilate import da_observations
from .general import call_url

DATALAKES_API = "https://api.datalakes-eawag.ch"


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
    # Built directly rather than via DataFrame.stack: some files repeat a timestamp, which stack
    # rejects. Duplicate (time, depth) rows are dropped by the caller (first kept).
    return pd.DataFrame({"time": np.tile(times, len(depths)),
                         "depth": np.repeat(depths, len(times)),
                         "value": values.ravel()}).dropna(subset=["value"]).reset_index(drop=True)


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
# The keys of a lake's "observations" block that decide the assimilated series; the rest describe
# the source.
PROCESSING_KEYS = ("filter", "schedule", "depths", "depth_tolerance_m")


def _read_existing_observations(out_csv):
    """Load a previously written observation CSV as a clean (time, depth, value) frame, or None."""
    if not os.path.isfile(out_csv):
        return None
    df = pd.read_csv(out_csv)
    df.columns = [c.strip().lower() for c in df.columns]
    if not {"time", "depth", "value"}.issubset(df.columns):
        return None
    df["time"] = pd.to_datetime(df["time"], utc=True, format="ISO8601")
    df = df.dropna(subset=["time", "depth", "value"])[["time", "depth", "value"]]
    return df if not df.empty else None


def processing_config(observations_cfg):
    """The part of a lake's "observations" block that decides the assimilated series."""
    return {k: observations_cfg[k] for k in PROCESSING_KEYS if k in observations_cfg}


def update_raw_observations(key, observations_cfg, args, raw_csv):
    """Fetch new raw (sub-daily) observations from the lake's source and append them to `raw_csv`.
    On a cold start the full record is fetched; afterwards only the last stored day onwards (it is
    re-fetched so a partial day is completed). Returns (raw frame, first day changed or None)."""
    source_cfg = dict(observations_cfg or {})
    source_cfg["key"] = key
    source_cfg.setdefault("parameter", "temperature")
    source = source_cfg.get("source")
    if source not in OBSERVATION_SOURCES:
        raise ValueError("Unknown observation source '{}' for lake '{}'. Available: {}"
                         .format(source, key, sorted(OBSERVATION_SOURCES)))

    earliest = None
    if args.get("first_obs_date"):
        earliest = pd.Timestamp(datetime.strptime(args["first_obs_date"], "%Y%m%d")).tz_localize("UTC")

    existing = _read_existing_observations(raw_csv)
    since = existing["time"].max().floor("D") if existing is not None else None
    fetch_since = since
    if earliest is not None:
        fetch_since = earliest if fetch_since is None else max(fetch_since, earliest)

    df = OBSERVATION_SOURCES[source](source_cfg, args, since=fetch_since)
    df = df.dropna(subset=["time", "depth", "value"])[["time", "depth", "value"]]
    if since is not None:
        df = df[df["time"] >= since]
        df = pd.concat([existing[existing["time"] < since], df], ignore_index=True)
    df = df.drop_duplicates(subset=["time", "depth"], keep="last")
    df = df.sort_values(["time", "depth"]).reset_index(drop=True)
    if earliest is not None:
        df = df[df["time"] >= earliest].reset_index(drop=True)

    os.makedirs(os.path.dirname(raw_csv), exist_ok=True)
    df.to_csv(raw_csv, index=False)
    return df, since


def fetch_observations(key, observations_cfg, args, out_dir, log=None):
    """Fetch the lake's raw observations and produce the series the run assimilates.

        <out_dir>/raw.csv           the raw sub-daily record (time, depth, value), appended daily
        <out_dir>/temperature.csv   the assimilated series: raw -> seiche filter -> depth grid ->
                                    one profile per day (the lake's "observations" block, applied by
                                    the data-assimilation package's assimilator.observations)
        <out_dir>/processing.json   the processing settings temperature.csv was made with

    Daily, only the tail from the last stored day is reprocessed (with the look-back the causal
    filter needs); a change of the processing settings reprocesses the whole stored record.
    Returns (temperature.csv path, first_obs, last_obs), the times None if there is nothing."""
    if not observations_cfg:
        raise ValueError("Lake '{}' has no 'observations' block in its assimilation setup".format(key))
    raw_csv = os.path.join(out_dir, "raw.csv")
    obs_csv = os.path.join(out_dir, "temperature.csv")
    meta_json = os.path.join(out_dir, "processing.json")

    try:
        raw, since = update_raw_observations(key, observations_cfg, args, raw_csv)
    except Exception as e:
        # The source is unreachable (or failing): carry on with the stored record, so the run still
        # advances and forecasts. Nothing stored yet, or debugging: fail loudly.
        stored = _read_existing_observations(raw_csv)
        if stored is None or args.get("debug"):
            raise
        if log is not None:
            log.warning("Fetching new observations for {} failed ({}); using the stored record up to {}."
                        .format(key, e, stored["time"].max()), indent=1)
        raw, since = stored, stored["time"].max().floor("D")

    processing = processing_config(observations_cfg)
    if not processing.get("schedule") and log is not None:
        log.warning("No 'schedule' for {}: every raw instant becomes an analysis.".format(key), indent=1)
    previous = None
    if os.path.isfile(meta_json):
        with open(meta_json) as f:
            previous = json.load(f)
    existing = _read_existing_observations(obs_csv)

    if since is None or existing is None or previous != processing:
        reason = ("cold start" if since is None or existing is None
                  else "processing settings changed")
        if log is not None:
            log.info("Preprocessing the full record ({}, {} raw rows)".format(reason, len(raw)), indent=1)
        processed = da_observations.preprocess(raw, processing) if not raw.empty else raw
    else:
        tail_raw = raw[raw["time"] >= since - da_observations.lookback(processing)]
        tail = da_observations.preprocess(tail_raw, processing, since=since)
        # Cut the stored series where preprocess cut the tail: by the day a row is thinned into.
        kept_t = existing["time"]
        if processing.get("schedule"):
            kept_t = (kept_t + pd.Timedelta(minutes=30)).dt.floor("1h")
        processed = pd.concat([existing[kept_t < since], tail], ignore_index=True)
        if log is not None:
            log.info("Preprocessed the record from {} ({} rows)".format(since.date(), len(tail)), indent=1)

    processed = processed[["time", "depth", "value"]].copy()
    processed["time"] = pd.to_datetime(processed["time"], utc=True, format="ISO8601")
    processed = processed.sort_values(["time", "depth"]).reset_index(drop=True)
    processed.to_csv(obs_csv, index=False)
    with open(meta_json, "w") as f:
        json.dump(processing, f, indent=2)
    if processed.empty:
        return obs_csv, None, None
    return obs_csv, processed["time"].iloc[0].to_pydatetime(), processed["time"].iloc[-1].to_pydatetime()
