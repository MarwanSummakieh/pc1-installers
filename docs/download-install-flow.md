# Completed downloads and controller setup

The browser and FDM Controller completion notifications create persistent receipts
for EXE/MSI files in the player's Downloads folder. Legacy FDM filenames resolve
only when unique. Completed torrent folders offer their setup programs; ambiguous
names, symlinks and files outside Downloads cannot enter automatic cleanup.

When PC1 is back on its home screen, a completed setup offers Install or Later.
Setup runs in the controller wizard. Library registration explicitly chooses a
game with native controller input or an app with controller pointer input. This
prevents the pointer profile that previously stopped Tekken recognizing its pad.

Cleanup is offered only after a newer setup job exits successfully and its
installed executable has been registered. Keep is the default. Explicit cleanup
checks the original file identity before removing the downloaded setup file.
Failure, cancellation, portable apps, replaced downloads and older jobs cannot
authorize removal. BIN/CAB payloads stay intact because a nearby file does not
prove package ownership. Installed game files are kept.

Torrent files stay available for seeding. Stop seeding and remove their contents
through FDM when desired; this flow does not delete torrent payloads.

The backend regression covers successful cleanup, failure/cancellation,
replacement, old jobs, torrent retention, ambiguous paths and foreign links.
The actual FDM → controller setup → installed game → confirmed cleanup flow
still requires physical acceptance on PC1's new image.
