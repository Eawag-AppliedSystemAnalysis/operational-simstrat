# Simstrat Operational

[Simstrat](https://github.com/Eawag-AppliedSystemAnalysis/Simstrat) is a one-dimensional physical lake model for simulation of stratification and mixing in deep stratified lakes. 
The aquatic research institute [Eawag](https://eawag.ch) uses Simstrat to operationally simulate lake conditions for a number of lakes across Switzerland.

The model is coupled with [AED2](https://github.com/AquaticEcoDynamics/libaed2) for biogeochemical simulations.

This repository automates the production of model inputs, running the simulations and publishing the outputs. 

## Installation

- Clone the repository to your local machine using the command: 

 `git clone git@github.com:Eawag-AppliedSystemAnalysis/operational-simstrat.git`
 
 Note that the repository will be copied to your current working directory.

- Use Python 3 and install the requirements with:

 `pip install -r requirements.txt`

If you want to run the simulations as part of the pipeline you need to install docker.

- Install [docker engine](https://docs.docker.com/desktop/)

## Quick start

> [!WARNING]  
> The pipeline uses meteorological and hydraulic data from third party providers (MeteoSwiss, BAFU). This data can only 
> be accessed while connected to the Eawag network.

To run the pipeline you must pass the name of an arguments file from the `args/` folder to `src/main.py`

e.g. to run arguments file `args/quickstart.json`

```bash
python src/main.py quickstart
```
Arguments can be overwritten on the command line by passing them as key=value pairs.

```bash
python src/main.py quickstart run=false
```

A full list of the arguments can be seen in `src/configuration.py`

## Add lakes

In order to add additional lakes their parameters must be added to `static/lake_parameter.json` in the format of the
lakes already there.

```JSON
 {
    "key": "greifensee",
    "name": "Greifensee",
    "elevation": 435.0,
    "surface_area": 8.5,
    "type": "Natural",
    "volume": 0.15,
    "max_depth": 32.0,
    "average_depth": 18.0,
    "residence_time": 400.0,
    "mixing_regime": "Monomictic",
    "geothermal_flux": 0.09,
    "latitude": 47.35,
    "longitude": 8.678,
    "trophic_state": "Eutrophic",
    "datalakes_id": 3,
    "datalakes_bathymetry": true,
    "sediment_oxygen_uptake_rate": -32.5,
    "forcing": [
      {
        "id": "SMA",
        "type": "meteoswiss_meteostation"
      }
    ],
    "forcing_forecast": {
      "source": "MeteoSwiss",
      "model": "COSMO",
      "days": 5
    },
    "a_seiche": 0.0056684225,
    "f_wind": 0.672030072,
    "p_lw": 0.952833972,
    "snow_temp": 2.02759986
  }
```

## Observations

In-situ observations are used in two places:

- **Runs** — to initialise the model. For best performance you need in-situ data; without it the model
  falls back to a generic default profile and simulation quality suffers. Read from `observations_dir`
  (default `observations/`) with the structure `{lake-key}/temperature.csv` (and optionally
  `salinity.csv`).
- **Calibration** — required to calibrate the lakes. Read from `lake-calibrator/observations` with the
  structure `{lake-key}/{parameter}.csv`. See [Calibration](#observations-1) for the format.

## Run Simulation

### Docker

The Simstrat docker image is available [here.](https://hub.docker.com/r/eawag/simstrat)

```bash
cd {{ run_folder }}
docker run -v $(pwd):/simstrat/run eawag/simstrat:3.0.4 Settings.par
```

### Locally compiled

See the instructions [here](https://github.com/Eawag-AppliedSystemAnalysis/Simstrat) for details.

## Calibration

Simstrat operational uses [Lake-Calibrator](https://github.com/eawag-surface-waters-research/lake-calibrator) to calibrate the lakes. It is installed as a submodule as can be called as follows:

```bash
python src/calibrator.py calibration
```

Where calibration is the argument file in `args/`

The arguments follow the same structure as for running simstrat operational but with some additional parameters required.

### Observations

Observations are required for the calibration, by default they should be located in `lake-calibrator/observations` with the structure `{lake-key}/{parameter}.csv`. For example `greifensee/temperature.csv`.

Observation files should have the following structure:


| time                      | depth | latitude | longitude | value | weight |
|---------------------------|-------|----------|-----------|-------|--------|
| 2024-08-12T22:27:54+00:00 | 1.6   | 46.5     | 6.67      | 18.3  | 1      |
| 2024-08-12T23:27:54+00:00 | 8.4   | 46.5     | 6.67      | 8.5   | 1      |

There should be one file per lake and per parameter and the values should all have the same units. 


## Data Assimilation

Simstrat operational uses the [data-assimilation](data-assimilation) submodule (native Ensemble Kalman Filter / Particle Filter over a Simstrat ensemble, or OpenDA) to blend live in-situ measurements into the model. It is called as follows:

```bash
python src/data_assimilation.py assimilation
```

Where `assimilation` is the argument file in `args/`. The arguments follow the same structure as for running Simstrat operational, with additional data-assimilation arguments (`da_end_date`, `first_da_date`, `first_obs_date`, `spinup_years`, `max_assimilation_workers`, `openda_bin`).

**Everything per lake lives in one file, `static/assimilation/<lake>.json`**: its assimilation runs, the observation source and how the observations are processed, the observation error, the forcing perturbation, and the basin geometry the fits need. The data-assimilation submodule holds no per-lake values: `src/data_assimilation.py` builds a flat run config from this file and calls `assimilator.run(cfg)`.

```json
{
    "runs": {"python_enkf": {"engine": "python", "algorithm": "EnKF", "n_members": 20}},
    "observations": {
        "source": "datalakes", "id": 1334, "axis": "z",
        "filter":   {"w_seiche_h": 27.3},
        "schedule": {"hour": 6}
    },
    "sigma_obs": {"mixed": 0.299, "stratified": 0.586},
    "perturbations": {"variables": {"U": {"phi": 0.754, "sigma": 0.588}, "V": {...}, "GLOB": {...}}},
    "identity": {"length_km": 25.0}
}
```

| Key | Meaning |
|---|---|
| `runs` | One entry per assimilation run. Any key of the submodule's run config can go here (`engine`, `algorithm`, `n_members`, `inflation`, `sigma_scale`, `rng_seed`, `obs_operator`, …, see `data-assimilation/docs/configuration.md`); a key set here overrides the lake-level one (e.g. a run-specific `sigma_obs`). `engine` is `python` (`algorithm` `EnKF`/`PF`) or `openda` (`EnKF`/`DEnKF`/`EnSR`/`PF`, needs OpenDA 3.4.0 via the `openda_bin` argument). |
| `observations` | Where the raw record comes from (`source` and its keys) and how it becomes the assimilated series (`filter`, `schedule`, optional `depths`), see *Observations* below. Defaults, set only to change them: `filter.thermo_depth_min` 4 m, `schedule.max_offset_h` 1.5 h, `depth_tolerance_m` 0.5 m. |
| `sigma_obs` | Observation error, °C: a number or `{"mixed", "stratified"}` (stratified = May–Oct). **Valid only for the series it was fitted on**: refit after changing `filter`, `schedule` or `depths`. Without it the run uses `sigma_default` (0.5 °C). |
| `perturbations` | The AR(1) forcing perturbation that generates the ensemble. Only `variables` is used by the run; `fit_window`, `amplitude` and `persistence` are what the fit tool needs to refit the upper bound or switch bound without ICON. |
| `identity` | Inputs of the fits: basin length `length_km`, and `mean_depth` where it should differ from `average_depth` in `lake_parameters.json`. Optional `lake_bbox` sets the ICON request box (default: the bounds of the lake's outline in `static/lake_geometry.json`). |

A lake is assimilated when it is listed in `args/assimilation.json` and its file has a run and an observation `source`. Keys starting with `_` are comments. The seven study lakes were migrated from the data-assimilation research setup (branch `feat/da-combi`), each with a `python_enkf` run and its Datalakes dataset: upperlugano 1334, maggiore 1333, geneva 448 (LéXPLORE chain), aegeri 1077 (Idronaut depth-time grid), hallwil 1046, murten 956, greifensee 515 (profiler; its vertical axis is labelled pressure but holds the 1–16.9 m depth grid).

Every run gets its own state directory `runs/<lake>_<run>/` (e.g. `runs/upperlugano_python_enkf/`), containing the persistent ensemble under `assimilation/`, the `model_inputs/` analysis inputs + snapshots, the `forecast/` extension, and the `observations/` (the raw record `raw.csv`, the assimilated series `temperature.csv` and the settings it was made with, `processing.json`).

Unlike the forecast workflow (`src/main.py`), assimilation is **independent and rolling** — each run advances its own state forward instead of re-running the whole period. Each run has up to four phases:

1. **Observations** — fetch the new raw observations and preprocess them into the assimilated series. If the source cannot be reached, the run warns and continues with the stored record.
2. **Spin-up** (cold start only) — free Simstrat run from the origin to the first assimilation date.
3. **Assimilation** — `assimilator.run(cfg)` from the last snapshot to 00:00 UTC of the run day, seeded from the persisted ensemble.
4. **Forecast** (every run) — free Simstrat run from the run day to the forecast horizon, warm-started from the ensemble-mean analysis (overwritten each run).

**The run day is forecast, not hindcast**: its observations are never assimilated, and every observation from the days before it is. The assimilation always ends at 00:00 UTC of the run day (today, or `da_end_date=YYYYMMDD` to replay a given day), with or without new observations. A gap in the observations just advances the ensemble without updates, so the forecast always starts on the run day. A second run on the same day only forecasts.

**OpenDA runs.** An OpenDA run keeps its ensemble between analyses only within one OpenDA run, and each daily call starts a new one. To carry the ensemble (and its spread) from one day to the next, give the run a restart chain:

```json
"runs": {"openda_enkf": {"engine": "openda", "algorithm": "EnKF", "n_members": 20, "openda_restart": {"keep": 7}}}
```

Each daily call then runs OpenDA once, from where the chain ends to 00:00 of the run day, every observation analysed at its own time, and saves the ensemble as `runs/<lake>_<run>/openda_<lake>_<run>/restart/restart_<YYYYMMDDTHHMMZ>.zip` (the newest `keep` are kept). A cold start or a long catch-up is split into OpenDA runs of at most `cycle_max_days` (default 31). OpenDA is not started on a window without observations: during a gap at the end of the record the chain waits at the last one and the forecast starts there. OpenDA 3.4.0 is needed, through the `openda_bin` argument.

The combined assimilation + forecast temperature is written to `runs/<lake>_<run>/netcdf/`. When the run is invoked with `upload=true`, these NetCDF files are uploaded to `<results_folder_api>/<lake>_<run>` on the results server (keyed by the run name, so they do not overwrite the standard `src/main.py` forecast at `<results_folder_api>/<lake>`).

### Observations

Each run appends the new raw (sub-daily) observations to `raw.csv` and turns the record into the series it assimilates with the submodule's `assimilator.observations.preprocess`. Rows are only ever selected or averaged, never invented, and the series **is** the analysis schedule: one analysis per distinct observation time.

1. **Filter** (`filter`) — below `thermo_depth_min` (4 m) most hour-to-hour variance is the thermocline displaced by basin-scale seiches, which a 1D model cannot reproduce. Each depth there is replaced by a causal trailing mean over one fundamental seiche period `w_seiche_h`, from Merian's two-layer formula `T = 2L / sqrt(g'·h_eff)` at peak stratification (11 h on Ägeri, 27 h on Upper Lugano, 97 h on Geneva). A value is dropped when its window is less than a quarter full or holds only samples from before a gap. The top 4 m pass through untouched. Removing `filter` switches it off.
2. **Depth grid** (`depths`, optional) — keep the depth nearest each target within `depth_tolerance_m` (0.5 m), for dense sensor chains and interpolated profiles.
3. **Thin** (`schedule`) — one instant per day, all its depths: the one nearest `hour` UTC, skipping the day if it is more than `max_offset_h` away; or, with `"hour_window": [lo, hi]`, the first instant in that window (for lakes whose sampling phase drifts). The hour matters because assimilating at a fixed hour aliases that hour's model error into a bias; it is chosen where the model's diurnal bias is small relative to its day-to-day error, among early-morning hours so the analysis lands before the day it forecasts.

Each run reprocesses only the last stored day onwards (with the look-back the causal filter needs), so the daily series matches a full reprocess; changing the settings reprocesses the whole stored record. The port reproduces the research (`feat/da-combi`) series row for row.

Observation sources are pluggable (`OBSERVATION_SOURCES` in `src/functions/assimilation_observations.py`): register a `(source_cfg, args, since=None) -> DataFrame[time, depth, value]` function. The built-in `datalakes` source takes the [Datalakes](https://www.datalakes-eawag.ch) *dataset* `id` and the depth-resolved data `axis` (usually `z`; the map is at `https://api.datalakes-eawag.ch/datasetparameters?datasets_id=<id>`).

### Fitting a lake

The fitted values are produced by the tools in `src/assimilation_tools/`, which load the data and call the submodule's `assimilator.fitting`. Each prints its result; `--write` stores it in the lake's file. To add a lake: run the operational simulation for it, create `static/assimilation/<lake>.json` with `observations.source`, `identity.length_km` and empty `runs`, then

```bash
python src/assimilation_tools/fit_perturbation.py <lake> --icon --write   # ICON reanalysis, Eawag network
python src/assimilation_tools/calibrate_filter.py <lake> --write           # -> observations.filter
python src/assimilation_tools/make_free_run.py   <lake> --start <first obs day> --end <last forcing day>
python src/assimilation_tools/select_hour.py     <lake> --write           # -> observations.schedule
python src/assimilation_tools/fit_sigma.py       <lake> --write           # -> sigma_obs, on the final series
```

and add a run under `runs` and the lake to `args/assimilation.json`. `make_free_run.py` builds the hourly free run the hour and sigma fits compare against (the operational run outputs only every 3 h). `select_hour.py` searches 00–06 UTC by default; pass `--window 23-6` to include 23 UTC (as chosen for Ägeri and Hallwil). Raw observations come from the lake's run, or are fetched from its source; `--raw file.csv` overrides.

The analysis hours, depth grids and perturbations are the research choices. The seiche windows were refitted on the Datalakes records to October 2026 (except Maggiore, kept at the research 77.5 h: the refit gives 68.5 h, and its window already costs a lot of lag), and `sigma_obs` was refitted on the resulting series against `make_free_run.py` free runs of the operational setup (Ägeri as one annual value, `fit_sigma.py --annual`, as in the research). The stored perturbation amplitudes were fitted on the whole of Lake Lugano; a refit uses the upper basin outline from `static/lake_geometry.json`.

The data-assimilation submodule can also run on its own on any Simstrat folder (`python data-assimilation/src/assimilate.py run --sim-dir ...`); see its README.

Tests: `python -m pytest data-assimilation/tests`.
