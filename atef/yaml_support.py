import yaml

_yaml_initialized = False


def init_yaml_support():
    """Add necessary information to PyYAML for serialization."""
    global _yaml_initialized
    if _yaml_initialized:
        # Make it idempotent
        return

    _yaml_initialized = True

    def int_enum_representer(dumper, data):
        """Helper for pyyaml to represent enums as just integers."""
        return dumper.represent_int(data.value)

    def str_enum_representer(dumper, data):
        """Helper for pyyaml to represent string enums as just strings."""
        return dumper.represent_str(data.value)

    # The ugliness of this makes me think we should use a different library
    from . import enums, reduce

    yaml.add_representer(enums.Severity, int_enum_representer)
    yaml.add_representer(enums.GroupResultMode, str_enum_representer)
    yaml.add_representer(reduce.ReduceMethod, str_enum_representer)
