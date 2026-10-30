"""Fit a lake's observation error sigma_obs on the series its runs assimilate.

    python src/assimilation_tools/fit_sigma.py upperlugano                   # print
    python src/assimilation_tools/fit_sigma.py upperlugano --write           # -> sigma_obs (+ sigma_obs_fit)

The series is made from the raw record with the lake's current "observations" settings and the
same function the operational pipeline uses, so the fit always matches what is assimilated.
Refit after any change to the filter, the schedule or the depth grid.
"""
import common

from assimilator.fitting.sigma import fit_sigma


def main():
    ap = common.parser(__doc__.split("\n")[0])
    ap.add_argument("--raw", help="raw sub-daily CSV (default: the run's raw store, else fetched)")
    ap.add_argument("--free-run", help="free-run T_out.dat (default: runs/<lake>/Results/T_out.dat)")
    ap.add_argument("--year", type=int, help="restrict to one calendar year")
    ap.add_argument("--max-depth", type=float, help="ignore observations below this depth (m)")
    cli = ap.parse_args()
    common.setup_logging()

    cfg = common.lake_config(cli.lake)
    series = common.assimilated_series(common.raw_observations(cli.lake, cfg, cli.raw), cfg)
    result = fit_sigma(series, common.free_run(cli.lake, cli.free_run), year=cli.year,
                       max_depth=cli.max_depth)
    common.show(result)
    if cli.write:
        fit = {k: v for k, v in result.items() if k != "sigma_obs"}
        fit["fitted_on_series"] = {k: v for k, v in cfg["observations"].items()
                                   if k in ("filter", "schedule", "depths")}
        fit["fitted_by"] = "src/assimilation_tools/fit_sigma.py"
        common.write_section(cli.lake, ["sigma_obs"], result["sigma_obs"])
        common.write_section(cli.lake, ["sigma_obs_fit"], fit)


if __name__ == "__main__":
    main()
