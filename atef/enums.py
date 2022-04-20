import enum


class Severity(enum.IntEnum):
    success = 0
    warning = 1
    error = 2
    internal_error = 3
