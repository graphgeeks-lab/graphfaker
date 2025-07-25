def parse_date_range(date_range: str) -> tuple:
    """
    Validate and parse a date range string in the format 'YYYY-MM-DD,YYYY-MM-DD'.

    Args:
        date_range (str): The date range string to validate.

    Returns:
        tuple: A tuple containing the start and end dates as strings.

    Raises:
        ValueError: If the date range format is invalid.
    """
    try:
        start_date, end_date = date_range.split(",")
        if len(start_date) != 10 or len(end_date) != 10:
            raise ValueError("Date range must be in YYYY-MM-DD format.")
        return start_date, end_date
    except ValueError as e:
        raise ValueError(f"Invalid date range format: {e}") from e
    except AttributeError:
        raise ValueError(
            "Date range must contain exactly two dates separated by a comma."
        )
