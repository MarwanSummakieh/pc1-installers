# FitGirl controller setup update, 2026-10-05

PC1 (`192.168.50.206`) now uses the updated shell, Windows setup manager, and
native setup bridge from `/var/marwanos/controller-setup-20261005`. The existing
`marwanos-bench-fixes.service` keeps these overrides across restarts. Deployment
backups are in `/var/tmp/pc1-fitgirl-music-update/backup`.

The wizard preserves the installer's page and options in a scrollable panel,
with a controller footer and a labelled **Installer music** action. Native
TEKKEN 8 music tests confirmed successive presses mute and restore playback;
the native graphic can lag behind playback. D-pad and A fixtures cover its
navigation, including pages with unsupported controls.

Interactive installs create a writable, owned `~/Games/<attempt-id>` folder
and map it as `C:\Games`. The manager checks write access through that mapping
before launch. Identified Inno installers receive `/DIR` at launch, so the
location is selected before the destination page appears. The observed FitGirl
destination uses `TEdit`; the bridge recognizes that control in FitGirl sources
as well as Inno's `TNewEdit`.

Validation passed 30 local-installation tests, 17 recipe-installation tests,
native destination/music recognition and command tests, and controller/layout
fixtures. The native bridge builds with `-Wall -Wextra -Werror`.

On the user's instruction, all five existing TEKKEN 8 attempts and the temporary
music/location probe copies were removed. The existing FDM installation remains.
Downloads source file sizes and modification times were unchanged, and the
installer SHA-256 remained
`351318ed9377090fe267f204d0471ce872f4807523df705240b931f62ed00dd8`.

A fresh installation was started from
`/var/home/player/Downloads/TEKKEN 8 [FitGirl Repack]/setup.exe`, with the default
components and the 2 GB RAM limit for PC1's 8 GB of memory. Its owned destination
is `/var/home/player/Games/local-tekken8-fresh-1791234744`. The real destination
page selected `C:\Games` automatically, downloads completed, and the installer
entered extraction. This record does not certify completion of the game install.

A player-owned, one-shot completion helper is running for this attempt. On the
real Finish page it clears launch/web/redirect actions, closes the installer,
verifies every file against `_Redist/fitgirl.md5`, and registers the primary
TEKKEN 8 executable only after a successful installation and integrity check.
It does not retry or remove a failed install. Its state is published to
`~/.local/share/marwanos/windows/fresh-tekken-state.json` and its log is
`~/.local/share/marwanos/windows/logs/local-tekken8-fresh-1791234744-completion.log`.

## Microsoft runtime navigation fix, 2026-10-06

The x86 Microsoft Visual C++ 2015–2022 prerequisite page exposed a
`ThemeStaticOwnerDraw` logo as an unsupported control. This blocked its standard
agreement checkbox and Install button, and the unsupported-page rule also
disabled Close. [WiX's theme implementation](https://github.com/wixtoolset/wix3/blob/develop/src/libs/dutil/thmutil.cpp)
derives this decoration class from `Static`.

The bridge now treats that class as read-only. The shell also accepts this class
from a bridge already running, so the current installation need not restart.
Supported choices stay editable on unsupported pages, and Close remains
available; genuinely unsupported choices still block advancing. Native disabled
Install buttons stay disabled until the installer enables them.

Controller fixtures verify Down reaches the agreement after the license reader,
A toggles its real control, Down reaches enabled Install, and Right reaches
Close. The full shell/wizard/layout checks and a native static-logo/checkbox/button
fixture passed. The updated shell and bridge were deployed with backups under
`/var/tmp/pc1-vc-controller-20261006/backup`, restarting only the shell.

## Game installation completed, 2026-10-06

The installer exited successfully. The completion helper verified all 533 files
against the supplied MD5 manifest, registered `TEKKEN 8.exe` as
`managed.local-tekken8-fresh-1791234744`, and published the TEKKEN 8 library card.
The card was withheld while verification ran, which explains the temporary gap
between the installer finishing and the home-screen entry appearing.
