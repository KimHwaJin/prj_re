"""No-op stand-in for the customer DataLake ``extract_data`` Tool."""


def extract_data(
    data_type: str,
    lot_cd: str | None = None,
    process: list | str | None = None,
    query_mode: str | None = None,
    start_dt: str | None = None,
    end_dt: str | None = None,
    limit: int | None = None,
    **kwargs: object,
) -> dict[str, object]:
    """Accept the customer extraction arguments and return a placeholder result.

    This function deliberately performs no file or DataLake access. It only
    preserves the planned call/return contract until the customer implementation
    replaces it. The following transform Tool supplies local wide mock data.
    """
    normalized_type = str(data_type).strip().lower()
    query_params = {
        "lot_cd": lot_cd,
        "process": process,
        "query_mode": query_mode,
        "start_dt": start_dt,
        "end_dt": end_dt,
        "limit": limit,
        **kwargs,
    }
    return {
        "data": {},
        "metadata": {
            "shape": [0, 0],
            "columns": [],
            "dtypes": {},
            "placeholder": True,
        },
        "preview_data": [],
        "shape": [0, 0],
        "columns": [],
        "cache_status": "mock",
        "file_path": "",
        "data_type": normalized_type,
        "query_params": query_params,
        "cache_key": "",
    }
