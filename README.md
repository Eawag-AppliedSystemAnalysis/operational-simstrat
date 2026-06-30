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

Simstrat operational uses the [data-assimilation](data-assimilation) submodule (native Ensemble Kalman Filter / Particle Filter over a Simstrat ensemble) to blend live in-situ measurements into the model. It is called as follows:

```bash
python src/assimilator.py assimilation
```

Where `assimilation` is the argument file in `args/`. The arguments follow the same structure as for running Simstrat operational, with additional data-assimilation parameters (`engine`, `algorithm`, `n_members`, `sigma_obs`, `inflation`, `sigma_scale`, `rng_seed`, `spinup_years`, `first_da_date`).

Unlike the forecast workflow (`src/main.py`), assimilation is **independent and rolling** — each lake keeps its own state under `run/<lake>_assimilate/` (the persistent ensemble plus `model_inputs/` analysis inputs + snapshots and `forecast/` forecast extension) and advances forward each run instead of re-running the whole period. Each run has up to three phases:

1. **Spin-up** (cold start only) — free Simstrat run from the origin to the first assimilation date.
2. **Assimilation** — EnKF/PF from the previous-last to the new-last observation date, seeded from the persisted ensemble.
3. **Forecast** (every run) — free Simstrat run from the last assimilation date to the forecast horizon, warm-started from the ensemble-mean analysis (overwritten each run).

### Observations

Observations are fetched live each run through a **pluggable source layer** (`src/functions/assimilation_observations.py`) and written to `run/{lake-key}_assimilate/observations/temperature.csv` with columns `time,depth,value`. Add a new source by registering a `(parameters, source_cfg, args) -> DataFrame[time, depth, value]` function in `OBSERVATION_SOURCES`.

Everything about a lake's observations is configured **per lake** with an `assimilation_observations` block in `static/lake_parameters.json` — the source, the dataset, and the **decimation** (temporal resampling applied after fetching). For the `datalakes` source, `id` is the [Datalakes](https://www.datalakes-eawag.ch) *dataset* id (e.g. `1334` = Upper Lake Lugano buoy) and `axis` is the data axis to assimilate — the depth-resolved 2D grid, usually `z`:

```json
"assimilation_observations": {
    "source": "datalakes",
    "id": 1334,
    "axis": "z",
    "decimation": { "time": "1h", "aggregation": "mean" }
}
```

A dataset's axis→variable map is at `https://api.datalakes-eawag.ch/datasetparameters?datasets_id=<id>` (for 1334: `x`=time, `y`=depth, `z`=temp [degC], `y1`=surface_temp, …), so set `axis` to the depth-resolved variable you want to assimilate.

`decimation.time` is a pandas offset (`"1h"`, `"6h"`, `"1D"`, …) and `aggregation` is `mean`/`median`/`min`/`max`/`first`/`last`/`nearest`. Built-in sources: `datalakes` and `alplakes` (the `{data_api}/insitu/{parameter}/{key}` API, which uses `parameter` instead of `axis`).

### Perturbations

The ensemble is generated by AR(1) perturbations of the forcing. These are sourced **first** from an optional `perturbations` block in `static/lake_parameters.json`, and **fall back** to `data-assimilation/perturbations/{lake-key}.json` (fitted offline with the submodule's `notebooks/perturbations_from_icon.py`). If neither exists the run errors clearly. The block follows the submodule's AR(1) format:

```json
"perturbations": {
    "variables": {
        "U":    { "phi": 0.656, "sigma": 0.676 },
        "V":    { "phi": 0.691, "sigma": 0.902 },
        "GLOB": { "phi": 0.486, "sigma": 45.531 }
    }
}
```
