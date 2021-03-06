""" Module to work with Sentinel-5p data """

import os
import re
import glob
from netCDF4 import Dataset
import matplotlib.pyplot as plt
import numpy as np
import numpy.ma as ma
from pandas.io.json import json_normalize
from shapely.geometry import Polygon
import geopandas as gpd
import rasterio as rio
from rasterio.plot import plotting_extent
from rasterio.transform import from_origin
import harp
from harp._harppy import NoDataError
import earthpy.plot as ep
import radiance as rd


def create_resample_operations(
    bounding_box, cell_size=0.025, quality_value=50
):
    """Returns a string that is formatted to
    work with the harp.import_product() function.

    Parameters
    ----------
    bounding_box : tuple (of int or float)
        Tuple (min longitude, min latitude,
        max longitude, max latitude) or
        (left, bottom, right, top) for
        the study area.

    cell_size : int or float
        Cell size in degrees (at the equator) of
        the output/resampled data. Default value
        is 0.025 (similar to native data resolution).

    quality_value : int
        Value used to filter data on. Only data
        with QA values greater that the specified
        value will be included. Valid values are
        0-100. Default value is 50.

    Returns
    -------
    operations : str
        String formatted to work with the
        harp.import_product() function.

    Example
    -------
        >>> # Create operations
        >>> no2_operations = no2_resample_operations(
        ...     bounding_box=(113.5, 30, 115.5, 31),
        ...     cell_size=0.025,
        ...     quality_value=50
        ... )
        >>> # Display operations
        >>> no2_operations
        'tropospheric_NO2_column_number_density_validity > 50;
         latitude > 30 [degree_north];
         latitude < 31 [degree_north];
         longitude > 113.5 [degree_east];
         longitude < 115.5 [degree_east];
         bin_spatial(41, 30, 0.025, 81, 113.5, 0.025);
         derive(latitude {latitude});
         derive(longitude {longitude})'
    """
    # Compute number of lat/lon grid cells in each column/row
    num_latitude_cells = int(
        (bounding_box[3] - bounding_box[1]) / cell_size + 1
    )
    num_longitude_cells = int(
        (bounding_box[2] - bounding_box[0]) / cell_size + 1
    )

    # Define operations
    operations = (
        # Filter cells for data quality
        f"tropospheric_NO2_column_number_density_validity > {quality_value}; "
        # Filter cells by lat/lon bounds
        f"latitude > {bounding_box[1]} [degree_north]; "
        f"latitude < {bounding_box[3]} [degree_north]; "
        f"longitude > {bounding_box[0]} [degree_east]; "
        f"longitude < {bounding_box[2]} [degree_east]; "
        # Re-sample grid
        f"bin_spatial({num_latitude_cells}, {bounding_box[1]}, {cell_size}, "
        f"{num_longitude_cells}, {bounding_box[0]}, {cell_size}); "
        # Derive pixel centers for re-sampled grid
        "derive(latitude {latitude}); "
        "derive(longitude {longitude})"
    )

    # Return operations
    return operations


def remove_trailing(operations_string):
    """Removes unnecessary characters from an import operations string."""
    # Remove characters
    if operations_string[-1] == ";":
        operations = operations_string[:-1]
    elif operations_string[-2:] == "; ":
        operations = operations_string[:-2]
    else:
        pass

    # Return updated operations string
    return operations


def create_import_operations(
    quality_variable=None,
    quality_comparison=None,
    quality_threshold=None,
    bounding_box=None,
    cell_size=None,
    derive_variables=None,
    keep_variables=None,
):
    """Returns a string that is formatted to
    work with the harp.import_product() function
    operations parameter.

    Documentation:
        https://stcorp.github.io/harp/doc/html/operations.html

    Parameters
    ----------
    quality_variable : str
        Variable name of the data quality indicator of interest.
        Data will be filtered based on this variable.

    quality_comparison : str
        Operator used to compare/filter data based on the quality_variable
        and quality_threshold. Value values are:

        '==' (equal to, data == 5)
        '!=' (not equal to, data != 5)
        '<'  (less than, data < 5)
        '<=' (less than or equal to, data <= 5)
        '>=' (greater than or equal to, data >= 5)
        '>'  (greater than, data > 5)
        '=&' (bitfield, data =& 5, True if both bits 1 and 3 in data are set)
        '!&' (bitfield, data !& 5, True if neither bits 1 or 3 in data are set)

    quality_threshold : int or float
        Threshold value to filter the data on.

    bounding_box : tuple of int or float
        Bounding box to filter the data on, formatted as
        (longitude_min, latitude_min, longitude_max, latitude_max).

    cell_size : int or float
        Cell size in degrees for the resampled grid.

    derive_variables : list of str
        List of variables to derive during import, with units and/or
        dimensions as applicable.

    keep_variables : list of str
        List of variables to keep during import.

    Returns
    -------
    operations : str
        String of the harp import operations.

    Example
    -------
        >>>
        >>>
        >>>
        >>>
    """
    # Define quality component
    if quality_variable and quality_comparison and quality_threshold:
        quality_str = (
            f"{quality_variable} {quality_comparison} {quality_threshold}; "
        )
    else:
        quality_str = None

    # Define geographic bounds and bin_spatial() components
    if bounding_box and cell_size:
        lat_min_str = f"latitude > {bounding_box[1]} [degree_north]; "
        lat_max_str = f"latitude < {bounding_box[3]} [degree_north]; "
        lon_min_str = f"longitude > {bounding_box[0]} [degree_east]; "
        lon_max_str = f"longitude < {bounding_box[2]} [degree_east]; "
        geographic_bounds_str = (
            lat_min_str + lat_max_str + lon_min_str + lon_max_str
        )

        # Compute number of lat/lon grid cells in each column/row
        num_latitude_cells = int(
            (bounding_box[3] - bounding_box[1]) / cell_size + 1
        )
        num_longitude_cells = int(
            (bounding_box[2] - bounding_box[0]) / cell_size + 1
        )

        # Deifine parameters for bin_spatial() function
        bin_spatial_params = map(
            str,
            [
                num_latitude_cells,
                bounding_box[1],
                cell_size,
                num_longitude_cells,
                bounding_box[0],
                cell_size,
            ],
        )

        # Define bin_spatial() string
        bin_spatial_str = f"bin_spatial({', '.join(bin_spatial_params)}); "

    else:
        geographic_bounds_str = None
        bin_spatial_str = None

    # Define derive variables component
    if derive_variables:
        derive_str = "".join(
            [f"derive({variable}); " for variable in derive_variables]
        )
    else:
        derive_str = None

    # Define keep variables component
    if keep_variables:
        keep_str = f"keep({', '.join(keep_variables)});"
    else:
        keep_str = None

    # Create list of operation components
    operations_list = [
        quality_str,
        geographic_bounds_str,
        bin_spatial_str,
        derive_str,
        keep_str,
    ]

    # Join operations into single string
    operations_string = "".join(
        [operation for operation in operations_list if operation]
    )

    # Remove unnecessary trailing characters
    operations = (
        remove_trailing(operations_string)
        if operations_string
        else operations_string
    )

    # Return operations
    return operations


def extract_acquisition_time(netcdf4_path):
    """Extracts and returns the acqusition date
    and time for a Sentinel-5P netCDF4 file.

    Parameters
    ----------
    netcdf4_path : str
        Path to the netCDF4 file.

    Returns
    -------
    acquisiton_time : str
        Acqusition start time for the data,
        formatted as YYYY-MM-DDTHHMMSS'

    Example
    -------
        >>> # Extract time
        >>> extract_acquisition_time(nc_file_path)
        '2019-02-02-T052350Z'
    """
    # Extract acquisition start time
    with Dataset(netcdf4_path, "r") as netcdf4:
        acquisition_time = netcdf4.groups["PRODUCT"]["time_utc"][0][0][
            :19
        ].replace(":", "")

    # Separate y/m/d from time and add indicator of Zulu time
    acquisition_time = f"{acquisition_time[:10]}-{acquisition_time[10:]}Z"

    # Return start time
    return acquisition_time


def resample_netcdf4(
    netcdf4_path,
    resample_operations,
    export_folder,
    file_prefix,
    acquisition_time,
):
    """Resamples a Sentinel-5P netCDF4 file to a
    spatially-uniform grid cell size and exports
    the resampled file to netCDF4 format.

    Parameters
    ----------
    netcdf4_path : str
        Path to the netCDF4 file.

    resample_operations : str
        Operations used to filter and resample
        the netCDF4 file.

    export_folder : str
        Folder (exluding filename) for the
        exported netCDF4 file.

    acquisition_time : str
        Acquisition time of the data, for use
        in output file naming.

    Returns
    -------
    out_message : str
        Path of exported file if successful, otherwise
        message indicating failure to export the file.

    Example
    -------
        >>> # Resample netCDF file
        >>> resampled_path = resample_netcdf4(
        ...     nc_file, no2_operations,
        ...     export_folder="02-processed-data",
        ...     acquisition_time="2020-05-05-T052030Z"
        ... )
        "02-processed-data/S5P_OFFL_L3_NO2_2020-05-05-T052030Z.nc"
    """
    # Import netCDF file into harp, with operations
    resampled_data = harp.import_product(netcdf4_path, resample_operations)

    # Define export path
    export_path = os.path.join(
        export_folder, f"{file_prefix}-{acquisition_time}.nc"
    )

    try:
        # Export re-sampled harp product to netCDF
        harp.export_product(resampled_data, export_path, file_format="netcdf")

    except Exception:
        # Define failure message
        out_message = "Failed to export."

    else:
        # Define success message
        out_message = export_path
        print(f"Exported: {os.path.split(export_path)[-1]}")

    # Return message
    return out_message


def extract_no2_data(netcdf_path):
    """Extracts NO2 data from a Sentinel-5P netCDF4 file
    to a NumPy array and fills no data with NaN values.

    Intended for use with resampled data (Sentinel-5P L3).

    Parameters
    ----------
    netcdf_path : str
        Path to the netCDF file.

    Returns
    -------
    no2_data_filled : numpy array
        Array containing extracted NO2 values and no
        data values filled with NaN, if applicable.

    Example
    -------
        >>> # Define file path
        >>> netcdf_file = os.path.join(
        ...     "02-processed-data",
        ...     "S5P_OFFL_L3_NO2_2020-05-05-T052030Z.nc"
        ... )
        >>> # Extract NO2 data
        >>> no2_data = extract_no2_data(netcdf_file)
    """
    # Open .nc file as netCDF Dataset
    with Dataset(netcdf_path, "r") as netcdf_resampled:

        # Get NO2 data
        no2_data = netcdf_resampled.variables.get(
            "tropospheric_NO2_column_number_density"
        )[0]

        # Change fill value to NaN and fill array
        if isinstance(no2_data, np.ma.core.MaskedArray):

            # Change fill value to NaN
            ma.set_fill_value(no2_data, np.nan)

            # Fill masked values with NaN
            no2_data_filled = no2_data.filled()

        else:
            no2_data_filled = np.copy(no2_data)

        # Flip array to correct orientation
        no2_data_filled_flipped = np.flipud(no2_data_filled)

    # Return array
    return no2_data_filled_flipped


def store_no2_data(no2_data, no2_dict, acquisition_time):
    """Stores NO2 array in a dictionary, indexed by
    data acquisition year, month, day, and time.

    Parameters
    ----------
    no2_data : numpy array
        Array containing NO2 values.

    no2_dict : dict
        Dictionary for storing extracted NO2 data.

    acquisition_time : str
        Acquisition time of the data, used to index
        the extracted data in the dictionary.

    Returns
    -------
    message : str
        Message indicating success or failure to
        add NO2 data to the dictionary.

    Example
    -------
        >>> # Define file path
        >>> netcdf_file = os.path.join(
        ...     "02-processed-data",
        ...     "S5P_OFFL_L3_NO2_2020-05-05-T052030Z.nc"
        ... )
        >>> # Extract NO2 data
        >>> no2_data = extract_no2_data(netcdf_file)
        >>> # Initialize NO2 dictionary
        >>> no2 = {}
        >>> # Store NO2 data
        >>> store_no2_data(
        ...     no2_data, no2, "2020-05-05-T052030Z"
        ... )
        Added 2020-05-05-T052030Z array to dictionary.
    """
    # Split acquisition time into components
    filename_split = os.path.splitext(acquisition_time)[0].split("-")
    year = filename_split[-4]
    month = filename_split[-3]
    day = filename_split[-2]
    time = filename_split[-1]

    # Add year to dictionary if not existing key
    if year not in no2_dict.keys():
        no2_dict[year] = {}

    # Add month to dictionary if not existing key
    if month not in no2_dict.get(year).keys():
        no2_dict[year][month] = {}

    # Add day to dictionary if not existing key
    if day not in no2_dict.get(year).get(month).keys():
        no2_dict[year][month][day] = {}

    # Store NO2 array, indexed by acquistion year, month, day, and time
    if time not in no2_dict.get(year).get(month).get(day).keys():
        no2_dict[year][month][day][time] = no2_data

        # Define output message
        message = f"Added {acquisition_time} array to dictionary."

    else:
        # Define output message
        message = (
            f"{acquisition_time} index already in dictionary. Skipping..."
        )

    # Return message
    return message


def extract_no2_transform(netcdf_path):
    """Extracts and returns the transform from
    a netCDF file.

    Intended for use with resampled data (Sentinel-5P L3).

    Parameters
    ----------
    netcdf_path : str
         Path to the netCDF file.

    Returns
    -------
    transform : affine.Affine object
        Geographic transform for the file.

    Example
    ------
        >>> # Define file path
        >>> netcdf_file = os.path.join(
        ...     "02-processed-data",
        ...     "S5P_OFFL_L3_NO2_2020-05-05-T052030Z.nc"
        ... )
        >>> # Create transform
        >>> transform = extract_no2_transform(netcdf_file)
        >>> # Display transform
        >>> transform
        Affine(0.025, 0.0, -73.0,
               0.0, -0.025, 42.5)
    """
    # Open .nc file as netCDF Dataset
    with Dataset(netcdf_path, "r") as netcdf_resampled:

        # Get longitude pixel bounds
        # Lower-left, lower-right, upper-right, upper-left
        longitude_bounds = netcdf_resampled.variables.get("longitude_bounds")[
            :
        ]

        # Get longitude pixel bounds
        # Lower-left, lower-right, upper-right, upper-left
        latitude_bounds = netcdf_resampled.variables.get("latitude_bounds")[:]

        # Get top-left corner of image
        longitude_min = longitude_bounds.min()
        latitude_max = latitude_bounds.max()

        # Define cell spacing (degrees)
        column_spacing = round(
            longitude_bounds[0][-1] - longitude_bounds[0][0], 6
        )
        row_spacing = round(latitude_bounds[0][-1] - latitude_bounds[0][0], 6)

        # Define transform
        # Top-left corner: west, north, and pixel size: xsize, ysize
        transform = from_origin(
            longitude_min, latitude_max, column_spacing, row_spacing
        )

    # Return transform
    return transform


def extract_acquisition_time_processed(file_path):
    """Extracts the acquistion time for a Level-3
    (L3) processed Sentinel-5P NO2 data file.

    Parameters
    ----------
    file_path : str
        Path to the L3 netCDF file.

    Returns
    -------
    acquisition_time : str
        Acquisition time of the file.

    Example
    -------
        >>> # Extract time
        >>> extract_acquisition_time(nc_file_path)
        '2019-02-02-T052350Z'
    """
    # Parse path for file name
    file, _ = os.path.splitext(os.path.basename(file_path))

    # Extract acquisition time from file
    acquisition_time = file.split("_")[-1]

    # Return acquisition time
    return acquisition_time


def extract_arrays_to_list(no2, dates):
    """Returns a list of arrays from a nested dictionary,
    that is indexed by dictionary[Year][Month][Day][Time].

    Meant for intra and inter-month date ranges (both
    continuous and not continuous).

    Parameters
    ----------
    no2 : dict
        Dictionary containing masked daily NO2 arrays,
        indexed by dictionary['YYYY']['MM']['DD']['THHMMSSZ'].

    dates : list
        List of dates (strings), formatted as 'YYYY-MM-DD'.

    Returns
    -------
    array_list : list
        List of NO2 arrays within the specified dates.

    Example
    -------
        # Create date range to extract
        >>> date_range = create_date_list('2019-02-02', '2019-02-28'),
        >>> # Get NO2 array for each date into list
        >>> no2_arrays = extract_arrays_to_list(
        ...     no2=no2_data, dates=date_range)
    """
    # Flatten dataframe into dictionary
    no2_df = json_normalize(no2)

    # Replace '.' with '-' in column names
    no2_df.columns = [column.replace(".", "-") for column in no2_df.columns]

    # Create list of arrays based on date list
    array_list = [
        no2_df[col].loc[0]
        for day in dates
        for col in no2_df.columns
        if re.compile(f"^{day}").match(col)
    ]

    # Return list of arrays
    return array_list


def store_continuous_range_statistic(
    no2_daily, date_range_list, statistic="mean"
):
    """Calculates the specified statistic for each entry
    (year/month/day) in a list of and stores the statistics
    values in a dictionary.

    Parameters
    ----------
    radiance_daily : dict
        Dictionary containing daily radiance arrays,
        indexed by radiance['YYYY']['MM']['DD'].

    date_range_list : list (of str)
        List containing strings of format 'YYYY-MM-DD'.

    Returns
    -------
    no2_date_range_statistic : dict
        Dictionary containig date range variance radiance arrays,
        indexed by radiance_date_range_statisic['YYYYMMDD-YYYYMMDD'].

    Example
    -------
        >>> # Define date ranges
        >>> feb_date_range_list = [
        ...     ('2019-02-02', '2019-02-28')
        ... ]
        >>> # Store varaiance
        >>> feb_2019_variance = store_continuous_range_statistic(
        ...     no2_daily=no2_feb_2019,
        ...     date_range_list=feb_date_range_list,
        ...     statistic='variance')
        >>> # Show keys
        >>> for key in feb_2018_variance.keys():
        ...     print(key)
        20190202-20190228
    """
    # Raise error if input radiance data is not a dictionary
    if not isinstance(no2_daily, dict):
        raise TypeError("Input data must be of type dict.")

    # Raise error if input date data is not a list
    if not isinstance(date_range_list, list):
        raise TypeError("Input data must be of type list.")

    # Create list of date ranges for start/end date combo
    date_ranges = [
        rd.create_date_list(start_date, end_date)
        for start_date, end_date in date_range_list
    ]

    # Initialize dictionary to store radiance arrays
    no2_date_range_statistic = {}

    # Loop through all months
    for date_range in date_ranges:

        # Create index based on date range
        date_key = (
            f"{date_range[0].replace('-', '')}"
            f"-{date_range[-1].replace('-', '')}"
        )

        # Get arrays for all dates into list
        no2_arrays = extract_arrays_to_list(no2=no2_daily, dates=date_range)

        # Check statistic type
        # Mean
        if statistic == "mean":

            # Get mean for each pixel, over all arrays (bands)
            no2_statistic = rd.calculate_statistic(
                no2_arrays, statistic="mean"
            )

        # Variance
        elif statistic == "variance":

            # Get variance for each pixel, over all arrays (bands)
            no2_statistic = rd.calculate_statistic(
                no2_arrays, statistic="variance"
            )

        # Standard deviation
        elif statistic == "deviation":

            # Get standard deviation for each pixel, over all arrays (bands)
            no2_statistic = rd.calculate_statistic(
                no2_arrays, statistic="deviation"
            )

        # Any other value
        else:
            raise ValueError(
                "Invalid statistic. Function supports "
                "'mean', 'variance', or 'deviation'."
            )

        # Add statistic array to dictionary
        if date_key not in no2_date_range_statistic.keys():
            no2_date_range_statistic[date_key] = no2_statistic

    # Return date range statistic
    return no2_date_range_statistic


def convert_level2_to_level3(
    level2_folder,
    import_operations,
    level3_netcdf_folder,
    level3_geotiff_folder,
    level3_prefix,
):
    """Converts Sentinel-5P Level-2 netCDF files to Level-3
    netCDF and GeoTiff files.
    """
    # Create harp import operations string
    operations = create_import_operations(**import_operations)

    # Loop through Level-2 files
    for level2_file in level2_folder:

        # Extract acquisition time
        acquisition_time = extract_acquisition_time(level2_file)

        try:
            # Convert netCDF from Level-2 to Level-3
            level3_path = resample_netcdf4(
                level2_file,
                operations,
                export_folder=level3_netcdf_folder,
                file_prefix=level3_prefix,
                acquisition_time=acquisition_time,
            )

        # Empty file (no data)
        except NoDataError as error:
            print(f"{acquisition_time} {error}")

        # Non-empty file
        else:
            # Extract Level-3 data to array
            level3_data = extract_no2_data(level3_path)

            # Create transform
            transform = extract_no2_transform(level3_path)

            # Define export metadata
            metadata = rd.create_metadata(
                array=level3_data, transform=transform, nodata=np.nan
            )

            # Define outpath
            output_path = os.path.join(
                level3_geotiff_folder,
                f"{level3_prefix}-{acquisition_time}.tif",
            )

            # Export array to GeoTiff
            rd.export_array(
                array=level3_data, output_path=output_path, metadata=metadata
            )


def store_level3_data(level3_folder):
    """Returns an array containing Level-3 data from netCDF
    files, indexed by data acquisition year, month, day,
    and time.

    Parameters
    ----------
    level3_folder : str
        Path to the the folder containing the Level-3 netCDF files.

    Returns
    -------

    Example
    -------
    """
    # Initialize dictionary for data arrays
    data_dict = {}

    # Loop through Level-3 netCDF files
    for level3_file in glob.glob(os.path.join(level3_folder, "*.nc")):

        # Store NO2 data
        store_no2_data(
            no2_data=extract_no2_data(level3_file),
            no2_dict=data_dict,
            acquisition_time=extract_acquisition_time_processed(level3_file),
        )

    # Return dictionary
    return data_dict


def plot_change(
    pre_change,
    post_change,
    location="South Korea",
    title="NO2",
    data_source="European Space Agency",
):
    """Plots two arrays and the difference
    between the arrays on the same figure.

    pre_change : numpy array
        Numpy array containing radiance values.

    post_change : numpy array
        Numpy array containing radiance values.

    location : str, optional
        Name of study area location. Included in plot
        super-title. Default value is 'Penn State Campus'.

    titles : list of str, optional
        Plot sub-titles. Default value is ['Radiance',
        'Radiance', 'Radiance']. Intended for ['September
        2019 Mean Radiance', 'March 2020 Mean Radiance',
       'Change in Mean Radiance (September 2019 vs. March
       2020)'].

    data_source : str, optional
        Sources of data used in the plot.
        Default value is 'NASA Black Marble'.

    Returns
    -------
    tuple

        fig : matplotlib.figure.Figure object
            The figure object associated with the histogram.

        ax : matplotlib.axes._subplots.AxesSubplot objects
            The axes objects associated with the histogram.

    Example
    -------
        >>> # Define titles
        >>> plot_titles = [
        ...     'September 2019 Mean Radiance',
        ...     'March 2020 Mean Radiance',
        ...     'Change in Mean Radiance (September 2019 vs. March 2020)'
        ... ]
        >>> # Plot Sept 2019 and March 2020
        >>> fig, ax = plot_change(
        >>>     pre_change=radiance_monthtly_mean.get('2019').get('09'),
        >>>     post_change=radiance_monthtly_mean.get('2020').get('03'),
                titles=plot_titles)
    """
    # Calculate difference (post-change - pre-change)
    diff = post_change - pre_change

    # Find absolute values for change min & max
    diff_min_abs = np.absolute(diff.min())
    diff_max_abs = np.absolute(diff.max())

    # Determine max value (for plotting vmin/vmax)
    diff_vmax = diff_min_abs if (diff_min_abs > diff_max_abs) else diff_max_abs

    diff_vmin = -diff_vmax

    # Define radiance units
    units = r"$\mathrm{mol \cdot m^{-2}}$"

    # Define title
    diff_title = f"{title} ({units})"

    # Define color maps
    diff_cmap = "RdBu_r"

    # Use dark background
    with plt.style.context("dark_background"):

        # Create figure and axes objects
        fig, ax = plt.subplots(figsize=(12, 12))

        # Add super title
        plt.suptitle(f"{location} Nitrogen Dioxide Change", size=24)

        # Adjust spacing
        # plt.subplots_adjust(hspace=0.15)
        plt.subplots_adjust(top=0.98)

        # Plot diff array
        ep.plot_bands(
            diff,
            scale=False,
            title=diff_title,
            vmin=diff_vmin,
            vmax=diff_vmax,
            cmap=diff_cmap,
            ax=ax,
        )

        # Add caption
        fig.text(
            0.5, 0.15, f"Data Source: {data_source}", ha="center", fontsize=16
        )

        # Set title size
        ax.title.set_size(20)

    # Return figure and axes object
    return fig, ax


def plot_change_with_boundary(
    pre_change,
    post_change,
    extent_file,
    location="South Korea",
    title="NO2",
    data_source="European Space Agency",
):
    """Plots two arrays and the difference
    between the arrays on the same figure.

    pre_change : numpy array
        Numpy array containing radiance values.

    post_change : numpy array
        Numpy array containing radiance values.

    location : str, optional
        Name of study area location. Included in plot
        super-title. Default value is 'Penn State Campus'.

    titles : list of str, optional
        Plot sub-titles. Default value is ['Radiance',
        'Radiance', 'Radiance']. Intended for ['September
        2019 Mean Radiance', 'March 2020 Mean Radiance',
       'Change in Mean Radiance (September 2019 vs. March
       2020)'].

    data_source : str, optional
        Sources of data used in the plot.
        Default value is 'NASA Black Marble'.

    Returns
    -------
    tuple

        fig : matplotlib.figure.Figure object
            The figure object associated with the histogram.

        ax : matplotlib.axes._subplots.AxesSubplot objects
            The axes objects associated with the histogram.

    Example
    -------
        >>> # Define titles
        >>> plot_titles = [
        ...     'September 2019 Mean Radiance',
        ...     'March 2020 Mean Radiance',
        ...     'Change in Mean Radiance (September 2019 vs. March 2020)'
        ... ]
        >>> # Plot Sept 2019 and March 2020
        >>> fig, ax = plot_change(
        >>>     pre_change=radiance_monthtly_mean.get('2019').get('09'),
        >>>     post_change=radiance_monthtly_mean.get('2020').get('03'),
                titles=plot_titles)
    """
    # Get plotting extent
    with rio.open(extent_file) as src:
        src_extent = plotting_extent(src)

    # Calculate difference (post-change - pre-change)
    diff = post_change - pre_change

    # Find absolute values for change min & max
    diff_min_abs = np.absolute(diff.min())
    diff_max_abs = np.absolute(diff.max())

    # Determine max value (for plotting vmin/vmax)
    diff_vmax = diff_min_abs if (diff_min_abs > diff_max_abs) else diff_max_abs

    diff_vmin = -diff_vmax

    # Define radiance units
    units = r"$\mathrm{mol \cdot m^{-2}}$"

    # Define titles
    diff_title = f"{title} ({units})"

    # Define color maps
    diff_cmap = "RdBu_r"

    # Use dark background
    with plt.style.context("dark_background"):

        # Create figure and axes objects
        fig, ax = plt.subplots(figsize=(12, 12))

        # Add super title
        plt.suptitle(f"{location} Nitrogen Dioxide Change", size=24)

        # Adjust spacing
        # plt.subplots_adjust(hspace=0.15)
        plt.subplots_adjust(top=0.98)

        # Plot diff array
        ep.plot_bands(
            diff,
            scale=False,
            title=diff_title,
            vmin=diff_vmin,
            vmax=diff_vmax,
            cmap=diff_cmap,
            ax=ax,
            extent=src_extent,
        )

        # Add caption
        fig.text(
            0.5, 0.15, f"Data Source: {data_source}", ha="center", fontsize=16
        )

        # Set title size
        ax.title.set_size(20)

    # Return figure and axes object
    return fig, ax


def plot_percent_change(
    percent_change,
    location="South Korea",
    title="NO2",
    data_source="European Space Agency",
):
    """Plots two arrays and the difference
    between the arrays on the same figure.

    pre_change : numpy array
        Numpy array containing radiance values.

    post_change : numpy array
        Numpy array containing radiance values.

    location : str, optional
        Name of study area location. Included in plot
        super-title. Default value is 'Penn State Campus'.

    titles : list of str, optional
        Plot sub-titles. Default value is ['Radiance',
        'Radiance', 'Radiance']. Intended for ['September
        2019 Mean Radiance', 'March 2020 Mean Radiance',
       'Change in Mean Radiance (September 2019 vs. March
       2020)'].

    data_source : str, optional
        Sources of data used in the plot.
        Default value is 'NASA Black Marble'.

    Returns
    -------
    tuple

        fig : matplotlib.figure.Figure object
            The figure object associated with the histogram.

        ax : matplotlib.axes._subplots.AxesSubplot objects
            The axes objects associated with the histogram.

    Example
    -------
        >>> # Define titles
        >>> plot_titles = [
        ...     'September 2019 Mean Radiance',
        ...     'March 2020 Mean Radiance',
        ...     'Change in Mean Radiance (September 2019 vs. March 2020)'
        ... ]
        >>> # Plot Sept 2019 and March 2020
        >>> fig, ax = plot_change(
        >>>     pre_change=radiance_monthtly_mean.get('2019').get('09'),
        >>>     post_change=radiance_monthtly_mean.get('2020').get('03'),
                titles=plot_titles)
    """
    # Find absolute values for change min & max
    percent_change_min_abs = np.absolute(percent_change.min())
    percent_change_max_abs = np.absolute(percent_change.max())

    # Determine max value (for plotting vmin/vmax)
    diff_vmax = (
        percent_change_min_abs
        if (percent_change_min_abs > percent_change_max_abs)
        else percent_change_max_abs
    )

    diff_vmin = -diff_vmax

    # Define radiance units
    units = "%"

    # Define titles
    diff_title = f"{title} ({units})"

    # Define color maps
    diff_cmap = "RdBu_r"

    # Use dark background
    with plt.style.context("dark_background"):

        # Create figure and axes objects
        fig, ax = plt.subplots(figsize=(12, 12))

        # Add super title
        plt.suptitle(f"{location} Nitrogen Dioxide Change", size=24)

        # Adjust spacing
        # plt.subplots_adjust(hspace=0.15)
        plt.subplots_adjust(top=0.98)

        # Plot diff array
        ep.plot_bands(
            percent_change,
            scale=False,
            title=diff_title,
            vmin=diff_vmin,
            vmax=diff_vmax,
            cmap=diff_cmap,
            ax=ax,
        )

        # Add caption
        fig.text(
            0.5, 0.15, f"Data Source: {data_source}", ha="center", fontsize=16
        )

        # Set title size
        ax.title.set_size(20)

    # Return figure and axes object
    return fig, ax


def plot_percent_change_with_boundary(
    percent_change,
    extent_file,
    location="South Korea",
    title="NO2",
    data_source="European Space Agency",
):
    """Plots two arrays and the difference
    between the arrays on the same figure.

    pre_change : numpy array
        Numpy array containing radiance values.

    post_change : numpy array
        Numpy array containing radiance values.

    location : str, optional
        Name of study area location. Included in plot
        super-title. Default value is 'Penn State Campus'.

    titles : list of str, optional
        Plot sub-titles. Default value is ['Radiance',
        'Radiance', 'Radiance']. Intended for ['September
        2019 Mean Radiance', 'March 2020 Mean Radiance',
       'Change in Mean Radiance (September 2019 vs. March
       2020)'].

    data_source : str, optional
        Sources of data used in the plot.
        Default value is 'NASA Black Marble'.

    Returns
    -------
    tuple

        fig : matplotlib.figure.Figure object
            The figure object associated with the histogram.

        ax : matplotlib.axes._subplots.AxesSubplot objects
            The axes objects associated with the histogram.

    Example
    -------
        >>> # Define titles
        >>> plot_titles = [
        ...     'September 2019 Mean Radiance',
        ...     'March 2020 Mean Radiance',
        ...     'Change in Mean Radiance (September 2019 vs. March 2020)'
        ... ]
        >>> # Plot Sept 2019 and March 2020
        >>> fig, ax = plot_change(
        >>>     pre_change=radiance_monthtly_mean.get('2019').get('09'),
        >>>     post_change=radiance_monthtly_mean.get('2020').get('03'),
                titles=plot_titles)
    """
    # Get plotting extent
    with rio.open(extent_file) as src:
        src_extent = plotting_extent(src)

    # Find absolute values for change min & max
    percent_change_min_abs = np.absolute(percent_change.min())
    percent_change_max_abs = np.absolute(percent_change.max())

    # Determine max value (for plotting vmin/vmax)
    diff_vmax = (
        percent_change_min_abs
        if (percent_change_min_abs > percent_change_max_abs)
        else percent_change_max_abs
    )

    diff_vmin = -diff_vmax

    # Define radiance units
    units = "%"

    # Define titles
    diff_title = f"{title} ({units})"

    # Define color maps
    diff_cmap = "RdBu_r"

    # Use dark background
    with plt.style.context("dark_background"):

        # Create figure and axes objects
        fig, ax = plt.subplots(figsize=(12, 12))

        # Add super title
        plt.suptitle(f"{location} Nitrogen Dioxide Change", size=24)

        # Adjust spacing
        plt.subplots_adjust(hspace=0.15)
        plt.subplots_adjust(top=0.98)

        # Plot diff array
        ep.plot_bands(
            percent_change,
            scale=False,
            title=diff_title,
            vmin=diff_vmin,
            vmax=diff_vmax,
            cmap=diff_cmap,
            ax=ax,
            extent=src_extent,
        )

        # Add caption
        fig.text(
            0.5, 0.15, f"Data Source: {data_source}", ha="center", fontsize=16
        )

        # Set title size
        ax.title.set_size(20)

    # Return figure and axes object
    return fig, ax


def plot_histogram(
    radiance,
    location="South Korea",
    title="Distribution of Percent Change, Jan-Jun Mean NO2, 2019-2020",
    xlabel="Percent Change",
    ylabel="Pixel Count",
    data_source="European Space Agency",
    difference=True,
):
    """Plots the distribution of values in a radiance array.

    Parameters
    ----------
    radiance : numpy array
        Array containing raw values, mean values,
        or difference values.

    location : str, optional
        Name of study area location. Included in plot
        super-title. Default value is 'Penn State Campus'.

    title : str, optional
        Plot sub-title. Default value is 'Distribution of
        Radiance'. Intended for 'Distribution of September
        2019 Mean Radiance' or 'Distribution of the Change
        in Mean Radiance (September 2019 vs. March 2020).'

    xlabel : str, optional
        Label on the x-axis. Default value is 'Radiance'.

    ylabel : str, optional
        Label on the y-axis. Default value is 'Pixel Count'.

    data_source : str, optional
        Sources of data used in the plot.
        Default value is 'NASA Black Marble'.

    difference : bool, optional
        Boolean indicating if the array contains raw
        values or mean values (False) or contains
        difference values (True). Default value is False.

    Returns
    -------
    tuple

        fig : matplotlib.figure.Figure object
            The figure object associated with the histogram.

        ax : matplotlib.axes._subplots.AxesSubplot object
            The axes object associated with the histogram.

    Example
    -------
        >>> # Plot Sept 2019 vs. March 2020 change histogram
        >>> fig, ax = plot_histogram(
        ...     diff_sep_2019_march_2020,
        ...     title="Distribution of the Change in Mean Radiance",
        ...     xlabel='Change in Mean Radiance',
        ...     difference=True)
    """
    # Find absolute values for radiance min & max
    radiance_min_abs = np.absolute(radiance.min())
    radiance_max_abs = np.absolute(radiance.max())

    # Determine max value (for plotting vmin/vmax)
    plot_max = (
        radiance_min_abs
        if (radiance_min_abs > radiance_max_abs)
        else radiance_max_abs
    )

    # Define vmin and vmax
    hist_min = -plot_max if difference else 0
    hist_max = plot_max

    # Define histogram range
    hist_range = (hist_min, hist_max)

    # Define radiance units
    units = "%"

    # Use dark background
    with plt.style.context("dark_background"):

        # Create figure and axes object
        fig, ax = ep.hist(
            radiance,
            hist_range=hist_range,
            colors="#984ea3",
            title=title,
            xlabel=f"{xlabel} ({units})",
            ylabel=ylabel,
        )

        # Add super title
        plt.suptitle(f"{location} Nitrogen Dioxide", size=24)

        # Adjust spacing
        plt.subplots_adjust(top=0.9)

        # Add caption
        fig.text(
            0.5, 0.03, f"Data Source: {data_source}", ha="center", fontsize=14
        )

    # Return figure and axes object
    return fig, ax


def plot_normalized_histogram(
    data,
    location="South Korea",
    title="Distribution of Percent Masked, Jul 2018 - Jul 2020",
    xlabel="Percent Masked (%)",
    ylabel="Normalized Pixel Count (Probability Density)",
    data_source="European Space Agency",
):
    """Plots the distribution of data, normalized to a probability density.

    Parameters
    ----------
    data : numpy array
        Array containing to plot.

    location : str, optional
        Name of study area location. Included in plot
        super-title. Default value is 'South Korea'.

    title : str, optional
        Plot sub-title. Default value is 'Distribution of Percent Masked,
        Jul 2018 - Jul 2020'.

    xlabel : str, optional
        Label on the x-axis. Default value is 'Percent Masked (%)'.

    ylabel : str, optional
        Label on the y-axis. Default value is 'Normalized Pixel Count
        (Probability Density)'.

    data_source : str, optional
        Sources of data used in the plot.
        Default value is 'European Space Agency'.

    Returns
    -------
    tuple

        fig : matplotlib.figure.Figure object
            The figure object associated with the histogram.

        ax : matplotlib.axes._subplots.AxesSubplot object
            The axes object associated with the histogram.

    Example
    -------
        >>> # Plot normalized histogram
        >>> fig, ax = plot_normalized_histogram(percent_masked_arr)
    """
    # Plot histogram normalized to form a probability density
    with plt.style.context("dark_background"):
        fig, ax = plt.subplots(figsize=(12, 12))
        plt.hist(data, density=True, bins=20, color="#984ea3")
        plt.suptitle(f"{location} Nitrogen Dioxide", size=24)
        plt.subplots_adjust(top=0.9)
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        fig.text(
            0.5, 0.03, f"Data Source: {data_source}", ha="center", fontsize=14
        )

    return fig, ax


def extract_plotting_extent(raster_path):
    """Extracts the plotting extent from a raster.

    Parameters
    ----------
    raster_path : str
        Path to the raster file.

    Returns
    -------
    raster_extent : tuple
        Tuple containing (longitude min, longitude max,
        latitude min, latitude max).

    Example
    -------
        >>> # Extract plotting extent
        >>> extract_plotting_extent(
        ...  "S5P-OFFL-L3-NO2-20190101-20190630-MEAN-MOL-PER-M2.tif"
        ... )
        (125.0, 131.0, 33.1, 38.7)
    """
    # Raise error for invalid file path
    if not os.path.exists(raster_path):
        raise ValueError("Invalid raster file path.")

    # Get plotting extent
    with rio.open(raster_path) as src:
        raster_extent = plotting_extent(src)

    return raster_extent


def project_vector(vector_path, raster_path):
    """Projects a vector to match a raster CRS if the two differ.

    Parameters
    ----------
    vector_path : str
        Path to the vector file. The file that will be projected.

    raster_path : str
        Path to the raster file. The file that has the CRS to which
        the vector will be projected.

    Returns
    -------
    vector_projected : geopandas geodataframe
        Geodataframe of the vector in the same CRS as the raster file.

    Example
    -------

    """
    # Raise error for invalid file path
    if not os.path.exists(vector_path):
        raise ValueError("Invalid vector file path.")
    if not os.path.exists(raster_path):
        raise ValueError("Invalid raster file path.")

    # Project vector to raster CRS if the two do not match
    vector = gpd.read_file(vector_path)
    with rio.open(raster_path) as src:
        vector_projected = (
            vector.to_crs(src.crs) if vector.crs != src.crs else vector
        )

    return vector_projected


def create_polygon_from_extent(extent):
    """Creates a Shapely polygon from a bounding box extent.

    Parameters
    ----------
    extent : tuple
        Tuple containing (longitude min, longitude max,
        latitude min, latitude max).

    Returns
    -------
    polygon : shapely.geometry.polygon.Polygon

    Example
    -------

    """
    # Raise errors for invalid extent values
    if not isinstance(extent, tuple):
        raise TypeError("Extent must be a tuple")
    if len(extent) != 4:
        raise ValueError(
            "Extent must have 4 values: (lon_min, lon_max, lat_min, lat_max)."
        )
    if extent[0] == extent[1]:
        raise ValueError("Extent longitude min/max must differ.")
    if extent[2] == extent[3]:
        raise ValueError("Extent latitude min/max must differ.")
    if (extent[0] >= extent[1]) or (extent[2] >= extent[3]):
        raise ValueError(
            "Extent order must be: (lon_min, lon_max, lat_min, lat_max)."
        )
    if extent[0] < -180:
        raise ValueError("Minimum longitude must be >= -180.")
    if extent[1] > 180:
        raise ValueError("Maximum longitude must be <= 180.")
    if extent[2] < -90:
        raise ValueError("Minimum latitude must be >= -90.")
    if extent[3] > 90:
        raise ValueError("Maximum latitude must be <= 90.")

    # Create polygon from extent
    polygon = Polygon(
        [
            (extent[0], extent[2]),
            (extent[0], extent[3]),
            (extent[1], extent[3]),
            (extent[1], extent[2]),
            (extent[0], extent[2]),
        ]
    )

    return polygon


def read_geotiff_into_array(geotiff_path, dimensions=1):
    """Reads a GeoTif file into a Numpy array.

    Parameters
    ----------
    geotiff_path : str
        Path to the GeoTiff file.

    Returns
    -------
    array : numpy array
        Array containing the data.

    Example
    -------

    """
    # Read-in array
    with rio.open(geotiff_path) as file:
        array = file.read(dimensions)

    return array


def extract_geotiff_metadata(geotiff_path):
    """Extracts the metadata from a GeoTiff file.

    Parameters
    ----------
    geotiff_path : str
        Path to the GeoTiff file.

    Returns
    -------
    metadata : dict
        Dictionary containing the metadata.

    Example
    -------

    """
    # Extract metadata
    with rio.open(geotiff_path) as file:
        metadata = file.meta

    return metadata
