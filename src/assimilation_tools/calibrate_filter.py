"""Derive a lake's seiche-filter window from its raw record and basin geometry.

    python src/assimilation_tools/calibrate_filter.py upperlugano            # print
    python src/assimilation_tools/calibrate_filter.py upperlugano --write    # -> observations.filter

Needs identity.length_km (published basin length) in static/assimilation/<lake>.json; the mean
depth is identity.mean_depth, else lake_parameters.json's average_depth. Changing the filter
changes the assimilated series: refit sigma afterwards (fit_sigma.py).
"""
import common

from assimilator.fitting.filter import derive_filter


def main():
    ap = common.parser(__doc__.split("\n")[0])
    ap.add_argument("--raw", help="raw sub-daily CSV (default: the run's raw store, else fetched)")
    ap.add_argument("--thermo-depth-min", type=float, default=None)
    cli = ap.parse_args()
    common.setup_logging()

    cfg = common.lake_config(cli.lake)
    identity = cfg.get("identity", {})
    if identity.get("length_km") is None:
        raise SystemExit('{}: set identity.length_km (basin length, km)'.format(cli.lake))
    mean_depth = identity.get("mean_depth") or common.lake_parameters(cli.lake)["average_depth"]

    raw = common.raw_observations(cli.lake, cfg, cli.raw)
    result = derive_filter(raw, identity["length_km"], mean_depth, thermo_depth_min=cli.thermo_depth_min)
    common.show(result)
    if cli.write:
        block = {"w_seiche_h": result["w_seiche_h"], "thermo_depth_min": result["thermo_depth_min"],
                 "derived_on": result["derived_on"], "derived_by": "src/assimilation_tools/calibrate_filter.py",
                 "two_layer": result["two_layer"], "diagnostics": result["diagnostics"]}
        common.write_section(cli.lake, ["observations", "filter"], block)


if __name__ == "__main__":
    main()
