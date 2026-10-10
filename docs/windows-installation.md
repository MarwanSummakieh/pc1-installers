# Windows installation

Files accepts ordinary Windows EXE and MSI installers without a recipe. Setup
runs interactively through umu/Proton in a separate managed prefix for each
attempt. This enables general installation; it does not guarantee compatibility
with every Windows program. Windows kernel drivers, some anti-cheat systems and
apps requiring unavailable Windows services remain incompatible.

## Controller flow

1. Select an `.exe` or `.msi` anywhere accessible in Files and press Cross/A.
2. Choose **Run Windows setup**. Keep any adjacent CAB/BIN installer files in
   the same folder. First-time runtime preparation can take several minutes.
3. Complete the wizard in PC1's controller setup screen. D-pad selects controls;
   Cross/A chooses them, and Back invokes the wizard's Back button when present.
   Text fields open the shared controller keyboard. Home opens Resume, Return to
   Files and Stop setup; returning to Files leaves setup running. Select the
   installer again and choose Resume setup to return. Stop retains partial files.
   **Original Windows setup** remains available for unsupported pages and uses
   the controller pointer; stopping controller setup and choosing Original starts
   a fresh attempt.
4. After successful setup, the helper automatically adds a program identified
   by a unique Desktop/Start Menu shortcut or a single launchable executable.
   Crash reporters and bundled runtime installers are excluded. A unique game
   executable matching a prelauncher's shortcut title is preferred to that
   prelauncher; installs in the managed Games mapping use native controller input.
   The card is published during setup completion and home updates on the next
   half-second installation poll, without reopening Install or rebooting.
   Ambiguous or interrupted setups with usable programs appear on home as
   **Finish adding**; select that card to choose the executable and input mode.
   Failed or cancelled setup never automatically claims an installation succeeded.
   The list scans C: within that attempt, including Program Files and AppData,
   excluding Wine built-ins, linked directories and maintenance executables.
   Files installed outside the managed C: drive are not discovered.
   Valid Desktop and Start Menu shortcuts provide friendly names and put their
   target executables first in the list. Only local targets inside that attempt
   are accepted.
5. Launch the new card. Back from the installation screen restores Files to
   the same folder. Pending cards open selection without rerunning setup.

For a standalone EXE, **Add as portable app** creates a card without running
setup. Keep its original file, folder and any removable drive available.

Each new interactive installation owns a folder under `~/Games/<attempt-id>`.
Its `C:\Games` directory maps to that folder, so `C:\Games\Game Name` installs
the files in `~/Games/<attempt-id>/Game Name`. Before opening setup, the helper
creates this folder and verifies it can write through the `C:\Games` mapping.
Identified Inno/FitGirl installers start with `C:\Games` selected using `/DIR`,
including controller setup, so their original drive default cannot cause an
invalid-location prompt before the destination page. Controller setup also
initializes the destination field if the installer replaces that launch default.
Editing the destination remains available through the controller keyboard; returning
to a page preserves the user's edit. The original interface also defaults to
`C:\Games` for identified Inno installers using the documented
[`/DIR` parameter](https://jrsoftware.org/ishelp/topic_setupcmdline.htm).
Other Windows installers retain their own directory controls.
The location beneath the content panel shows the actual Linux Games folder.
Discovery, library registration, launching, removal and interrupted-attempt cleanup
include this explicitly owned mapping. Other drive links remain excluded; removing
an attempt keeps neighbouring games and the source installer.

Home → **Minimize** returns to the shell while preserving the running process.
Select its library card, or **Resume** in the processes menu, to return to the
same app. The shell tracks one external app at a time; close or resume that app
before launching another. A minimized setup remains available through the
processes menu. **Close** stops the tracked runtime group and returns focus.

On the native gamescope session, pointer applications' ordinary windows are
resized to the display's native pixel dimensions at handover and resume. This
prevents a small Wine window from being stretched across the screen. Dialogs,
transient windows and application fullscreen modes retain their requested size.
Minimize unmaps the application's windows so gamescope returns to home even
without Steam integration; Resume maps those same windows again.

Select a Windows library card and press **Options** to remove it. The confirmation
opens on **Cancel**; **Remove app** stops it and deletes its managed prefix,
including saved data. Portable source files and the original installer remain.
The Install screen also offers removal and **Remove setup files** for unfinished
attempts. Requests contain an app ID, never a deletion path; the helper validates
the prefix as a direct real directory inside its owned `prefixes` root and the
matching Games folder as a direct real directory beneath the user's `~/Games`.
It rejects other external destinations and symlinked metadata directories.

The top-bar Install screen also offers the optional automatic **7-Zip 26.03
x64** recipe. Recipe installs run in the background with hash verification and
known silent arguments. Back leaves those installs running; Cancel stops them.

## Interactive helper and data

Controller setup uses `manager.py guided` and the image-built Windows
`setup-bridge.exe`. The unmodified installer runs on a private Xvfb display in
its own prefix. The helper copies the image-owned adapter into the attempt's
private UI directory, which remains visible inside Proton's Steam runtime.
The adapter reads visible controls from the installer's process
family and publishes an atomic `setup-ui/<id>/page.json`. Godot presents the
actual page copy, buttons, checked states, text fields, dropdown items and progress.
Long text remains available in full through a controller-scrollable reader.
Actions contain the page fingerprint and a current control ID; the adapter
re-enumerates before accepting them and rejects stale, disabled or unknown actions.
No silent arguments or automatic acceptance are inferred.

Standard buttons, edits, combos and accessible checkbox/radio children have native
rows. The wizard uses a page heading and scrollable content panel with a fixed
horizontal Back / Next or Install / Cancel footer. D-pad Up/Down moves through
options and into the footer; Left/Right moves between footer actions; A activates
the real control, and B uses Back. FitGirl's RAM limit, language/component choices,
runtime installers, shortcut choices, verification and finish-page actions stay in
the live page's order and retain their actual state when present. No repack-specific
options are invented or automatically accepted.
The FitGirl speaker button is labelled **Installer music** in the footer. Select
it with the D-pad and press A to mute or unmute; the button preserves the native
speaker graphic. That graphic can lag behind playback in this installer, so it
is not presented as a separate checked on/off switch. The bridge recognizes the verified icon-only footer
button in FitGirl-named installer sources and uses its own mouse handler on the
private setup display. It does not invent a switch for installers without that
control or change system-wide volume. Unknown icon-only buttons retain their
existing handling.

A music-only acceptance test used the real TEKKEN 8 FitGirl welcome page in a
temporary prefix and private display, without advancing into installation.
Playback-output measurements confirmed five successive presses alternated
between silence and audible playback. Controller fixtures also verify that A
addresses the speaker control without pressing Next, that Left/Right includes
it in the footer, and that music remains available on an unsupported page.
Older Inno Setup `TNewCheckListBox` controls retain their rendered options in
an enlarged panel: enter Edit options, use D-pad and A on the real control, then
B to return to the page. This preserves the actual checked state without guessing
private Delphi data layouts. Other unsupported interactive controls block native
advancement. The original wizard is the fallback; this is not universal EXE support.

The helper tracks preparation as well as the runtime so Stop works during startup.
When the bridge reports that setup ended, the helper closes its runtime group and
hidden display even if newly installed Windows services keep Proton waiting.
Executable discovery and automatic library registration then run; ambiguous
results retain explicit program selection.
Running guided attempts can be recovered after a shell restart by checking the
helper's recorded PID and process start time. UI data is removed with its attempt.

The shell invokes `manager.py setup local-<unique-id> /absolute/source.exe` in
the player session. MSI uses `umu-run msiexec /i Z:\\...`; EXE receives no guessed
silent arguments. Argument arrays and the source folder preserve filenames and
multipart installers. File headers are checked before starting the runtime.

`jobs/<id>.json` stores progress and executable choices; `prefixes/<id>` retains
the attempt, including partial files after cancellation. `register <id> <choice>`
validates the choice again before committing `apps/<id>.json`. The shell reads
these manifests directly, so interactive installation needs no background worker.
Logs live in `logs/<id>.log`. Close verifies recorded process start times and
stops the setup wrapper and runtime process group. Prefixes are compatibility
environments, not security sandboxes.

## Automatic recipe worker and data contract

`marwanos-windows.service` runs `windows/manager.py daemon` as **player**.
It has a private `/tmp` and device namespace; each installer gets an Xvfb display
with TCP disabled. It does not inherit the shell's X11/Wayland display or Steam
window tags. GLX is disabled on the installer's Xvfb: the NVIDIA image's GLX
initialization crashed in the GPU-less acceptance container, while the installer's
2D UI needs no GLX. It does not affect the game's display or rendering runtime.
This prevents installer windows and runtime setup dialogs from
appearing on the TV. Wine prefixes themselves are not security sandboxes.

The default data root is `~player/.local/share/marwanos/windows`:

| Path | Contract |
|---|---|
| `requests/*.json` | Unique requests, published via `.tmp` then rename |
| `state.json` | Atomic state snapshot: status, detail, progress, job ID, heartbeat, recipes, source candidates and library |
| `prefixes/<recipe>-<uuid>/` | Separate prefix per attempt; failed/cancelled attempts removed |
| `apps/<recipe>.json` | Committed application manifest; contains prefix, executable and argument arrays |
| `logs/<recipe>.log` | Most recent installer/runtime output for developer diagnosis |
| `running/<recipe>.json` | Runtime group and wrapper PID/start times, checked before Close signals them |

Install requests are `{"verb":"install","recipe_id":"7zip","source_id":"download"}`.
For a local file, use its published candidate ID instead of `download`. Cancel
requests are `{"verb":"cancel","job_id":"<current job>"}`; a stale cancellation
cannot cancel a later installation. One installation runs at a time. Closing the
screen does not cancel it. Requests never contain shell commands or caller-chosen
installation destinations.

The worker scans Downloads and `/run/media/player` to depth three, at most 512
directories per root and 200 candidates. Filename matching identifies a possible
recipe; size and SHA-256 of the copied installer authorize execution. Copies go
to private temporary storage so ejecting/changing the source after verification
cannot replace the executable being run.

The shell treats a heartbeat older than 15 seconds as unavailable. Interrupted
active state becomes a retryable failure when the worker restarts. A hard power
loss may leave an uncommitted prefix; it is never published as installed. Local
attempts can be discarded from the Install screen. On worker restart, abandoned
runtime groups with verified PID/start identities are stopped; wrappers still
running are preserved.

`manager.py launch <recipe>` uses the committed prefix and keeps a wrapper alive
to track the application. `stop <recipe>` signals its process group. Managed
entries carry argument arrays directly; they bypass the legacy whitespace-based
`apps.tsv` command encoding. Existing standalone executable discovery remains
separate.

## Recipe provenance and runtime

The image owns `windows/recipes.json`. The 7-Zip installer comes from the
[publisher's 26.03 release](https://github.com/ip7z/7zip/releases/tag/26.03).
The size and SHA-256 were calculated from that release asset on 2026-09-05.
Version bumps must update the URL, filename, hash, size and validation evidence
together. There is no runtime “latest installer” lookup.

The [7-Zip FAQ](https://www.7-zip.org/faq.html) documents `/S` and `/D` for its
EXE installer. This recipe uses `/S` and `/D=C:\PC1\7-Zip` and verifies `7zFM.exe`,
`7z.exe` and `7z.dll`. 7-Zip's license and source are available from
[7-zip.org](https://www.7-zip.org/); PC1 downloads the unmodified installer on demand.

The existing umu runtime receives `WINEPREFIX`, `GAMEID=0`, and
`PROTON_VERB=waitforexitandrun`, following its
[documented invocation](https://github.com/Open-Wine-Components/umu-launcher/blob/main/docs/umu.1.scd).
umu may download Proton and the Steam Linux Runtime on first use, so first-time
installation needs network access and can take minutes. Runtime provisioning is
currently stage-based progress, not an invented percentage. The recipe pins the
installer; Proton selection still follows the existing umu default.

## Verification

```bash
python3 -m unittest discover -s tests -v
GODOT_BIN=/path/to/pinned/godot bash scripts/check-windows-shell.sh
GODOT_BIN=/path/to/pinned/godot bash scripts/check-window-geometry.sh
```

Tests need Linux, Python 3 and Xvfb, with a writable X11 socket directory. Under
WSLg, run them inside a container so `/tmp/.X11-unix` is not WSLg's read-only
mount. The suite exercises the real worker and hidden display with a fake Windows
runtime: hashing, explicit argument boundaries, verification, timeout,
cancellation, retry state, duplicate installs, discovery, launch/close, stale
PID protection, orphan cleanup, safe removal and portable-file preservation.
The Godot controller fixture covers minimize/resume without respawning, removal
confirmation, cancellation and focus. These are not a Proton compatibility test.
The geometry fixture checks native backing pixels, minimize/resume of the same
window and preservation of dialog and fullscreen dimensions on a real X11 display.

Fixture overrides (development only): `MARWANOS_WINDOWS_HOME` for the state root,
`MARWANOS_WINDOWS_RECIPES` for the worker's recipe file, and
`MARWANOS_WINDOWS_RUNTIME` for its executable runner. Production uses the
image-owned recipe and umu. The shell and worker must share the same state root.
`MARWANOS_WINDOWS_HELPER` lets a bench shell use its matching staged helper.
`tests/test_windows_local.py` covers general setup arguments, session display,
multipart files, invalid files, selection, portable apps and cancellation.
The Files controller checks cover EXE/MSI routing, selection and focus restoration.

`tests/windows_setup_wizard.gd` covers controller selection, Back, keyboard edits,
stale modal revisions, dropdowns, custom options, unsupported controls and full
license text. It runs as part of `scripts/check-windows-shell.sh`.

### Controller setup bench evidence, 2026-10-05

The physical NVIDIA/gamescope bench completed its existing
`/home/player/fdm_x64_setup.exe` (FDM 6.35.1.7021) in an isolated acceptance prefix.
Automated Godot joypad events operated the production setup controls: install mode,
all 30 languages (Dansk and back to English), destination keyboard, Start Menu,
Back and forward navigation, the actual desktop-shortcut checkbox, summary,
Install and Finish. The real bridge rejected a deliberately stale action. The
resulting FDM executable was registered and launched at 3440×1440, then closed.
The run reported zero failures and no script/image errors. This uses simulated
joypad events on the real bench, not physical-button or hotplug acceptance.

Evidence is retained in `out/fdm-controller-*.png` and on the bench in
`~player/.local/share/marwanos/guided-acceptance/evidence`. The optional harness is
`tests/windows_setup_bench.gd`. The existing installed FDM app and source EXE were
retained; disposable acceptance applications can be removed independently.

The normal bench shell was refreshed using
`scripts/install-controller-setup-bench.sh`. Its existing override unit now mounts
the matching shell and Windows helper directory from
`/var/marwanos/controller-setup-20261005`. The original service definition is saved
as `/etc/systemd/system/marwanos-bench-fixes.service.before-controller-setup-20261005`.
To roll back, stop that unit, restore its saved definition, reload systemd and start
the unit, then terminate the supervised shell to reload the previous export.
No OS image was published or rebooted for this test.
The normal exported shell also recovered the same live FDM setup after a shell
restart, retained compositor focus and displayed its native controller rows.
Its compositor screenshot is `out/fdm-controller-normal-session.png`. Stop and
discard removed only that disposable check and returned to home with the original
FDM card. Godot fixtures separately verify that job updates do not steal setup
focus, Return to Files restores focus, and Resume returns it to the wizard.

Before calling this slice appliance-verified, run the real pinned installer
on the target with only a controller:
install, return home during installation, launch from the new card, open overlay,
resume, close, unplug/replug the controller, cancel and retry. Verify that the
game/application does not also receive overlay button presses. Steam input
ownership is handled by the session controller broker. Physical-controller
hotplug and gamescope focus behavior still require a target run.

### Lifecycle evidence from 2026-10-05

- The cached, hash-checked 7-Zip 26.03 installer completed its real interactive
  wizard through umu 1.4.4 / UMU-Proton-10.0-4 without network access. Selecting
  `7zFM.exe` committed a new managed library entry.
- The current Godot source launched that real app, opened the controller overlay,
  minimized while preserving the wrapper PID, resumed the same process, verified
  X keyboard focus on the app, and closed its runtime group. Managed removal
  deleted the disposable prefix and retained the original installer.
- This run used Xvfb and a fixture that mirrored gamescope's base-layer focus
  property to X focus; it verifies real Wine process life and the connected shell
  code, with compositor arbitration still a hardware acceptance check.
- A second real-runtime run used Openbox and xcompmgr with
  `MARWANOS_COMPOSITOR=x11`, without that focus fixture. The real active-window
  property changed to the home window on Minimize and back to the Windows app on
  Resume. Three additional minimize/resume cycles preserved the same wrapper
  PID, and typing focus, close and removal passed. Launch readiness checked the
  focused window's process ancestry, including nested runtime PIDs. An independent
  X window with an unrelated live PID sharing the prefix was rejected, preventing
  a stale setup window from prematurely completing a new launch. Shell focus
  waits briefly for Openbox to manage Godot's recreated window. This is the
  VM-compatible session path; production GPUs continue to use gamescope.
- The final Python regression run passed all 47 tests, including exclusive-lock
  protection against orphan cleanup racing a new launch. The Godot Windows
  controller fixture reported zero failures. The real Openbox acceptance also
  exited successfully with zero lifecycle failures.
- `tests/windows_real_shell.gd` and `out/windows-lifecycle-real.py` provide the
  optional real-runtime check. The standard fixture also verifies removal opens
  on Cancel, Back writes no request, and confirmed removal writes one request.
- The final booted image (`337e11badd5a`, built `2026-10-05T01:41:33Z`) passed
  actual service acceptance with SELinux Enforcing and no development override.
  Controller input through a physical kernel uinput fixture started the pinned
  recipe from the UI. The service provisioned Proton and Steam Linux Runtime on
  first use, published the card, and launched the real app. Four minimize/resume
  cycles retained the same verified process identities and actual foreground
  focus changed correctly. Close, removal cancellation and confirmed removal
  passed. All 43 controller commands were exclusively grabbed and produced no
  escaped physical or application-pad events. Evidence and screenshots are in
  `out/vm-windows-acceptance/`. The guest retained 7.0 GiB free after provisioning.

### Evidence from 2026-09-05

- The general `setup` path opened the real 7-Zip wizard on the session display
  under Xvfb, completed with interactive input and no silent arguments, and
  offered the three installed 7-Zip executables. Explicitly selecting `7zFM`
  produced a manifest; launch mapped its real window and managed Close ended it.
  This used the cached umu/Proton runtime described below, not a recipe install.
- All 27 Python regression tests passed; the seven general setup tests passed
  again after the runtime-file filter. Both Godot controller suites passed with
  the final shell changes. The updated export started headlessly
  with its browser extension, and the staged bundle checksums passed.
- The pinned 7-Zip installer downloaded, passed its hash check and installed
  unattended through umu 1.4.4 in a disposable container based on the existing
  MarwanOS image, running as player. umu provisioned UMU-Proton-10.0-4 and
  Steam Linux Runtime sniper 3.0.20260805.254768. All three expected files were
  present and a library manifest was committed.
- The real installed `7zFM.exe` mapped a visible 7-Zip window under Xvfb, then
  the managed Close command ended the launch wrapper. A screenshot was inspected.
- The exported shell launched the real installed app, activated its controller
  pointer bridge, opened/resumed its overlay, and closed the managed app. Xvfb
  supplied the display and a fixture supplied gamescope's focus property; this
  verifies the connected code paths but not physical compositor/input behavior.
- The shell exported with Godot 4.7.1 and displayed the installation screen under
  Xvfb. The layout and installed state were inspected visually.
- `tests/windows_shell.gd` drives Godot joypad events through installation,
  cancellation, return-home, library publication and launch/overlay/close.
  Worker state and compositor handoff are fixtures in that test, not hardware
  evidence. The Python suite tests the actual worker with a fake runtime.

No new OS image was deployed to the physical appliance. Controller hotplug,
Steam ownership, real compositor handoff and TV behavior remain target checks.

## Upgrading a development bench

The 2026-10-06 physical candidate boots the image-owned installer worker with
bench overrides retired. Its real embedded-browser 7-Zip 26.04 run exposed a
modeless Close hang: synchronous foreign-thread button activation destroyed the
dialog but did not advance the installer's GetMessage loop. Native button actions
now queue BM_CLICK, allowing the installer to return its genuine exit code.
The real Win32 regression (`tests/setup_bridge_exit.c` and its Python runner)
reproduces the old windowless active process, checks stale-action rejection and
requires actual installer/bridge exit zero from the production bridge in an
isolated UMU-Proton prefix/display. It passed on PC1's installed runtime. The
final candidate `0be4ae6` also passed the genuine embedded-browser duplicate
download, guided Install/Close (actual exit zero), explicit app registration,
confirmed source cleanup and installed-app launch/minimize/same-process
resume/close cycle. No success is inferred from an empty dialog. See the dated
[acceptance record](acceptance-20261006.md) for the baked-image retest and hardware
limits.

The `build` workflow publishes `ghcr.io/marwansummakieh/marwanos:latest` plus
a dated version tag. Wait for its Push step to succeed, then run
`sudo bootc upgrade` on the bench. Before rebooting, disable an existing
`/var/marwanos/dev-shell/marwanos-shell` override by removing its executable bit:
`sudo chmod a-x /var/marwanos/dev-shell/marwanos-shell` (only if that file exists).
The session then uses the shell and helper shipped together in the OS image.
Final image acceptance requires `/var/marwanos/devmode` to be absent. The image
enables SSH separately; that development marker is unnecessary for its checks.
After `sudo systemctl reboot`, `/usr/share/marwanos/build-info` identifies the
running build. The old override remains on disk and can be re-enabled explicitly.
