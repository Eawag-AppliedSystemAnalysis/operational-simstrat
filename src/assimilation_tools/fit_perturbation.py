"""Fit a lake's AR(1) forcing perturbation (the ensemble's spread generator).

    python src/assimilation_tools/fit_perturbation.py upperlugano --icon --write   # amplitude + both bounds
    python src/assimilation_tools/fit_perturbation.py upperlugano --write          # refit the upper bound only
    python src/assimilation_tools/fit_perturbation.py upperlugano --bound lower    # switch bound (exact, offline)

AMPLITUDE (and the lower persistence bound) come from the ICON KENDA-CH1 reanalysis lake mean minus
the control forcing, so --icon needs the Alplakes API (Eawag network). The UPPER bound needs only
the control forcing, read from the operational run (runs/<lake>/Forcing.dat). The math is in the
data-assimilation submodule (assimilator.fitting.perturbation); this script gets the data.

The lake outline is the lake's feature in static/lake_geometry.json; the ICON request box is
identity.lake_bbox, else the outline's bounds. --check writes QA plots next to the cache.
"""
import os
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

import numpy as np
import pandas as pd

import common
from functions.general import call_url
from assimilator.models import get_model
from assimilator.fitting import perturbation as P

ICON_PATH = "meteoswiss/icon/area/reanalysis/kenda-ch1"
ICON_VARIABLES = ["T_2M", "U", "V", "GLOB"]
DEFAULT_API = "http://eaw-alplakes2.eawag.wroot.emp-eaw.ch:8000"


# ------------------------------------------------------------------------------- geometry --

def lake_outline(key):
    with open(os.path.join(common.REPO, "static", "lake_geometry.json")) as f:
        for feature in json.load(f)["features"]:
            if feature["properties"].get("key") == key:
                return feature["geometry"]
    raise SystemExit('no outline for "{}" in static/lake_geometry.json'.format(key))


def _polygons(geometry):
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        return geometry["coordinates"]
    raise ValueError("unsupported geometry type {}".format(geometry["type"]))


def inside(geometry, lon, lat):
    """Boolean mask: which (lon, lat) points fall inside the outline (holes excluded)."""
    from matplotlib.path import Path
    pts = np.column_stack([lon, lat])
    mask = np.zeros(len(pts), dtype=bool)
    for rings in _polygons(geometry):
        m = Path(np.asarray(rings[0])[:, :2]).contains_points(pts)
        for hole in rings[1:]:
            m &= ~Path(np.asarray(hole)[:, :2]).contains_points(pts)
        mask |= m
    return mask


def bbox_of(geometry, margin=0.01):
    """[lat_min, lon_min, lat_max, lon_max] around the outline."""
    xy = np.vstack([np.asarray(r)[:, :2] for rings in _polygons(geometry) for r in rings])
    return [round(xy[:, 1].min() - margin, 3), round(xy[:, 0].min() - margin, 3),
            round(xy[:, 1].max() + margin, 3), round(xy[:, 0].max() + margin, 3)]


# ------------------------------------------------------------------------------- ICON data --

def icon_lake_mean(geometry, bbox, start, end, api=DEFAULT_API, workers=8):
    """Hourly ICON reanalysis averaged over the grid points inside the lake: time, T_2M, U, V, GLOB.
    Also returns the flat per-grid-point frame (for the QA map)."""
    lat1, lon1, lat2, lon2 = bbox
    days = [start.date() + timedelta(days=i) for i in range((end.date() - start.date()).days + 1)]

    def get(day):
        d = day.strftime("%Y%m%d")
        url = "{}/{}/{}/{}/{}/{}/{}/{}?{}".format(api.rstrip("/"), ICON_PATH, d, d, lat1, lon1, lat2, lon2,
                                                  "&".join("variables=" + v for v in ICON_VARIABLES))
        return call_url(url, timeout=60)

    frames, errors = [], 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(get, d): d for d in days}
        for fut in as_completed(futures):
            try:
                frames.append(_flatten(fut.result()))
            except Exception as e:                                  # noqa: BLE001 - keep going
                errors += 1
                common.logger.warning("ICON %s: %s", futures[fut], e)
    if not frames:
        raise SystemExit("no ICON data retrieved ({} errors)".format(errors))
    common.logger.info("ICON: %d days retrieved, %d errors", len(frames), errors)
    flat = pd.concat(frames, ignore_index=True)
    pts = flat[["lat", "lon"]].drop_duplicates()
    in_lake = pts[inside(geometry, pts["lon"].values, pts["lat"].values)]
    common.logger.info("ICON: %d of %d grid points inside the lake", len(in_lake), len(pts))
    mean = (flat.merge(in_lake, on=["lat", "lon"])
                .groupby("time")[[v for v in ICON_VARIABLES if v in flat.columns]].mean().reset_index())
    mean["time"] = pd.to_datetime(mean["time"], utc=True)
    return mean, flat


def _flatten(d):
    times, lat, lon = d["time"], np.array(d["lat"]), np.array(d["lng"])
    ti, ii, ji = np.meshgrid(range(len(times)), range(lat.shape[0]), range(lat.shape[1]), indexing="ij")
    df = pd.DataFrame({"time": np.array(times)[ti.ravel()],
                       "lat": lat[ii.ravel(), ji.ravel()], "lon": lon[ii.ravel(), ji.ravel()]})
    for var, meta in d["variables"].items():
        df[var] = np.array(meta["data"]).ravel()
    return df


# ------------------------------------------------------------------------------------- main --

def stored_form(base):
    """What the lake file keeps: the run's `variables`, plus the amplitude and both bounds needed
    to refit the upper bound or switch bound offline. Provenance is only printed."""
    pers = base["persistence"]
    out = {"variables": base["variables"],
           "amplitude": {"stationary_std": base["amplitude"]["stationary_std"]},
           "persistence": {"bound": pers["bound"], "chosen": pers["chosen"],
                           **{b: {"tau_h": pers[b]["tau_h"], "phi": pers[b]["phi"]}
                              for b in ("lower", "upper") if b in pers}}}
    if base.get("fit_window"):
        out = {"fit_window": base["fit_window"], **out}
    return out


def control_forcing(key, start, end, sim_dir=None):
    sim_dir = sim_dir or os.path.join(common.REPO, "runs", key)
    if not os.path.isfile(os.path.join(sim_dir, "Forcing.dat")):
        raise SystemExit("no control forcing at {} — run the operational simulation first, or pass "
                         "--sim-dir".format(os.path.join(sim_dir, "Forcing.dat")))
    f = get_model("simstrat").read_forcing(sim_dir)
    return f[(f["time"] >= start) & (f["time"] <= end)].reset_index(drop=True)


def main():
    ap = common.parser(__doc__.split("\n")[0])
    ap.add_argument("--icon", action="store_true", help="refit the amplitude from ICON (Eawag network)")
    ap.add_argument("--bound", default="envelope", choices=P.BOUNDS)
    ap.add_argument("--fit-start", help="fit window start (default: the stored one, else last year)")
    ap.add_argument("--fit-end", help="fit window end")
    ap.add_argument("--sim-dir", help="folder with the control Forcing.dat (default: runs/<lake>)")
    ap.add_argument("--api", default=DEFAULT_API, help="Alplakes API base for the ICON reanalysis")
    ap.add_argument("--check", action="store_true", help="write QA plots (needs --icon)")
    cli = ap.parse_args()
    common.setup_logging()

    cfg = common.lake_config(cli.lake)
    stored = cfg.get("perturbations")
    window = (stored or {}).get("fit_window", {})
    last_year = pd.Timestamp.now(tz="UTC").year - 1
    start = pd.Timestamp(cli.fit_start or window.get("start") or "{}-01-01".format(last_year), tz="UTC")
    end = pd.Timestamp(cli.fit_end or window.get("end") or "{}-12-31".format(last_year), tz="UTC") \
        + pd.Timedelta(hours=23, minutes=59)
    ctrl = control_forcing(cli.lake, start, end, cli.sim_dir)
    if ctrl.empty:
        raise SystemExit("the control forcing has no rows over {} .. {}".format(start.date(), end.date()))

    if cli.icon:
        geometry = lake_outline(cli.lake)
        bbox = cfg.get("identity", {}).get("lake_bbox") or bbox_of(geometry)
        icon, flat = icon_lake_mean(geometry, bbox, start, end, cli.api)
        df = icon.rename(columns={"U": "U_icon", "V": "V_icon", "GLOB": "GLOB_icon"}).merge(ctrl, on="time")
        if df.empty:
            raise SystemExit("no overlap between the ICON lake mean and the control forcing")
        residual = {v: df["{}_icon".format(v)] - df[v] for v in P.PERTURB_VARS}
        base = P.fit_amplitude(residual, {"start": str(start.date()), "end": str(end.date())},
                               source="ICON KENDA-CH1 lake mean (static/lake_geometry.json) - control Forcing.dat")
        if cli.check:
            import check_perturbations
            out_dir = os.path.join(common.CACHE, cli.lake)
            check_perturbations.check(cli.lake, out_dir, geometry, flat, icon, df, base["variables"])
    else:
        if not stored:
            raise SystemExit("{} has no stored perturbations: run once with --icon".format(cli.lake))
        base = P.migrate(json.loads(json.dumps(stored)))

    base["persistence"]["upper"] = P.fit_upper(ctrl)
    result = P.ordered(P.select(base, cli.bound))
    common.show(result)
    if cli.write:
        common.write_section(cli.lake, ["perturbations"], stored_form(result))


if __name__ == "__main__":
    main()
