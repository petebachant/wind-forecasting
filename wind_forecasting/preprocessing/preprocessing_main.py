# %%
# ! module load mambaforge or mamba
# ! mamba create -n wind_forecasting_env python=3.12
# ! mamba activate wind_forecasting_env
# ! conda install -c conda-forge jupyterlab mpi4py impi_rt
# git clone https://github.com/achenry/wind-forecasting.git
# git checkout feature/nacelle_calibration
# git submodule update --init --recursive
# ! pip install ./OpenOA # have to change pyproject.toml to allow for python 3.12.7
# ! pip install floris polars windrose netCDF4 statsmodels h5pyd seaborn pyarrow memory_profiler scikit-learn
# ! python -m ipykernel install --user --name=wind_forecasting_env
# ./run_jupyter_preprocessing.sh && http://localhost:7878/lab

from sys import platform
import os
import logging

from wind_forecasting.preprocessing.data_loader import DataLoader
from wind_forecasting.preprocessing.data_filter import (DataFilter, 
                                                        add_df_continuity_columns, add_df_agg_continuity_columns, 
                                                        get_continuity_group_index, group_df_by_continuity, 
                                                        merge_adjacent_periods, compute_offsets)
from wind_forecasting.preprocessing.data_inspector import DataInspector
from wind_forecasting.preprocessing.OpenOA.openoa.utils import plot, filters, power_curve

import polars as pl
import polars.selectors as cs
import numpy as np
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('TkAgg') 

from scipy.stats import norm
from floris import FlorisModel

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# %%
if __name__ == "__main__":
    PLOT = False 
    RELOAD_DATA = False
    FILTERS = ["unresponsive_sensor", "range_flag", "bin_filter", "std_range_flag", "impute_missing_data", "normalize"]
    # FILTERS = ["std_range_flag", "split", "impute_missing_data", "normalize"]
    assert all(filt in ["nacelle_calibration", "unresponsive_sensor", "inoperational", "range_flag", "window_range_flag", "bin_filter", "std_range_flag", "split", "impute_missing_data", "normalize"] for filt in FILTERS)
    
    if platform == "darwin":
        DATA_DIR = "/Users/ahenry/Documents/toolboxes/wind_forecasting/examples/data/raw_data"
        # PL_SAVE_PATH = "/Users/ahenry/Documents/toolboxes/wind_forecasting/examples/data/kp.turbine.zo2.b0.raw.parquet"
        # FILE_SIGNATURE = "kp.turbine.z02.b0.*.*.*.nc"
        PL_SAVE_PATH = "/Users/ahenry/Documents/toolboxes/wind_forecasting/examples/data/filled_data.parquet"
        # PL_SAVE_PATH = "/Users/ahenry/Documents/toolboxes/wind_forecasting/examples/data/short_loaded_data.parquet"
        # FILE_SIGNATURE = "kp.turbine.z02.b0.202203*.*.*.nc"
        FILE_SIGNATURE = "kp.turbine.z02.b0.*.*.*.nc"
        MULTIPROCESSOR = "cf"
        TURBINE_INPUT_FILEPATH = "/Users/ahenry/Documents/toolboxes/wind_forecasting/examples/inputs/ge_282_127.yaml"
        FARM_INPUT_FILEPATH = "/Users/ahenry/Documents/toolboxes/wind_forecasting/examples/inputs/gch_KP_v4.yaml"
        FEATURES = ["time", "turbine_id", "turbine_status", "wind_direction", "wind_speed", "power_output", "nacelle_direction"]
        WIDE_FORMAT = False
        COLUMN_MAPPING = {"time": "date",
                                "turbine_id": "turbine_id",
                                "turbine_status": "WTUR.TurSt",
                                "wind_direction": "WMET.HorWdDir",
                                "wind_speed": "WMET.HorWdSpd",
                                "power_output": "WTUR.W",
                                "nacelle_direction": "WNAC.Dir"
                                }
    elif platform == "linux":
        DATA_DIR = "/pl/active/paolab/awaken_data/kp.turbine.z02.b0/"
        PL_SAVE_PATH = "/scratch/alpine/aohe7145/awaken_data/filled_data.parquet"
        FILE_SIGNATURE = "kp.turbine.z02.b0.*.*.*.nc"
        MULTIPROCESSOR = "mpi"
        TURBINE_INPUT_FILEPATH = "/projects/aohe7145/toolboxes/wind-forecasting/examples/inputs/ge_282_127.yaml"
        FARM_INPUT_FILEPATH = "/projects/aohe7145/toolboxes/wind-forecasting/examples/inputs/gch_KP_v4.yaml"
        FEATURES = ["time", "turbine_id", "turbine_status", "wind_direction", "wind_speed", "power_output", "nacelle_direction"]
        WIDE_FORMAT = False
        COLUMN_MAPPING = {"time": "date",
                                "turbine_id": "turbine_id",
                                "turbine_status": "WTUR.TurSt",
                                "wind_direction": "WMET.HorWdDir",
                                "wind_speed": "WMET.HorWdSpd",
                                "power_output": "WTUR.W",
                                "nacelle_direction": "WNAC.Dir"
                                }

    DT = 5
    CHUNK_SIZE = 100000
    FEATURES = ["time", "turbine_id", "turbine_status", "wind_direction", "wind_speed", "power_output", "nacelle_direction"]
    WIDE_FORMAT = True
    DATA_FORMAT = "netcdf"
    FFILL_LIMIT = int(60 * 60 * 10 // DT)

    if FILE_SIGNATURE.endswith(".nc"):
        DATA_FORMAT = "netcdf"
    elif FILE_SIGNATURE.endswith(".csv"):
        DATA_FORMAT = "csv"
    else:
        raise ValueError("Invalid file signature. Please specify either '*.nc' or '*.csv'.")
    data_loader = DataLoader(
        data_dir=DATA_DIR,
        file_signature=FILE_SIGNATURE,
        save_path=PL_SAVE_PATH,
        multiprocessor=MULTIPROCESSOR,
        chunk_size=CHUNK_SIZE,
        desired_feature_types=FEATURES,
        dt=DT,
        ffill_limit=FFILL_LIMIT,
        data_format=DATA_FORMAT,
        column_mapping=COLUMN_MAPPING,
        wide_format=WIDE_FORMAT
    )

    # %%
    data_loader.print_netcdf_structure(data_loader.file_paths[0])

    # %%
    if not RELOAD_DATA and os.path.exists(data_loader.save_path):
        # Note that the order of the columns in the provided schema must match the order of the columns in the CSV being read.
        logging.info("🔄 Loading existing Parquet file")
        df_query = pl.scan_parquet(source=data_loader.save_path)
        logging.info("✅ Loaded existing Parquet file successfully")
        data_loader.available_features = sorted(df_query.collect_schema().names())
        data_loader.turbine_ids = sorted(set(col.split("_")[-1] for col in data_loader.available_features if "wt" in col))
    else:
        logging.info("🔄 Processing new data files")
        df_query = data_loader.read_multi_files()
        if df_query is not None:
            # Perform any additional operations on df_query if needed
            logging.info("✅ Data processing completed successfully")
        else:
            logging.warning("⚠️ No data was processed")
            
    # ## Plot Wind Farm, Data Distributions

    # %%
    data_inspector = DataInspector(
        turbine_input_filepath=TURBINE_INPUT_FILEPATH,
        farm_input_filepath=FARM_INPUT_FILEPATH,
        data_format='auto'  # This will automatically detect the data format (wide or long)
    )

    # %%
    if PLOT:
        logging.info("🔄 Generating plots.")
        data_inspector.plot_wind_farm()
        data_inspector.plot_wind_speed_power(df_query, turbine_ids=["wt073"])
        data_inspector.plot_wind_speed_weibull(df_query, turbine_ids="all")
        data_inspector.plot_wind_rose(df_query, turbine_ids="all")
        data_inspector.plot_correlation(df_query, 
        DataInspector.get_features(df_query, feature_types=["wind_speed", "wind_direction", "nacelle_direction"], turbine_ids=["wt073"]))
        data_inspector.plot_boxplot_wind_speed_direction(df_query, turbine_ids=["wt073"], feature_types=["wind_speed", "wind_direction", "nacelle_direction"])
        data_inspector.plot_time_series(df_query, turbine_ids=["wt073"])
        plot.column_histograms(data_inspector.collect_data(df=df_query, 
        feature_types=data_inspector.get_features(df_query, ["wind_speed", "wind_direction", "power_output", "nacelle_direction"])))
        logging.info("✅ Generated plots.")

    # %% check time series
    def print_df_state(df_query, feature_types=None):
        if feature_types is None:
            feature_types = ["wind_speed", "wind_direction"]
        print("n unique values", pl.concat([df_query.select(cs.starts_with(feat_type))\
                                                            .select(pl.min_horizontal(pl.all().drop_nulls().n_unique()).alias(f"{feat_type}_min_n_unique"), 
                                                                    pl.max_horizontal(pl.all().drop_nulls().n_unique()).alias(f"{feat_type}_max_n_unique"))\
                                                            .collect() for feat_type in feature_types], how="horizontal"), sep="\n")
        print("n non-null values", pl.concat([df_query.select(cs.starts_with(feat_type))\
                                                            .select(pl.min_horizontal(pl.all().count()).alias(f"{feat_type}_min_non_null"), 
                                                                    pl.max_horizontal(pl.all().count()).alias(f"{feat_type}_max_non_null"))\
                                                            .collect() for feat_type in feature_types], how="horizontal"), sep="\n")
    if PLOT:
        print_df_state(df_query, ["wind_speed", "wind_direction"])
        data_inspector.plot_time_series(df_query, feature_types=["wind_speed", "wind_direction"], turbine_ids=["wt001"], continuity_groups=None)

    # %%
    if "nacelle_calibration" in FILTERS or RELOAD_DATA or not os.path.exists(PL_SAVE_PATH.replace(".parquet", "_calibrated.parquet")): 
        # Nacelle Calibration 
        # Find and correct wind direction offsets from median wind plant wind direction for each turbine
        logging.info("Subtracting median wind direction from wind direction and nacelle direction measurements.")

        # add the 3 degrees back to the wind direction signal
        offset = 3.0
        df_query2 = df_query.with_columns((cs.starts_with("wind_direction") + offset).mod(360.0))
        df_query_10min = df_query2\
                            .with_columns(pl.col("time").dt.round(f"{10}m").alias("time"))\
                            .group_by("time").agg(cs.numeric().mean()).sort("time")

        wd_median = df_query_10min.select(cs.starts_with("wind_direction").radians().sin().name.suffix("_sin"),
                                           cs.starts_with("wind_direction").radians().cos().name.suffix("_cos"))
        wd_median = wd_median.select(wind_direction_sin_median=np.nanmedian(wd_median.select(cs.ends_with("_sin")).collect().to_numpy(), axis=1), 
                                     wind_direction_cos_median=np.nanmedian(wd_median.select(cs.ends_with("_cos")).collect().to_numpy(), axis=1))\
                               .select(pl.arctan2(pl.col("wind_direction_sin_median"), pl.col("wind_direction_cos_median")).degrees().alias("wind_direction_median"))\
                               .collect().to_numpy().flatten()
        
        yaw_median = df_query_10min.select(cs.starts_with("nacelle_direction").radians().sin().name.suffix("_sin"),
                                         cs.starts_with("nacelle_direction").radians().cos().name.suffix("_cos"))
        yaw_median = yaw_median.select(nacelle_direction_sin_median=np.nanmedian(yaw_median.select(cs.ends_with("_sin")).collect().to_numpy(), axis=1), 
                                       nacelle_direction_cos_median=np.nanmedian(yaw_median.select(cs.ends_with("_cos")).collect().to_numpy(), axis=1))\
                               .select(pl.arctan2(pl.col("nacelle_direction_sin_median"), pl.col("nacelle_direction_cos_median")).degrees().alias("nacelle_direction_median"))\
                               .collect().to_numpy().flatten()

        df_query_10min = df_query_10min.with_columns(wd_median=wd_median, yaw_median=yaw_median).collect().lazy()

        if PLOT:
            data_inspector.plot_wind_offset(df_query_10min, "Original", data_loader.turbine_ids)

        # remove biases from median direction

        df_offsets = {"turbine_id": [], "northing_bias": []}
        for turbine_id in data_loader.turbine_ids:
            
            bias = df_query_10min\
                        .filter(pl.col(f"power_output_{turbine_id}") >= 0)\
                        .select("time", f"wind_direction_{turbine_id}", f"nacelle_direction_{turbine_id}", "wd_median", "yaw_median")\
                        .select(wd_bias=(pl.col(f"wind_direction_{turbine_id}") - pl.col("wd_median")), 
                                yaw_bias=(pl.col(f"nacelle_direction_{turbine_id}") - pl.col("yaw_median")))\
                        .select(pl.all().radians().sin().mean().name.suffix("_sin"), pl.all().radians().cos().mean().name.suffix("_cos"))\
                        .select(wd_bias=pl.arctan2("wd_bias_sin", "wd_bias_cos").degrees().mod(360),
                                yaw_bias=pl.arctan2("yaw_bias_sin", "yaw_bias_cos").degrees().mod(360))\
                        .select(pl.when(pl.all() > 180.0).then(pl.all() - 360.0).otherwise(pl.all()))

            df_offsets["turbine_id"].append(turbine_id)
            bias = 0.5 * (bias.select('wd_bias').collect().item() + bias.select("yaw_bias").collect().item())
            df_offsets["northing_bias"].append(np.round(bias, 2))
            
            df_query_10min = df_query_10min.with_columns((pl.col(f"wind_direction_{turbine_id}") - bias).mod(360.0).alias(f"wind_direction_{turbine_id}"), 
                                                         (pl.col(f"nacelle_direction_{turbine_id}") - bias).mod(360.0).alias(f"nacelle_direction_{turbine_id}"))
            df_query2 = df_query2.with_columns((pl.col(f"wind_direction_{turbine_id}") - bias).mod(360.0).alias(f"wind_direction_{turbine_id}"), 
                                               (pl.col(f"nacelle_direction_{turbine_id}") - bias).mod(360.0).alias(f"nacelle_direction_{turbine_id}"))

            print(f"Turbine {turbine_id} bias from median wind direction: {bias} deg")

        df_offsets = pl.DataFrame(df_offsets)

        if PLOT:
            data_inspector.plot_wind_offset(df_query_10min, "Corrected", data_loader.turbine_ids)
            
        # make sure we have corrected the bias between wind direction and yaw position by adding 3 deg. to the wind direction
        bias = 0
        for turbine_id in data_loader.turbine_ids:
            bias += df_query_10min.filter(pl.col(f"power_output_{turbine_id}") >= 0)\
                            .select("time", f"wind_direction_{turbine_id}", f"nacelle_direction_{turbine_id}")\
                            .select(bias=(pl.col(f"wind_direction_{turbine_id}") - pl.col(f"nacelle_direction_{turbine_id}")))\
                            .select(sin=pl.all().radians().sin().mean(), cos=pl.all().radians().cos().mean())\
                            .select(pl.arctan2("sin", "cos").degrees().mod(360).alias("bias"))\
                            .select(pl.when(pl.all() > 180.0).then(pl.all() - 360.0).otherwise(pl.all()))\
                            .collect().item()
                        
            # bias += DataFilter.wrap_180(DataFilter.circ_mean(df.select(pl.col(f"wind_direction_{turbine_id}") - pl.col(f"nacelle_direction_{turbine_id}")).collect().to_numpy().flatten()))
            
        print(f"Average Bias = {bias / len(data_loader.turbine_ids)} deg")

        # %%
        # Find offset to true North using wake loss profiles

        logging.info("Finding offset to true North using wake loss profiles.")

        # Find offsets between direction of alignment between pairs of turbines 
        # and direction of peak wake losses. Use the average offset found this way 
        # to identify the Northing correction that should be applied to all turbines 
        # in the wind farm.
        fi = FlorisModel(data_inspector.farm_input_filepath)
        
        dir_offsets = compute_offsets(df_query_10min, fi,
                                      turbine_pairs=[(51,50),(43,42),(41,40),(18,19),(34,33),(22,21),(87,86),(62,63),(33,32),(59,60),(43,42)],
                                      plot=PLOT
                                    #   turbine_pairs=[(61,60),(51,50),(43,42),(41,40),(18,19),(34,33),(17,16),(21,22),(87,86),(62,63),(32,33),(59,60),(42,43)]
                                      )
        
        if dir_offsets:
            # Apply Northing offset to each turbine
            for turbine_id in data_loader.turbine_ids:
                df_query_10min = df_query_10min.with_columns((pl.col(f"wind_direction_{turbine_id}") - np.mean(dir_offsets)).mod(360).alias(f"wind_direction_{turbine_id}"),
                                                            (pl.col(f"nacelle_direction_{turbine_id}") - np.mean(dir_offsets)).mod(360).alias(f"nacelle_direction_{turbine_id}"))
                
                df_query2 = df_query2.with_columns((pl.col(f"wind_direction_{turbine_id}") - np.mean(dir_offsets)).mod(360).alias(f"wind_direction_{turbine_id}"),
                                                (pl.col(f"nacelle_direction_{turbine_id}") - np.mean(dir_offsets)).mod(360).alias(f"nacelle_direction_{turbine_id}"))

            # Determine final wind direction correction for each turbine
            df_offsets = df_offsets.with_columns(
                northing_bias=(pl.col("northing_bias") + np.mean(dir_offsets)))\
                .with_columns(northing_bias=pl.when(pl.col("northing_bias") > 180.0)\
                        .then(pl.col("northing_bias") - 360.0)\
                        .otherwise(pl.col("northing_bias"))\
                        .round(2))
            
            # verify that Northing calibration worked properly
            new_dir_offsets = compute_offsets(df_query_10min, fi,
                                            turbine_pairs=[(51,50),(43,42),(41,40),(18,19),(34,33),(22,21),(87,86),(62,63),(33,32),(59,60),(43,42)],
                                            plot=PLOT
            ) 

        df_query = df_query2
        df_query.collect().write_parquet(PL_SAVE_PATH.replace(".parquet", "_calibrated.parquet"), statistics=False)
    else:
        df_query = pl.scan_parquet(PL_SAVE_PATH.replace(".parquet", "_calibrated.parquet"))

    # %% [markdown]
    # ## OpenOA Data Preparation & Inspection

    # %%
    ws_cols = data_inspector.get_features(df_query, "wind_speed")
    wd_cols = data_inspector.get_features(df_query, "wind_direction")
    pwr_cols = data_inspector.get_features(df_query, "power_output")

    # %%
    print(f"Features of interest = {data_loader.desired_feature_types}")
    print(f"Available features = {data_loader.available_features}")
    # qa.describe(DataInspector.collect_data(df=df_query))
    
    # %% check time series
    if PLOT:
        print_df_state(df_query, ["wind_speed", "wind_direction"])
        data_inspector.plot_time_series(df_query, feature_types=["wind_speed", "wind_direction"], turbine_ids=["wt001"], continuity_groups=None)

    # %%
    data_filter = DataFilter(turbine_availability_col=None, turbine_status_col="turbine_status", multiprocessor=MULTIPROCESSOR, data_format='wide')

    if RELOAD_DATA or not os.path.exists(PL_SAVE_PATH.replace(".parquet", "_filtered.parquet")):
        # %%
        if "unresponsive_sensor" in FILTERS:
            logging.info("Nullifying unresponsive sensor cells.")
            # find stuck sensor measurements for each turbine and set them to null
            # this filter must be applied before any cells are nullified st null values aren't considered repeated values
            # find values of wind speed/direction, where there are duplicate values with nulls inbetween
            thr = int(np.timedelta64(20, 'm') / np.timedelta64(data_loader.dt, 's'))
            if not os.path.exists(PL_SAVE_PATH.replace(".parquet", "_frozen_sensors.npy")):
                
                frozen_sensors = filters.unresponsive_flag(
                    data_pl=df_query.select(cs.starts_with("wind_speed"), cs.starts_with("wind_direction")), threshold=thr)
                
                frozen_sensors = {"wind_speed": frozen_sensors[ws_cols].values, 
                                  "wind_direction": frozen_sensors[wd_cols].values}
                np.save(PL_SAVE_PATH.replace(".parquet", "_frozen_sensors.npy"), frozen_sensors)
            else:
                frozen_sensors = np.load(PL_SAVE_PATH.replace(".parquet", "_frozen_sensors.npy"), allow_pickle=True)[()]
            
            ws_mask = lambda tid: ~frozen_sensors["wind_speed"][:, data_loader.turbine_ids.index(tid)]
            wd_mask = lambda tid: ~frozen_sensors["wind_direction"][:, data_loader.turbine_ids.index(tid)]

            # change the values corresponding to frozen sensor measurements to null or interpolate (instead of dropping full row, since other sensors could be functioning properly)
            # fill stuck sensor measurements with Null st they are marked for interpolation later,
            threshold = 0.01
            logging.info("Nullifying wind speed frozen sensor measurements in dataframe.")
            df_query = data_filter.conditional_filter(df_query, threshold, ws_mask, ws_cols, check_js=False)
            logging.info("Nullifying wind direction frozen sensor measurements in dataframe.")
            df_query = data_filter.conditional_filter(df_query, threshold, wd_mask, wd_cols, check_js=False)

            # check time series
            if PLOT:
                for feature_type, mask in frozen_sensors.items():
                    plot.plot_power_curve(
                        data_inspector.collect_data(df=df_query, feature_types="wind_speed"),
                        data_inspector.collect_data(df=df_query, feature_types="power_output"),
                        flag=mask,
                        flag_labels=(f"{feature_type} Unresponsive Sensors (n={mask.sum():,.0f})", "Normal Turbine Operations"),
                        xlim=(-1, 15),  # optional input for refining plots
                        ylim=(-100, 3000),  # optional input for refining plots
                        legend=True,  # optional flag for adding a legend
                        scatter_kwargs=dict(alpha=0.4, s=10)  # optional input for refining plots
                )
                print_df_state(df_query, ["wind_speed", "wind_direction"])
                data_inspector.plot_time_series(df_query, feature_types=["wind_speed", "wind_direction"], turbine_ids=["wt001"], continuity_groups=None) 

            del frozen_sensors
        # %%
        if "inoperational" in FILTERS:
            logging.info("Nullifying inoperational turbine cells.")
            # check if wind speed/dir measurements from inoperational turbines differ from fully operational
            status_codes = [1]
            mask = lambda tid: pl.col(f"turbine_status_{tid}").is_in(status_codes) | pl.col(f"turbine_status_{tid}").is_null()
            features = ws_cols
            
            # loop through each turbine's wind speed and wind direction columns, and compare the distribution of data with and without the inoperational turbines
            # fill out_of_range measurements with Null st they are marked for interpolation via impute or linear/forward fill interpolation later
            threshold = 0.01
            logging.info("Nullifying inoperational turbine measurements in dataframe.")
            df_query = data_filter.conditional_filter(df_query, threshold, mask, ws_cols + wd_cols, check_js=False)

            # check time series
            if PLOT:
                DataInspector.print_pc_unfiltered_vals(df_query, features, mask)
                DataInspector.plot_filtered_vs_unfiltered(df_query, mask, ws_cols + wd_cols, ["wind_speed", "wind_direction"], ["Wind Speed [m/s]", "Wind Direction [deg]"])
                print_df_state(df_query, ["wind_speed", "wind_direction"])
                data_inspector.plot_time_series(df_query, feature_types=["wind_speed", "wind_direction"], turbine_ids=["wt001"], continuity_groups=None)

            del mask
        # %%
        if "range_flag" in FILTERS:
            logging.info("Nullifying wind speed out-of-range cells.")

            # check for wind speed values that are outside of the acceptable range
            if not os.path.exists(PL_SAVE_PATH.replace(".parquet", "_out_of_range.npy")):
                ws = df_query.select(cs.starts_with("wind_speed")).collect().to_pandas()
                out_of_range = (filters.range_flag(ws, lower=0, upper=70) & ~ws.isna()).values # range flag includes formerly null values as nan
                del ws
                np.save(PL_SAVE_PATH.replace(".parquet", "_out_of_range.npy"), out_of_range)
            else:
                out_of_range = np.load(PL_SAVE_PATH.replace(".parquet", "_out_of_range.npy"))

            # check if wind speed/dir measurements from inoperational turbines differ from fully operational 
            mask = lambda tid: ~out_of_range[:, data_loader.turbine_ids.index(tid)]
            features = ws_cols

            # loop through each turbine's wind speed and wind direction columns, and compare the distribution of data with and without the inoperational turbines
            # fill out_of_range measurements with Null st they are marked for interpolation via impute or linear/forward fill interpolation later
            threshold = 0.01
            logging.info("Nullifying wind speed out of range measurements in dataframe.")
            df_query = data_filter.conditional_filter(df_query, threshold, mask, ws_cols, check_js=False)

            # check time series
            if PLOT:
                DataInspector.print_pc_unfiltered_vals(df_query, features, mask)
                DataInspector.plot_filtered_vs_unfiltered(df_query, mask, ws_cols, ["wind_speed"], ["Wind Speed [m/s]"])
                print_df_state(df_query, ["wind_speed", "wind_direction"])
                data_inspector.plot_time_series(df_query, feature_types=["wind_speed", "wind_direction"], turbine_ids=["wt001"], continuity_groups=None)

            del out_of_range, mask
        # %%
        if "window_range_flag" in FILTERS:
            logging.info("Nullifying wind speed-power curve out-of-window cells.")
            # apply a window range filter to remove data with power values outside of the window from 20 to 3000 kW for wind speeds between 5 and 40 m/s.
            # identifies when turbine is shut down, filtering for normal turbine operation
            if not os.path.exists(PL_SAVE_PATH.replace(".parquet", "_out_of_window.npy")):
                data_filter.multiprocessor = None
                out_of_window = data_filter.multi_generate_filter(df_query=df_query, filter_func=data_filter._single_generate_window_range_filter,
                                                                  feature_types=["wind_speed", "power_output"], turbine_ids=data_loader.turbine_ids,
                                                                  window_start=5., window_end=40., value_min=20., value_max=3000.)
                data_filter.multiprocessor = MULTIPROCESSOR
                np.save(PL_SAVE_PATH.replace(".parquet", "_out_of_window.npy"), out_of_window)
            else:
                out_of_window = np.load(PL_SAVE_PATH.replace(".parquet", "_out_of_window.npy"))

            # check if wind speed/dir measurements from inoperational turbines differ from fully operational 
            mask = lambda tid: ~out_of_window[:, data_loader.turbine_ids.index(tid)]
            features = ws_cols 

            # fill cells corresponding to values that are outside of power-wind speed window range with Null st they are marked for interpolation via impute or linear/forward fill interpolation later
            # loop through each turbine's wind speed and wind direction columns, and compare the distribution of data with and without the inoperational turbines
            threshold = 0.01
            logging.info("Nullifying wind speed-power curve out-of-window measurements in dataframe.")
            df_query = data_filter.conditional_filter(df_query, threshold, mask, features, check_js=False)
        
            # %% check time series
            if PLOT:
                DataInspector.print_pc_unfiltered_vals(df_query, features, mask)
                DataInspector.plot_filtered_vs_unfiltered(df_query, mask, features, ["wind_speed"], ["Wind Speed [m/s]"])

                # plot values that are outside of power-wind speed range
                plot.plot_power_curve(
                    data_inspector.collect_data(df=df_query, feature_types="wind_speed").to_numpy().flatten(),
                    data_inspector.collect_data(df=df_query, feature_types="power_output").to_numpy().flatten(),
                    flag=out_of_window.flatten(),
                    flag_labels=("Outside Acceptable Window", "Acceptable Power Curve Points"),
                    xlim=(-1, 15),
                    ylim=(-100, 3000),
                    legend=True,
                    scatter_kwargs=dict(alpha=0.4, s=10)
                )
                
                print_df_state(df_query, ["wind_speed", "wind_direction"])
                data_inspector.plot_time_series(df_query, feature_types=["wind_speed", "wind_direction"], turbine_ids=["wt001"], continuity_groups=None)
            
            del out_of_window, mask
         
        # %%
        if "bin_filter" in FILTERS:
            logging.info("Nullifying wind speed-power curve bin-outlier cells.")
            # apply a bin filter to remove data with power values outside of an envelope around median power curve at each wind speed
            if not os.path.exists(PL_SAVE_PATH.replace(".parquet", "_bin_outliers.npy")):
                data_filter.multiprocessor = None
                bin_outliers = data_filter.multi_generate_filter(df_query=df_query, filter_func=data_filter._single_generate_bin_filter,
                                                                  feature_types=["wind_speed", "power_output"], turbine_ids=data_loader.turbine_ids,
                                                                  bin_width=50, threshold=3, center_type="median", 
                                                                  bin_min=20., bin_max=0.90*(df_query.select(pl.max_horizontal(cs.starts_with(f"power_output").max())).collect().item() or 3000.),
                                                                  threshold_type="scalar", direction="below")
                data_filter.multiprocessor = MULTIPROCESSOR
                np.save(PL_SAVE_PATH.replace(".parquet", "_bin_outliers.npy"), bin_outliers)
            else:
                bin_outliers = np.load(PL_SAVE_PATH.replace(".parquet", "_bin_outliers.npy"))

            # check if wind speed/dir measurements from inoperational turbines differ from fully operational 
            mask = lambda tid: ~bin_outliers[:, data_loader.turbine_ids.index(tid)]
            features = ws_cols
            
            # fill cells corresponding to values that are outside of power-wind speed bins with Null st they are marked for interpolation via impute or linear/forward fill interpolation later
            # loop through each turbine's wind speed and wind direction columns, and compare the distribution of data with and without the inoperational turbines
            threshold = 0.01
            logging.info("Nullifying wind speed-power curve bin outlier measurements in dataframe.")
            df_query = data_filter.conditional_filter(df_query, threshold, mask, features, check_js=False)

            # %% check time series
            if PLOT:
                DataInspector.print_pc_unfiltered_vals(df_query, features, mask)
                DataInspector.plot_filtered_vs_unfiltered(df_query, mask, features, ["wind_speed"], ["Wind Speed [m/s]"])

                # plot values outside the power-wind speed bin filter
                plot.plot_power_curve(
                    data_inspector.collect_data(df=df_query, feature_types="wind_speed").to_numpy().flatten(),
                    data_inspector.collect_data(df=df_query, feature_types="power_output").to_numpy().flatten(),
                    flag=bin_outliers.flatten(),
                    flag_labels=("Anomylous Data", "Normal Wind Speed Sensor Operation"),
                    xlim=(-1, 15),
                    ylim=(-100, 3000),
                    legend=True,
                    scatter_kwargs=dict(alpha=0.4, s=10)
                )
                
                print_df_state(df_query, ["wind_speed", "wind_direction"])
                data_inspector.plot_time_series(df_query, feature_types=["wind_speed", "wind_direction"], turbine_ids=["wt001"], continuity_groups=None)

            del bin_outliers, mask
            
        # %%
        if "std_range_flag" in FILTERS:
            logging.info("Nullifying standard deviation outliers.")
            # apply a bin filter to remove data with power values outside of an envelope around median power curve at each wind speed
            if not os.path.exists(PL_SAVE_PATH.replace(".parquet", "_std_dev_outliers.npy")):
                data_filter.multiprocessor = None
                std_dev_outliers = data_filter.multi_generate_filter(df_query=df_query, filter_func=data_filter._single_generate_std_range_filter,
                                                                  feature_types=["wind_speed", "wind_direction"], turbine_ids=data_loader.turbine_ids)
                data_filter.multiprocessor = MULTIPROCESSOR
                np.save(PL_SAVE_PATH.replace(".parquet", "_std_dev_outliers.npy"), std_dev_outliers)
            else:
                std_dev_outliers = np.load(PL_SAVE_PATH.replace(".parquet", "_std_dev_outliers.npy"))

            # check if wind speed/dir measurements from inoperational turbines differ from fully operational 
            ws_mask = lambda tid: ~std_dev_outliers[:, data_loader.turbine_ids.index(tid), 0]
            wd_mask = lambda tid: ~std_dev_outliers[:, data_loader.turbine_ids.index(tid), 1]

            # fill cells corresponding to values that are outside of power-wind speed bins with Null st they are marked for interpolation via impute or linear/forward fill interpolation later
            # loop through each turbine's wind speed and wind direction columns, and compare the distribution of data with and without the inoperational turbines
            threshold = 0.01
            logging.info("Nullifying wind speed/direction standard deviation measurements in dataframe.")
            df_query = data_filter.conditional_filter(df_query, threshold, ws_mask, features=ws_cols, check_js=False)
            df_query = data_filter.conditional_filter(df_query, threshold, wd_mask, features=wd_cols, check_js=False)
            
            # check time series
            if PLOT:
                DataInspector.print_pc_unfiltered_vals(df_query, features, ws_mask)
                DataInspector.print_pc_unfiltered_vals(df_query, features, wd_mask)
                DataInspector.plot_filtered_vs_unfiltered(df_query, ws_mask, features, ["wind_speed"], ["Wind Speed [m/s]"])
                DataInspector.plot_filtered_vs_unfiltered(df_query, ws_mask, features, ["wind_direction"], ["Wind Direction [deg]"])
            
                print_df_state(df_query, ["wind_speed", "wind_direction"])
                data_inspector.plot_time_series(df_query, feature_types=["wind_speed", "wind_direction"], turbine_ids=["wt001"], continuity_groups=None)

            del std_dev_outliers, mask
        # %%
        if PLOT:
            logging.info("Power curve fitting.")
            # Fit the power curves
            iec_curve = power_curve.IEC(
                windspeed_col="wind_speed", power_col="power_output",
                data=DataInspector.unpivot_dataframe(df_query, feature_types=["wind_speed", "power_output"]).select("wind_speed", "power_output").filter(pl.all_horizontal(pl.all().is_not_null())).collect(streaming=True).to_pandas(),
                )

            l5p_curve = power_curve.logistic_5_parametric(
                windspeed_col="wind_speed", power_col="power_output",
                data=DataInspector.unpivot_dataframe(df_query, feature_types=["wind_speed", "power_output"]).select("wind_speed", "power_output").filter(pl.all_horizontal(pl.all().is_not_null())).collect(streaming=True).to_pandas(),
                )

            spline_curve = power_curve.gam(
                windspeed_col="wind_speed", power_col="power_output",
                data=DataInspector.unpivot_dataframe(df_query, feature_types=["wind_speed", "power_output"]).select("wind_speed", "power_output").filter(pl.all_horizontal(pl.all().is_not_null())).collect(streaming=True).to_pandas(), 
                n_splines=20)

            fig, ax = plot.plot_power_curve(
                data_inspector.collect_data(df=df_query, feature_types="wind_speed").to_numpy().flatten(),
                data_inspector.collect_data(df=df_query, feature_types="power_output").to_numpy().flatten(),
                flag=np.zeros(data_inspector.collect_data(df=df_query, feature_types="wind_speed").shape[0], dtype=bool),
                flag_labels=("", "Filtered Power Curve"),
                xlim=(-1, 15),  # optional input for refining plots
                ylim=(-100, 3000),  # optional input for refining plots
                legend=False,  # optional flag for adding a legend
                scatter_kwargs=dict(alpha=0.4, s=10),  # optional input for refining plots
                return_fig=True,
            )

            x = np.linspace(0, 20, 100)
            ax.plot(x, iec_curve(x), color="red", label = "IEC", linewidth = 3)
            ax.plot(x, spline_curve(x), color="C1", label = "Spline", linewidth = 3)
            ax.plot(x, l5p_curve(x), color="C2", label = "L5P", linewidth = 3)

            ax.legend()

            fig.tight_layout()
            plt.show()

        df_query.collect().write_parquet(PL_SAVE_PATH.replace(".parquet", "_filtered.parquet"), statistics=False)
    else:
        df_query = pl.scan_parquet(PL_SAVE_PATH.replace(".parquet", "_filtered.parquet"))

    # %% check time series
    if PLOT:
        print_df_state(df_query, ["wind_speed", "wind_direction"])
        data_inspector.plot_time_series(df_query, feature_types=["wind_speed", "wind_direction"], turbine_ids=["wt001"], continuity_groups=None)
     
    # %% 
    if "impute_missing_data" in FILTERS and (RELOAD_DATA or not os.path.exists(PL_SAVE_PATH.replace(".parquet", "_imputed.parquet"))): 
        logging.info("Impute/interpolate turbine missing dta from correlated measurements.")
        # else, for each of those split datasets, impute the values using the imputing.impute_all_assets_by_correlation function
        # fill data on single concatenated dataset
        df_query2 = data_filter._fill_single_missing_dataset(df_idx=0, df=df_query, impute_missing_features=["wind_speed", "wind_direction"], 
                                                interpolate_missing_features=["wind_direction", "wind_speed", "nacelle_direction"], 
                                                available_features=data_loader.available_features, parallel="turbine_id")

        df_query = df_query.drop([cs.starts_with(feat) for feat in ["wind_direction", "wind_speed", "nacelle_direction"]]).join(df_query2, on="time", how="left")
        df_query.collect().write_parquet(PL_SAVE_PATH.replace(".parquet", "_imputed.parquet"), statistics=False)
    else:
        df_query = pl.scan_parquet(PL_SAVE_PATH.replace(".parquet", "_imputed.parquet"))
    
    # %% check time series
    if PLOT:
        print_df_state(df_query, ["wind_speed", "wind_direction"])
        continuity_groups = df_query.select("continuity_group").unique().collect().to_numpy().flatten()
        data_inspector.plot_time_series(df_query, feature_types=["wind_speed", "wind_direction"], turbine_ids=data_loader.turbine_ids, continuity_groups=continuity_groups) 
    
    # %%
    if "split" in FILTERS and (RELOAD_DATA or not os.path.exists(PL_SAVE_PATH.replace(".parquet", "_split.parquet"))):
        logging.info("Split dataset during time steps for which many turbines have missing data.")
        
        # if there is a short or long gap for some turbines, impute them using the imputing.impute_all_assets_by_correlation function
        #       else if there is a short or long gap for many turbines, split the dataset
        missing_col_thr = max(1, int(len(data_loader.turbine_ids) * 0.1))
        missing_duration_thr = np.timedelta64(10, "m")
        minimum_not_missing_duration = np.timedelta64(20, "m")
        missing_data_cols = ["wind_speed", "wind_direction", "nacelle_direction"]

        # check for any periods of time for which more than 'missing_col_thr' features have missing data
        df_query2 = df_query\
                .with_columns([cs.contains(col).is_null().name.prefix("is_missing_") for col in missing_data_cols])\
                .with_columns(**{f"num_missing_{col}": pl.sum_horizontal((cs.contains(col) & cs.starts_with("is_missing"))) for col in missing_data_cols})

        # subset of data, indexed by time, which has <= the threshold number of missing columns
        df_query_not_missing_times = add_df_continuity_columns(df_query2, mask=pl.sum_horizontal(cs.starts_with("num_missing")) <= missing_col_thr, dt=data_loader.dt)

        # subset of data, indexed by time, which has > the threshold number of missing columns
        df_query_missing_times = add_df_continuity_columns(df_query2, mask=pl.sum_horizontal(cs.starts_with("num_missing")) > missing_col_thr, dt=data_loader.dt)

        # start times, end times, and durations of each of the continuous subsets of data in df_query_missing_times 
        df_query_not_missing = add_df_agg_continuity_columns(df_query_not_missing_times) 
        df_query_missing = add_df_agg_continuity_columns(df_query_missing_times)

        # start times, end times, and durations of each of the continuous subsets of data in df_query_not_missing_times 
        # AND of each of the continuous subsets of data in df_query_missing_times that are under the threshold duration time 
        df_query_not_missing = pl.concat([df_query_not_missing, 
                                                df_query_missing.filter(pl.col("duration") <= missing_duration_thr)])\
                                .sort("start_time")

        df_query_missing = df_query_missing.filter(pl.col("duration") > missing_duration_thr)

        if df_query_not_missing.select(pl.len()).collect().item() == 0:
            raise Exception("Parameters 'missing_col_thr' or 'missing_duration_thr' are too stringent, can't find any eligible durations of time.")

        df_query_missing = merge_adjacent_periods(agg_df=df_query_missing, dt=data_loader.dt)
        df_query_not_missing = merge_adjacent_periods(agg_df=df_query_not_missing, dt=data_loader.dt)

        df_query_missing = group_df_by_continuity(df=df_query2, agg_df=df_query_missing, missing_data_cols=missing_data_cols)
        df_query_not_missing = group_df_by_continuity(df=df_query2, agg_df=df_query_not_missing, missing_data_cols=missing_data_cols)
        df_query_not_missing = df_query_not_missing.filter(pl.col("duration") >= minimum_not_missing_duration)
        
        df_query = df_query2.select(data_loader.available_features)

        if PLOT:
            # Plot number of missing wind dir/wind speed data for each wind turbine (missing duration on x axis, turbine id on y axis, color for wind direction/wind speed)
            from matplotlib import colormaps
            from matplotlib.ticker import MaxNLocator
            fig, ax = plt.subplots(1, 1)
            for feature_type, marker in zip(missing_data_cols, ["o", "^"]):
                for turbine_id, color in zip(data_loader.turbine_ids, colormaps["tab20c"](np.linspace(0, 1, len(data_loader.turbine_ids)))):
                    df = df_query_missing.select("duration", f"is_missing_{feature_type}_{turbine_id}").collect().to_pandas()
                    ax.scatter(x=df["duration"].dt.seconds / 3600,
                                y=df[f"is_missing_{feature_type}_{turbine_id}"].astype(int),  
                    marker=marker, label=turbine_id, s=400, color=color)
            ax.set_title("Occurence of Missing Wind Speed (circle) and Wind Direction (triangle) Values vs. Missing Duration, for each Turbine")
            ax.set_xlabel("Duration of Missing Values [hrs]")
            ax.set_ylabel("Number of Missing Values over this Duration")
            h, l = ax.get_legend_handles_labels()
            # ax.legend(h[:len(data_loader.turbine_ids)], l[:len(data_loader.turbine_ids)], ncol=8)
            ax.yaxis.set_major_locator(MaxNLocator(integer=True))

            # Plot missing duration on x axis, number of missing turbines on y-axis, marker for wind speed vs wind direction,
            fig, ax = plt.subplots(1, 1)
            for feature_type, marker in zip(missing_data_cols, ["o", "^"]):
                df = df_query_missing.select("duration", (cs.contains(feature_type) & cs.starts_with("is_missing")))\
                                        .with_columns(pl.sum_horizontal([f"is_missing_{feature_type}_{tid}" for tid in data_loader.turbine_ids]).alias(f"is_missing_{feature_type}")).collect().to_pandas()
                ax.scatter(x=df["duration"].dt.seconds / 3600,
                            y=df[f"is_missing_{feature_type}"].astype(int),  
                marker=marker, label=feature_type, s=400)
            ax.set_title("Occurence of Missing Wind Speed (circle) and Wind Direction (triangle) Values vs. Missing Duration, for all Turbines")
            ax.set_xlabel("Duration of Missing Values [hrs]")
            ax.set_ylabel("Number of Missing Values over this Duration")
            h, l = ax.get_legend_handles_labels()
            # ax.legend(h[:len(missing_data_cols)], l[:len(missing_data_cols)], ncol=8)
            ax.yaxis.set_major_locator(MaxNLocator(integer=True))

        # if more than 'missing_col_thr' columns are missing data for more than 'missing_timesteps_thr', split the dataset at the point of temporal discontinuity
        # df_query = [df.lazy() for df in df_query.with_columns(get_continuity_group_index(df_query_not_missing).alias("continuity_group"))\
        #                           .filter(pl.col("continuity_group") != -1)\
        #                           .drop(cs.contains("is_missing") | cs.contains("num_missing"))
        #                           .collect(streaming=True)\
        #                           .sort("time")
        #                           .partition_by("continuity_group")]

        df_query = df_query.with_columns(get_continuity_group_index(df_query_not_missing).alias("continuity_group"))\
                                .filter(pl.col("continuity_group") != -1)\
                                .drop(cs.contains("is_missing") | cs.contains("num_missing"))\
                                .sort("time").collect().lazy()
        df_query.collect().write_parquet(PL_SAVE_PATH.replace(".parquet", "_split.parquet"), statistics=False)
        # check each split dataframe a) is continuous in time AND b) has <= than the threshold number of missing columns OR for less than the threshold time span
        # for df in df_query:
        #     assert df.select((pl.col("time").diff(null_behavior="drop") == np.timedelta64(data_loader.dt, "s")).all()).collect(streaming=True).item()
        #     assert (df.select((pl.sum_horizontal([(cs.numeric() & cs.contains(col)).is_null() for col in missing_data_cols]) <= missing_col_thr)).collect(streaming=True)
        #             |  ((df.select("time").max().collect(streaming=True).item() - df.select("time").min().collect(streaming=True).item()) < missing_duration_thr))
    else:
        df_query = pl.scan_parquet(PL_SAVE_PATH.replace(".parquet", "_split.parquet"))

    # %% check time series
    if PLOT:
        print_df_state(df_query, ["wind_speed", "wind_direction"])
        continuity_groups = df_query.select("continuity_group").unique().collect().to_numpy().flatten()
        data_inspector.plot_time_series(df_query, feature_types=["wind_speed", "wind_direction"], turbine_ids=["wt001"], continuity_groups=continuity_groups)

    # %%
    if "normalize" in FILTERS and (RELOAD_DATA or not os.path.exists(PL_SAVE_PATH.replace(".parquet", "_normalized.parquet"))): 
        # Normalization & Feature Selection
        logging.info("Normalizing and selecting features.")
        df_query = df_query\
                .with_columns(((cs.starts_with("wind_direction") - 180.).radians().sin()).name.map(lambda c: "wd_sin_" + c.split("_")[-1]),
                            ((cs.starts_with("wind_direction") - 180.).radians().cos()).name.map(lambda c: "wd_cos_" + c.split("_")[-1]))\
                .with_columns(**{f"ws_horz_{tid}": (pl.col(f"wind_speed_{tid}") * pl.col(f"wd_sin_{tid}")) for tid in data_loader.turbine_ids})\
                .with_columns(**{f"ws_vert_{tid}": (pl.col(f"wind_speed_{tid}") * pl.col(f"wd_cos_{tid}")) for tid in data_loader.turbine_ids})\
                .with_columns(**{f"nd_cos_{tid}": ((pl.col(f"nacelle_direction_{tid}") - 180.).radians().cos()) for tid in data_loader.turbine_ids})\
                .with_columns(**{f"nd_sin_{tid}": ((pl.col(f"nacelle_direction_{tid}") - 180.).radians().sin()) for tid in data_loader.turbine_ids})\
                .select(pl.col("time"), pl.col("continuity_group"), cs.contains("nd_sin"), cs.contains("nd_cos"), cs.contains("ws_horz"), cs.contains("ws_vert"))

        # store min/max of each column to rescale later
        # is_numeric = (cs.contains("ws") | cs.contains("nd"))
        feature_types = ["nd_cos", "nd_sin", "ws_horz", "ws_vert"]
        
        norm_vals = {}
        for feature_type in feature_types:
            norm_vals[f"{feature_type}_max"] = df_query.select(pl.max_horizontal(cs.starts_with(feature_type).max())).collect().item()
            norm_vals[f"{feature_type}_min"] = df_query.select(pl.min_horizontal(cs.starts_with(feature_type).min())).collect().item()

        norm_vals = pl.DataFrame(norm_vals).select(pl.all().round(2))
        norm_vals.write_csv(os.path.join(os.path.dirname(PL_SAVE_PATH), "normalization_consts.csv"))

        df_query = df_query.select([pl.col("time"), pl.col("continuity_group")] 
                                + [((2.0 * ((cs.starts_with(feature_type) - norm_vals.select(f"{feature_type}_min").item()) 
                                  / (norm_vals.select(f"{feature_type}_max").item() - norm_vals.select(f"{feature_type}_min").item()))) - 1.0).name.keep()
                                  for feature_type in feature_types])
        
        # cg = 6
        # df = df_query.filter(pl.col("continuity_group") == cg).select(pl.col("time"), cs.starts_with("ws_vert")).min().collect()
        # df = df_query.filter((pl.col("continuity_group") == 6) & (pl.col("time") > np.datetime64("2022-02-01"))).select(pl.col("time"), cs.starts_with("nd_cos")).collect()
        # plt.plot(df.select(pl.col("time")), df.select(cs.starts_with("nd_cos")))
         
        df_query.collect().write_parquet(PL_SAVE_PATH.replace(".parquet", "_normalized.parquet"), statistics=False)
    else:
        df_query = pl.scan_parquet(PL_SAVE_PATH.replace(".parquet", "_normalized.parquet"))

    # %%
    if PLOT:
        logging.info("Plotting time series.")
        continuity_groups = df_query.select(pl.col("continuity_group")).unique().collect().to_numpy().flatten()
        feature_types = ["nd_cos", "nd_sin", "ws_horz", "ws_vert"]
        data_inspector.plot_time_series(df_query, feature_types=["ws_horz", "ws_vert"], turbine_ids=data_loader.turbine_ids, continuity_groups=continuity_groups)
        plt.show()
        # df_query.filter(pl.col("continuity_group") == 5).select(cs.ends_with("wt080") | cs.ends_with("wt081")).select(cs.starts_with("ws_")).collect()

        logging.info("Plotting and fitting target value distribution.")
        data_inspector.plot_data_distribution(df_query, feature_types=["ws_horz", "ws_vert"], turbine_ids=data_loader.turbine_ids, distribution=norm)