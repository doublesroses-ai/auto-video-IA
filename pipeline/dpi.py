"""Чёткий текст в окнах на экранах с увеличенным масштабом.

По умолчанию Windows рисует окно так, будто экран обычный (96 точек на дюйм),
а потом растягивает картинку под настоящий масштаб — из-за этого текст выглядит
размытым. Здесь мы говорим системе, что нарисуем всё сами в полном разрешении.
"""
import ctypes
import os

DESIGN_DPI = 96.0  # размеры в коде рассчитаны на обычный экран


def enable() -> None:
    """Включает режим полного разрешения. Вызывать ДО создания окна Tk."""
    if os.name != "nt":
        return
    try:  # Windows 10 1703+: пересчитывается при переносе на другой монитор
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:  # Windows 8.1+
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except (AttributeError, OSError):
        pass
    try:  # Windows 7
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def scale_of(widget) -> float:
    """Во сколько раз экран крупнее обычного (1.25 при масштабе 125%)."""
    try:
        return widget.winfo_fpixels("1i") / DESIGN_DPI
    except Exception:
        return 1.0
