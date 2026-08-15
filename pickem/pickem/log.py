from logging.handlers import RotatingFileHandler


class SafeRotatingFileHandler(RotatingFileHandler):
    """Rotate logs, but skip rename when another process has the file open.

    Windows cannot rename a file that Django runserver, Celery beat, or another
    worker already has open. Python then dumps a PermissionError traceback on
    every rollover even though logging itself still works.
    """

    def doRollover(self):
        try:
            super().doRollover()
        except PermissionError:
            pass
