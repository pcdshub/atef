import enum


class Severity(enum.IntEnum):
    """Severity results for running comparisons."""
    #: A successful result without any issue.
    success = 0
    #: A successful result but with something worth noting.
    warning = 1
    #: A failing result.
    error = 2
    #: A failing and unexpected result.
    internal_error = 3


class GroupResultMode(str, enum.Enum):
    """How results of a group should be interpreted."""
    #: All items must succeed.
    all_ = "all"
    #: At least one item must succeed.
    any_ = "any"
