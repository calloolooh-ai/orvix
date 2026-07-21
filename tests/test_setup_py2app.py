"""
tests for setup.py's py2app bundle config. this only imports the module
(everything below module level is plain data, the actual setup() call is
guarded by `if __name__ == "__main__":`), it never triggers a real build --
that's scripts/build_app.sh's job and takes a couple minutes, not something
to run on every test invocation.

what's worth pinning down here is the stuff that's easy to silently break
while editing the OPTIONS dict and would only be noticed by someone actually
running a build and eyeballing the result: the app has to stay a menu-bar-
only accessory (no dock icon), keep a stable bundle id across rebuilds, and
never re-bundle the dev/test tooling that lives in the same venv as the
runtime deps (see the excludes list and its comment for why that's a real
failure mode, not a hypothetical one -- a build without it pulled in pytest,
setuptools, and py2app itself).
"""

import setup


def test_setup_does_not_run_the_build_on_import():
    # if this module executed setup() at import time it would try to parse
    # pytest's own sys.argv as distutils commands and blow up; getting this
    # far at all is most of the test
    assert hasattr(setup, "OPTIONS")


def test_app_entry_point_is_the_gui_not_the_cli():
    # orvix.app should open the menu bar app, not run_live()'s plain-cli path
    assert setup.APP == ["orvix/gui.py"]


def test_bundle_id_is_set_and_stable():
    plist = setup.OPTIONS["py2app"]["plist"]
    assert plist["CFBundleIdentifier"] == setup.BUNDLE_ID
    assert plist["CFBundleIdentifier"] == "com.orvix.menubar"


def test_is_a_menu_bar_only_accessory_app():
    # no Dock icon, no Cmd-Tab entry -- matches how it already runs from a
    # terminal (rumps sets the same accessory policy at runtime)
    assert setup.OPTIONS["py2app"]["plist"]["LSUIElement"] is True


def test_dev_and_build_tooling_is_excluded_from_the_bundle():
    excludes = set(setup.OPTIONS["py2app"]["excludes"])
    # every one of these showed up in a real build before excludes existed,
    # bloating the bundle by ~10MB with tooling orvix never imports at runtime
    for name in ("pytest", "_pytest", "pluggy", "setuptools", "pkg_resources", "py2app"):
        assert name in excludes, f"{name} should be excluded from the built app"


def test_orvix_package_itself_is_not_excluded():
    # sanity check against a copy-paste mistake in the excludes list, since
    # that would silently produce a bundle that can't import its own code
    excludes = set(setup.OPTIONS["py2app"]["excludes"])
    assert "orvix" not in excludes
    assert setup.OPTIONS["py2app"]["packages"] == ["orvix"]


def test_argv_emulation_is_off():
    # orvix.app isn't a document-opening app, this is a Finder drag-and-drop
    # feature that doesn't apply and py2app warns if it's left on for one
    assert setup.OPTIONS["py2app"]["argv_emulation"] is False
