import logging


PACKAGE_NAME = "graphfaker"
LOGGING_FORMAT = f"{PACKAGE_NAME}:%(asctime)s - %(name)s - %(levelname)s - %(message)s"

logger = logging.getLogger(PACKAGE_NAME)


# Set the default logging level to INFO.
logger.setLevel(logging.INFO)


console_handler = logging.StreamHandler()

console_formatter = logging.Formatter(LOGGING_FORMAT)
console_handler.setFormatter(console_formatter)

logger.addHandler(console_handler)


def add_file_logging(file_path=f"{PACKAGE_NAME}.log"):
    """
    Add file logging to the logger.

    Args:
        file_path (str): Path to the log file. Defaults to `graphfaker.log`.

    Example:
        from graphfaker import add_file_logging
        # Configure custom file log
        add_file_logging("my_log.log")
    """
    file_handler = logging.FileHandler(file_path)
    file_formatter = logging.Formatter(LOGGING_FORMAT)
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)


def configure_logging(level=logging.WARNING):
    """
    Configure the logging level for the package.

    Args:
        level (int): Logging level (e.g., logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR)

    Example:
        # Set logging level to INFO
        from graphfaker import configure_logging
        import logging
        # Configure logging to INFO level
        configure_logging(logging.INFO)
    """
    logger.setLevel(level)
    for handler in logger.handlers:
        handler.setLevel(level)
