"""Portal GLib loop must not share Qt's default main context."""

from arc_helper.screen.portal import ScreenCastSession


def test_portal_uses_private_main_context():
    session = ScreenCastSession()
    assert session._context is not None
    # Default context identity — private context must be a distinct object.
    from gi.repository import GLib

    assert session._context is not GLib.MainContext.default()


def test_portal_close_before_open_is_safe():
    session = ScreenCastSession()
    session.close()
    session.close()
