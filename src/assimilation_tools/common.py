"""Shared plumbing for the data-assimilation fitting tools.

The tools load the data (observations, the operational free run, the control forcing, the ICON
reanalysis), call the pure fitting functions of the data-assimilation submodule
(assimilator.fitting), and write the result into the lake's static/assimilation/<lake>.json.
"""
import os
import sys
import json
import logging
import argparse

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # src/, for configuration/functions
REPO = os.path.dirname(SRC)
sys.path.insert(0, SRC)
sys.path.append(os.path.join(REPO, "data-assimilation", "src"))

import pandas as pd                                                       # noqa: E402
from configuration import assimilation_config_path, save_assimilation_config  # noqa: E402
from functions.observations import (update_raw_observations, processing_config,  # noqa: E402
                                    assimilator_observations)

logger = logging.getLogger("assimilation-tools")
CACHE = os.path.join(REPO, "runs", "assimilation_tools")


def parser(description):
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("lake", help="lake key (static/assimilation/<lake>.json)")
    ap.add_argument("--write", action="store_true",
                    help="write the result into static/assimilation/<lake>.json (default: print only)")
    return ap


def setup_logging():
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")


def lake_config(key):
    path = assimilation_config_path(REPO, key)
    if not os.path.isfile(path):
        raise SystemExit("no {} — create it first (see the Data Assimilation section of README.md)".format(
            os.path.relpath(path, REPO)))
    with open(path) as f:
        return json.load(f)


def lake_parameters(key):
    with open(os.path.join(REPO, "static", "lake_parameters.json")) as f:
        for lake in json.load(f):
            if lake["key"] == key:
                return lake
    raise SystemExit('lake "{}" is not in static/lake_parameters.json'.format(key))


def write_section(key, path, value):
    """Set cfg[path[0]][path[1]]... = value in static/assimilation/<key>.json, keeping key order.
    Only working values are stored; the tools print their diagnostics instead."""
    cfg = lake_config(key)
    node = cfg
    for k in path[:-1]:
        node = node.setdefault(k, {})
    node[path[-1]] = value
    save_assimilation_config(REPO, key, cfg)
    logger.info("wrote %s -> %s", ".".join(path),
                os.path.relpath(assimilation_config_path(REPO, key), REPO))


def show(result):
    print(json.dumps(result, indent=2))


def raw_observations(key, cfg, raw_path=None, refresh=False):
    """The lake's raw sub-daily record (time, depth, value): `raw_path` if given, else the
    operational assimilation run's raw store (runs/<key>_<run>/observations/raw.csv), else a copy
    fetched from the lake's source into runs/assimilation_tools/<key>/raw.csv."""
    if raw_path:
        candidates = [raw_path]
    else:
        candidates = [os.path.join(REPO, "runs", "{}_{}".format(key, run), "observations", "raw.csv")
                      for run in cfg.get("runs", {})]
        candidates.append(os.path.join(CACHE, key, "raw.csv"))
    for path in candidates:
        if os.path.isfile(path) and not (refresh and path.startswith(CACHE)):
            logger.info("raw observations <- %s", os.path.relpath(path, REPO))
            return _read(path)
    if raw_path:
        raise SystemExit("no file {}".format(raw_path))
    obs_cfg = cfg.get("observations", {})
    if not obs_cfg.get("source"):
        raise SystemExit('{}: no observation source in its "observations" block, and no --raw file'
                         .format(key))
    dest = os.path.join(CACHE, key, "raw.csv")
    logger.info("fetching the raw record from %s -> %s", obs_cfg["source"], os.path.relpath(dest, REPO))
    df, _ = update_raw_observations(key, obs_cfg, {}, dest)
    return df


def _read(path):
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True, format="ISO8601")
    return df[["time", "depth", "value"]]


def assimilated_series(raw, cfg):
    """The series a run assimilates, made with the same function and settings as the operational
    pipeline (functions.observations.fetch_observations)."""
    return assimilator_observations().preprocess(raw, processing_config(cfg.get("observations", {})))


def free_run(key, path=None):
    """The hourly free run (never assimilated) the hour and sigma are fitted against, as the wide
    free-run frame: `path`, else the one make_free_run.py writes. It must be hourly: a fit at a
    given hour needs model output at that hour."""
    from assimilator.fitting import read_free_run
    path = path or os.path.join(CACHE, key, "free_run", "Results", "T_out.dat")
    if not os.path.isfile(path):
        raise SystemExit("no free run at {} — make one with src/assimilation_tools/make_free_run.py {} "
                         "--start .. --end .., or pass --free-run".format(os.path.relpath(path, REPO), key))
    logger.info("free run <- %s", os.path.relpath(path, REPO))
    df = read_free_run(path)
    step = pd.Series(df.index).diff().median()
    if step != pd.Timedelta(hours=1):
        raise SystemExit("the free run {} outputs every {}, the fits need hourly output (the "
                         "operational run is 3-hourly) — use make_free_run.py".format(path, step))
    return df
