import logging
import pathlib
from typing import List, Optional

from qtpy import QtWidgets

logger = logging.getLogger(__name__)


def save_widget_screenshot(
    widget: QtWidgets.QWidget,
    prefix: str,
    path: Optional[pathlib.Path] = None,
    format: str = "png",
) -> Optional[str]:
    """Save screenshots of the indicated widget to ``path``."""
    if not widget.isVisible():
        return None

    screen = (
        widget.screen()
        if hasattr(widget, "screen")
        else QtWidgets.QApplication.instance().primaryScreen()
    )
    screenshot = screen.grabWindow(widget.winId())
    if screenshot.width() > 2 * widget.width():
        logger.warning(
            "Unable to take screenshot of widget %r; grabWindow took a full-"
            "screen screenshot.", widget.windowTitle()
        )  # macOS only?
        return None

    name = widget.windowTitle().replace(" ", "_")

    path = pathlib.Path(path if path is not None else ".")
    filename = str(path / f"{prefix}.{name}.{format}")
    screenshot.save(filename, format)
    logger.info("Saved screenshot: %s", filename)
    return filename


def screenshot_top_level_widgets():
    """Yield screenshots of all top-level widgets."""
    app = QtWidgets.QApplication.instance()
    for screen_idx, screen in enumerate(app.screens(), 1):
        logger.debug("Screen %d: %s %s", screen_idx, screen, screen.geometry())

    primary_screen = app.primaryScreen()
    logger.debug("Primary screen: %s", primary_screen)

    def by_title(widget):
        return widget.windowTitle() or str(id(widget))

    index = 0
    for widget in sorted(app.topLevelWidgets(), key=by_title):
        if not widget.isVisible():
            continue
        screen = (
            widget.screen()
            if hasattr(widget, "screen")
            else primary_screen
        )
        screenshot = screen.grabWindow(widget.winId())
        name = widget.windowTitle().replace(" ", "_")
        suffix = f".{name}" if name else ""
        index += 1
        yield f"{index}{suffix}", screenshot


def save_all_screenshots(
    prefix: str, path: Optional[pathlib.Path] = None, format: str = "png"
) -> List[str]:
    """Save screenshots of all top-level widgets to ``path``."""
    path = pathlib.Path(path if path is not None else ".")
    screenshots = []
    for name, screenshot in screenshot_top_level_widgets():
        fn = str(path / f"{prefix}.{name}.{format}")
        screenshot.save(fn, format)
        logger.info("Saved screenshot: %s", fn)
        screenshots.append(fn)
    return screenshots
