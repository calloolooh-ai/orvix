"""
setup.py

py2app build script: turns orvix/gui.py into a real orvix.app bundle, so it
can run without a terminal and (more importantly) so macOS ties Accessibility
+ Input Monitoring permission to orvix.app itself instead of whatever
terminal happened to launch python -m orvix.gui (see docs/SETUP.md step 5,
and the README's "whichever terminal you launch from" caveat -- building the
.app removes that caveat).

only used for building a distributable app, not for running orvix day to
day, so py2app isn't in requirements.txt: `pip install -r requirements-build.txt`
first, then see scripts/build_app.sh.

everything below module level is plain data on purpose (no argv-dependent
code outside the __main__ guard), so tests can `import setup` and check the
bundle config without triggering an actual build.
"""

from setuptools import setup

APP = ["orvix/gui.py"]
DATA_FILES = []

# CFBundleIdentifier: reverse-DNS, doesn't need to resolve to anything real,
# just has to be stable so macOS treats upgrades as the same app (keeping
# any Accessibility/Input Monitoring grant across rebuilds) instead of a
# fresh install each time.
BUNDLE_ID = "com.orvix.menubar"

OPTIONS = {
    "py2app": {
        # not a document/file-opening app, no need for py2app's Finder
        # drag-and-drop argv shimming
        "argv_emulation": False,
        # bundle the whole orvix package, not just whatever gui.py's static
        # imports pull in -- main.py, calibration.py etc. are all reachable
        # at runtime (menu callbacks import them lazily in a couple of
        # spots) and modulegraph's static analysis can miss that
        "packages": ["orvix"],
        # the dev venv also has pytest/setuptools/py2app itself installed
        # (they're all in the same .venv as the runtime deps, see
        # requirements.txt vs requirements-build.txt), and modulegraph's
        # static analysis pulls in anything importable it can find a path
        # to, test tooling included. none of this is ever imported at
        # runtime by orvix itself, so keep it out of the shipped bundle --
        # a real build measured ~35MB smaller with these excluded.
        "excludes": [
            "pytest", "_pytest", "pluggy", "iniconfig", "pytest_asyncio",
            "setuptools", "pkg_resources", "distutils",
            "py2app", "altgraph", "macholib", "modulegraph",
        ],
        "iconfile": None,  # no custom .icns yet, py2app's default stand-in icon
        "plist": {
            "CFBundleName": "orvix",
            "CFBundleDisplayName": "orvix",
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "orvix",
            # menu-bar-only: no Dock icon, no app menu, no Cmd-Tab entry.
            # matches how it already behaves when run via `python -m
            # orvix.gui` from a terminal (rumps sets the same accessory
            # policy at runtime); this is the bundle-level equivalent so a
            # packaged orvix.app looks the same on first launch.
            "LSUIElement": True,
            # High Sierra: comfortably below Monterey (what this is actually
            # built/tested on, see README), just a floor so the bundle
            # doesn't silently claim to run on something ancient it's never
            # been near. py2app itself still only builds for whatever arch
            # you build it on, this key doesn't change that.
            "LSMinimumSystemVersion": "10.13",
        },
    }
}

if __name__ == "__main__":
    setup(
        app=APP,
        data_files=DATA_FILES,
        options=OPTIONS,
        setup_requires=["py2app"],
    )
