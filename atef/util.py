import ophyd


def ophyd_cleanup():
    """Clean up ophyd - avoid teardown errors by stopping callbacks."""
    dispatcher = ophyd.cl.get_dispatcher()
    if dispatcher is not None:
        dispatcher.stop()
